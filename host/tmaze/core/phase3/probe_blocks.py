"""
probe_blocks.py - unrewarded "probe" blocks for host-owned Phase 3 (pure core).

A probe block is a short run of trials where the cue plays and the choice is
SCORED exactly as normal but NO reward is delivered (no prime, no lick top-ups,
``reward_incorrect`` ignored). Comparing the animal's accuracy under a probe
(what it KNOWS, latent) against its reinforced accuracy (what it EXPRESSES when
paid) reads out cue->action knowledge separately from reward-driven performance -
the acquisition/expression dissociation of Drieu et al. 2025 (see the ADR). We
run them near disengagement onset, when a well-trained animal tends to stop
working for water even though it still knows the map.

Relationship to the debias / de-alternation interventions (debias.py,
alternation.py, intervention_stack.py): a probe block INSERTS on top of the
counterbalanced spine the same way an intervention block does - it occupies trial
slots without consuming the seed-deterministic L/R schedule (``TrialSequence``'s
``_spine_pos`` only advances on NORMAL trials). But a probe is ORTHOGONAL to those
closed-loop corrections: it is not a bias/alternation reaction, it forces no
particular side beyond staying internally counterbalanced, and it works under ANY
generator (including plain ``balanced``). It also takes PRIORITY over the
intervention stack and suspends it while active.

Manual only, for now. This controller has no automatic trigger evaluation: a
probe starts solely because the operator pressed "Start probe block". The
scaffolding for automatic triggers (fixed position / elapsed time / reward volume)
is deferred to future work (see the ADR + docs/future-work.md) so the operator can
first pilot where a probe is worth placing; because the trigger is a live operator
action (or, later, a wall-clock / volume threshold), probe PLACEMENT is not
reproducible from the seed alone - the CSV records where each probe landed.

Contract (mirrors DebiasBlockController so TrialSequence drives it the same way):
  - :meth:`decide` once per upcoming trial, told the current spine position, the
    counterbalanced block size, and whether an intervention block is running; it
    may start a pending block (only at a spine block boundary with no intervention
    active), continue a running one (handing out the next counterbalanced side),
    or end one (after ``block_len`` trials or an operator "End");
  - :meth:`force_block` / :meth:`end_block` latch an operator request applied on
    the next :meth:`decide`.
The probe does not need a per-trial :meth:`record` (it has no closed-loop signal
of its own); the sequence still feeds scored probe trials to the intervention
detectors so their windows stay warm across the probe (see the ADR).

Pure and single-threaded (the Phase 3 actuation thread), like the other cores: no
Tk/serial, no locks. Headless tests: python -m unittest test_phase3_probe_blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from tmaze.core.phase3.trial_sequence import (
    LEFT, RIGHT, generate_balanced, lcg,
)

# A distinct PRNG offset so the probe block's internal L/R plan is deterministic
# from the session seed yet independent of the three schedule streams (choice +
# the two return arms) TrialSequence threads off that seed. Placement is still not
# seed-reproducible (it is operator-triggered); only the WITHIN-block 5/5 pattern
# for the k-th probe of a session is deterministic given the seed.
_PROBE_SALT = 0x9E3779B9


@dataclass(frozen=True)
class ProbeBlockConfig:
    """Parameters of a probe block.

    ``enabled`` builds the controller at all (no probe machinery when False).
    ``block_len`` is the number of trials in one probe block (default 10, matching
    the paper); it is internally L/R counterbalanced, so an even length gives an
    exact 5/5 balance (an odd length leaves a one-trial imbalance).
    """
    enabled: bool = False
    block_len: int = 10


@dataclass(frozen=True)
class ProbeDecision:
    """The controller's ruling for ONE upcoming trial.

    ``active`` is True when this trial is a probe trial; ``side`` is then the
    forced (internally counterbalanced) correct side (LEFT/RIGHT), else None.
    ``block_id`` is 0 outside a probe and a 1-based id within one; ``index_in_block``
    is the 1-based position within the current probe block (0 outside)."""
    active: bool
    side: Optional[int]
    block_id: int
    index_in_block: int


class ProbeBlockController:
    """The manual probe-block state machine (see the module docstring)."""

    def __init__(self, config: Optional[ProbeBlockConfig] = None,
                 seed: int = 0) -> None:
        self.cfg = config or ProbeBlockConfig()
        self._seed = seed & 0xFFFFFFFF
        self.reset()

    def reset(self) -> None:
        """Back to no-probe with no pending request (a fresh session / Restart)."""
        self._pending = False
        self._active = False
        self._block_id = 0
        self._pos = 0                 # trials issued in the current block
        self._plan: List[int] = []    # the current block's counterbalanced sides
        self._manual_end = False

    # ------------------------------------------------------------------ reads
    @property
    def active(self) -> bool:
        return self._active

    @property
    def pending(self) -> bool:
        return self._pending

    @property
    def block_id(self) -> int:
        return self._block_id

    def state(self) -> str:
        """A compact state token for the snapshot/readout: ``ACTIVE`` while a probe
        runs, ``PENDING`` once requested but not yet started, else ``NORMAL``."""
        if self._active:
            return "ACTIVE"
        if self._pending:
            return "PENDING"
        return "NORMAL"

    # -------------------------------------------------------- operator overrides
    def force_block(self) -> None:
        """Request a probe block (operator "Start" button). It starts on the next
        :meth:`decide` that lands on a counterbalanced block boundary with no
        intervention block running; until then it is PENDING."""
        self._pending = True
        self._manual_end = False

    def end_block(self) -> None:
        """End a running probe early, or cancel a still-pending request (operator
        "End" button)."""
        if self._active:
            self._manual_end = True
        else:
            self._pending = False

    # -------------------------------------------------------------- per trial
    def decide(self, *, spine_pos: int, block_size: int,
               intervention_active: bool) -> ProbeDecision:
        """Rule the next upcoming trial; may start/continue/end a probe block.

        ``spine_pos`` is how many NORMAL trials have consumed the counterbalanced
        spine so far (so ``spine_pos % block_size == 0`` is a block boundary);
        ``block_size`` is the counterbalanced block length; ``intervention_active``
        is whether a closed-loop intervention block is currently forcing a side (a
        probe never interrupts one). Order: end a finished/aborted block, then
        continue a running one, then start a pending one at a boundary."""
        # 1) End a running block that is done (length reached) or aborted (operator).
        if self._active and (self._manual_end or self._pos >= len(self._plan)):
            self._active = False
            self._manual_end = False
            self._pos = 0
            self._plan = []

        # 2) Continue a running block: hand out the next counterbalanced side.
        if self._active:
            side = self._plan[self._pos]
            self._pos += 1
            return ProbeDecision(True, side, self._block_id, self._pos)

        # 3) Start a pending block, but only at a full counterbalanced boundary and
        # never on top of an active intervention (it is suspended, not interrupted).
        at_boundary = block_size <= 0 or (spine_pos % block_size == 0)
        if self._pending and at_boundary and not intervention_active:
            self._pending = False
            self._active = True
            self._block_id += 1
            self._pos = 0
            self._plan = self._make_plan()
            side = self._plan[self._pos] if self._plan else LEFT
            self._pos += 1
            return ProbeDecision(True, side, self._block_id, self._pos)

        return ProbeDecision(False, None, 0, 0)

    # ------------------------------------------------------------- internals
    def _make_plan(self) -> List[int]:
        """A counterbalanced L/R plan for one probe block (LEFT=0/RIGHT=1).

        Reuses the same balanced generator as the choice schedule so the block is
        an exact per-block balance (5/5 for the default even length). The PRNG is
        threaded off the session seed plus the salt and the block ordinal, so the
        k-th probe of a session is deterministic given the seed (placement is not;
        see the module docstring)."""
        n = max(1, int(self.cfg.block_len))
        rng = (self._seed ^ _PROBE_SALT) & 0xFFFFFFFF
        for _ in range(self._block_id):        # advance per block for distinct plans
            rng = lcg(rng)
        seq, _rng = generate_balanced(n, n, 0, rng)
        return [RIGHT if v else LEFT for v in seq]
