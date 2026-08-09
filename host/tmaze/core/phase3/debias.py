"""
debias.py - closed-loop side-bias correction for host-owned Phase 3 (pure core).

The default Phase 3 schedule is counterbalanced (trial_sequence.generate_balanced:
per-block L/R balance + a max_same_run cap; see docs/decisions/0009). That is an
OPEN-loop guard - it never reacts to what the animal actually does. This core adds
the CLOSED-loop half, as a small, opt-in state machine that a TrialSequence in
"debias_blocks" mode drives:

  NORMAL (counterbalanced) --|bias develops|--> INTERVENTION (corrective block)
  INTERVENTION --|3 consecutive correct, or a safety cap|--> NORMAL

While NORMAL, nothing changes - the sequence hands out its counterbalanced spine.
Once the animal's recent *directional-error* bias crosses a threshold, a corrective
block starts: every trial's correct side is forced to the animal's NON-preferred
side (the one it avoids), so a "always-preferred" strategy earns no reward until
it switches. The block ends after ``exit_consecutive`` consecutive correct choices
to that side, or is capped at ``max_block_len`` trials as a welfare failsafe; on
either exit the window is cleared so a re-trigger needs fresh evidence.

Why a bias-TRIGGERED block (not a graded probability shift, nor textbook per-error
correction trials): the operator wanted the counterbalanced schedule to stay the
default and to intervene only when a bias actually develops, with a clean,
predetermined exit and no experimenter-in-the-loop tuning. See the ADR.

Bias signal (directional errors). Over a sliding window of the last ``window``
scored trials, each ``(cued_side, chosen_side)``:
  - left-pull errors  = (cued RIGHT, chose LEFT)   - the animal pulled left
  - right-pull errors = (cued LEFT,  chose RIGHT)  - the animal pulled right
  - b = (left_pull - right_pull) / len(window)     in [-1, 1]; b > 0 => left bias
A block corrects the side the animal over-pulls to (b > 0 forces RIGHT correct).

Determinism. NORMAL sides come from the seed-deterministic spine (the caller); the
controller only decides WHEN a block runs and which side it forces, from the
realized choices. So a session is reproducible given (seed + the animal's choices +
any operator overrides) - see the header note in phase3_log.

Pure and single-threaded (the Phase 3 actuation thread), like the other cores: no
Tk/serial, no locks. Headless tests: python -m unittest test_debias.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from tmaze.core.phase3.trial_sequence import LEFT, RIGHT

# Scheduler states (also the ``sched_state`` value logged per trial).
NORMAL = "NORMAL"
INTERVENTION = "INTERVENTION"

# How a corrective block was started (the ``block_trigger`` logged per trial).
AUTO = "auto"
MANUAL = "manual"


@dataclass(frozen=True)
class DebiasBlockConfig:
    """Parameters of the corrective-block state machine (all operator-tunable).

    ``window`` is the sliding-window length (most-recent scored trials) the bias is
    measured over; ``min_samples`` is how many scored trials must fill it before a
    block can auto-trigger. ``trigger_bias`` is the |b| threshold that starts a
    block. ``exit_consecutive`` correct non-preferred choices end a block;
    ``max_block_len`` caps it as a welfare failsafe (auto-revert + flagged).
    """
    window: int = 20
    min_samples: int = 10
    trigger_bias: float = 0.3
    exit_consecutive: int = 3
    max_block_len: int = 15
    signal: str = "directional_errors"


@dataclass(frozen=True)
class DebiasDecision:
    """The controller's ruling for ONE upcoming trial.

    ``side`` is the forced correct side during a block (LEFT/RIGHT); ``None`` in
    NORMAL, meaning "use the counterbalanced spine". ``block_id`` is 0 in NORMAL
    and a 1-based id within a block. ``trigger`` is "" in NORMAL, else how the
    block began ("auto"/"manual"). ``bias`` is the signed directional-error bias
    measured at decision time (b > 0 = left-biased)."""
    state: str
    side: Optional[int]
    block_id: int
    trigger: str
    bias: float


class DebiasBlockController:
    """The NORMAL <-> INTERVENTION state machine (see the module docstring).

    Usage per trial (driven single-threaded by TrialSequence):
      - :meth:`decide` once, when the next trial's side must be fixed (it may
        transition state and, in a block, returns the forced side);
      - :meth:`record` once, when that trial's choice is scored, feeding the
        (cued, chosen) pair back so the bias + the block's consecutive-correct
        counter update for the next :meth:`decide`.
    Operator overrides (:meth:`force_block` / :meth:`end_block`) are latched and
    applied on the next :meth:`decide`.
    """

    def __init__(self, config: Optional[DebiasBlockConfig] = None) -> None:
        self.cfg = config or DebiasBlockConfig()
        self.reset()

    def reset(self) -> None:
        """Back to NORMAL with an empty window (a fresh session / Restart)."""
        self._hist: Deque[Tuple[int, int]] = deque(maxlen=max(1, self.cfg.window))
        self._state = NORMAL
        self._block_id = 0
        self._target: Optional[int] = None
        self._block_len = 0
        self._consec = 0
        self._trigger = ""
        self._manual_force = False
        self._manual_side: Optional[int] = None
        self._manual_end = False

    # ------------------------------------------------------------------ reads
    @property
    def state(self) -> str:
        return self._state

    @property
    def block_id(self) -> int:
        return self._block_id

    @property
    def target(self) -> Optional[int]:
        return self._target

    def bias(self) -> float:
        """Signed directional-error bias over the window (b > 0 = left-biased)."""
        if not self._hist:
            return 0.0
        left_pull = sum(1 for cued, chosen in self._hist
                        if cued == RIGHT and chosen == LEFT)
        right_pull = sum(1 for cued, chosen in self._hist
                         if cued == LEFT and chosen == RIGHT)
        return (left_pull - right_pull) / len(self._hist)

    # -------------------------------------------------------- operator overrides
    def force_block(self, side: Optional[int] = None) -> None:
        """Request a corrective block on the next decision (operator button).

        ``side`` is the correct side to force (the non-preferred side); ``None``
        lets the controller infer it from the current bias / choice tally."""
        self._manual_force = True
        self._manual_side = side
        self._manual_end = False

    def end_block(self) -> None:
        """Request an end to the current block on the next decision (operator)."""
        self._manual_end = True
        self._manual_force = False

    # -------------------------------------------------------------- per trial
    def record(self, cued_side: int, chosen_side: int) -> None:
        """Fold one scored trial's (cued, chosen) pair back into the window.

        Updates the bias window always; inside a block also updates the
        consecutive-correct counter that drives the success exit."""
        self._hist.append((cued_side, chosen_side))
        if self._state == INTERVENTION:
            if chosen_side == self._target:
                self._consec += 1
            else:
                self._consec = 0

    def decide(self) -> DebiasDecision:
        """Fix the next trial's schedule context; may transition state.

        Order: apply any latched operator override, then auto-exit a block that has
        met its success or cap criterion, then auto-trigger a block from NORMAL if
        the bias crosses threshold, then issue the trial (forced side in a block,
        else spine)."""
        b = self.bias()

        # 1) Operator overrides take precedence.
        if self._manual_end and self._state == INTERVENTION:
            self._manual_end = False
            self._end_block()
        if self._manual_force and self._state == NORMAL:
            side = self._manual_side
            self._manual_force = False
            self._manual_side = None
            self._start_block(MANUAL, side)

        # 2) Automatic exit (success or safety cap).
        if self._state == INTERVENTION and (
                self._consec >= self.cfg.exit_consecutive
                or self._block_len >= self.cfg.max_block_len):
            self._end_block()

        # 3) Automatic trigger from NORMAL once enough evidence has accrued.
        if self._state == NORMAL and len(self._hist) >= self.cfg.min_samples \
                and abs(b) >= self.cfg.trigger_bias:
            self._start_block(AUTO)

        # 4) Issue the trial.
        if self._state == INTERVENTION:
            self._block_len += 1
            return DebiasDecision(INTERVENTION, self._target, self._block_id,
                                  self._trigger, b)
        return DebiasDecision(NORMAL, None, 0, "", b)

    # ------------------------------------------------------------- internals
    def _infer_nonpreferred(self) -> int:
        """The side to correct when none is given: opposite the measured bias, else
        opposite the raw choice tally, else RIGHT (documented default)."""
        b = self.bias()
        if b > 0:
            return RIGHT
        if b < 0:
            return LEFT
        left = sum(1 for _cued, chosen in self._hist if chosen == LEFT)
        right = sum(1 for _cued, chosen in self._hist if chosen == RIGHT)
        if left > right:
            return RIGHT
        if right > left:
            return LEFT
        return RIGHT

    def _start_block(self, trigger: str, side: Optional[int] = None) -> None:
        self._state = INTERVENTION
        self._block_id += 1
        self._target = side if side in (LEFT, RIGHT) else self._infer_nonpreferred()
        self._block_len = 0
        self._consec = 0
        self._trigger = trigger

    def _end_block(self) -> None:
        self._state = NORMAL
        self._target = None
        self._trigger = ""
        self._consec = 0
        self._block_len = 0
        self._hist.clear()      # fresh evidence needed before another block
