"""
phase3_stats.py - live Phase 3 cued-task behavior stats (pure core).

Folds a published ``phase3_log.Phase3StateSnapshot`` (the immutable freeze of the
session table, docs/decisions/0050) into a small set of scalar training measures
for the host-owned Phase 3 cued task (docs/decisions/0046/0047): how far the
session has progressed, how accurately the rat maps each choice cue to its arm,
whether it is side-biased, its reward rate, and a naive win-stay/lose-shift
strategy readout. It is the Phase 3 twin of the Phase 2a
``behavior_stats.BehaviorStats`` readout (docs/decisions/0032), and it feeds the
same bottom-strip behavior panel once that panel goes phase-aware
(docs/decisions/0052).

Everything is derived from the snapshot ALONE: a "completed" trial is one the
snapshot already scored (``outcome`` set to CORRECT/INCORRECT by
``Phase3Log.choice_scored``), and every count/rate is read off the frozen
``Phase3RowView`` fields. There is no I/O, no locks, and no import of Tk/serial -
the snapshot is already the thread-safe hand-off, so a caller just re-reads it
each poll and calls :func:`compute`.

Measure definitions (all over the COMPLETED trials only):
  - ``accuracy`` = correct / completed, cumulative and over a rolling window of
    the most recent ``window_n`` completed trials (default 20). Because trials
    resolve in order, the completed trials are a contiguous prefix, so the window
    is simply their tail.
  - per-choice-cue accuracy (``high_*`` / ``low_*``) splits accuracy by the
    trial's planned CHOICE cue (HIGH vs LOW) - the two halves of "is the rat
    learning the cue->side map", since a cue-blind rat sits near chance on both.
  - side bias (``lr_bias``) = fraction of completed trials on which the rat chose
    LEFT vs RIGHT (from ``chosen_arm``), independent of correctness.
  - ``reward_rate`` = rewarded / completed (``rewarded == "1"``). With the
    reward-incorrect shaping aid on this approaches 1 and stops tracking accuracy.
  - win-stay / lose-shift are defined on the trial OUTCOME (not the reward),
    deliberately so they stay interpretable under the reward-incorrect shaping
    aid: over truly-adjacent free-choice trials, ``win_stay`` = P(same arm chosen
    next | this trial CORRECT) and ``lose_shift`` = P(different arm chosen next |
    this trial INCORRECT). A cue-following rat has no reason to stay/shift on its
    own outcome, so these flag a rat falling back on a naive strategy. A forced
    in-block trial (or any trial that intervened) breaks the pair rather than
    being skipped over, so the transition is always the rat's own trial-to-next-
    trial choice (docs/decisions/0093).
  - cue-zone dwell (``cue_dwell_mean_s`` / ``cue_dwell_median_s`` / ``cue_dwell_n``)
    is the per-trial interval ``choice_time_iso - choice_cue_on_iso`` reduced over
    the completed trials that carry both stamps (see docs/decisions/0108). It is a
    RELATIVE within-animal signal - how long the animal lingers between the choice
    cue turning on and its choice being scored - NOT an absolute reaction time:
    both stamps are HOST wall-clock actuation-decision times carrying
    uncharacterized serial/actuation latency (the same reason the bare "Choice RT"
    was removed in docs/decisions/0094), so only its CHANGE within an animal (e.g.
    before vs after a physical cue-zone speedbump) is interpretable. In the current
    rig the choice cue fires as the animal enters the stem
    (``choice_cue_on_iso`` == ``trial_start_iso``), so this is effectively its
    cue-zone / stem-traversal dwell. ADR-0108 relates to (does not supersede) 0094.
  - reward magnitude (``reward_pulses_by_ms``) is a calibration-free per-width tally
    of the reward pulses delivered over the completed trials (prime + top-ups),
    keyed by pulse width in ms. Because dispensed volume is NON-linear in pulse
    width, the water estimate downstream sums ``reward_volume_ul(width) * count``
    per width rather than converting a summed-ms total - exact even under a mid-run
    pulse-width change (docs/decisions/0110). Empty for a pre-magnitude session.

Pure stdlib, headless-testable over deterministic snapshots (build a Phase3Log,
fill it via its public methods, ``.snapshot(idx, armed=...)``):
    python -m unittest test_phase3_stats
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from tmaze.core.phase3.phase3_log import Phase3RowView, Phase3StateSnapshot
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.phase3.trial_sequence import HIGH, LOW

# Rolling-window size, in most-recent COMPLETED trials. A Phase 3 session is a few
# hundred trials, so 20 is ~a block's worth - long enough to smooth per-trial noise
# yet short enough to show within-session learning. See docs/decisions/0052.
DEFAULT_WINDOW = 20

# Arm labels as the snapshot stores them (phase3_log normalizes chosen_arm to
# upper-case); mirrors phase3_ribbon.LEFT_LABEL / RIGHT_LABEL.
LEFT_LABEL = "LEFT"
RIGHT_LABEL = "RIGHT"

# The per-trial scheduler state that marks an in-block (corrective) trial (the
# ``sched_state`` value phase3_log records; debias.INTERVENTION).
INTERVENTION_LABEL = "INTERVENTION"


@dataclass(frozen=True)
class Phase3Stats:
    """Immutable readout of the Phase 3 measures (the view formats display).

    Fractions are 0..1 or None when their denominator is empty; the view turns
    them into percentages. ``lr_bias`` is ``(left_frac, right_frac)`` of the arm
    the rat chose among completed trials.
    """
    window_n: int
    total: int                       # planned trials in the session (len rows)
    current_index: int               # 0-based trial in progress (from the snapshot)
    completed: int                   # trials with a scored outcome
    correct: int
    incorrect: int
    # Correct count over OUT-OF-BLOCK completed trials only - the numerator that
    # matches ``accuracy_cum`` (whose denominator is ``oob_completed``). Distinct
    # from ``correct`` (all completed, including forced in-block trials), so the
    # displayed [n/N] bracket agrees with the shown percentage. Equal to
    # ``correct`` for a balanced session. See docs/decisions/0092.
    oob_correct: int
    # ---- accuracy ----
    accuracy_cum: Optional[float]              # correct_oob / oob_completed
    accuracy_win: Optional[float]              # correct_oob / oob_completed over last N
    # ---- per choice-cue accuracy ----
    high_accuracy: Optional[float]
    high_completed: int
    low_accuracy: Optional[float]
    low_completed: int
    # ---- side bias (chosen arm) ----
    lr_bias: Optional[Tuple[float, float]]
    left_choices: int
    right_choices: int
    # ---- reward ----
    rewarded: int
    # Rewarded count over OUT-OF-BLOCK completed trials only - the numerator that
    # matches ``reward_rate`` (denominator ``oob_completed``); ``rewarded`` stays
    # over ALL completed for the water estimate (which wants every pulse). Equal
    # for a balanced session. See docs/decisions/0092.
    oob_rewarded: int
    reward_rate: Optional[float]               # rewarded_oob / oob_completed
    # Total lick-triggered TOP-UP pulses over the completed trials (extras beyond
    # each ``rewarded`` prime; docs/decisions/0069). Summed here so the per-session
    # water estimate has a single source for the pulse count (docs/decisions/0073).
    topups: int
    # ---- naive strategy (outcome-based; see module docstring) ----
    win_stay: Optional[float]
    lose_shift: Optional[float]
    # NOTE: the cue-onset -> scored-choice reaction time (``mean_rt_s`` / ``rt_n``)
    # was REMOVED (docs/decisions/0094): it was a host wall-clock interval carrying
    # uncharacterized serial/actuation latency, so it was misleading to headline as
    # a reaction time. The raw ``choice_cue_on_iso`` / ``choice_time_iso`` stamps
    # stay in the per-trial CSV, so it can be reinstated once those latencies are
    # measured.
    # ---- closed-loop debias (blank/neutral for the balanced generator) ----
    # The learning measures above are over OUT-OF-BLOCK completed trials only, so a
    # corrective block (which forces one side) never distorts them; the in-block
    # trials are summarized separately here. See the module docstring + the ADR.
    generator: str = "balanced"
    sched_state: str = "NORMAL"                 # current scheduler state
    # Reward-incorrect shaping aid on this run (docs/decisions/0094): when True the
    # reward rate is ~100% by construction and uninformative, so the view/summary
    # hide that line instead of showing a misleading figure.
    reward_incorrect: bool = False
    # Headline: signed directional-error bias over OUT-OF-BLOCK completed trials
    # (b > 0 = left-biased); left/right "pull" error counts that make it up.
    dir_bias: Optional[float] = None
    dir_bias_left_pull: int = 0
    dir_bias_right_pull: int = 0
    live_bias: float = 0.0                       # controller's sliding-window bias
    trigger_bias: float = 0.0                    # |b| threshold that starts a block
    # Side-bias auto-trigger evidence gate: a block needs >= ``debias_min_errors``
    # directional errors in the window before |bias| can fire it; ``debias_window_
    # errors`` is how many are there now (so the readout shows gate progress and why
    # a high |bias| hasn't triggered). ``debias_enabled`` = the child exists.
    debias_enabled: bool = False
    debias_min_errors: int = 0
    debias_window_errors: int = 0
    # ---- in-block vs out-of-block split ----
    oob_completed: int = 0
    inblock_completed: int = 0
    inblock_accuracy: Optional[float] = None
    inblock_reward_rate: Optional[float] = None
    inblock_fraction: Optional[float] = None    # in-block / all completed
    # ---- corrective-block efficacy (SIDE-BIAS blocks only; see _block_efficacy) ----
    n_blocks: int = 0
    blocks_success: int = 0                      # ended by the consecutive-correct exit
    blocks_capped: int = 0                       # hit the max-length failsafe
    block_mean_len: Optional[float] = None       # mean completed trials per block
    # ---- de-alternation block summary (intervention_blocks; 0/None otherwise) ----
    # Kept as a plain count + mean length for the beta rather than a success/capped
    # split: the alternation exit is a relaxed-rate test, not consecutive-correct,
    # so its efficacy is read from the per-trial ``alt_rate`` in the CSV instead.
    n_alt_blocks: int = 0
    alt_block_mean_len: Optional[float] = None
    # Live cue-aware alternation rate + which intervention owns the active block
    # ("side_bias"/"alternation"; "" in NORMAL), for the readout header.
    alt_rate: float = 0.0
    active_kind: str = ""
    # Alternation auto-trigger threshold + evidence gate: a block needs >= ``alt_min_
    # samples`` repeat-cued samples in the window before ``alt_rate`` >= ``alt_trigger_
    # rate`` can fire it; ``alt_window_samples`` is how many are there now (gate
    # progress). ``alt_enabled`` = the child exists.
    alt_enabled: bool = False
    alt_trigger_rate: float = 0.0
    alt_min_samples: int = 0
    alt_window_samples: int = 0
    # ---- cue-zone dwell (RELATIVE host-clock interval; see module docstring) ----
    # ``choice_time_iso - choice_cue_on_iso`` reduced over the completed trials that
    # carry both stamps: mean + median seconds and the sample count ``n``. None /
    # 0 until at least one such trial exists. Labeled as a relative dwell (not a
    # reaction time) because the stamps carry uncharacterized latency; only its
    # within-animal CHANGE is interpretable (docs/decisions/0108, relates to 0094).
    cue_dwell_mean_s: Optional[float] = None
    cue_dwell_median_s: Optional[float] = None
    cue_dwell_n: int = 0
    # ---- unrewarded probe blocks (probe_blocks.py; 0/None without probes) ----
    # Probe trials are EXCLUDED from every reinforced/out-of-block measure above
    # (they are neither reinforced nor coerced), and summarised on their own: their
    # accuracy is the "latent knowledge" read-out to compare against ``accuracy_cum``
    # (expressed/reinforced performance). The correct-choice trigger trial is
    # excluded from this read-out too (always correct by construction; docs/
    # decisions/0134), so ``probe_completed`` counts only the forced block trials.
    # See docs/decisions/0115-probe.
    probe_completed: int = 0
    probe_correct: int = 0
    probe_accuracy: Optional[float] = None
    # ---- per-width reward pulse tally (calibration-free; see docs/decisions/0110) --
    # Maps a pulse width in ms (as a string key, e.g. "500") to the number of pulses
    # delivered at that width over the completed trials (primes + lick top-ups).
    # Empty when no width was recorded (a pre-magnitude session). Volume is NON-
    # linear in ms, so downstream water math sums reward_volume_ul(width) * count per
    # width rather than converting a summed-ms total. See session_summary.
    reward_pulses_by_ms: Dict[str, int] = field(default_factory=dict)


def _int_or_zero(x: Optional[str]) -> int:
    """A row's stringified count (e.g. ``topups``) as an int; 0 when absent/blank
    or unparseable (a cell is None until its first increment, per phase3_log)."""
    try:
        return int(x) if x else 0
    except (TypeError, ValueError):
        return 0


def _accuracy(rows: List[Phase3RowView]) -> Optional[float]:
    """Correct fraction over ``rows`` (all assumed completed), or None if empty."""
    if not rows:
        return None
    return sum(1 for r in rows if r.outcome == CORRECT) / len(rows)


def _parse_iso(iso: Optional[str]) -> Optional[datetime]:
    """Parse one host wall-clock ISO stamp (as phase3_log writes them), or None if
    absent/blank/unparseable. Reintroduced for the cue-zone dwell readout after
    docs/decisions/0094 removed the reaction-time helper; the raw stamps it reads
    (``choice_cue_on_iso`` / ``choice_time_iso``) were always retained in the CSV."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _cue_dwells(rows: List[Phase3RowView]) -> List[float]:
    """Per-trial cue-zone dwell seconds (``choice_time_iso - choice_cue_on_iso``)
    over ``rows`` that carry both parseable stamps. A negative interval (a clock
    anomaly - the choice can't precede its own cue) is dropped rather than folded
    into the mean/median. See the module docstring / docs/decisions/0108."""
    out: List[float] = []
    for r in rows:
        on = _parse_iso(r.choice_cue_on_iso)
        choice = _parse_iso(r.choice_time_iso)
        if on is None or choice is None:
            continue
        dwell = (choice - on).total_seconds()
        if dwell >= 0.0:
            out.append(dwell)
    return out


def _reward_pulses_by_ms(rows: List[Phase3RowView]) -> Dict[str, int]:
    """Tally delivered reward pulses by width (ms) over ``rows`` (completed trials).

    A rewarded trial contributes ONE prime pulse at ``prime_ms``; its top-ups
    contribute ``topups`` pulses whose per-pulse width is reconstructed from the
    running ``topup_ms_total`` sum (top-ups within a trial share the run's pulse
    width, so ``total / count`` recovers it exactly). Widths are kept as string
    keys so a mid-run pulse-width change is representable. Rows without the width
    columns (a pre-magnitude session) simply contribute nothing - the tally is
    then empty and downstream water math falls back (docs/decisions/0110)."""
    tally: Dict[str, int] = {}
    for r in rows:
        if r.rewarded == "1" and r.prime_ms:
            tally[r.prime_ms] = tally.get(r.prime_ms, 0) + 1
        n = _int_or_zero(r.topups)
        total = _int_or_zero(r.topup_ms_total)
        if n > 0 and total > 0:
            width = str(int(round(total / n)))
            tally[width] = tally.get(width, 0) + n
    return tally


def _median(values: List[float]) -> Optional[float]:
    """Median of ``values`` (mean of the two middle elements for an even count),
    or None when empty. A tiny local helper so the core stays stdlib-only."""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def compute(snapshot: Optional[Phase3StateSnapshot], *,
            window_n: int = DEFAULT_WINDOW) -> Phase3Stats:
    """Reduce a published Phase 3 table snapshot to the scalar training measures.

    Reads only the frozen snapshot (no live rows, no I/O), so it is safe to call
    on the Tk poll with whatever ``published_state()`` returned. ``window_n`` sizes
    the rolling accuracy window (most-recent completed trials). A ``None`` or empty
    snapshot yields an all-empty readout (every rate None, every count 0).
    """
    n = max(1, int(window_n))
    rows: Tuple[Phase3RowView, ...] = snapshot.rows if snapshot is not None else ()
    total = snapshot.total if snapshot is not None else 0
    current_index = snapshot.current_index if snapshot is not None else 0

    # A trial is "completed" once the snapshot has SCORED it (a real
    # CORRECT/INCORRECT outcome). Operator-abandoned trials (outcome ABANDONED,
    # docs/decisions/0058) are excluded so a skip never counts against accuracy or
    # reward rate. Trials resolve in order, so this list is a contiguous prefix and
    # its tail is the rolling window.
    completed = [r for r in rows if r.outcome in (CORRECT, INCORRECT)]
    ncomp = len(completed)
    correct = sum(1 for r in completed if r.outcome == CORRECT)
    incorrect = sum(1 for r in completed if r.outcome == INCORRECT)

    # Partition on the closed-loop scheduler state. Out-of-block (NORMAL or, for the
    # balanced generator, unset) trials are the animal's UNCOERCED behavior, so all
    # learning measures use them; in-block (INTERVENTION) trials force one side and
    # are summarized on their own. For a balanced session inb is empty and oob is
    # every completed trial, so every measure below matches the pre-debias readout.
    # Probe trials (unrewarded read-out; probe_blocks.py) are their OWN partition:
    # excluded from both the in-block (coerced) and out-of-block (reinforced) sets,
    # so probe extinction never drags the reinforced accuracy down. Summarised
    # separately as the latent-knowledge accuracy. The correct-choice TRIGGER trial
    # (probe_trigger; docs/decisions/0134) is unrewarded and excluded from the
    # reinforced sets like any probe trial, but is ALSO excluded from the probe
    # accuracy read-out (it is correct by construction, so counting it would inflate
    # latent accuracy). See docs/decisions/0115-probe.
    inb = [r for r in completed
           if r.sched_state == INTERVENTION_LABEL and r.probe != "1"]
    oob = [r for r in completed
           if r.sched_state != INTERVENTION_LABEL and r.probe != "1"]
    noob = len(oob)
    probe_block_rows = [r for r in completed
                        if r.probe == "1" and r.probe_trigger != "1"]
    probe_completed = len(probe_block_rows)
    probe_correct = sum(1 for r in probe_block_rows if r.outcome == CORRECT)
    probe_accuracy = (probe_correct / probe_completed) if probe_completed else None

    correct_oob = sum(1 for r in oob if r.outcome == CORRECT)
    accuracy_cum = (correct_oob / noob) if noob else None
    accuracy_win = _accuracy(oob[-n:])

    high = [r for r in oob if r.choice_cue == HIGH]
    low = [r for r in oob if r.choice_cue == LOW]

    left = sum(1 for r in oob if r.chosen_arm == LEFT_LABEL)
    right = sum(1 for r in oob if r.chosen_arm == RIGHT_LABEL)
    sided = left + right
    lr_bias = (left / sided, right / sided) if sided else None

    rewarded = sum(1 for r in completed if r.rewarded == "1")
    rewarded_oob = sum(1 for r in oob if r.rewarded == "1")
    reward_rate = (rewarded_oob / noob) if noob else None
    topups = sum(_int_or_zero(r.topups) for r in completed)
    # Per-width pulse tally over completed trials (calibration-free; ADR-0110), so
    # downstream water math can sum non-linear per-width volumes exactly even under
    # a mid-run pulse-width change.
    reward_pulses_by_ms = _reward_pulses_by_ms(completed)

    win_stay, lose_shift = _win_stay_lose_shift(completed)

    # Cue-zone dwell (RELATIVE host-clock interval; docs/decisions/0108). Over the
    # completed trials that carry both the cue-on and choice stamps - a within-
    # animal before/after signal, deliberately not headlined as a reaction time.
    dwells = _cue_dwells(completed)
    cue_dwell_mean = (sum(dwells) / len(dwells)) if dwells else None
    cue_dwell_median = _median(dwells)

    # Headline directional-error bias (over out-of-block trials) + the block split.
    dir_bias, lpull, rpull = _dir_bias(oob)
    inblock_accuracy = _accuracy(inb)
    inblock_reward_rate = (sum(1 for r in inb if r.rewarded == "1") / len(inb)
                           if inb else None)
    inblock_fraction = (len(inb) / ncomp) if ncomp else None
    exit_consecutive = snapshot.exit_consecutive if snapshot is not None else 0
    max_block_len = snapshot.max_block_len if snapshot is not None else 0
    # Split in-block trials by which intervention forced them. Side-bias blocks get
    # the consecutive-correct success/capped efficacy; de-alternation blocks (whose
    # exit is a relaxed-rate test) are reported as a count + mean length only. A
    # legacy/unset kind is treated as side-bias (the only pre-existing block type).
    side_inb = [r for r in inb if (r.block_kind or "side_bias") == "side_bias"]
    alt_inb = [r for r in inb if r.block_kind == "alternation"]
    n_blocks, succ, capped, mean_len = _block_efficacy(
        side_inb, exit_consecutive, max_block_len)
    n_alt_blocks, alt_mean_len = _alt_block_summary(alt_inb)

    return Phase3Stats(
        window_n=n, total=total, current_index=current_index,
        completed=ncomp, correct=correct, incorrect=incorrect,
        oob_correct=correct_oob,
        accuracy_cum=accuracy_cum, accuracy_win=accuracy_win,
        high_accuracy=_accuracy(high), high_completed=len(high),
        low_accuracy=_accuracy(low), low_completed=len(low),
        lr_bias=lr_bias, left_choices=left, right_choices=right,
        rewarded=rewarded, oob_rewarded=rewarded_oob,
        reward_rate=reward_rate, topups=topups,
        win_stay=win_stay, lose_shift=lose_shift,
        generator=(snapshot.generator if snapshot is not None else "balanced"),
        sched_state=(snapshot.debias_state if snapshot is not None else "NORMAL"),
        reward_incorrect=(snapshot.reward_incorrect
                          if snapshot is not None else False),
        dir_bias=dir_bias, dir_bias_left_pull=lpull, dir_bias_right_pull=rpull,
        live_bias=(snapshot.debias_bias if snapshot is not None else 0.0),
        trigger_bias=(snapshot.trigger_bias if snapshot is not None else 0.0),
        debias_enabled=(snapshot.debias_enabled if snapshot is not None else False),
        debias_min_errors=(snapshot.debias_min_errors
                           if snapshot is not None else 0),
        debias_window_errors=(snapshot.debias_window_errors
                              if snapshot is not None else 0),
        oob_completed=noob, inblock_completed=len(inb),
        inblock_accuracy=inblock_accuracy, inblock_reward_rate=inblock_reward_rate,
        inblock_fraction=inblock_fraction,
        n_blocks=n_blocks, blocks_success=succ, blocks_capped=capped,
        block_mean_len=mean_len,
        n_alt_blocks=n_alt_blocks, alt_block_mean_len=alt_mean_len,
        alt_rate=(snapshot.alt_rate if snapshot is not None else 0.0),
        active_kind=(snapshot.active_kind if snapshot is not None else ""),
        alt_enabled=(snapshot.alt_enabled if snapshot is not None else False),
        alt_trigger_rate=(snapshot.alt_trigger_rate if snapshot is not None else 0.0),
        alt_min_samples=(snapshot.alt_min_samples if snapshot is not None else 0),
        alt_window_samples=(snapshot.alt_window_samples
                            if snapshot is not None else 0),
        cue_dwell_mean_s=cue_dwell_mean, cue_dwell_median_s=cue_dwell_median,
        cue_dwell_n=len(dwells), reward_pulses_by_ms=reward_pulses_by_ms,
        probe_completed=probe_completed, probe_correct=probe_correct,
        probe_accuracy=probe_accuracy)


def _dir_bias(rows: List[Phase3RowView]) -> Tuple[Optional[float], int, int]:
    """Signed directional-error bias over ``rows`` (out-of-block completed trials).

    A "pull" error is choosing the arm the cue told the rat NOT to: a left-pull is
    (cued RIGHT, chose LEFT), a right-pull is (cued LEFT, chose RIGHT). Returns
    ``(b, left_pull, right_pull)`` with ``b = (left_pull - right_pull) /
    (left_pull + right_pull)`` in [-1, 1] (b > 0 = left-biased), 0.0 when there are
    no directional errors, or ``(None, 0, 0)`` if empty. Normalising by the error
    count (not the trial count) keeps coincidentally-correct trials from diluting
    it; this is the session-level twin of the sliding-window measure the debias
    controller acts on (debias.py, ADR-0090)."""
    if not rows:
        return None, 0, 0
    left_pull = sum(1 for r in rows
                    if r.correct_arm == RIGHT_LABEL and r.chosen_arm == LEFT_LABEL)
    right_pull = sum(1 for r in rows
                     if r.correct_arm == LEFT_LABEL and r.chosen_arm == RIGHT_LABEL)
    errors = left_pull + right_pull
    b = 0.0 if errors == 0 else (left_pull - right_pull) / errors
    return b, left_pull, right_pull


def _block_efficacy(
        inb: List[Phase3RowView], exit_consecutive: int, max_block_len: int
) -> Tuple[int, int, int, Optional[float]]:
    """Summarize the corrective blocks over the in-block completed trials.

    Groups them by ``block_id`` (a block is a maximal run of one id) and returns
    ``(n_blocks, n_success, n_capped, mean_len)``: a block is a SUCCESS if its last
    ``exit_consecutive`` completed trials are all CORRECT (the consecutive-correct
    exit), else CAPPED if it reached ``max_block_len`` completed trials (the
    failsafe); a still-open block is counted in ``n_blocks`` but neither. Order is
    preserved from the snapshot rows (insertion-ordered dict)."""
    groups: "dict[str, List[Phase3RowView]]" = {}
    for r in inb:
        if r.block_id:
            groups.setdefault(r.block_id, []).append(r)
    if not groups:
        return 0, 0, 0, None
    succ = capped = 0
    for g in groups.values():
        tail = g[-exit_consecutive:] if exit_consecutive > 0 else []
        is_success = (exit_consecutive > 0 and len(tail) >= exit_consecutive
                      and all(r.outcome == CORRECT for r in tail))
        if is_success:
            succ += 1
        elif max_block_len > 0 and len(g) >= max_block_len:
            capped += 1
    mean_len = sum(len(g) for g in groups.values()) / len(groups)
    return len(groups), succ, capped, mean_len


def _alt_block_summary(
        alt_inb: List[Phase3RowView]
) -> Tuple[int, Optional[float]]:
    """Summarize the de-alternation blocks over their in-block completed trials.

    Returns ``(n_alt_blocks, mean_len)``: the number of distinct alternation blocks
    (grouped by ``block_id``) and their mean completed length, or ``(0, None)`` when
    none. A success/capped split is deliberately omitted (see ``Phase3Stats``): the
    alternation exit is a relaxed-rate test, best judged from the per-trial
    ``alt_rate`` in the CSV rather than a single ended-how flag."""
    groups: "dict[str, int]" = {}
    for r in alt_inb:
        if r.block_id:
            groups[r.block_id] = groups.get(r.block_id, 0) + 1
    if not groups:
        return 0, None
    return len(groups), sum(groups.values()) / len(groups)


def _win_stay_lose_shift(
        completed: List[Phase3RowView]
) -> Tuple[Optional[float], Optional[float]]:
    """Win-stay / lose-shift over truly-adjacent free-choice trials (outcome-based).

    ``win_stay`` = P(next choice is the SAME arm | this trial was CORRECT);
    ``lose_shift`` = P(next choice is a DIFFERENT arm | this trial was INCORRECT).
    Each is None until its conditioning outcome has occurred. See the module
    docstring for why outcome (not reward) is the signal.

    A pair ``(a, b)`` is counted only when it is a genuine trial-to-next-trial
    transition of the rat's OWN choices (docs/decisions/0093):
      - ``a`` and ``b`` are consecutive trials (``b.trial == a.trial + 1``), so a
        trial that sits between them - a forced in-block corrective trial, an
        operator-abandoned trial, or any not-yet-scored one - breaks the pair
        rather than being skipped over (``completed`` is the CORRECT/INCORRECT
        prefix, so a gap in trial numbers means such a trial intervened); and
      - neither ``a`` nor ``b`` is an in-block (INTERVENTION) trial: a forced
        choice is not a strategy the rat adopted, and conditioning on a forced
        outcome (or asking whether it "stayed" into a forced next trial) is
        meaningless. For a balanced session every completed trial is out-of-block
        and consecutive, so this reduces to the plain adjacent-pair definition.
    Pairs whose arms aren't both LEFT/RIGHT are skipped.
    """
    stay_n = stay_c = shift_n = shift_c = 0
    for a, b in zip(completed, completed[1:]):
        if b.trial != a.trial + 1:
            continue
        if INTERVENTION_LABEL in (a.sched_state, b.sched_state):
            continue
        # A probe trial (unrewarded) is not the rat's freely-reinforced strategy,
        # so break the pair rather than count a stay/shift into or out of it.
        if getattr(a, "probe", None) == "1" or getattr(b, "probe", None) == "1":
            continue
        if a.chosen_arm not in (LEFT_LABEL, RIGHT_LABEL):
            continue
        if b.chosen_arm not in (LEFT_LABEL, RIGHT_LABEL):
            continue
        stayed = a.chosen_arm == b.chosen_arm
        if a.outcome == CORRECT:
            stay_n += 1
            stay_c += int(stayed)
        elif a.outcome == INCORRECT:
            shift_n += 1
            shift_c += int(not stayed)
    win_stay = (stay_c / stay_n) if stay_n else None
    lose_shift = (shift_c / shift_n) if shift_n else None
    return win_stay, lose_shift
