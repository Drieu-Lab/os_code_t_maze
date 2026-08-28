# ADR 0140: Draw the Phase 3 trial-sequence shuffle from stdlib `random.Random`

- **Status:** Accepted
- **Date:** 2026-08-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/trial_sequence.py` (removes `lcg`/`_U32`, rewrites `generate_balanced`/`generate_sequence`, threads one `random.Random` through `__init__`/`extend`), `host/tmaze/core/phase3/probe_blocks.py` (`_make_plan` seeds a per-block `random.Random`), `host/tmaze/core/phase3/phase3_config.py` + `host/tmaze/ui/phase3_controller.py` (stale-comment fixes only), `host/tests/test_trial_sequence.py`
- **Supersedes / Superseded by:** supersedes the firmware-LCG-parity rationale of [ADR-0009](0009-balanced-antibias-sequence.md) / [ADR-0046](0046-host-owned-phase3.md) for the host generator; the single-seed reproducibility of [ADR-0047](0047-phase3-return-schedules-and-session-log.md) and the append-only `extend` of [ADR-0059](0059-append-only-trial-sequence-extension.md) are retained.

## Context

The host trial-sequence generator (`trial_sequence.py`) drove its per-block
Fisher-Yates shuffle from a hand-written 32-bit linear congruential generator
(`lcg`, `s*1664525 + 1013904223`, sampling the high bits via `>> 16`). That code
was a deliberate byte-exact port of the firmware's `generateSequence`/`lcg` in
`firmware/cue-guided-task/task_core.h` (ADR-0009/0046), so a schedule produced on
the host matched what the firmware would have produced and stayed reproducible
from one integer seed.

Two things make that parity no longer worth its cost:

- Phase 3 is host-owned and the firmware trial FSM is off (ADR-0046), so nothing
  on the rig still needs the host and firmware to generate the *same* bytes.
- The LCG is a weak PRNG (hence the `>> 16` high-bit sampling). Python's stdlib
  `random.Random` (Mersenne Twister) is a better, well-understood generator with
  a documented, cross-version-stable stream, and `random.shuffle` expresses the
  intent directly.

A seed does not store a schedule; the schedule is computed from
`f(seed, algorithm)`. Keeping the seed but changing the algorithm therefore
changes the produced schedule - the central consequence recorded below.

## Decision

Replace the firmware LCG + hand-rolled Fisher-Yates with stdlib `random.Random`:

- `generate_balanced(n, block, max_run, rng, max_alt=0)` now takes a
  `random.Random` and draws from it in place (`rng.shuffle`), returning just the
  schedule (was `(seq, int_state)`). The exact per-block 0/1 balance, the
  `max_run`/`max_alt` anti-bias re-roll (ADR-0126), and the 200-attempt cap are
  unchanged; the balanced multiset is built as `[0]*(block-half) + [1]*half` so
  the even/odd split matches the old code exactly.
- `TrialSequence` creates ONE `random.Random(seed)` and threads that same object
  through the choice schedule, both return-arm schedules, and every `extend()`
  call, so a whole session (and its extensions) stays reproducible from the one
  seed (ADR-0047/0059 preserved).
- `probe_blocks._make_plan` seeds a per-block `random.Random` from
  `(seed ^ _PROBE_SALT) + block_id * _PROBE_SALT`, so the k-th probe of a session
  is still deterministic given the seed + caps.
- `lcg` and `_U32` are removed. `random` (stdlib) is used rather than `numpy` so
  the pure core stays stdlib-only (the `core/` layer rule; numpy is a
  vision/analysis dependency, not a core one).

## Alternatives considered

- **Keep the firmware LCG.** Rejected: it buys firmware parity that is no longer
  used, and it is a weaker generator than the stdlib one.
- **Use `numpy` (`numpy.random.Generator`).** Rejected: it would pull numpy into
  a pure `core/` module (which is stdlib-only by convention), and numpy's stream
  stability across major versions is only best-effort, whereas CPython commits to
  `random`'s Mersenne Twister stream across versions.
- **Keep returning `(seq, int_state)` and thread an int.** Rejected: a mutable
  `random.Random` threads implicitly, so the tuple contract was redundant churn.

## Consequences

- **The same integer seed now yields a DIFFERENT schedule than before.** A
  session pinned to (say) seed 42 will not regenerate its pre-0140 left/right
  order, because that order came from the old LCG. This affects all three
  schedules (choice + both return arms) and the probe-block plans.
- **Reproducing a pre-0140 dataset's sequence from its seed requires the OLD LCG
  code** - i.e. check out the pre-0140 revision of `trial_sequence.py` (and
  `probe_blocks.py`) from git history and run it with the recorded seed. The old
  algorithm is also fully described here (`s*1664525 + 1013904223`, u32, Fisher-
  Yates sampling `>> 16`) for reference. This is NOT data loss: each run writes
  its realized per-trial schedule into the session CSV, so any past session is
  recoverable from its log regardless of the generator.
- Reproducibility going forward is preserved and re-based onto `random.Random`:
  same seed + same code = same schedule; one seed still reproduces choice + both
  return schedules + any `extend`.
- Firmware/host generator parity is retired; the firmware `task_core.h` generator
  is unaffected (its path is unused under host-owned Phase 3).
- Slightly better randomness quality (Mersenne Twister vs a weak LCG); the task's
  balanced-plus-capped statistical design is unchanged.

## Evidence / links

- Supersedes the firmware-parity rationale of ADR-0009/0046; retains ADR-0047
  (one-seed, three-schedule reproducibility) and ADR-0059 (append-only `extend`);
  composes with ADR-0126 (alternation cap) and ADR-0138 (probe honours caps).
- Old algorithm for reference / reproduction: firmware `lcg`
  (`s*1664525 + 1013904223`, uint32) + per-block Fisher-Yates sampling `>> 16`,
  as it stood in `trial_sequence.py` before this ADR.
- Headless tests: `host/tests/test_trial_sequence.py` (balance / run caps /
  seed-determinism / append-only extend, all RNG-agnostic and still passing).
