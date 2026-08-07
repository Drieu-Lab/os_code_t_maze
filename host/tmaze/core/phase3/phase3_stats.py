"""
phase3_stats.py - live Phase 3 cued-task behavior stats (pure core).

Folds a published ``phase3_log.Phase3StateSnapshot`` (the immutable freeze of the
session table, docs/decisions/0050) into a small set of scalar training measures
for the host-owned Phase 3 cued task (docs/decisions/0046/0047): how far the
session has progressed, how accurately the rat maps each choice cue to its arm,
whether it is side-biased, its reward rate, a naive win-stay/lose-shift strategy
readout, and its mean choice reaction time. It is the Phase 3 twin of the Phase 2a
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
    aid: over adjacent completed trials, ``win_stay`` = P(same arm chosen next |
    this trial CORRECT) and ``lose_shift`` = P(different arm chosen next | this
    trial INCORRECT). A cue-following rat has no reason to stay/shift on its own
    outcome, so these flag a rat falling back on a naive strategy.
  - ``mean_rt_s`` = mean of ``choice_time_iso - choice_cue_on_iso`` over the
    completed trials that carry both stamps. These are host wall-clock ISO times
    (they carry serial/actuation latency, docs/decisions/0047), so this is an
    approximate cue-onset->choice latency, not a hardware-precise reaction time.

Pure stdlib, headless-testable over deterministic snapshots (build a Phase3Log,
fill it via its public methods, ``.snapshot(idx, armed=...)``):
    python -m unittest test_phase3_stats
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

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
    # ---- accuracy ----
    accuracy_cum: Optional[float]              # correct / completed
    accuracy_win: Optional[float]              # correct / completed over last N
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
    reward_rate: Optional[float]               # rewarded / completed
    # Total lick-triggered TOP-UP pulses over the completed trials (extras beyond
    # each ``rewarded`` prime; docs/decisions/0069). Summed here so the per-session
    # water estimate has a single source for the pulse count (docs/decisions/0073).
    topups: int
    # ---- naive strategy (outcome-based; see module docstring) ----
    win_stay: Optional[float]
    lose_shift: Optional[float]
    # ---- reaction time (cue onset -> scored choice) ----
    mean_rt_s: Optional[float]
    rt_n: int


def _parse_iso(iso: Optional[str]) -> Optional[datetime]:
    """Parse a host wall-clock ISO stamp, or None if absent/unparseable."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


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

    accuracy_cum = (correct / ncomp) if ncomp else None
    win = completed[-n:]
    accuracy_win = _accuracy(win)

    high = [r for r in completed if r.choice_cue == HIGH]
    low = [r for r in completed if r.choice_cue == LOW]

    left = sum(1 for r in completed if r.chosen_arm == LEFT_LABEL)
    right = sum(1 for r in completed if r.chosen_arm == RIGHT_LABEL)
    sided = left + right
    lr_bias = (left / sided, right / sided) if sided else None

    rewarded = sum(1 for r in completed if r.rewarded == "1")
    reward_rate = (rewarded / ncomp) if ncomp else None
    topups = sum(_int_or_zero(r.topups) for r in completed)

    win_stay, lose_shift = _win_stay_lose_shift(completed)
    mean_rt_s, rt_n = _mean_reaction_time(completed)

    return Phase3Stats(
        window_n=n, total=total, current_index=current_index,
        completed=ncomp, correct=correct, incorrect=incorrect,
        accuracy_cum=accuracy_cum, accuracy_win=accuracy_win,
        high_accuracy=_accuracy(high), high_completed=len(high),
        low_accuracy=_accuracy(low), low_completed=len(low),
        lr_bias=lr_bias, left_choices=left, right_choices=right,
        rewarded=rewarded, reward_rate=reward_rate, topups=topups,
        win_stay=win_stay, lose_shift=lose_shift,
        mean_rt_s=mean_rt_s, rt_n=rt_n)


def _win_stay_lose_shift(
        completed: List[Phase3RowView]
) -> Tuple[Optional[float], Optional[float]]:
    """Win-stay / lose-shift over adjacent completed trials (outcome-based).

    ``win_stay`` = P(next choice is the SAME arm | this trial was CORRECT);
    ``lose_shift`` = P(next choice is a DIFFERENT arm | this trial was INCORRECT).
    Pairs whose arms aren't both LEFT/RIGHT are skipped. Each is None until its
    conditioning outcome has occurred. See the module docstring for why outcome
    (not reward) is the signal.
    """
    stay_n = stay_c = shift_n = shift_c = 0
    for a, b in zip(completed, completed[1:]):
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


def _mean_reaction_time(
        completed: List[Phase3RowView]
) -> Tuple[Optional[float], int]:
    """Mean ``choice_time_iso - choice_cue_on_iso`` (s) over trials with both
    stamps; returns ``(mean_or_None, n_used)``. A missing/unparseable or negative
    interval is skipped (a negative would mean the choice was scored before the
    cue onset was logged - not a real reaction time)."""
    deltas: List[float] = []
    for r in completed:
        onset = _parse_iso(r.choice_cue_on_iso)
        chose = _parse_iso(r.choice_time_iso)
        if onset is None or chose is None:
            continue
        dt = (chose - onset).total_seconds()
        if dt >= 0:
            deltas.append(dt)
    if not deltas:
        return None, 0
    return sum(deltas) / len(deltas), len(deltas)
