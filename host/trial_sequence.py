"""
trial_sequence.py - the host-owned trial schedule + cue identity for Phase 3.

A pure, headless-testable core (stdlib only, like maze_state.py / task_events.py /
cue_gate.py) that owns what the firmware's ``task_core.h`` used to: the balanced,
anti-bias L/R trial sequence and the mapping from a trial's correct arm to the
cue tone frequency. In the initial host-owned Phase 3 (see docs/decisions/0046)
the firmware trial FSM is off, so the HOST generates the schedule, resolves cue
identity, and sends it explicitly via ``CUE_ON,<kind>,<hz>``.

Why port it verbatim. The generator here is a faithful Python port of
``generateSequence`` / ``cueFor`` / ``lcg`` in
``firmware/cue-guided-task/task_core.h``: the same 32-bit LCG, the same
per-block balance + Fisher-Yates shuffle, and the same ``MAX_SAME_RUN`` re-roll
(Holleman et al. 2019, cf. ADR-0009). Keeping the algorithm identical means the
task's statistical properties are unchanged by the move to the host and that a
sequence stays reproducible from its seed. This module is also the natural home
for the deferred richer generators (FW-0001's 60/40 transition bias, FW-0008's
correction trials), which are far easier to write and test in Python.

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


def cue_for(correct_arm: int, cfg: TrialConfig) -> int:
    """Cue tone frequency for a trial's correct arm (port of ``cueFor``).

    High tone when the correct arm matches the high-tone side.
    """
    high = (correct_arm == LEFT) == cfg.high_tone_is_left
    return cfg.high_tone_hz if high else cfg.low_tone_hz


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


def generate_sequence(cfg: TrialConfig, seed: int) -> Tuple[List[int], int]:
    """Build the balanced, per-block-shuffled, anti-bias L/R schedule.

    Returns ``(seq, rng)``: the trial sequence (0=LEFT, 1=RIGHT) and the ADVANCED
    PRNG state after generation (the firmware threads this same state into the
    RETURN cue's random hi/lo pick; see :meth:`TrialSequence.next_return_freq`).

    Faithful port of ``generateSequence``: each block keeps an exact L/R balance,
    but a block whose shuffle would create a same-side run longer than
    ``max_same_run`` (counting across the boundary with the already-fixed previous
    blocks) is re-rolled up to ``MAX_ATTEMPTS`` times; the PRNG is threaded across
    attempts and blocks, and the last attempt is accepted unconditionally.
    """
    n = cfg.max_trials
    block = cfg.block_size
    half = block // 2
    MAX_ATTEMPTS = 200
    seq = [LEFT] * n
    rng = seed & _U32
    off = 0
    while off + block <= n:
        for _attempt in range(MAX_ATTEMPTS):
            for i in range(half):
                seq[off + i] = LEFT
                seq[off + i + half] = RIGHT
            for i in range(block - 1, 0, -1):   # Fisher-Yates within the block
                rng = lcg(rng)
                j = (rng >> 16) % (i + 1)
                seq[off + i], seq[off + j] = seq[off + j], seq[off + i]
            # Validate the prefix so far (catches runs spanning the block boundary).
            if max_run_ok(seq, off + block, cfg.max_same_run):
                break
        off += block
    return seq, rng


class TrialSequence:
    """A generated schedule plus per-trial identity lookups.

    Holds the immutable sequence and the threaded PRNG state; ``correct_arm`` /
    ``choice_freq`` are pure lookups, while ``next_return_freq`` advances the PRNG
    exactly as the firmware does for the RETURN cue's random hi/lo pick.
    """

    def __init__(self, cfg: Optional[TrialConfig] = None, seed: int = 0) -> None:
        self.cfg = cfg or TrialConfig()
        self.seed = seed & _U32
        self.seq, self._rng = generate_sequence(self.cfg, self.seed)

    def __len__(self) -> int:
        return len(self.seq)

    def correct_arm(self, index: int) -> Optional[int]:
        """The correct arm (0=LEFT, 1=RIGHT) for trial ``index``, or None if out of range."""
        if 0 <= index < len(self.seq):
            return self.seq[index]
        return None

    def choice_freq(self, index: int) -> Optional[int]:
        """The CHOICE cue frequency for trial ``index``, or None if out of range."""
        arm = self.correct_arm(index)
        return None if arm is None else cue_for(arm, self.cfg)

    def next_return_freq(self) -> int:
        """A random hi/lo RETURN cue frequency (advances the threaded PRNG).

        Mirrors the firmware's RETURN identity: ``rng = lcg(rng)`` then bit 16
        selects high/low, so the host resolves the same kind of value the Due did.
        """
        self._rng = lcg(self._rng)
        high = bool((self._rng >> 16) & 1)
        return self.cfg.high_tone_hz if high else self.cfg.low_tone_hz
