# ADR 0019: Log reward pulse duration on the wire and track dispensed volume on the host

- **Status:** Accepted
- **Date:** 2026-06-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/common/rig_config.h` (PROTO_VERSION 3 -> 4), `firmware/cue-guided-task/task_core.h` + `cue-guided-task.ino` (REWARD/SOL lines), `firmware/PROTOCOL.md`, `host/reward_volume.py`, `host/experiment_control.py`
- **Supersedes / Superseded by:** —

## Context

During a training/experiment session the experimenter needs to know how much
reward (water) the animal has received, in order to titrate the daily
water-restriction top-up (the animals are on a water-restriction protocol; see
`os_code_t_maze_resources/animal_protocol/RA23A232.pdf`). The rig dispenses
reward by opening a solenoid for a fixed time; the empirically measured volume
is ~60 uL at the current pulse width. Nothing in the software encoded a volume,
and the GUI showed no running total.

Two facts made naive counting unreliable:

- Reward is dispensed at several durations. Automatic task rewards use the fixed
  `REWARD_PULSE_MS` (300 ms), but manual fires from the GUI's solenoid bar use
  whatever pulse width is set there (1-5000 ms). Volume is roughly linear in
  pulse width but not exactly, so per-duration calibration is wanted.
- Before v4 the `REWARD`/`SOL` lines did **not** carry the pulse duration. The
  host could only know a manual pulse's duration if *it* sent the command, which
  fails for any other command source (legacy `maze_gui.py`, future tools) and is
  awkward to correlate with the asynchronous `REWARD,...,MANUAL` echo.

## Decision

Put the **actual pulse duration on the wire** and compute volume on the host
from those device-emitted events.

- **Protocol v4 (firmware -> host).** Append a trailing `<dur_ms>` field to the
  `cue-guided-task` reward/solenoid lines:
  `REWARD,CENTER,<dur_ms>`, `REWARD,<port>,MANUAL,<dur_ms>`,
  `SOL,<port>,<sol>,<dur_ms>` (and the auto well-clean rinse/drain SOL lines).
  The field is *appended*, so a type-dispatching parser that ignores trailing
  fields it does not need is unaffected. `PROTO_VERSION` is bumped 3 -> 4.
- The automatic reward duration is threaded through the pure core as a new
  `TaskConfig.rewardPulseMs` (set from `REWARD_PULSE_MS` in `makeConfig()`), so
  the value the core *logs* on `REWARD,CENTER` equals the value the shell *fires*
  (`applyEffects` drives `openSolenoid` from `cfg.rewardPulseMs`). The core stays
  hardware-agnostic (it cannot see the `REWARD_PULSE_MS` macro), which is why the
  duration is carried in config rather than referenced directly. A new
  `Effect.tail` numeric field (printed after the detail token) carries it without
  disturbing the existing `logN*` "numbers before detail" ordering.
- **Host volume model.** A small `host/reward_volume.py` holds a *config
  constant* calibration table mapping pulse width (ms) -> measured volume (uL),
  e.g. `{300: 60.0}`. `reward_volume_ul(dur_ms)` returns the exact value for a
  calibrated width, else linearly inter/extrapolates across the measured points
  (origin-anchored, i.e. volume proportional to duration, when only one point is
  known). The experiment GUI parses `REWARD` lines, sums the per-pulse volume,
  and shows a running total (with an automatic-vs-manual split), reset on each
  `MODE` (session restart) and via a button.

We track **dispensed** volume only (count of reward pulses x calibrated volume),
not "consumed" - lick-based consumption is a rougher proxy and was explicitly
out of scope.

## Alternatives considered

- **Host-only, no firmware change** (count `REWARD,CENTER` as 300 ms; attribute
  manual pulses the duration *this* GUI sent). Rejected: only counts manual
  rewards triggered from `experiment_control.py`, can't be re-derived offline
  from the log, and re-introduces a hardcoded 300 ms assumption on the host.
- **Duration as a leading numeric field** (`REWARD,<dur>,CENTER`). Rejected:
  manual lines already end `...,MANUAL`, so a leading position would put the
  duration in a *different* field index for automatic vs manual lines; a trailing
  field is the last token in both, so the host reads `fields[-1]` uniformly.
- **GUI-editable calibration field.** Deferred: the experimenter asked for the
  value to live in code/config for now (re-measure and edit the table when the
  pulse width changes). Could be revisited if recalibration becomes frequent.
- **Fixed volume per activation regardless of duration.** Rejected: manual
  pulses vary in width, so a per-duration model is needed for accuracy.

## Consequences

- Reward volume is computed from what the device actually fired, is correct for
  any command source, and is recorded self-describingly in `host/data/*.csv`, so
  a session's dispensed volume can be recomputed offline.
- Protocol bumped to v4. The host parsers tolerate a missing trailing field
  (fall back to `REWARD_PULSE_MS`) so the GUI still runs against an
  un-reflashed Due, but the Due must be reflashed to get accurate per-pulse
  durations. No host client asserts the exact `PROTO` value, so the bump is
  non-breaking there.
- `tmaze_test` is unchanged (it emits no `REWARD` lines and its `SOL` ack carries
  no duration); only `cue-guided-task` `SOL` lines gain the field.
- The calibration constant (`{300: 60.0}`) is a placeholder anchored on a single
  bench measurement; re-measure per pulse width used and extend the table.

## Evidence / links

- Reward-at-center design: ADR-0007. Manual solenoid + well-clean: ADR-0010.
- Pure-core unit tests (`firmware/cue-guided-task/test/test_task_core.cpp`)
  assert the reward line now carries `tail == rewardPulseMs`; all tests pass.
- Protocol field tables: `firmware/PROTOCOL.md` (v4).
