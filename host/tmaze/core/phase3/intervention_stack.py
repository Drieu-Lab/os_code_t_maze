"""
intervention_stack.py - compose the closed-loop Phase 3 interventions (pure core).

The side-bias ``debias`` block (debias.py, ADR-0086) and the de-alternation block
(alternation.py) each counter ONE history-based cheat strategy. This core runs
them together as one scheduler so a rat can't escape one habit straight into
another unchecked: it is the "stack the experts" seam that a later adversarial
schedule (FW-0035) grows from. A TrialSequence in "intervention_blocks" mode
drives it exactly as it drives a single DebiasBlockController - same
``decide()`` / ``record()`` / ``target`` / ``state`` / ``block_id`` contract - so
the wiring is unchanged.

Only ONE block is active at a time. Strong side bias (low switch rate) and strong
alternation (high switch rate) are near mutually exclusive, so simultaneous
triggers are rare; when NORMAL the children are polled in priority order
(side-bias first - the established, trusted mechanism - then alternation) and the
first to trigger owns the block until it exits. A broken alternator that then
settles into a side bias is caught by the side-bias child on a later window (and
vice versa).

Which children exist is set by the caller: pass a config for each intervention you
want enabled (``None`` = that intervention off), so the tab's two enable
checkboxes map directly onto the constructed children. With neither enabled the
stack is inert (always NORMAL) and the schedule is the plain counterbalanced spine.

Pure and single-threaded (the Phase 3 actuation thread), like the other cores: no
Tk/serial, no locks. Headless tests: python -m unittest test_intervention_stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from tmaze.core.phase3.alternation import (
    AlternationBlockConfig, AlternationBlockController,
)
from tmaze.core.phase3.debias import (
    INTERVENTION, NORMAL, DebiasBlockConfig, DebiasBlockController,
)

# The intervention kinds (also the ``block_kind`` value logged per in-block trial).
KIND_SIDE_BIAS = "side_bias"
KIND_ALTERNATION = "alternation"


@dataclass(frozen=True)
class StackDecision:
    """The stack's ruling for ONE upcoming trial (the union of the child decisions).

    ``side`` is the forced correct side during a block, ``None`` in NORMAL. ``kind``
    is which intervention owns an active block ("side_bias"/"alternation"; "" in
    NORMAL). ``bias`` (side-bias child) and ``alt_rate`` (alternation child) are
    both carried every trial - the live signals for logging - regardless of which,
    if either, is active."""
    state: str
    side: Optional[int]
    block_id: int
    trigger: str
    kind: str
    bias: float
    alt_rate: float


class InterventionStackController:
    """Compose the enabled closed-loop interventions into one scheduler (see the
    module docstring). Constructed with one config per intervention (``None`` =
    disabled); children are polled side-bias-first with a single active block."""

    def __init__(self, debias_cfg: Optional[DebiasBlockConfig] = None,
                 alternation_cfg: Optional[AlternationBlockConfig] = None) -> None:
        # Priority order: side-bias first, then alternation.
        self._children: List[Tuple[str, object]] = []
        self._debias_ctl: Optional[DebiasBlockController] = None
        self._alt_ctl: Optional[AlternationBlockController] = None
        if debias_cfg is not None:
            self._debias_ctl = DebiasBlockController(debias_cfg)
            self._children.append((KIND_SIDE_BIAS, self._debias_ctl))
        if alternation_cfg is not None:
            self._alt_ctl = AlternationBlockController(alternation_cfg)
            self._children.append((KIND_ALTERNATION, self._alt_ctl))
        self.reset()

    def reset(self) -> None:
        """Back to NORMAL with every child rewound (a fresh session / Restart)."""
        for _kind, ctl in self._children:
            ctl.reset()
        self._active: Optional[int] = None      # index into _children, or None
        self._block_id = 0                       # stack-owned monotonic id
        self._active_block_id = 0

    # ------------------------------------------------------------------ reads
    @property
    def state(self) -> str:
        return INTERVENTION if self._active is not None else NORMAL

    @property
    def block_id(self) -> int:
        return self._active_block_id if self._active is not None else 0

    @property
    def target(self) -> Optional[int]:
        if self._active is None:
            return None
        return self._children[self._active][1].target

    @property
    def active_kind(self) -> str:
        return "" if self._active is None else self._children[self._active][0]

    def bias(self) -> float:
        """The side-bias child's signed directional-error bias (0.0 if disabled)."""
        return self._debias_ctl.bias() if self._debias_ctl is not None else 0.0

    def alt_rate(self) -> float:
        """The alternation child's cue-aware alternation rate (0.0 if disabled)."""
        return self._alt_ctl.alt_rate() if self._alt_ctl is not None else 0.0

    def window_errors(self) -> int:
        """The side-bias child's window directional-error count (0 if disabled) -
        the count its auto-trigger gates on (``min_errors``)."""
        return self._debias_ctl.window_errors() if self._debias_ctl is not None else 0

    def window_samples(self) -> int:
        """The alternation child's window repeat-cued sample count (0 if disabled) -
        the count its auto-trigger gates on (``min_samples``)."""
        return self._alt_ctl.window_samples() if self._alt_ctl is not None else 0

    # -------------------------------------------------------- operator overrides
    def force_block(self, side: Optional[int] = None,
                    kind: str = KIND_SIDE_BIAS) -> None:
        """Request a corrective block of ``kind`` on the next decision (operator).

        Routes to that child if it is enabled; a no-op otherwise. Default kind is
        side-bias, preserving the existing single-button semantics."""
        ctl = self._ctl_for(kind)
        if ctl is not None:
            ctl.force_block(side)

    def end_block(self, kind: Optional[str] = None) -> None:
        """Request an end to a corrective block on the next decision (operator).

        ``kind=None`` ends whichever block is currently active; otherwise routes to
        the named child."""
        if kind is None:
            if self._active is not None:
                self._children[self._active][1].end_block()
            return
        ctl = self._ctl_for(kind)
        if ctl is not None:
            ctl.end_block()

    def _ctl_for(self, kind: str):
        if kind == KIND_SIDE_BIAS:
            return self._debias_ctl
        if kind == KIND_ALTERNATION:
            return self._alt_ctl
        return None

    # -------------------------------------------------------------- per trial
    def record(self, cued_side: int, chosen_side: int) -> None:
        """Feed a scored trial's (cued, chosen) to ALL enabled children so every
        detector's window stays warm, even the one whose block isn't running."""
        for _kind, ctl in self._children:
            ctl.record(cued_side, chosen_side)

    def decide(self) -> StackDecision:
        """Fix the next trial's schedule context, arbitrating across children.

        If a block is active, delegate only to its child (and clear it on exit).
        Otherwise poll children in priority order and let the first to trigger own
        the block. A non-triggering NORMAL child ``decide()`` is side-effect-free,
        so polling and stopping at the first trigger never advances the others."""
        if self._active is not None:
            kind, ctl = self._children[self._active]
            dec = ctl.decide()
            if dec.side is None:                 # the active block just exited
                self._active = None
                return self._normal_decision()
            return StackDecision(INTERVENTION, dec.side, self._active_block_id,
                                 dec.trigger, kind, self.bias(), self.alt_rate())

        for i, (kind, ctl) in enumerate(self._children):
            dec = ctl.decide()
            if dec.side is not None:             # this child triggered a block
                self._active = i
                self._block_id += 1
                self._active_block_id = self._block_id
                return StackDecision(INTERVENTION, dec.side, self._active_block_id,
                                     dec.trigger, kind, self.bias(), self.alt_rate())
        return self._normal_decision()

    def _normal_decision(self) -> StackDecision:
        return StackDecision(NORMAL, None, 0, "", "", self.bias(), self.alt_rate())
