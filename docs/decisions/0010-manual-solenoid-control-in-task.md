# ADR 0010: Full manual solenoid control + auto well-clean in cue-guided-task

- **Status:** Accepted
- **Date:** 2026-06-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/cue-guided-task.ino`,
  `firmware/cue-guided-task/task_core.h`, `firmware/common/rig_config.h`,
  `firmware/cue-guided-task/test/test_task_core.cpp`, `firmware/PROTOCOL.md`,
  `host/experiment_control.py`
- **Supersedes / Superseded by:** Refines ADR-0009 (manual-reward path); builds on
  ADR-0007 (reward at the central port).

## Context

Two gaps remained after ADR-0009:

1. **Manual valve control was crippled under the task sketch.** The Solenoid-test
   bar in `experiment_control.py` sends `R<port><sol>[,<ms>]`, and `tmaze_test`
   honors it fully (all 15 valves, per-pulse duration, a `SOL,<port>,<sol>` ack
   that flashes the exact Rig-view marker). But `cue-guided-task` only parsed the
   port digit, ignored the column and the ms, routed the fire through the pure
   core as `Command::Flush` (so it required an active session), and emitted no
   `SOL` ack (no marker flash). `tmaze_test` achieves its control with a
   **blocking** `delay(dur)` — unacceptable in the task sketch, where a stalled
   `loop()` would drop beam edges and freeze the tone.

2. **No automatic well hygiene.** Across trials, uncollected/old reward
   accumulates in the central well and the line is never rinsed. The experimenter
   wanted the well rinsed and drained at the start of each trial so every trial's
   reward is fresh — triggered by the animal actually starting a new trial
   (coming down a return arm into the well, then leaving it into the central
   stem), not on an ambiguous single beam break.

A key observation drove the design of (2): the trial state machine in
`task_core.h` already encodes that `return-arm beam -> central-entry beam`
direction. `AwaitReturn` only advances to `Idle` on a return beam, and the next
`B_ENTRY` (leaving the well) is the `Idle -> CueRunning` transition that emits
`TRIAL`. So "the animal is starting a new trial" is exactly trial start — no
separate, fragile directional detector is needed.

## Decision

**Manual solenoid control moves entirely into the shell, non-blocking.**

- Replace the 3-valve manager in `cue-guided-task.ino` with a 15-solenoid
  (`SOLENOID_TABLE`) non-blocking pulse manager. Each valve supports a scheduled
  start (`onAt`) in addition to an off-time (`offAt`), so timed sequences run
  without blocking.
- Add `handleManualSolenoid()`, called from `drainSerial()` for any `R...` line,
  independent of `session.active`. It parses `R<port><sol>[,<ms>]` (port 0-2, sol
  0-4, ms clamped 1..5000, default `REWARD_PULSE_MS`; a bare `R<port>` fires the
  reward spout), fires non-blocking, emits `SOL,<port>,<sol>`, and for a
  reward-spout fire also emits `REWARD,<port>,MANUAL`.
- Remove `Command::Flush` from the core entirely; the core no longer touches
  manual plumbing. The automatic-reward `OpenValve` effect is unchanged (it now
  maps to `openSolenoid(port, REWARD_SOLENOID_INDEX, REWARD_PULSE_MS)`).

**Auto well-clean is a config-toggled core effect fired at trial start.**

- `task_core.h` gains a `CleanWell(rinseMs, drainMs)` effect and `TaskConfig`
  fields `autoDrainOnTrial`, `rinseMs`, `drainMs`. At the `Idle -> CueRunning`
  transition (where `TRIAL` is emitted), the core also emits `CleanWell` when the
  toggle is on and it is not the first trial (`index > 0` — the first trial has no
  preceding return/reward). This keeps the behavior pure and host-unit-tested.
- The shell maps `CleanWell` to a non-blocking **rinse-then-drain** on the
  central well: open `RINSE` now for `rinseMs`, then `DRAIN` for `drainMs`
  starting at `+rinseMs`. Each fire emits a `SOL,2,<sol>` line (`SOL,2,4` rinse,
  `SOL,2,3` drain) so the Rig view flashes and the clean is in the log.
- Tunables live in `rig_config.h`: `AUTO_DRAIN_ON_TRIAL` (default on),
  `WELL_RINSE_MS`, `WELL_DRAIN_MS`, plus `DRAIN_SOLENOID_INDEX` /
  `RINSE_SOLENOID_INDEX` for readability.

This stays **protocol v3** (v3 is unreleased): `SOL` is added to the
`cue-guided-task` event set and the `R<port><sol>[,<ms>]` command is documented as
fully supported there.

## Alternatives considered

- **Port tmaze_test's blocking `delay()` fire into the task sketch.** Rejected:
  it would freeze `loop()` for up to 5 s, dropping beam edges and stalling the
  tone ISR's housekeeping.
- **Keep manual fire in the pure core (extend `Command::Flush` to carry
  port+sol+ms).** Rejected: manual valve plumbing is an I/O concern; pushing it
  to the shell keeps the core focused on the task and avoids coupling the core to
  the physical solenoid layout. The auto well-clean stays in the core only
  because it is *task behavior* (it must be tied to trial start and be testable).
- **Detect the return->entry sequence with a dedicated sensor-sequence watcher.**
  Rejected as redundant: the FSM already guarantees a return beam precedes the
  entry that starts a trial, so hooking trial start is simpler and robust.
- **Drain only (no rinse), or rinse and drain simultaneously.** Rejected: the
  experimenter wants a fresh-water flush, which needs the rinse to fill *before*
  the drain evacuates — hence the sequenced (scheduled-start) rinse-then-drain.
- **Fire the clean on the return beam (a moment earlier, away from the cue).**
  Deferred: trial start was chosen for simplicity; the click coincides with cue
  onset (noted below). Easy to revisit by moving the emit to `AwaitReturn`.

## Consequences

- All 15 Solenoid-test buttons and the Pulse(ms) field now work under
  `cue-guided-task`, with the correct marker flash, in any mode and even between
  sessions. `host/rig_view.py` is unchanged (it already flashes on `SOL`).
- Each trial (after the first) auto-fires a center rinse+drain. **Caveats:** the
  drain must evacuate faster than the rinse fills (else the well overflows), and
  the solenoid clicks land near cue onset (an added sound during the cue). Both
  are acceptable but documented in `rig_config.h`; disable with
  `AUTO_DRAIN_ON_TRIAL false`.
- The pure core stays fully host-testable: new tests cover `CleanWell` firing at
  trial start (not the first) and being gated by the toggle, and the exhaustive
  no-overflow proof now includes the auto-clean effect. The shell solenoid path,
  like tmaze_test's, is not host-unit-tested.
- `WELL_RINSE_MS` / `WELL_DRAIN_MS` are `CONFIRM` placeholders pending bench
  verification against the actual well volume and drain rate.

## Evidence / links

- Code: `handleManualSolenoid()` / `openSolenoid()` / `updateValves()` and the
  `FxTag::CleanWell` case in
  [`cue-guided-task.ino`](../../firmware/cue-guided-task/cue-guided-task.ino);
  `cleanWell()` + the `Idle` emit in
  [`task_core.h`](../../firmware/cue-guided-task/task_core.h).
- Tests: `test_auto_drain`, updated `test_free_roam` and
  `test_no_overflow_exhaustive` in
  [`test_task_core.cpp`](../../firmware/cue-guided-task/test/test_task_core.cpp)
  (suite passes: "ALL TESTS PASSED", 5120 exhaustive cases).
- Protocol: [`PROTOCOL.md`](../../firmware/PROTOCOL.md) (SOL event, command table,
  auto rinse/drain note).
- Reference: blocking fire in
  [`tmaze_test.ino`](../../firmware/tests/tmaze_test/tmaze_test.ino) `handleSerial()`.
