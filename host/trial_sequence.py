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
from typing import List, Optional, Tuple

# 32-bit mask so the LCG matches the firmware's uint32_t wraparound exactly.
_U32 = 0xFFFFFFFF

# Sides (match task_core.h: 0 = LEFT, 1 = RIGHT).
LEFT = 0
RIGHT = 1

# Cue tone identities (what a matrix cell / the return-arm schedules store).
HIGH = "HIGH"
LOW = "LOW"


@dataclass(frozen=True)
class TrialConfig:
    """Sequence + tone parameters (defaults mirror firmware/common/rig_config.h).

    ``max_trials`` should be a multiple of ``block_size`` (any remainder is left
    as LEFT, exactly as the firmware does). ``max_same_run`` is the anti-bias cap
    on consecutive same-side trials (0 disables). The tone fields map a correct
    arm to a frequency via :func:`cue_for`.
    """
    max_trials: int = 200
    block_size: int = 20
    max_same_run: int = 3
    high_tone_is_left: bool = True
    high_tone_hz: int = 8000
    low_tone_hz: int = 4000


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
        self.seq, rng = generate_sequence(self.cfg, self.seed)
        # Two independent balanced hi/lo schedules, one per physical return arm,
        # threaded off the SAME PRNG after the choice schedule so the whole
        # session is reproducible from the one seed. (0=LOW, 1=HIGH.)
        n, block, run = len(self.seq), self.cfg.block_size, self.cfg.max_same_run
        left_bits, rng = generate_balanced(n, block, run, rng)
        right_bits, rng = generate_balanced(n, block, run, rng)
        self._return_bits = {LEFT: left_bits, RIGHT: right_bits}
        self._rng = rng

    def __len__(self) -> int:
        return len(self.seq)

    def correct_arm(self, index: int) -> Optional[int]:
        """The correct arm (0=LEFT, 1=RIGHT) for trial ``index``, or None if out of range."""
        if 0 <= index < len(self.seq):
            return self.seq[index]
        return None

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
