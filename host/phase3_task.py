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
vision edges rather than beams: a choice is scored on ``choice_entered`` (gated
by ``outbound_used_stem`` so only a genuine up-the-stem cued trial counts), and
the trial advances on ``loop_closed`` (the rat's return to the hub) - the same
choice-then-return structure, resolved from continuous position instead of the
ENTRY/EXIT/CHOICE/RETURN beam sequence.

Pure and single-threaded (the Phase 3 actuation thread), like the other cores;
no Tk/OpenCV/serial, no locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trial_sequence import LEFT, RIGHT, TrialSequence

# task_events choice/loop sides are the strings "left"/"right"; map to arm codes.
_ARM_OF_SIDE = {"left": LEFT, "right": RIGHT}

CORRECT = "CORRECT"
INCORRECT = "INCORRECT"


@dataclass(frozen=True)
class Phase3Config:
    """Task policy knobs (behavioral, not timing).

    ``reward_incorrect`` mirrors the firmware ``START_REWARD_ENABLED`` shaping
    flag: reward the central well even on an incorrect choice. ``reward_port`` /
    ``reward_pulse_ms`` are what the controller fires (central well = port 2).
    """
    reward_incorrect: bool = False
    reward_port: int = 2
    reward_pulse_ms: int = 300


@dataclass(frozen=True)
class Phase3Decision:
    """One tick's outcome for the controller to act + log on.

    ``choice_side`` / ``outcome`` are set only on the tick a choice is scored;
    ``reward`` requests a dispense THIS tick; ``advanced`` flags that a trial just
    completed (index moved on); ``complete`` is true once all trials are done.
    ``trial_index`` / ``correct_arm`` describe the trial CURRENTLY in progress.
    """
    trial_index: int
    correct_arm: Optional[int]
    choice_side: Optional[str] = None
    outcome: Optional[str] = None
    reward: bool = False
    advanced: bool = False
    complete: bool = False


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
        self._scored_this_trial = False   # one score per trial (choice-arm dithering safe)
        self._awaiting_return = False     # a choice was scored; waiting for the return lap

    # ---------------------------------------------------------------- reads
    @property
    def index(self) -> int:
        return self._index

    @property
    def complete(self) -> bool:
        return self._index >= len(self.seq)

    def current_correct_arm(self) -> Optional[int]:
        return self.seq.correct_arm(self._index)

    def current_choice_freq(self) -> Optional[int]:
        """The CHOICE cue frequency for the trial in progress (for CUE_ON,CHOICE,<hz>)."""
        return self.seq.choice_freq(self._index)

    # ----------------------------------------------------------------- tick
    def update(self, frame) -> Phase3Decision:
        """Fold one TaskEventFrame; return the trial decision for this tick.

        ``frame`` is a duck-typed task_events.TaskEventFrame (fields read by name:
        ``valid``, ``choice_entered``, ``outbound_used_stem``, ``loop_closed``).
        Invalid frames are inert (no scoring/advance), so a tracking dropout never
        fabricates a choice or advances a trial.
        """
        idx = self._index
        correct = self.seq.correct_arm(idx)
        if self.complete or not getattr(frame, "valid", False):
            return Phase3Decision(trial_index=idx, correct_arm=correct,
                                  complete=self.complete)

        # --- Score the choice: a genuine cued trial is an up-the-stem excursion
        # into a choice arm (outbound_used_stem), scored once per trial. ---
        choice_side = getattr(frame, "choice_entered", None)
        if (choice_side in _ARM_OF_SIDE and getattr(frame, "outbound_used_stem", False)
                and not self._scored_this_trial and not self._awaiting_return):
            chosen = _ARM_OF_SIDE[choice_side]
            is_correct = (chosen == correct)
            self._scored_this_trial = True
            self._awaiting_return = True
            return Phase3Decision(
                trial_index=idx, correct_arm=correct,
                choice_side=choice_side,
                outcome=CORRECT if is_correct else INCORRECT,
                reward=bool(is_correct or self.cfg.reward_incorrect))

        # --- Advance on the return lap (rat back at the hub after the choice). ---
        if self._awaiting_return and getattr(frame, "loop_closed", None) is not None:
            self._index += 1
            self._scored_this_trial = False
            self._awaiting_return = False
            return Phase3Decision(
                trial_index=idx, correct_arm=correct,
                advanced=True, complete=self.complete)

        return Phase3Decision(trial_index=idx, correct_arm=correct)
