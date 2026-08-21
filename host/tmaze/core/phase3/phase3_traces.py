"""
phase3_traces.py - live Phase 3 within-session metric TRACES (pure core).

Where ``phase3_stats.compute`` reduces a published
``phase3_log.Phase3StateSnapshot`` to a set of scalar training measures (the
bottom-strip text readout; docs/decisions/0052), this module produces the
per-trial TIME SERIES behind those same measures, so a live panel can plot how
they move over a session instead of only showing their latest value (see
docs/decisions/0114). It is the live twin of the offline
``analysis/rolling_metrics.build_traces``, but reads the immutable snapshot
directly (stdlib only, no pandas) rather than a loaded CSV bundle.

Everything is derived from the snapshot ALONE (the same thread-safe hand-off the
ribbon and text stats read), so a caller re-reads ``published_state()`` each poll
and calls :func:`build_traces`. One point is emitted per COMPLETED trial (a trial
the snapshot has scored CORRECT/INCORRECT), placed on a wall-clock x-axis:
``t`` is seconds since the earliest trial-start stamp in the table.

Series produced (each a list parallel to the completed trials):
  - ``iti_med_s`` - rolling MEDIAN inter-trial interval in seconds, the
    disengagement signal. A trial's ITI is its DEAD TIME:
    ``trial_start_iso[N] - trial_end_iso[N-1]`` - the interval from the rat
    closing the previous trial's loop back at the hub to it next departing up
    the stem to begin this one (what climbs as the animal checks out; see the
    trial-start/-end definitions in phase3_log/phase3_task and docs/decisions/
    0114). This is a cleaner disengagement measure than the start-to-start gap
    the offline ``rolling_metrics`` uses. A default 45 s threshold (matching
    ``analysis/mode_label.ModeThresholds.disengaged_gap_s``) is a plotted line
    the view draws, not a value computed here.
  - ``accuracy`` + per-choice-cue ``high_acc`` / ``low_acc`` - rolling correct
    fraction over the OUT-OF-BLOCK (uncoerced) completed trials in the window.
  - strategy (rolling, over out-of-block trials): ``side_bias_left`` (P(LEFT)),
    ``switch_rate`` (behavioural alternation: P(switch) over truly-adjacent own
    pairs), ``win_stay``, ``lose_shift``, ``cue_follow`` (P(chose the cued arm)).
  - closed-loop trigger signals: ``dir_bias`` (signed directional-ERROR bias,
    the session-level twin of the measure the debias scheduler acts on) and
    ``sched_alt_rate`` (the scheduler's per-trial cue-aware alternation rate, a
    passthrough of the row's ``alt_rate``; blank outside an intervention run).
    The view pairs these with the snapshot's ``trigger_bias`` / ``alt_trigger_
    rate`` threshold lines.
  - ``blocks`` - contiguous INTERVENTION spans (start/end x + kind) for shading.

Rolling windows are the trailing ``window_n`` COMPLETED trials (default 20, as in
phase3_stats); a rate is ``None`` until its denominator reaches ``min_den``
(default 3) so an early window doesn't show a spuriously extreme value.

Pure stdlib, headless-testable over deterministic snapshots (build a Phase3Log,
fill it via its public methods incl. ``trial_started`` / ``trial_ended`` for the
ITI stamps, ``.snapshot(idx, armed=...)``):
    python -m unittest test_phase3_traces
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from tmaze.core.phase3.phase3_log import Phase3RowView, Phase3StateSnapshot
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.phase3.trial_sequence import HIGH, LOW
# Reuse the settled definitions from the scalar readout rather than re-deriving
# them, so the live traces and the text panel never disagree: the ISO parser and
# median helper, plus the arm / intervention labels and the default window.
from tmaze.core.phase3.phase3_stats import (
    DEFAULT_WINDOW, INTERVENTION_LABEL, LEFT_LABEL, RIGHT_LABEL, _median,
    _parse_iso)

# Minimum denominator before a rolling rate is reported (else None). Mirrors
# analysis/rolling_metrics.DEFAULT_MIN_DEN - a few samples so an early window
# isn't dominated by one trial. See docs/decisions/0114.
DEFAULT_MIN_DEN = 3

# Default "disengaged" inter-trial-interval line (seconds). Not used to compute
# anything here (the view draws it); kept as the single source for that default
# so it stays in step with analysis/mode_label.ModeThresholds.disengaged_gap_s.
DEFAULT_DISENGAGED_GAP_S = 45.0


@dataclass(frozen=True)
class BlockSpan:
    """A contiguous run of INTERVENTION (corrective) completed trials, in x
    (seconds since t0), for shading behind the traces. ``kind`` is the block
    kind (``side_bias`` / ``alternation``; "" if unset)."""
    start_t: float
    end_t: float
    kind: str


@dataclass(frozen=True)
class TraceResult:
    """Parallel per-completed-trial series + intervention spans for one snapshot.

    Every list has one entry per completed trial, in order; rate series hold
    ``None`` where their window denominator was below ``min_den`` (or undefined).
    ``t`` is seconds since ``t0_iso`` (the earliest trial-start stamp); an entry
    is ``None`` only if a trial carried no usable timestamp at all.
    """
    t: List[Optional[float]] = field(default_factory=list)
    trial: List[int] = field(default_factory=list)
    # engagement
    iti_med_s: List[Optional[float]] = field(default_factory=list)
    # accuracy
    accuracy: List[Optional[float]] = field(default_factory=list)
    high_acc: List[Optional[float]] = field(default_factory=list)
    low_acc: List[Optional[float]] = field(default_factory=list)
    # strategy
    side_bias_left: List[Optional[float]] = field(default_factory=list)
    switch_rate: List[Optional[float]] = field(default_factory=list)
    win_stay: List[Optional[float]] = field(default_factory=list)
    lose_shift: List[Optional[float]] = field(default_factory=list)
    cue_follow: List[Optional[float]] = field(default_factory=list)
    # closed-loop trigger signals
    dir_bias: List[Optional[float]] = field(default_factory=list)
    sched_alt_rate: List[Optional[float]] = field(default_factory=list)
    # context
    blocks: List[BlockSpan] = field(default_factory=list)
    t0_iso: Optional[str] = None
    window_n: int = DEFAULT_WINDOW

    def __len__(self) -> int:
        return len(self.trial)


@dataclass
class _Point:
    """Pre-reduced per-completed-trial facts the windowed rates sum over."""
    x: Optional[float]
    trial: int
    oob: bool                       # out-of-block (uncoerced) trial
    correct: bool
    choice: Optional[str]           # LEFT / RIGHT / None
    cue: Optional[str]              # HIGH / LOW / None (planned choice cue)
    cue_side: Optional[str]         # cued arm LEFT / RIGHT / None
    correct_arm: Optional[str]
    sched_state: str
    kind: str
    sched_alt: Optional[float]
    iti: Optional[float]
    # pair facts relative to the PREVIOUS completed trial (own-pair rules)
    pair_valid: bool
    switched: bool
    prev_correct: bool
    prev_incorrect: bool


def _cue_side(cue: Optional[str], high_tone_is_left: bool) -> Optional[str]:
    """Map a planned choice cue (HIGH/LOW) to the cued arm, or None if unknown.
    Mirrors ``choice_signals.cue_side`` and ``phase3_stats``' HIGH/LOW split."""
    if cue == HIGH:
        return LEFT_LABEL if high_tone_is_left else RIGHT_LABEL
    if cue == LOW:
        return RIGHT_LABEL if high_tone_is_left else LEFT_LABEL
    return None


def _to_float(s: Optional[str]) -> Optional[float]:
    """A stringified float cell (e.g. the row's ``alt_rate``) as a float, or
    None when absent/blank/unparseable."""
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _rate(num: int, den: int, min_den: int) -> Optional[float]:
    """A masked rate num/den, or None if the denominator is below ``min_den``."""
    return (num / den) if den >= min_den and den > 0 else None


def _row_iti(rows: Tuple[Phase3RowView, ...], r: int) -> Optional[float]:
    """Dead-time ITI for row ``r``: ``trial_start_iso[r] - trial_end_iso[r-1]``
    in seconds, or None when either stamp is missing/unparseable (or r == 0). A
    negative gap (a clock anomaly - a trial can't start before the prior one
    ends) is dropped rather than plotted."""
    if r <= 0:
        return None
    start = _parse_iso(rows[r].trial_start_iso)
    prev_end = _parse_iso(rows[r - 1].trial_end_iso)
    if start is None or prev_end is None:
        return None
    gap = (start - prev_end).total_seconds()
    return gap if gap >= 0.0 else None


def _row_x_iso(row: Phase3RowView) -> Optional[str]:
    """The best available wall-clock stamp to place a trial on the time axis:
    its start, else the cue-on, else the scored-choice, else its end."""
    return (row.trial_start_iso or row.choice_cue_on_iso
            or row.choice_time_iso or row.trial_end_iso)


def build_traces(snapshot: Optional[Phase3StateSnapshot], *,
                 window_n: int = DEFAULT_WINDOW,
                 min_den: int = DEFAULT_MIN_DEN) -> TraceResult:
    """Reduce a published Phase 3 snapshot to per-completed-trial rolling traces.

    Reads only the frozen snapshot (no I/O, no locks), so it is safe to call on
    the Tk poll with whatever ``published_state()`` returned. ``window_n`` sizes
    the trailing rolling window (most-recent completed trials); ``min_den`` is
    the smallest denominator a rate is reported at. A ``None`` or empty snapshot
    yields an empty result.
    """
    n = max(1, int(window_n))
    md = max(1, int(min_den))
    rows: Tuple[Phase3RowView, ...] = snapshot.rows if snapshot is not None else ()
    high_left = snapshot.high_tone_is_left if snapshot is not None else True
    if not rows:
        return TraceResult(window_n=n)

    # t0 = the earliest parseable trial-start stamp in the table (the session's
    # wall-clock origin for the x-axis). Fall back to any usable stamp so a
    # session that predates trial_start logging still plots.
    t0 = None
    t0_iso: Optional[str] = None
    for row in rows:
        dt = _parse_iso(row.trial_start_iso) or _parse_iso(_row_x_iso(row))
        if dt is not None and (t0 is None or dt < t0):
            t0 = dt
            t0_iso = row.trial_start_iso or _row_x_iso(row)

    # First pass: reduce each COMPLETED trial to a _Point (keeping its row index
    # so ITI can reach the physically-preceding row, completed or not).
    pts: List[_Point] = []
    prev_completed: Optional[Phase3RowView] = None
    for r, row in enumerate(rows):
        if row.outcome not in (CORRECT, INCORRECT):
            continue
        choice = row.chosen_arm if row.chosen_arm in (LEFT_LABEL, RIGHT_LABEL) else None
        cue = row.choice_cue if row.choice_cue in (HIGH, LOW) else None
        oob = row.sched_state != INTERVENTION_LABEL
        x_dt = _parse_iso(_row_x_iso(row))
        x = (x_dt - t0).total_seconds() if (x_dt is not None and t0 is not None) else None

        # Own-pair facts vs the previous COMPLETED trial (docs/decisions/0093):
        # a genuine trial-to-next-trial transition of the rat's OWN choices -
        # consecutive trial numbers (so an abandoned / in-block trial between
        # them breaks the pair), neither trial in-block, both arms L/R.
        pair_valid = switched = prev_correct = prev_incorrect = False
        if prev_completed is not None:
            pchoice = (prev_completed.chosen_arm
                       if prev_completed.chosen_arm in (LEFT_LABEL, RIGHT_LABEL) else None)
            consecutive = row.trial == prev_completed.trial + 1
            neither_block = (row.sched_state != INTERVENTION_LABEL
                             and prev_completed.sched_state != INTERVENTION_LABEL)
            if consecutive and neither_block and choice is not None and pchoice is not None:
                pair_valid = True
                switched = choice != pchoice
                prev_correct = prev_completed.outcome == CORRECT
                prev_incorrect = prev_completed.outcome == INCORRECT

        pts.append(_Point(
            x=x, trial=row.trial, oob=oob, correct=row.outcome == CORRECT,
            choice=choice, cue=cue, cue_side=_cue_side(cue, high_left),
            correct_arm=row.correct_arm if row.correct_arm in (LEFT_LABEL, RIGHT_LABEL) else None,
            sched_state=row.sched_state or "NORMAL", kind=row.block_kind or "",
            sched_alt=_to_float(row.alt_rate), iti=_row_iti(rows, r),
            pair_valid=pair_valid, switched=switched,
            prev_correct=prev_correct, prev_incorrect=prev_incorrect))
        prev_completed = row

    out = TraceResult(t0_iso=t0_iso, window_n=n)
    for j in range(len(pts)):
        w = pts[max(0, j - n + 1): j + 1]
        p = pts[j]
        out.t.append(p.x)
        out.trial.append(p.trial)

        # Engagement: rolling median dead-time ITI over the window's completed
        # trials (a single sample is informative, so no min_den gate here).
        itis = [q.iti for q in w if q.iti is not None]
        out.iti_med_s.append(_median(itis))

        # Accuracy over the out-of-block completed trials in the window.
        oob = [q for q in w if q.oob]
        out.accuracy.append(_rate(sum(1 for q in oob if q.correct), len(oob), md))
        hi = [q for q in oob if q.cue == HIGH]
        lo = [q for q in oob if q.cue == LOW]
        out.high_acc.append(_rate(sum(1 for q in hi if q.correct), len(hi), md))
        out.low_acc.append(_rate(sum(1 for q in lo if q.correct), len(lo), md))

        # Strategy (out-of-block trials with a valid choice).
        sided = [q for q in oob if q.choice is not None]
        out.side_bias_left.append(
            _rate(sum(1 for q in sided if q.choice == LEFT_LABEL), len(sided), md))
        cueable = [q for q in oob if q.choice is not None and q.cue_side is not None]
        out.cue_follow.append(
            _rate(sum(1 for q in cueable if q.choice == q.cue_side), len(cueable), md))

        # Own-pair strategy rates (behavioural alternation + win-stay/lose-shift).
        pairs = [q for q in w if q.pair_valid]
        out.switch_rate.append(
            _rate(sum(1 for q in pairs if q.switched), len(pairs), md))
        after_correct = [q for q in pairs if q.prev_correct]
        out.win_stay.append(
            _rate(sum(1 for q in after_correct if not q.switched), len(after_correct), md))
        after_incorrect = [q for q in pairs if q.prev_incorrect]
        out.lose_shift.append(
            _rate(sum(1 for q in after_incorrect if q.switched), len(after_incorrect), md))

        # Signed directional-ERROR bias over out-of-block trials (b > 0 = left):
        # a left-pull is (cued/correct RIGHT, chose LEFT), a right-pull the
        # reverse; normalise by the error count, undefined (None) with no errors.
        # Mirrors phase3_stats._dir_bias. See debias.py / ADR-0090.
        left_pull = sum(1 for q in oob
                        if q.correct_arm == RIGHT_LABEL and q.choice == LEFT_LABEL)
        right_pull = sum(1 for q in oob
                         if q.correct_arm == LEFT_LABEL and q.choice == RIGHT_LABEL)
        errors = left_pull + right_pull
        out.dir_bias.append((left_pull - right_pull) / errors if errors else None)

        # Scheduler cue-aware alternation rate: a passthrough of the current
        # trial's recorded alt_rate (already a rate; blank outside an
        # intervention run), not re-windowed.
        out.sched_alt_rate.append(p.sched_alt)

    out.blocks.extend(_block_spans(pts))
    return out


def _block_spans(pts: List[_Point]) -> List[BlockSpan]:
    """Contiguous runs of INTERVENTION completed trials as x-spans for shading.
    A run's span is [first.x, last.x]; runs with no usable x are skipped."""
    spans: List[BlockSpan] = []
    run: List[_Point] = []

    def flush() -> None:
        xs = [q.x for q in run if q.x is not None]
        if xs:
            spans.append(BlockSpan(start_t=min(xs), end_t=max(xs),
                                   kind=run[0].kind))
        run.clear()

    for p in pts:
        if p.sched_state == INTERVENTION_LABEL:
            run.append(p)
        elif run:
            flush()
    if run:
        flush()
    return spans
