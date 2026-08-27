"""
phase3_task.py - the host-owned Phase 3 trial state machine (pure core).

In the initial host-owned Phase 3 (see docs/decisions/0046) the firmware trial
FSM is off and the HOST owns the task. This pure core is that task's brain: it
folds the task-semantic ``TaskEventFrame`` stream (task_events.py, driven off
VISION - not raw beams) into per-trial decisions - which arm is correct, whether
a choice was correct, whether to dispense reward, and when to advance the trial.

Division of labour (see docs/decisions/0046):
  - WHEN the cue sounds is the positional job of cue_gate.py (CueGate), reused
    unchanged.
  - WHAT tone a trial uses + scoring/advancement is this module, over a
    trial_sequence.TrialSequence (the seed-deterministic schedule + identity).
  - The thin phase3_controller.py shell composes the two and does the I/O
    (CUE_ON,<kind>,<hz> / CUE_OFF / R<port> reward).

This mirrors the firmware's trial FSM (task_core.h ``step``) but keyed off
vision edges rather than beams: a choice is scored on the shared once-per-trip
``choice_committed`` edge (task_events.py; gated by ``left_hub_via_stem`` so only
a trip that actually DEPARTED the hub up the stem - a genuine trial start -
counts), and the trial advances on ``loop_closed`` (the rat's return to the hub)
- the same choice-then-return structure, resolved from continuous position
instead of the ENTRY/EXIT/CHOICE/RETURN beam sequence. The once-per-trip guard is
the shared edge, not a local latch (docs/decisions/0055, FW-0020). The scoring
credential is the hub-departure one the choice cue also uses, so the cue, the
reward, and the score agree on what a trial is (docs/decisions/0067, 0099).

Pure and single-threaded (the Phase 3 actuation thread), like the other cores;
no Tk/OpenCV/serial, no locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tmaze.core.phase3.trial_sequence import LEFT, RIGHT, TrialSequence

# task_events choice/loop sides are the strings "left"/"right"; map to arm codes.
_ARM_OF_SIDE = {"left": LEFT, "right": RIGHT}

CORRECT = "CORRECT"
INCORRECT = "INCORRECT"
# Outcome for a trial the operator abandoned mid-run (Skip / Repeat buttons on
# the Rig view; docs/decisions/0058). Distinct from a scored CORRECT/INCORRECT so
# the stats readout can exclude it from accuracy and the ribbon can mark it
# neutrally.
ABANDONED = "ABANDONED"


@dataclass(frozen=True)
class Phase3Config:
    """Task policy knobs (behavioral, not timing).

    ``reward_incorrect`` mirrors the firmware ``START_REWARD_ENABLED`` shaping
    flag: reward the central well even on an incorrect choice. ``reward_port`` /
    ``reward_pulse_ms`` are what the controller fires (central well = port 2).
    """
    reward_incorrect: bool = False
    reward_port: int = 2
    reward_pulse_ms: int = 500


@dataclass(frozen=True)
class Phase3Decision:
    """One tick's outcome for the controller to act + log on.

    ``choice_side`` / ``outcome`` are set only on the tick a choice is scored;
    ``reward`` requests a dispense THIS tick; ``advanced`` flags that a trial just
    completed (index moved on); ``complete`` is true once all trials are done.
    ``trial_index`` / ``correct_arm`` describe the trial CURRENTLY in progress.

    ``sched_state`` / ``block_id`` / ``block_trigger`` / ``bias`` / ``block_kind`` /
    ``alt_rate`` carry the closed-loop context of ``trial_index`` (from the
    TrialSequence's metadata; NORMAL/0/""/0.0/""/None for the balanced generator),
    so the controller can log and display it. ``block_kind`` names the active
    intervention ("side_bias"/"alternation"); ``alt_rate`` is the cue-aware
    alternation rate (None when no alternation detector is running). ``probe`` is
    True on an unrewarded probe trial (probe_blocks.py) - the choice is scored
    normally but ``reward`` is forced False. ``probe_trigger`` is True only on the
    correct-choice trial that STARTED the block (always correct, so excluded from
    probe accuracy downstream).
    """
    trial_index: int
    correct_arm: Optional[int]
    choice_side: Optional[str] = None
    outcome: Optional[str] = None
    reward: bool = False
    advanced: bool = False
    complete: bool = False
    sched_state: str = "NORMAL"
    block_id: int = 0
    block_trigger: str = ""
    bias: float = 0.0
    block_kind: str = ""
    alt_rate: Optional[float] = None
    probe: bool = False
    probe_trigger: bool = False


class Phase3Task:
    """Folds a ``TaskEventFrame`` stream into host-owned Phase 3 trial decisions."""

    def __init__(self, sequence: TrialSequence,
                 config: Optional[Phase3Config] = None) -> None:
        self.seq = sequence
        self.cfg = config or Phase3Config()
        self.reset()

    def reset(self) -> None:
        """Restart at trial 0 (e.g. when the tab is re-armed)."""
        self._index = 0
        # A choice was scored; waiting for the return lap to advance. This also
        # blocks scoring a second choice before the return. The "one commit per
        # trip" guard now lives in the shared task_events choice-commit edge
        # (docs/decisions/0055, FW-0020), so no separate _scored_this_trial flag.
        self._awaiting_return = False
        # Rewind the closed-loop debias scheduler too (no-op for the balanced
        # generator), so a Restart re-decides the schedule from scratch.
        self.seq.reset_debias()

    def _ensure_committed(self) -> None:
        """Fix the current trial's correct side (and any before it), in order.

        No-op for the balanced generator; for debias_blocks this is the decision
        point that reads the animal's recorded choices, so it must run before the
        cue frequency / correct arm of the current trial is read."""
        self.seq.commit(self._index)

    def _meta(self):
        return self.seq.trial_meta(self._index)

    # ----------------------------------------------- operator manual controls
    # The Rig-view trial-control buttons drive these directly (via the actuation
    # thread; docs/decisions/0058). They move the trial cursor WITHOUT folding a
    # frame, so scoring/advancement stay driven only by vision edges in update().
    def abandon_current(self) -> int:
        """Abandon the current trial and advance to the next (Skip button).

        Clears the awaiting-return latch so the next trial scores fresh, and moves
        the cursor on by one (clamped at the end). Returns the index of the trial
        that was abandoned."""
        abandoned = self._index
        self._awaiting_return = False
        if self._index < len(self.seq):
            self._index += 1
        return abandoned

    def repeat_current(self) -> int:
        """Abandon the current trial but stay on it, to be re-run (Repeat button).

        Clears the awaiting-return latch so the SAME trial can be re-scored on the
        next up-the-stem excursion; the cursor does not move. Returns the index."""
        self._awaiting_return = False
        return self._index

    def step_back(self) -> int:
        """Step the cursor back one trial (Back button; undo an accidental advance).

        Clears the awaiting-return latch and decrements the index (floored at 0).
        Returns the new current index."""
        self._awaiting_return = False
        if self._index > 0:
            self._index -= 1
        return self._index

    # ---------------------------------------------------------------- reads
    @property
    def index(self) -> int:
        return self._index

    @property
    def complete(self) -> bool:
        return self._index >= len(self.seq)

    def current_correct_arm(self) -> Optional[int]:
        self._ensure_committed()
        return self.seq.correct_arm(self._index)

    def current_choice_freq(self) -> Optional[int]:
        """The CHOICE cue frequency for the trial in progress (for CUE_ON,CHOICE,<hz>)."""
        self._ensure_committed()
        return self.seq.choice_freq(self._index)

    # ------------------------------------------------ debias operator controls
    # The Rig-view "debias block" toggle drives these (via the actuation thread,
    # like the other manual controls; docs/decisions/0058). They take effect on the
    # next committed trial. No-ops for the balanced generator.
    def force_debias_block(self, side: Optional[int] = None) -> None:
        self.seq.force_block(side)

    def end_debias_block(self) -> None:
        self.seq.end_block()

    def force_alternation_block(self) -> None:
        self.seq.force_alternation_block()

    def end_alternation_block(self) -> None:
        self.seq.end_alternation_block()

    def end_probe_block(self) -> None:
        """Operator: end a running probe block early (abort). There is no manual
        START - a probe fires automatically on the correct-choice window trigger
        (probe_blocks.py / docs/decisions/0134). No-op if probe is disabled."""
        self.seq.end_probe_block()

    def debias_state(self) -> str:
        return self.seq.debias_state()

    def debias_bias(self) -> float:
        return self.seq.debias_bias()

    def alt_rate(self) -> float:
        return self.seq.alt_rate()

    def active_kind(self) -> str:
        return self.seq.active_kind()

    @property
    def is_debias(self) -> bool:
        return self.seq.is_debias

    # ----------------------------------------------------------------- tick
    def update(self, frame) -> Phase3Decision:
        """Fold one TaskEventFrame; return the trial decision for this tick.

        ``frame`` is a duck-typed task_events.TaskEventFrame (fields read by name:
        ``valid``, ``choice_committed``, ``left_hub_via_stem``, ``loop_closed``).
        Invalid frames are inert (no scoring/advance), so a tracking dropout never
        fabricates a choice or advances a trial.
        """
        idx = self._index
        # Fix this trial's side before reading it (the closed-loop decision point;
        # no-op for the balanced generator).
        self._ensure_committed()
        correct = self.seq.correct_arm(idx)
        meta = self._meta()
        if self.complete or not getattr(frame, "valid", False):
            return Phase3Decision(trial_index=idx, correct_arm=correct,
                                  complete=self.complete, **meta)

        # --- Score the choice on the SHARED once-per-excursion choice-commit edge
        # (task_events.choice_committed; docs/decisions/0055, FW-0020). A genuine
        # cued trial is a trip that DEPARTED the hub up the stem, so we gate on
        # left_hub_via_stem - the same hub-departure credential the choice cue uses
        # (docs/decisions/0067, 0099), not the looser outbound_used_stem (which is
        # also true when the rat set off up a side arm and later hopped into the
        # stem over the divider). A commit on such a non-trial excursion yields no
        # score and leaves the trial in place (the trip's single commit is spent,
        # but the trial index only advances after a real score, so the sequence is
        # preserved). _awaiting_return blocks a second score before the rat returns
        # home. ---
        choice_side = getattr(frame, "choice_committed", None)
        if (choice_side in _ARM_OF_SIDE and getattr(frame, "left_hub_via_stem", False)
                and not self._awaiting_return):
            chosen = _ARM_OF_SIDE[choice_side]
            is_correct = (chosen == correct)
            self._awaiting_return = True
            # Feed the (cued, chosen) pair back so the debias scheduler's bias +
            # block counters update for the NEXT trial's decision (no-op balanced).
            if correct is not None:
                self.seq.record_outcome(idx, correct, chosen)
            # Correct-choice PROBE trigger (docs/decisions/0134): a correct choice
            # on a NORMAL trial inside a probe window withholds reward and starts an
            # unrewarded block on the following trials. Evaluated here because it
            # needs the OUTCOME; if it fires, this trial is reclassified as a probe
            # (its meta gains probe/probe_trigger), so re-read meta before scoring.
            if self.seq.probe_trigger_on_score(
                    idx, is_correct,
                    intervention_active=(meta.get("sched_state") == "INTERVENTION")):
                meta = self._meta()
            # Suppress reward on a probe trial: the cue plays and the choice is
            # scored as usual, but no prime fires (and, because the controller only
            # tops up after a prime, no top-ups) and reward_incorrect is ignored -
            # the unrewarded read-out of latent knowledge (probe_blocks.py).
            reward = bool((is_correct or self.cfg.reward_incorrect)
                          and not meta.get("probe", False))
            return Phase3Decision(
                trial_index=idx, correct_arm=correct,
                choice_side=choice_side,
                outcome=CORRECT if is_correct else INCORRECT,
                reward=reward, **meta)

        # --- Advance on the return lap (rat back at the hub after the choice). ---
        if self._awaiting_return and getattr(frame, "loop_closed", None) is not None:
            self._index += 1
            self._awaiting_return = False
            return Phase3Decision(
                trial_index=idx, correct_arm=correct,
                advanced=True, complete=self.complete, **meta)

        return Phase3Decision(trial_index=idx, correct_arm=correct, **meta)
