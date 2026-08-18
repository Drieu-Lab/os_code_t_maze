"""
alternation.py - closed-loop de-alternation intervention for host-owned Phase 3
(pure core).

The counterbalanced schedule (trial_sequence.generate_balanced) and the side-bias
``debias`` block (debias.py, ADR-0086) both leave one "cheat" strategy untouched:
ALTERNATION - switching arms every trial. Against an animal-independent schedule
an alternator still collects ~50% of trials, so it never has a reason to climb to
cue use. This core is the alternation twin of the debias block: a small state
machine a TrialSequence in "intervention_blocks" mode drives (composed with the
side-bias block by intervention_stack.py), targeting the switch habit instead of
the side bias.

  NORMAL (counterbalanced) --|alternation develops|--> INTERVENTION (corrective)
  INTERVENTION --|alternation relaxes, or a safety cap|--> NORMAL

While NORMAL nothing changes - the sequence hands out its counterbalanced spine.
Once the animal's recent alternation crosses a threshold, a corrective block
starts: every trial's correct side is forced to the animal's LAST CHOICE, so the
rat must REPEAT to be rewarded and a relentless alternator earns nothing until it
breaks the habit. The forced side is recomputed each trial from the last choice
(unlike debias's fixed per-block side).

Detector (cue-aware alternation rate). We do NOT just count switches in the choice
stream: a rat correctly FOLLOWING an (alternating stretch of the) cue would then
look like an alternator and be punished for using the cue. Instead we condition on
trials where the cue instructed the rat to REPEAT ITS OWN LAST CHOICE
(``cued_side == prev_chosen``; NOT ``cued_side == prev_cued``) - call these
"samples" - and measure

    alt_rate = P(switch | repeat-cued) = #(chosen != prev_chosen among samples)
                                          / #samples

Key identity: for an alternator (``chosen = opposite(prev_chosen)``), a
repeat-cued trial is EXACTLY an error trial, so the evidence accrues in proportion
to how much the alternation is COSTING the rat, and a winning / cue-following rat
(who repeats when the cue points back to its last choice) is never flagged. To
stop a couple of early samples reading as certainty, a block auto-triggers only
once at least ``min_samples`` repeat-cued trials have accrued in the window.

Exit (alt_rate normalized, not consecutive-correct). Inside a block the forced
side is the last choice, so EVERY in-block trial is repeat-cued by construction
and ``alt_rate`` over the in-block samples reads compliance directly: a complier
repeats (rate falls -> exit), a stubborn alternator keeps switching (rate stays
high -> rides to the ``max_block_len`` welfare cap). We exit when the in-block
``alt_rate`` relaxes to ``exit_rate`` (hysteresis under the trigger) rather than
on N-consecutive-correct - which here would necessarily be N repeats on ONE side
and could reinforce a pre-existing side bias (that residual bias, if it appears,
is caught by the concurrently-running debias block instead).

Determinism. NORMAL sides come from the seed-deterministic spine (the caller); the
controller only decides WHEN a block runs and (as the last choice) which side it
forces, from the realized choices - so a session is reproducible given (seed + the
animal's choices + any operator overrides), the same closed-loop caveat as debias.

Pure and single-threaded (the Phase 3 actuation thread), like the other cores: no
Tk/serial, no locks. Headless tests: python -m unittest test_alternation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

# Reuse the shared scheduler-state / trigger labels so the log's ``sched_state``
# column means the same thing for either intervention kind.
from tmaze.core.phase3.debias import AUTO, INTERVENTION, MANUAL, NORMAL


@dataclass(frozen=True)
class AlternationBlockConfig:
    """Parameters of the de-alternation corrective-block state machine.

    A "sample" is a repeat-cued trial (``cued == prev_chosen``); ``alt_rate`` is
    the switch fraction over those samples (see the module docstring).

    ``window`` is the sliding-window length (most-recent scored trials) the rate is
    measured over - a bit longer than debias's 20 because the cue-aware conditioning
    only uses the repeat-cued subset. ``min_samples`` is how many repeat-cued trials
    must accrue in the window before a block can auto-trigger (the evidence guard,
    the alternation twin of debias's ``min_errors``). ``trigger_rate`` is the
    ``alt_rate`` threshold that starts a block; ``exit_rate`` (< ``trigger_rate``,
    hysteresis) is the in-block ``alt_rate`` at/below which the block ends once
    ``exit_min_samples`` in-block samples have accrued; ``max_block_len`` caps a
    block as a welfare failsafe (auto-revert + flagged).
    """
    window: int = 30
    min_samples: int = 8
    trigger_rate: float = 0.8
    exit_rate: float = 0.6
    exit_min_samples: int = 6
    max_block_len: int = 15
    signal: str = "cue_aware"


@dataclass(frozen=True)
class AlternationDecision:
    """The controller's ruling for ONE upcoming trial (mirrors DebiasDecision).

    ``side`` is the forced correct side during a block (the last choice); ``None``
    in NORMAL, meaning "use the counterbalanced spine". ``block_id`` is 0 in NORMAL
    and a 1-based id within a block. ``trigger`` is "" in NORMAL, else "auto" /
    "manual". ``alt_rate`` is the cue-aware alternation rate measured at decision
    time (the in-block rate during a block, else the window rate)."""
    state: str
    side: Optional[int]
    block_id: int
    trigger: str
    alt_rate: float


class AlternationBlockController:
    """The NORMAL <-> INTERVENTION de-alternation state machine (see the module
    docstring). Same per-trial contract as DebiasBlockController so a TrialSequence
    / intervention_stack can drive either interchangeably:

      - :meth:`decide` once, when the next trial's side must be fixed (it may
        transition state and, in a block, returns the forced side = last choice);
      - :meth:`record` once, when that trial's choice is scored, feeding the
        (cued, chosen) pair back so the alternation rate + the block's in-block
        counters update for the next :meth:`decide`.
    Operator overrides (:meth:`force_block` / :meth:`end_block`) are latched and
    applied on the next :meth:`decide`.
    """

    def __init__(self, config: Optional[AlternationBlockConfig] = None) -> None:
        self.cfg = config or AlternationBlockConfig()
        self.reset()

    def reset(self) -> None:
        """Back to NORMAL with an empty window (a fresh session / Restart)."""
        # Each entry: (is_sample, is_switch_sample) for one scored trial. A sample
        # is a repeat-cued trial (cue told the rat to repeat its last choice).
        self._hist: Deque[Tuple[bool, bool]] = deque(
            maxlen=max(1, self.cfg.window))
        self._prev_chosen: Optional[int] = None
        self._state = NORMAL
        self._block_id = 0
        self._target: Optional[int] = None
        self._block_len = 0
        self._block_samples = 0
        self._block_switched = 0
        self._trigger = ""
        self._manual_force = False
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

    def _sample_counts(self) -> Tuple[int, int]:
        """(samples, switched_samples) over the window: samples are repeat-cued
        trials, switched_samples are the ones on which the rat switched anyway."""
        samples = sum(1 for is_s, _sw in self._hist if is_s)
        switched = sum(1 for _is_s, sw in self._hist if sw)
        return samples, switched

    def alt_rate(self) -> float:
        """Cue-aware alternation rate over the window: switched / samples, or 0.0
        when there are no repeat-cued samples yet (see the module docstring)."""
        samples, switched = self._sample_counts()
        return (switched / samples) if samples else 0.0

    def window_samples(self) -> int:
        """Repeat-cued samples currently in the window - the count the auto-trigger
        gates on (a block needs >= ``min_samples`` of these before ``alt_rate`` can
        fire one). Surfaced so the readout can show gate progress (samples N/
        min_samples), explaining why a high alt_rate hasn't yet triggered a block."""
        samples, _switched = self._sample_counts()
        return samples

    def _block_rate(self) -> float:
        """In-block ``alt_rate`` (switched / samples since the block started); 0.0
        until the first in-block sample. This is what the success exit reads."""
        return (self._block_switched / self._block_samples
                if self._block_samples else 0.0)

    # -------------------------------------------------------- operator overrides
    def force_block(self, side: Optional[int] = None) -> None:
        """Request a de-alternation block on the next decision (operator button).

        ``side`` is ignored - an alternation block always forces the animal's last
        choice; it is accepted only to match the DebiasBlockController signature."""
        self._manual_force = True
        self._manual_end = False

    def end_block(self) -> None:
        """Request an end to the current block on the next decision (operator)."""
        self._manual_end = True
        self._manual_force = False

    # -------------------------------------------------------------- per trial
    def record(self, cued_side: int, chosen_side: int) -> None:
        """Fold one scored trial's (cued, chosen) pair back into the window.

        Classifies the trial as a repeat-cued sample when ``cued == prev_chosen``
        and a switched sample when the rat then switched; inside a block also
        updates the in-block sample/switch counters that drive the success exit."""
        if self._prev_chosen is not None:
            is_sample = (cued_side == self._prev_chosen)
            is_switch = is_sample and (chosen_side != self._prev_chosen)
            self._hist.append((is_sample, is_switch))
            if self._state == INTERVENTION and is_sample:
                self._block_samples += 1
                if chosen_side != self._prev_chosen:
                    self._block_switched += 1
        self._prev_chosen = chosen_side

    def decide(self) -> AlternationDecision:
        """Fix the next trial's schedule context; may transition state.

        Order: apply any latched operator override, then auto-exit a block that has
        met its success (in-block alt_rate relaxed) or cap criterion, then
        auto-trigger a block from NORMAL if the alternation rate crosses threshold,
        then issue the trial (forced side = last choice in a block, else spine)."""
        rate = self.alt_rate()

        # 1) Operator overrides take precedence.
        if self._manual_end and self._state == INTERVENTION:
            self._manual_end = False
            self._end_block()
        if self._manual_force and self._state == NORMAL:
            self._manual_force = False
            if self._prev_chosen is not None:      # need a last choice to force
                self._start_block(MANUAL)

        # 2) Automatic exit (alternation relaxed, or the safety cap).
        if self._state == INTERVENTION and (
                (self._block_samples >= self.cfg.exit_min_samples
                 and self._block_rate() <= self.cfg.exit_rate)
                or self._block_len >= self.cfg.max_block_len):
            self._end_block()

        # 3) Automatic trigger from NORMAL once enough repeat-cued samples have
        # accrued and the alternation rate crosses threshold.
        samples, _switched = self._sample_counts()
        if self._state == NORMAL and samples >= self.cfg.min_samples \
                and rate >= self.cfg.trigger_rate and self._prev_chosen is not None:
            self._start_block(AUTO)

        # 4) Issue the trial.
        if self._state == INTERVENTION:
            self._target = self._prev_chosen       # force a repeat of the last choice
            self._block_len += 1
            return AlternationDecision(INTERVENTION, self._target, self._block_id,
                                       self._trigger, self._block_rate())
        return AlternationDecision(NORMAL, None, 0, "", rate)

    # ------------------------------------------------------------- internals
    def _start_block(self, trigger: str) -> None:
        self._state = INTERVENTION
        self._block_id += 1
        self._target = self._prev_chosen           # last choice (may be None-guarded)
        self._block_len = 0
        self._block_samples = 0
        self._block_switched = 0
        self._trigger = trigger

    def _end_block(self) -> None:
        self._state = NORMAL
        self._target = None
        self._trigger = ""
        self._block_len = 0
        self._block_samples = 0
        self._block_switched = 0
        self._hist.clear()      # fresh evidence needed before another block
