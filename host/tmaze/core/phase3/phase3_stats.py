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
    # ---- closed-loop debias (blank/neutral for the balanced generator) ----
    # The learning measures above are over OUT-OF-BLOCK completed trials only, so a
    # corrective block (which forces one side) never distorts them; the in-block
    # trials are summarized separately here. See the module docstring + the ADR.
    generator: str = "balanced"
    sched_state: str = "NORMAL"                 # current scheduler state
    # Headline: signed directional-error bias over OUT-OF-BLOCK completed trials
    # (b > 0 = left-biased); left/right "pull" error counts that make it up.
    dir_bias: Optional[float] = None
    dir_bias_left_pull: int = 0
    dir_bias_right_pull: int = 0
    live_bias: float = 0.0                       # controller's sliding-window bias
    trigger_bias: float = 0.0                    # |b| threshold that starts a block
    # ---- in-block vs out-of-block split ----
    oob_completed: int = 0
    inblock_completed: int = 0
    inblock_accuracy: Optional[float] = None
    inblock_reward_rate: Optional[float] = None
    inblock_fraction: Optional[float] = None    # in-block / all completed
    # ---- corrective-block efficacy ----
    n_blocks: int = 0
    blocks_success: int = 0                      # ended by the consecutive-correct exit
    blocks_capped: int = 0                       # hit the max-length failsafe
    block_mean_len: Optional[float] = None       # mean completed trials per block


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

    # Partition on the closed-loop scheduler state. Out-of-block (NORMAL or, for the
    # balanced generator, unset) trials are the animal's UNCOERCED behavior, so all
    # learning measures use them; in-block (INTERVENTION) trials force one side and
    # are summarized on their own. For a balanced session inb is empty and oob is
    # every completed trial, so every measure below matches the pre-debias readout.
    inb = [r for r in completed if r.sched_state == INTERVENTION_LABEL]
    oob = [r for r in completed if r.sched_state != INTERVENTION_LABEL]
    noob = len(oob)

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
    reward_rate = (sum(1 for r in oob if r.rewarded == "1") / noob) if noob else None
    topups = sum(_int_or_zero(r.topups) for r in completed)

    win_stay, lose_shift = _win_stay_lose_shift(oob)
    mean_rt_s, rt_n = _mean_reaction_time(oob)

    # Headline directional-error bias (over out-of-block trials) + the block split.
    dir_bias, lpull, rpull = _dir_bias(oob)
    inblock_accuracy = _accuracy(inb)
    inblock_reward_rate = (sum(1 for r in inb if r.rewarded == "1") / len(inb)
                           if inb else None)
    inblock_fraction = (len(inb) / ncomp) if ncomp else None
    exit_consecutive = snapshot.exit_consecutive if snapshot is not None else 0
    max_block_len = snapshot.max_block_len if snapshot is not None else 0
    n_blocks, succ, capped, mean_len = _block_efficacy(
        inb, exit_consecutive, max_block_len)

    return Phase3Stats(
        window_n=n, total=total, current_index=current_index,
        completed=ncomp, correct=correct, incorrect=incorrect,
        accuracy_cum=accuracy_cum, accuracy_win=accuracy_win,
        high_accuracy=_accuracy(high), high_completed=len(high),
        low_accuracy=_accuracy(low), low_completed=len(low),
        lr_bias=lr_bias, left_choices=left, right_choices=right,
        rewarded=rewarded, reward_rate=reward_rate, topups=topups,
        win_stay=win_stay, lose_shift=lose_shift,
        mean_rt_s=mean_rt_s, rt_n=rt_n,
        generator=(snapshot.generator if snapshot is not None else "balanced"),
        sched_state=(snapshot.debias_state if snapshot is not None else "NORMAL"),
        dir_bias=dir_bias, dir_bias_left_pull=lpull, dir_bias_right_pull=rpull,
        live_bias=(snapshot.debias_bias if snapshot is not None else 0.0),
        trigger_bias=(snapshot.trigger_bias if snapshot is not None else 0.0),
        oob_completed=noob, inblock_completed=len(inb),
        inblock_accuracy=inblock_accuracy, inblock_reward_rate=inblock_reward_rate,
        inblock_fraction=inblock_fraction,
        n_blocks=n_blocks, blocks_success=succ, blocks_capped=capped,
        block_mean_len=mean_len)


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
