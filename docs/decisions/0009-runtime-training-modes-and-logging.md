# ADR 0009: Runtime-selectable training modes, anti-bias sequencing, and a persistent session logger

- **Status:** Accepted
- **Date:** 2026-06-23
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/task_core.h`, `firmware/cue-guided-task/cue-guided-task.ino`, `firmware/common/rig_config.h`, `firmware/PROTOCOL.md`, `host/rig_logger.py`, `host/experiment_control.py`, `host/start_rig.bat`, `PROGRAMS.md`
- **Supersedes / Superseded by:** —

## Context

Training a rat on the cued two-alternative T-maze task is not a single step:
animals first have to learn the apparatus and where reward is delivered before
the cue->side->reward contingency is introduced. The standard rodent shaping
literature (e.g. Holleman et al. 2019, *Sci Rep* 9:12589; the RIFF; A1
associative-learning studies) breaks training into graded stages and stresses
two things this rig did not previously support:

1. A pre-task **habituation / free-roam** stage where the animal explores and
   collects freely available reward, with no cues or trial structure.
2. **Counterbalanced trial sequences that discourage side-bias** — rats exploit
   long same-side runs, so sequences with >3 consecutive repeats are rejected.

Two further gaps blocked real experiments:

- The task ran in one fixed mode chosen at compile time; switching required a
  reflash, which is impractical for daily, per-animal training.
- Nothing persisted the data. `rig_broker.py` only printed to stdout, and in
  Free Roam the reward is delivered by the experimenter clicking a solenoid
  button — an event that was not logged at all.

The architecture constrains the solution: the behavioral task lives entirely in
the firmware functional core (`task_core.h`), the broker is a transparent
passthrough (ADR-0001/0002), and clients connect only via the broker (ADR-0003).

## Decision

Add **two runtime-selectable training modes** and proper logging:

- A `Mode { FreeRoam, FullTask }` enum in `task_core.h`, carried in `TaskConfig`.
  In `step()`, `FreeRoam` gates off the trial state machine (no cues, no trials,
  no session timeout) while still honoring host commands; the manual flush path
  now emits `REWARD,<port>,MANUAL` so Free Roam reward delivery is recorded.
- A `MODE,FREE` / `MODE,FULL` host command. Because switching re-seeds,
  regenerates the sequence, and re-dumps the handshake, it is handled in the
  imperative shell (`cue-guided-task.ino`), not the pure core. The firmware boots
  into `DEFAULT_MODE` (Free Roam) so a session never auto-starts on the wrong
  animal; selecting Full task is what starts trials.
- **Anti-bias sequence generation**: `generateSequence()` keeps exact per-block
  L/R balance but re-rolls any block whose shuffle would create a same-side run
  longer than `MAX_SAME_RUN` (default 3), counting across block boundaries. It
  stays deterministic from the seed (re-rolls use the threaded PRNG) and
  host-testable.
- **`SESSION_SUMMARY,<trials>,<correct>,<incorrect>`** is emitted before
  `SESSION_COMPLETE` for Full task sessions, to support criterion-based stage
  advancement without post-hoc parsing.
- A **persistent logger** `host/rig_logger.py`: a headless broker client (keeps
  ADR-0003) that appends every firmware line to `host/data/<animal>_<time>.csv`,
  prefixed with a host wall-clock column. The wall clock anchors events across
  mode switches (the firmware `ms` resets each session).
- The protocol is bumped to **v3** (`PROTO_VERSION = 3`) for the new `MODE`
  command/line, the `REWARD,...,MANUAL` form, and `SESSION_SUMMARY`.

## Alternatives considered

- **More training stages (cue-reward "hint", forced single-side, correction
  trials).** Deferred at the user's request to keep this change minimal; the
  `Mode` enum makes them straightforward to add later. The existing
  `START_REWARD_ENABLED` flag already covers the "reward regardless of choice"
  hint behavior if needed.
- **Move task/stage logic to the host.** Rejected: it would contradict the
  firmware-owns-the-task and transparent-broker decisions (ADR-0001/0002) and add
  latency/round-trips to a real-time loop.
- **Compile-time mode constant (reflash per mode).** Rejected as impractical for
  daily training; runtime selection is the whole point.
- **Implement the literature's ~60/40 same/switch transition bias.** Deferred:
  a deterministic 60/40 generator conflicts with exact per-block balance and is
  fiddly in integer-only firmware. The hard, testable rule (run-length cap) is
  the high-value part and is documented as the anchor; the ratio is left as a
  future tunable.
- **Tee logging inside the broker.** Rejected to keep the broker a transparent
  passthrough; a dedicated client is more modular and independently restartable.

## Consequences

- Daily training is now a GUI action: boot into Free Roam, refill the well with
  the `R2` button, then switch to Full task when the animal is ready — no
  reflashing.
- All devices/clients must be on protocol v3 together (no dual-format support).
- `Effect` gained a third numeric field (`c`) to carry the three-number
  `SESSION_SUMMARY`; all effect builders were updated and the no-overflow
  exhaustive test still passes (`FX_CAP` headroom is unaffected).
- Recorded data now lives in `host/data/` (git-ignored); the wall-clock column
  makes sessions alignable with external recordings (e.g. Open Ephys).
- A mid-session logger start names the file `unknown_<time>.csv` (no ANIMAL
  handshake to read) but loses no lines.

## Evidence / links

- Holleman, Mąka, Schröder & Battaglia (2019). *An incremental training method
  with automated, extendable maze...* Sci Rep 9:12589.
  https://doi.org/10.1038/s41598-019-48965-w
- Host unit tests: `firmware/cue-guided-task/test/test_task_core.cpp`
  (`test_free_roam`, `test_anti_bias_run_length`, `test_session_summary`) — all
  13 tests pass via `g++ -std=c++11 -I.. test_task_core.cpp`.
- Protocol: `firmware/PROTOCOL.md` (v3).
- Refs ADR-0007 (central-port reward, unchanged here).
