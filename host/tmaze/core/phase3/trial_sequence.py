"""
trial_sequence.py - the host-owned trial schedule + cue identity for Phase 3.

A pure, headless-testable core (stdlib only, like maze_state.py / task_events.py /
cue_gate.py) that owns what the firmware's ``task_core.h`` used to: the balanced,
anti-bias L/R trial sequence and the mapping from a trial's correct arm to the
cue tone frequency. In the initial host-owned Phase 3 (see docs/decisions/0046)
the firmware trial FSM is off, so the HOST generates the schedule, resolves cue
identity, and sends it explicitly via ``CUE_ON,<kind>,<hz>``.

Three schedules, one seed. Besides the CHOICE schedule (the balanced L/R correct
arms, whose tone follows deterministically from the correct arm), this core also
pre-generates a balanced ``HIGH``/``LOW`` schedule for EACH physical return arm
(left, right). All three use the same generator and are threaded off the ONE
session seed, so the whole session - choice arms and both return-arm cue series -
is reproducible from that seed (see docs/decisions/0047). This replaces the
earlier design where the return cue was a random hi/lo drawn per onset
(``next_return_freq``); the return cue actually played is now looked up by the
physical return arm the rat enters and the current trial index.

Why port it verbatim. The generator here is a faithful Python port of
``generateSequence`` / ``cueFor`` / ``lcg`` in
``firmware/cue-guided-task/task_core.h``: the same 32-bit LCG, the same
per-block balance + Fisher-Yates shuffle, and the same ``MAX_SAME_RUN`` re-roll
(Holleman et al. 2019, cf. ADR-0009). Keeping the algorithm identical means the
task's statistical properties are unchanged by the move to the host and that a
sequence stays reproducible from its seed. The same balanced generator is reused
for the two return-arm schedules so they share that structure. This module is
also the natural home for the deferred richer generators (FW-0001's 60/40
transition bias, FW-0008's correction trials), which are far easier to write and
test in Python.

Thread-safety: plain values + a small stateful ``TrialSequence`` object driven
single-threaded (the Phase 3 actuation thread); it takes no locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:               # avoid a runtime import cycle (debias imports us)
    from tmaze.core.phase3.alternation import AlternationBlockConfig
    from tmaze.core.phase3.debias import DebiasBlockConfig
    from tmaze.core.phase3.probe_blocks import ProbeBlockConfig

# 32-bit mask so the LCG matches the firmware's uint32_t wraparound exactly.
_U32 = 0xFFFFFFFF

# Sides (match task_core.h: 0 = LEFT, 1 = RIGHT).
LEFT = 0
RIGHT = 1

# Cue tone identities (what a matrix cell / the return-arm schedules store).
HIGH = "HIGH"
LOW = "LOW"

# Trial-sequence generators (the ``sequence.generator`` selector, ADR/docs):
#   "balanced"           - the counterbalanced, open-loop schedule (default);
#   "debias_blocks"      - counterbalanced + closed-loop corrective blocks when a
#                          side bias develops (see debias.py);
#   "intervention_blocks"- counterbalanced + a stack of independently-enable-able
#                          closed-loop interventions (side-bias debias AND/OR
#                          de-alternation) run concurrently, one active at a time
#                          (see intervention_stack.py, alternation.py).
GEN_BALANCED = "balanced"
GEN_DEBIAS_BLOCKS = "debias_blocks"
GEN_INTERVENTION_BLOCKS = "intervention_blocks"


@dataclass(frozen=True)
class TrialConfig:
    """Sequence + tone parameters (defaults mirror firmware/common/rig_config.h).

    ``max_trials`` should be a multiple of ``block_size`` (any remainder is left
    as LEFT, exactly as the firmware does). ``max_same_run`` is the anti-bias cap
    on consecutive same-side trials (0 disables). The tone fields map a correct
    arm to a frequency via :func:`cue_for`.

    ``generator`` selects the schedule: ``"balanced"`` (default, the original
    counterbalanced generator), ``"debias_blocks"`` (counterbalanced plus the
    closed-loop side-bias corrective blocks in ``debias``; see debias.py), or
    ``"intervention_blocks"`` (counterbalanced plus a concurrent stack of the
    interventions whose configs are non-None: ``debias`` for side-bias and
    ``alternation`` for de-alternation; see intervention_stack.py / alternation.py).
    ``debias`` / ``alternation`` are only consulted for their respective generators.
    """
    max_trials: int = 200
    block_size: int = 20
    max_same_run: int = 4
    high_tone_is_left: bool = True
    high_tone_hz: int = 8000
    low_tone_hz: int = 4000
    generator: str = GEN_BALANCED
    debias: Optional["DebiasBlockConfig"] = None
    alternation: Optional["AlternationBlockConfig"] = None
    # Unrewarded probe blocks (probe_blocks.py). Orthogonal to the debias /
    # de-alternation stack and available under ANY generator, so it is carried on
    # TrialConfig directly rather than under a generator. None (or ``enabled``
    # False) => no probe machinery is built. See docs/decisions/0115-probe.
    probe: Optional["ProbeBlockConfig"] = None


def lcg(state: int) -> int:
    """One step of the firmware's threaded LCG (``s*1664525 + 1013904223``, u32)."""
    return (state * 1664525 + 1013904223) & _U32


def _is_high(correct_arm: int, cfg: TrialConfig) -> bool:
    """True if a trial's correct arm maps to the HIGH tone (port of ``cueFor``'s
    predicate): high when the correct arm matches the configured high-tone side."""
    return (correct_arm == LEFT) == cfg.high_tone_is_left


def cue_for(correct_arm: int, cfg: TrialConfig) -> int:
    """Cue tone frequency for a trial's correct arm (port of ``cueFor``).

    High tone when the correct arm matches the high-tone side.
    """
    return cfg.high_tone_hz if _is_high(correct_arm, cfg) else cfg.low_tone_hz


def max_run_ok(seq: List[int], n: int, max_run: int) -> bool:
    """True if ``seq[0..n)`` never has more than ``max_run`` identical in a row.

    ``max_run == 0`` disables the check (port of ``maxRunOk``).
    """
    if max_run == 0 or n < 2:
        return True
    run = 1
    for i in range(1, n):
        if seq[i] == seq[i - 1]:
            run += 1
            if run > max_run:
                return False
        else:
            run = 1
    return True


def generate_balanced(n: int, block: int, max_run: int,
                      rng: int) -> Tuple[List[int], int]:
    """Build a balanced, per-block-shuffled, anti-bias binary (0/1) schedule.

    The shared engine behind BOTH the choice L/R schedule and the two return-arm
    hi/lo schedules (0/1 there meaning LOW/HIGH). Takes the incoming PRNG state
    and returns ``(seq, rng)``: the schedule and the ADVANCED PRNG state, so a
    caller can thread one seed through several schedules (choice, then each return
    arm) and keep the whole session reproducible.

    Faithful port of ``generateSequence``: each block keeps an exact 0/1 balance,
    but a block whose shuffle would create a run of the same value longer than
    ``max_run`` (counting across the boundary with the already-fixed previous
    blocks) is re-rolled up to ``MAX_ATTEMPTS`` times; the PRNG is threaded across
    attempts and blocks, and the last attempt is accepted unconditionally.
    """
    half = block // 2
    MAX_ATTEMPTS = 200
    seq = [0] * n
    rng &= _U32
    off = 0
    while off + block <= n:
        for _attempt in range(MAX_ATTEMPTS):
            for i in range(half):
                seq[off + i] = 0
                seq[off + i + half] = 1
            for i in range(block - 1, 0, -1):   # Fisher-Yates within the block
                rng = lcg(rng)
                j = (rng >> 16) % (i + 1)
                seq[off + i], seq[off + j] = seq[off + j], seq[off + i]
            # Validate the prefix so far (catches runs spanning the block boundary).
            if max_run_ok(seq, off + block, max_run):
                break
        off += block
    return seq, rng


def generate_sequence(cfg: TrialConfig, seed: int) -> Tuple[List[int], int]:
    """Build the balanced, per-block-shuffled, anti-bias L/R choice schedule.

    Returns ``(seq, rng)``: the trial sequence (0=LEFT, 1=RIGHT) and the ADVANCED
    PRNG state after generation. ``TrialSequence`` threads that state on to build
    the two return-arm hi/lo schedules, so one seed reproduces all three (see
    docs/decisions/0047). Thin wrapper over :func:`generate_balanced` (LEFT/RIGHT
    are 0/1), kept for back-compat and to name the choice-schedule intent.
    """
    return generate_balanced(cfg.max_trials, cfg.block_size, cfg.max_same_run,
                             seed & _U32)


class TrialSequence:
    """A generated schedule plus per-trial identity lookups.

    Holds three immutable, seed-deterministic schedules threaded off one seed:
    the choice L/R arms (``seq``) and a balanced hi/lo cue schedule for EACH
    physical return arm (``_return_bits[LEFT|RIGHT]``, 0=LOW, 1=HIGH). All lookups
    are pure; nothing advances at runtime (the old random per-onset
    ``next_return_freq`` is gone - the return cue is now an indexed lookup, see
    docs/decisions/0047).
    """

    def __init__(self, cfg: Optional[TrialConfig] = None, seed: int = 0) -> None:
        self.cfg = cfg or TrialConfig()
        self.seed = seed & _U32
        # ``self.seq`` is the counterbalanced spine (the choice L/R schedule). In
        # "balanced" mode it IS the realized schedule; in "debias_blocks" mode it
        # is the pool of NORMAL-trial sides, consumed in order as normal trials run
        # while corrective blocks insert forced sides on top (see below).
        self.seq, rng = generate_sequence(self.cfg, self.seed)
        # Two independent balanced hi/lo schedules, one per physical return arm,
        # threaded off the SAME PRNG after the choice schedule so the whole
        # session is reproducible from the one seed. (0=LOW, 1=HIGH.) Return cues
        # are navigational and index-keyed, so they are unaffected by debias mode.
        n, block, run = len(self.seq), self.cfg.block_size, self.cfg.max_same_run
        left_bits, rng = generate_balanced(n, block, run, rng)
        right_bits, rng = generate_balanced(n, block, run, rng)
        self._return_bits = {LEFT: left_bits, RIGHT: right_bits}
        self._rng = rng

        # Closed-loop state (used in "debias_blocks" / "intervention_blocks" mode).
        # The correct side of a trial is then decided lazily, IN ORDER, the first
        # time it is committed (commit()), so it can depend on the animal's realized
        # choices; committed sides + their metadata are cached, and the spine is
        # consumed only by NORMAL trials (blocks insert, they do not consume it).
        self._closed_loop = self.cfg.generator in (
            GEN_DEBIAS_BLOCKS, GEN_INTERVENTION_BLOCKS)
        self._committed: Dict[int, int] = {}
        self._meta: Dict[int, object] = {}
        # index -> (block_id, index_in_block) for committed probe trials.
        self._probe_meta: Dict[int, Tuple[int, int]] = {}
        self._frontier = 0        # next index to commit (== len(committed))
        self._spine_pos = 0       # how many NORMAL trials have consumed the spine
        self._ctl = None
        if self.cfg.generator == GEN_DEBIAS_BLOCKS:
            from tmaze.core.phase3.debias import DebiasBlockController
            self._ctl = DebiasBlockController(self.cfg.debias)
        elif self.cfg.generator == GEN_INTERVENTION_BLOCKS:
            from tmaze.core.phase3.intervention_stack import (
                InterventionStackController,
            )
            self._ctl = InterventionStackController(
                self.cfg.debias, self.cfg.alternation)
        # Optional unrewarded probe blocks - orthogonal to the intervention stack
        # and buildable under ANY generator (probe_blocks.py). Present it and the
        # lazy-commit path turns on even for the "balanced" generator, so a probe
        # can insert on top of the spine without consuming it.
        self._probe = None
        if self.cfg.probe is not None and self.cfg.probe.enabled:
            from tmaze.core.phase3.probe_blocks import ProbeBlockController
            self._probe = ProbeBlockController(self.cfg.probe, seed=self.seed)
        # The lazy, in-order commit machinery (correct side decided the first time
        # a trial is reached) is needed whenever something inserts trials on top of
        # the spine: a closed-loop generator OR a probe block under any generator.
        self._lazy_commit = self._closed_loop or (self._probe is not None)

    def __len__(self) -> int:
        return len(self.seq)

    # ---------------------------------------------------------- correct arm
    def correct_arm(self, index: int) -> Optional[int]:
        """The correct arm (0=LEFT, 1=RIGHT) for trial ``index``, or None if out of
        range. A PEEK: in debias mode it returns the committed side for a decided
        trial, else a provisional side for display/logging (it never commits - use
        :meth:`commit`)."""
        if not (0 <= index < len(self.seq)):
            return None
        if not self._lazy_commit:
            return self.seq[index]
        if index in self._committed:
            return self._committed[index]
        return self._provisional_arm(index)

    def _provisional_arm(self, index: int) -> int:
        """A best-effort correct side for an un-committed (future) trial, for DISPLAY
        only (never commits). Predicts what :meth:`commit` would decide assuming no
        *new* block triggers between the decision frontier and ``index``:

          - if a corrective block is open, the immediate next trial continues it
            (forced side); trials beyond it are assumed NORMAL;
          - NORMAL trials consume the counterbalanced spine IN ORDER from the next
            unconsumed slot (``_spine_pos``).

        Crucially it walks the spine from ``_spine_pos``, NOT ``self.seq[index]``:
        a corrective block inserts forced trials WITHOUT consuming the spine, so the
        spine cursor trails the absolute trial index by the number of forced trials
        once any block has run. Indexing by the absolute number showed the wrong
        (often opposite) cue for every trial after a block, even back in NORMAL mode
        (the ribbon display bug; see docs/decisions/0096). Trials far ahead are still
        inherently uncertain in a closed loop - this is the aligned best guess,
        refined as each trial actually commits."""
        ahead = max(0, index - self._frontier)     # 0 == the next trial to commit
        spine_pos = self._spine_pos
        if self._ctl is not None and self._ctl.target is not None:
            # A block is open: the immediate next trial continues it (no spine
            # consumption); assume trials after it are NORMAL.
            if ahead == 0:
                return self._ctl.target
            spine_pos += ahead - 1
        else:
            spine_pos += ahead
        return self.seq[min(spine_pos, len(self.seq) - 1)]

    def is_committed(self, index: int) -> bool:
        """True if trial ``index``'s correct side is finalized: always True when no
        block can insert (plain balanced, no probe); under a closed-loop generator
        or with probe blocks enabled, once :meth:`commit` has reached it.
        Un-committed trials only have a provisional side (see :meth:`_provisional_arm`).
        """
        return (not self._lazy_commit) or (index in self._committed)

    def commit(self, index: int) -> Optional[int]:
        """Fix (and cache) the correct side for trial ``index`` and every trial
        before it, in order - the decision point of the closed loop.

        No-op in "balanced" mode (the schedule is already fixed). In
        "debias_blocks" mode it advances the decision frontier up to ``index``,
        asking the controller for each trial: a NORMAL trial takes the next spine
        side, a block trial takes the forced non-preferred side. Idempotent for an
        already-committed index. Returns the committed side (or None if out of
        range)."""
        if not (0 <= index < len(self.seq)):
            return None
        if not self._lazy_commit:
            return self.seq[index]
        while self._frontier <= index:
            self._commit_one(self._frontier)
        return self._committed[index]

    def _next_spine_side(self) -> int:
        """Take (and consume) the next counterbalanced spine side. Clamps at the
        tail so a burst of inserted trials near the end can't index past it."""
        side = self.seq[min(self._spine_pos, len(self.seq) - 1)]
        self._spine_pos += 1
        return side

    def _commit_one(self, i: int) -> None:
        # Probe blocks take PRIORITY over the intervention stack and suspend it
        # while active: a probe trial forces its own counterbalanced side, does NOT
        # consume the spine, and does NOT poll the stack (so no intervention block
        # advances during a probe). See probe_blocks.py / docs/decisions/0115-probe.
        if self._probe is not None:
            interv_active = self._ctl is not None and self._ctl.target is not None
            pdec = self._probe.decide(
                spine_pos=self._spine_pos, block_size=self.cfg.block_size,
                intervention_active=interv_active)
            if pdec.active:
                self._committed[i] = pdec.side
                self._meta[i] = None          # a probe carries no stack decision
                self._probe_meta[i] = (pdec.block_id, pdec.index_in_block)
                self._frontier = i + 1
                return
        if self._ctl is not None:
            dec = self._ctl.decide()
            side = dec.side if dec.side is not None else self._next_spine_side()
            self._meta[i] = dec
        else:
            side = self._next_spine_side()    # plain balanced spine (probe enabled)
            self._meta[i] = None
        self._committed[i] = side
        self._frontier = i + 1

    def record_outcome(self, index: int, cued_side: int, chosen_side: int) -> None:
        """Feed a scored trial's (cued, chosen) back to the closed-loop controller
        so the next :meth:`commit` reflects it. No-op in "balanced" mode."""
        if self._ctl is not None:
            self._ctl.record(cued_side, chosen_side)

    def trial_meta(self, index: int):
        """The per-trial scheduler metadata for trial ``index`` as a dict:
        ``{sched_state, block_id, block_trigger, bias, block_kind, alt_rate,
        probe}``. For a committed trial these are its decided values; otherwise a
        provisional read of the current controller state. ``block_kind`` names the
        active intervention ("side_bias"/"alternation"; "" when NORMAL) and
        ``alt_rate`` is the cue-aware alternation rate (None when no alternation
        detector is running). ``probe`` is True on an unrewarded probe trial
        (orthogonal to the intervention state, which stays NORMAL). In plain
        balanced mode every non-probe trial is NORMAL."""
        d = {"sched_state": "NORMAL", "block_id": 0, "block_trigger": "",
             "bias": 0.0, "block_kind": "", "alt_rate": None, "probe": False}
        # A committed probe trial: NORMAL scheduler state, just flagged probe (it
        # carries no intervention decision - the stack was suspended).
        if index in self._probe_meta:
            d["probe"] = True
            return d
        if not self._lazy_commit or self._ctl is None:
            return d
        dec = self._meta.get(index)
        if dec is not None:
            kind = getattr(dec, "kind", None)
            if kind is None:                       # single DebiasBlockController
                kind = "side_bias" if dec.state == "INTERVENTION" else ""
            d.update(sched_state=dec.state, block_id=dec.block_id,
                     block_trigger=dec.trigger, bias=dec.bias,
                     block_kind=kind, alt_rate=getattr(dec, "alt_rate", None))
            return d
        active_kind = getattr(self._ctl, "active_kind", None)
        if active_kind is None:                    # single DebiasBlockController
            active_kind = "side_bias" if self._ctl.state == "INTERVENTION" else ""
        alt_rate = self._ctl.alt_rate() if hasattr(self._ctl, "alt_rate") else None
        d.update(sched_state=self._ctl.state, block_id=self._ctl.block_id,
                 block_trigger="", bias=self._ctl.bias(),
                 block_kind=active_kind, alt_rate=alt_rate)
        return d

    # ------------------------------------------------- operator overrides
    def force_block(self, side: Optional[int] = None) -> None:
        """Operator: start a side-bias corrective block on the next committed trial.

        Under "intervention_blocks" this routes to the side-bias child (the default
        the stack's ``force_block`` uses), matching the existing single-button UX."""
        if self._ctl is not None:
            self._ctl.force_block(side)

    def end_block(self) -> None:
        """Operator: end the current corrective block on the next committed trial.

        Under "intervention_blocks" this ends whichever block is active."""
        if self._ctl is not None:
            self._ctl.end_block()

    def force_probe_block(self) -> None:
        """Operator: request an unrewarded probe block. It starts at the next
        counterbalanced block boundary with no intervention active (probe_blocks.py).
        A no-op when probe blocks are not enabled this run."""
        if self._probe is not None:
            self._probe.force_block()

    def end_probe_block(self) -> None:
        """Operator: end a running probe early, or cancel a still-pending request.
        A no-op when probe blocks are not enabled this run."""
        if self._probe is not None:
            self._probe.end_block()

    @property
    def probe_enabled(self) -> bool:
        """Whether unrewarded probe blocks are available this run (the operator
        button is live only then)."""
        return self._probe is not None

    def probe_state(self) -> str:
        """Probe scheduler state for the snapshot/readout: ``ACTIVE`` while a probe
        runs, ``PENDING`` once requested but not started, else ``NORMAL`` (also when
        probe is disabled)."""
        return self._probe.state() if self._probe is not None else "NORMAL"

    def force_alternation_block(self) -> None:
        """Operator: start a de-alternation block on the next committed trial.

        Only meaningful under "intervention_blocks" with the alternation child
        enabled; a no-op otherwise."""
        if self._ctl is not None and self.cfg.generator == GEN_INTERVENTION_BLOCKS:
            self._ctl.force_block(kind="alternation")

    def end_alternation_block(self) -> None:
        """Operator: end the current de-alternation block on the next committed
        trial (only meaningful under "intervention_blocks")."""
        if self._ctl is not None and self.cfg.generator == GEN_INTERVENTION_BLOCKS:
            self._ctl.end_block(kind="alternation")

    def debias_state(self) -> str:
        """Current scheduler state (NORMAL/INTERVENTION); NORMAL if open-loop."""
        return self._ctl.state if self._ctl is not None else "NORMAL"

    def debias_bias(self) -> float:
        """Current signed directional-error bias (0.0 if no side-bias detector)."""
        return self._ctl.bias() if self._ctl is not None else 0.0

    def alt_rate(self) -> float:
        """Current cue-aware alternation rate (0.0 if no alternation detector)."""
        if self._ctl is not None and hasattr(self._ctl, "alt_rate"):
            return self._ctl.alt_rate()
        return 0.0

    def debias_errors(self) -> int:
        """Directional errors currently in the side-bias detector's window - the
        count its auto-trigger gates on (``min_errors``); 0 if no side-bias
        detector. For readout gate-progress (errors N/min)."""
        if self._ctl is not None and hasattr(self._ctl, "window_errors"):
            return self._ctl.window_errors()
        return 0

    def alt_samples(self) -> int:
        """Repeat-cued samples currently in the alternation detector's window - the
        count its auto-trigger gates on (``min_samples``); 0 if no alternation
        detector. For readout gate-progress (samples N/min)."""
        if self._ctl is not None and hasattr(self._ctl, "window_samples"):
            return self._ctl.window_samples()
        return 0

    def active_kind(self) -> str:
        """Which intervention owns the active block ("side_bias"/"alternation"; ""
        when NORMAL or open-loop)."""
        if self._ctl is None:
            return ""
        kind = getattr(self._ctl, "active_kind", None)
        if kind is None:                           # single DebiasBlockController
            return "side_bias" if self._ctl.state == "INTERVENTION" else ""
        return kind

    @property
    def is_debias(self) -> bool:
        """True whenever the lazy, in-order commit path is active: any closed-loop
        generator (debias_blocks / intervention_blocks) OR a probe-enabled run.

        Kept under the historical name for back-compat: the log/task shells gate
        their per-trial "set the decided cue + scheduler context" paths on it, and
        those paths must run for probe trials too (so the probe flag is logged even
        under the plain balanced generator)."""
        return self._lazy_commit

    @property
    def is_closed_loop(self) -> bool:
        """Clearer alias for :attr:`is_debias` (any lazy-commit run)."""
        return self._lazy_commit

    def reset_debias(self) -> None:
        """Rewind the lazy-commit state to a fresh session (Restart button):
        the intervention controller, the probe controller, and the commit caches."""
        if not self._lazy_commit:
            return
        if self._ctl is not None:
            self._ctl.reset()
        if self._probe is not None:
            self._probe.reset()
        self._committed.clear()
        self._meta.clear()
        self._probe_meta.clear()
        self._frontier = 0
        self._spine_pos = 0

    def choice_cue(self, index: int) -> Optional[str]:
        """The CHOICE cue identity (HIGH/LOW) for trial ``index``, or None if out of range."""
        arm = self.correct_arm(index)
        return None if arm is None else (HIGH if _is_high(arm, self.cfg) else LOW)

    def choice_freq(self, index: int) -> Optional[int]:
        """The CHOICE cue frequency for trial ``index``, or None if out of range."""
        arm = self.correct_arm(index)
        return None if arm is None else cue_for(arm, self.cfg)

    def return_cue(self, side: int, index: int) -> Optional[str]:
        """The RETURN cue identity (HIGH/LOW) planned for physical arm ``side``
        (LEFT/RIGHT) on trial ``index``, or None if out of range."""
        bits = self._return_bits.get(side)
        if bits is None or not (0 <= index < len(bits)):
            return None
        return HIGH if bits[index] else LOW

    def return_freq(self, side: int, index: int) -> Optional[int]:
        """The RETURN cue frequency for physical arm ``side`` on trial ``index``,
        or None if out of range (what the host sends as ``CUE_ON,RETURN,<hz>``)."""
        cue = self.return_cue(side, index)
        if cue is None:
            return None
        return self.cfg.high_tone_hz if cue == HIGH else self.cfg.low_tone_hz

    def extend(self, n_more: Optional[int] = None) -> int:
        """Append more trials to all three schedules, in place; return the new length.

        Used when the rat runs past the planned count (docs/decisions/0059): the
        session is extended rather than ending. The extra trials continue the ONE
        stored PRNG cursor (``self._rng``, the state left after the initial three
        schedules), so the result is deterministic per seed + how much was appended
        and the existing prefix is left byte-identical (append-only). ``n_more`` is
        rounded up to a whole ``block_size`` so each appended segment keeps the same
        exact per-block L/R (and hi/lo) balance as the initial plan; ``None`` adds
        one block. The anti-bias run cap is enforced only WITHIN each appended
        block, not across the join with the existing tail.
        """
        block = self.cfg.block_size
        run = self.cfg.max_same_run
        if n_more is None:
            n_more = block
        if n_more <= 0:
            return len(self.seq)
        n_more = ((n_more + block - 1) // block) * block   # whole blocks only
        rng = self._rng
        choice_ext, rng = generate_balanced(n_more, block, run, rng)
        left_ext, rng = generate_balanced(n_more, block, run, rng)
        right_ext, rng = generate_balanced(n_more, block, run, rng)
        self.seq.extend(choice_ext)
        self._return_bits[LEFT].extend(left_ext)
        self._return_bits[RIGHT].extend(right_ext)
        self._rng = rng
        return len(self.seq)
