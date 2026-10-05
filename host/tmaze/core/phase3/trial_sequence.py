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

Randomness source. The shuffle draws from Python's stdlib ``random.Random``
(the Mersenne Twister). ONE seeded generator is threaded through all three
schedules (and any later ``extend``), so the whole session stays reproducible
from the one seed. This replaces the earlier byte-exact port of the firmware's
32-bit LCG (``generateSequence`` / ``lcg`` in
``firmware/cue-guided-task/task_core.h``); the per-block exact balance, the
Fisher-Yates shuffle, and the ``MAX_SAME_RUN`` re-roll (Holleman et al. 2019)
are unchanged in structure - only the number stream driving the shuffle differs.
See docs/decisions/0140 (superseding the firmware-parity rationale of ADR-0009).
CONSEQUENCE: the same integer seed now yields a DIFFERENT schedule than the LCG
did, so reproducing a pre-0140 session's exact L/R order from its seed alone
requires the old LCG code (the pre-0140 revision of this file in git history);
each run's realized per-trial schedule is also written to the session log, so
historical data is fully recoverable from the log regardless. This module is
also the natural home for the deferred richer generators (FW-0001's 60/40
transition bias, FW-0008's correction trials), which are far easier to write and
test in Python.

Thread-safety: plain values + a small stateful ``TrialSequence`` object driven
single-threaded (the Phase 3 actuation thread); it takes no locks.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:               # avoid a runtime import cycle (debias imports us)
    from tmaze.core.phase3.alternation import AlternationBlockConfig
    from tmaze.core.phase3.debias import DebiasBlockConfig
    from tmaze.core.phase3.probe_blocks import ProbeBlockConfig

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
    on consecutive same-side trials (0 disables). ``max_alt_run`` is the companion
    anti-bias cap on consecutive *alternating* trials - runs like L R L R that let
    a rat succeed by simply alternating (0 disables; see docs/decisions/0126). The
    tone fields map a correct arm to a frequency via :func:`cue_for`.

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
    # Anti-bias cap on consecutive ALTERNATING trials (L R L R ...). Default 0
    # (disabled) so existing seeds/schedules stay byte-identical unless a config
    # opts in via phase3.json. See docs/decisions/0126.
    max_alt_run: int = 0
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


def alt_run_ok(seq: List[int], n: int, max_alt: int) -> bool:
    """True if ``seq[0..n)`` never alternates for more than ``max_alt`` trials in
    a row.

    The companion of :func:`max_run_ok` for the *alternating* bias: it counts a
    run of strictly alternating trials (L R L R ...) by trial, so ``max_alt == 4``
    permits ``L R L R`` but rejects a 5th trial that would extend it (``L R L R L``).
    ``max_alt == 0`` disables the check. See docs/decisions/0126.
    """
    if max_alt == 0 or n < 2:
        return True
    run = 1
    for i in range(1, n):
        if seq[i] != seq[i - 1]:
            run += 1
            if run > max_alt:
                return False
        else:
            run = 1
    return True


def generate_balanced(n: int, block: int, max_run: int,
                      rng: random.Random, max_alt: int = 0) -> List[int]:
    """Build a balanced, per-block-shuffled, anti-bias binary (0/1) schedule.

    The shared engine behind BOTH the choice L/R schedule and the two return-arm
    hi/lo schedules (0/1 there meaning LOW/HIGH). Takes a stdlib ``random.Random``
    and DRAWS from it in place, returning just the schedule, so a caller threads
    ONE seeded generator through several schedules (choice, then each return arm)
    and keeps the whole session reproducible (see docs/decisions/0140).

    Each block keeps an exact 0/1 balance, but a block whose shuffle would create
    a run of the same value longer than ``max_run`` (counting across the boundary
    with the already-fixed previous blocks) is re-rolled up to ``MAX_ATTEMPTS``
    times; the generator is drawn across attempts and blocks, and the last attempt
    is accepted unconditionally. ``max_alt`` adds a second anti-bias constraint
    checked the same way: a block that would alternate for more than ``max_alt``
    trials in a row is also re-rolled (0 disables; see docs/decisions/0126).
    """
    half = block // 2
    MAX_ATTEMPTS = 200
    seq = [0] * n
    # One balanced multiset per block; ``block - half`` keeps the exact even/odd
    # split the old hand-rolled Fisher-Yates produced (even -> half/half; odd ->
    # one extra 0). ``rng.shuffle`` draws a uniform permutation each attempt.
    vals = [0] * (block - half) + [1] * half
    off = 0
    while off + block <= n:
        for _attempt in range(MAX_ATTEMPTS):
            rng.shuffle(vals)
            seq[off:off + block] = vals
            # Validate the prefix so far (catches runs spanning the block boundary).
            if (max_run_ok(seq, off + block, max_run)
                    and alt_run_ok(seq, off + block, max_alt)):
                break
        off += block
    return seq


def generate_sequence(cfg: TrialConfig, rng: random.Random) -> List[int]:
    """Build the balanced, per-block-shuffled, anti-bias L/R choice schedule.

    Returns the trial sequence (0=LEFT, 1=RIGHT), drawing from ``rng`` in place.
    ``TrialSequence`` threads that same ``random.Random`` on to build the two
    return-arm hi/lo schedules, so one seed reproduces all three (see
    docs/decisions/0047, 0140). Thin wrapper over :func:`generate_balanced`
    (LEFT/RIGHT are 0/1), kept to name the choice-schedule intent.
    """
    return generate_balanced(cfg.max_trials, cfg.block_size, cfg.max_same_run,
                             rng, max_alt=cfg.max_alt_run)


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
        self.seed = seed
        # ONE seeded PRNG threaded through all three schedules (and later extend),
        # so the whole session is reproducible from the one seed (docs/decisions/
        # 0140, superseding the firmware LCG of ADR-0009/0047).
        self._rng = random.Random(self.seed)
        # ``self.seq`` is the counterbalanced spine (the choice L/R schedule). In
        # "balanced" mode it IS the realized schedule; in "debias_blocks" mode it
        # is the pool of NORMAL-trial sides, consumed in order as normal trials run
        # while corrective blocks insert forced sides on top (see below).
        self.seq = generate_sequence(self.cfg, self._rng)
        # Two independent balanced hi/lo schedules, one per physical return arm,
        # drawn from the SAME PRNG after the choice schedule so the whole session
        # is reproducible from the one seed. (0=LOW, 1=HIGH.) Return cues are
        # navigational and index-keyed, so they are unaffected by debias mode.
        n, block, run = len(self.seq), self.cfg.block_size, self.cfg.max_same_run
        alt = self.cfg.max_alt_run
        left_bits = generate_balanced(n, block, run, self._rng, max_alt=alt)
        right_bits = generate_balanced(n, block, run, self._rng, max_alt=alt)
        self._return_bits = {LEFT: left_bits, RIGHT: right_bits}

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
            # Thread the session's anti-bias caps through so the forced probe block
            # honours the same max_same_run / max_alt_run as the main schedule
            # (docs/decisions/0138).
            self._probe = ProbeBlockController(
                self.cfg.probe, seed=self.seed,
                max_same_run=self.cfg.max_same_run,
                max_alt_run=self.cfg.max_alt_run)
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

          - if a probe block is running, its remaining trials take their planned
            forced sides (the plan is fixed when the probe fires), and the walk
            below starts after them;
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
        if self._probe is not None:
            # A running probe takes priority in _commit_one, so its remaining plan
            # comes first; neither it nor the suspended stack consumes the spine.
            side = self._probe.upcoming_side(ahead)
            if side is not None:
                return side
            ahead -= self._probe.upcoming_forced()
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
        # An ACTIVE probe block INSERTS its forced trials on top of the spine and
        # takes PRIORITY over the intervention stack (suspended while a probe runs):
        # a probe-block trial forces its own counterbalanced side, does NOT consume
        # the spine, and does NOT poll the stack. The probe's TRIGGER trial is an
        # ordinary spine trial reclassified at scoring (probe_trigger_on_score), not
        # here. See probe_blocks.py / docs/decisions/0134.
        if self._probe is not None and self._probe.active:
            pdec = self._probe.advance()
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

    def probe_trigger_on_score(self, index: int, correct: bool,
                               intervention_active: bool) -> bool:
        """Evaluate the correct-choice probe trigger for a just-scored trial.

        Called by phase3_task at scoring time (the trigger needs the OUTCOME). If it
        fires, the just-scored TRIGGER trial (``index``) is reclassified as a probe
        trial - flagged both ``probe`` and ``probe_trigger`` - and the following
        ``block_len`` trials become a forced counterbalanced probe block handed out
        by :meth:`_commit_one`. ``index`` is 0-based; the window is checked against
        the 1-based trial number. Returns whether a probe just fired; a no-op (False)
        when probe blocks are not enabled this run. See probe_blocks.py /
        docs/decisions/0134."""
        if self._probe is None:
            return False
        fired = self._probe.on_scored(
            trial_no=index + 1, correct=correct,
            intervention_active=intervention_active)
        if fired:
            # The trigger trial is block position 0 (marks it probe_trigger); the
            # following forced block trials get positions 1..block_len via advance().
            self._probe_meta[index] = (self._probe.block_id, 0)
        return fired

    def trial_meta(self, index: int):
        """The per-trial scheduler metadata for trial ``index`` as a dict:
        ``{sched_state, block_id, block_trigger, bias, block_kind, alt_rate,
        probe, probe_trigger}``. For a committed trial these are its decided values;
        otherwise a provisional read of the current controller state. ``block_kind``
        names the active intervention ("side_bias"/"alternation"; "" when NORMAL) and
        ``alt_rate`` is the cue-aware alternation rate (None when no alternation
        detector is running). ``probe`` is True on an unrewarded probe trial
        (orthogonal to the intervention state, which stays NORMAL); ``probe_trigger``
        is True only on the correct-choice trigger trial that started the block. In
        plain balanced mode every non-probe trial is NORMAL."""
        d = {"sched_state": "NORMAL", "block_id": 0, "block_trigger": "",
             "bias": 0.0, "block_kind": "", "alt_rate": None, "probe": False,
             "probe_trigger": False}
        # A committed probe trial: NORMAL scheduler state, just flagged probe (it
        # carries no intervention decision - the stack was suspended). Block position
        # 0 is the correct-choice TRIGGER trial (always correct by construction, so
        # excluded from probe accuracy downstream); positions 1.. are the block.
        if index in self._probe_meta:
            d["probe"] = True
            d["probe_trigger"] = (self._probe_meta[index][1] == 0)
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

    def end_probe_block(self) -> None:
        """Operator: end a running probe block early (abort). There is no manual
        START - a probe fires automatically on the correct-choice window trigger
        (probe_trigger_on_score). A no-op when probe is disabled or none is running."""
        if self._probe is not None:
            self._probe.end_block()

    def set_probe_armed(self, armed: bool) -> None:
        """Operator: arm/disarm the correct-choice probe trigger mid-session
        (docs/decisions/0137). Disarming stops any future window firing and aborts a
        running block; re-arming lets remaining un-fired windows trigger again. A
        no-op when probe blocks were not built this run."""
        if self._probe is not None:
            self._probe.set_armed(armed)

    @property
    def probe_enabled(self) -> bool:
        """Whether unrewarded probe blocks are available this run (the on/off toggle
        is live only then)."""
        return self._probe is not None

    @property
    def probe_armed(self) -> bool:
        """Whether the correct-choice trigger is currently live (True unless the
        operator disabled probes mid-session; docs/decisions/0137). False when probe
        blocks were not built this run."""
        return self._probe.armed if self._probe is not None else False

    @property
    def probe_ever_disarmed(self) -> bool:
        """Whether the operator disabled probes at any point this run, so a planned
        window may never have fired (recorded in the CSV header; docs/decisions/0137).
        False when probe blocks were not built this run."""
        return self._probe.ever_disarmed if self._probe is not None else False

    def probe_state(self) -> str:
        """Probe scheduler state for the snapshot/readout: ``ACTIVE`` while a probe
        block runs, else ``NORMAL`` (also when probe is disabled)."""
        return self._probe.state() if self._probe is not None else "NORMAL"

    def probe_upcoming(self) -> int:
        """Forced probe-block trials still ahead of the trials committed so far, for
        the ribbon's upcoming-trial marker (docs/decisions/0152). 0 when probe is
        disabled, no block is running, or an operator end/disable is latched."""
        return self._probe.upcoming_forced() if self._probe is not None else 0

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

    def intervention_will_continue(self) -> bool:
        """Predict whether the NEXT committed trial would still be in a corrective
        block (an already-open block that won't exit on the evidence scored so far),
        for the ribbon's upcoming-trial marker (docs/decisions/0152). False when
        open-loop or no block is active. An operator force-START from NORMAL is not
        pre-marked - it is reflected once the trial commits (matches the provisional
        cue-side prediction in :meth:`_provisional_arm`)."""
        return self._ctl.will_continue() if self._ctl is not None else False

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
        stored PRNG (``self._rng``, the ``random.Random`` left after the initial
        three schedules), so the result is deterministic per seed + how much was
        appended and the existing prefix is left byte-identical (append-only).
        ``n_more`` is
        rounded up to a whole ``block_size`` so each appended segment keeps the same
        exact per-block L/R (and hi/lo) balance as the initial plan; ``None`` adds
        one block. The anti-bias run caps (same-side and alternating) are enforced
        only WITHIN each appended block, not across the join with the existing tail.
        """
        block = self.cfg.block_size
        run = self.cfg.max_same_run
        alt = self.cfg.max_alt_run
        if n_more is None:
            n_more = block
        if n_more <= 0:
            return len(self.seq)
        n_more = ((n_more + block - 1) // block) * block   # whole blocks only
        choice_ext = generate_balanced(n_more, block, run, self._rng, max_alt=alt)
        left_ext = generate_balanced(n_more, block, run, self._rng, max_alt=alt)
        right_ext = generate_balanced(n_more, block, run, self._rng, max_alt=alt)
        self.seq.extend(choice_ext)
        self._return_bits[LEFT].extend(left_ext)
        self._return_bits[RIGHT].extend(right_ext)
        return len(self.seq)
