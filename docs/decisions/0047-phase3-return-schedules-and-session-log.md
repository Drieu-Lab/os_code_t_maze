# ADR 0047: Deterministic per-arm return-cue schedules + a Phase 3 session data file

- **Status:** Accepted
- **Date:** 2026-07-23
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/trial_sequence.py` (two balanced return-arm schedules; drop
  `next_return_freq`), `host/cue_gate.py` (`CueDecision.return_arm`),
  `host/phase3_log.py` (new pure core) + `host/test_phase3_log.py`,
  `host/phase3_controller.py` (matrix-driven return cue, event logging, atomic
  session file, Animal ID field), `host/test_trial_sequence.py`,
  `host/test_cue_gate.py`, `host/tooltip_text.py`, `PROGRAMS.md`
- **Supersedes / Superseded by:** builds on ADR-0046 (host-owned Phase 3), ADR-0045
  (the `CueGate` this extends), ADR-0026 (task-event layer), ADR-0043 (actuation
  thread + routed lick edges); —

## Context

In the initial host-owned Phase 3 (ADR-0046) the CHOICE cue is a seed-deterministic,
balanced, anti-bias schedule (the ported `task_core.h` generator), but the RETURN
cue was a **random hi/lo pick drawn per onset** (`TrialSequence.next_return_freq`,
mirroring the firmware). So the return-arm tones were neither planned nor
reproducible per trial, and there was **no per-session data record** of the
task at all: the Phase 3 tab only kept in-memory tallies. The only disk logging
was the separate `rig_logger.py` process dumping raw firmware lines.

The experiment needs (a) the return-arm cues generated the same principled way as
the choice cue, and (b) a self-describing, real-time per-session data file that
pairs the planned cue schedule with the animal's choices and event timing, so a
session can be analyzed (and reproduced from its seed) without re-deriving
anything from the raw serial log.

Two facts shaped the design. First, the T-maze has two physical return arms
(`LEFT_RETURN` / `RIGHT_RETURN`), and the tone that should sound depends on which
arm the rat actually enters — but `CueGate` reported only `started == RETURN`, not
the arm. Second, licks are **not** in the vision pipeline; they arrive only as
firmware `L,<i>` serial lines through the module's `apply()` (the serial-reader
thread), while everything else in Phase 3 runs on the actuation thread.

## Decision

**1. Three seed-deterministic schedules, one seed.** Refactor the balanced
generator into `generate_balanced(n, block, max_run, rng) -> (seq, rng)` and reuse
it for the choice L/R schedule (unchanged) **and** a balanced `HIGH`/`LOW`
schedule for **each** physical return arm. All three are threaded off the one
session seed (choice, then left-return, then right-return), so the whole session —
including both return-arm cue series — reproduces from the seed. `next_return_freq`
is removed; the played return cue is now `TrialSequence.return_freq(side, index)`,
looked up by the physical arm the rat enters.

**2. `CueGate` reports the return arm.** `CueDecision` gains `return_arm`
(`"left"`/`"right"`, set only on a RETURN onset) so the controller can pick that
arm's planned cue.

**3. A pure session-log core + atomic file.** `phase3_log.Phase3Log` holds the
planned `N`-row matrix (`correct_arm`, `choice_cue`, `return_cue_left`,
`return_cue_right`) and fills each trial's outcome + event timestamps as it
resolves, rendering the whole table to CSV text. The controller writes it to
`host/data/phase3_<animal>_<ts>.csv`, written on arm (plan) and **atomically
re-written** (temp + `os.replace`) on each update, so a crash never leaves a
half-written file. An **Animal ID** field on the tab names the file.

**4. Timeline + lick attribution (the corrected Phase 3 trial shape).**
`trial_start` = the first `HUB→stem` transition of a trial; `choice_time` = the
scored choice (choice-arm entry); `reward_cmd` = the host reward fire; `trial_end`
= `loop_closed` (back at hub, which also advances the trial — **unchanged**). The
reward-collection **lick** is logged only as `lick_time` (+ firmware `lick_fw_ms`),
not as a trial boundary: it is the first center-well lick **after** the reward
command. Because that lick lands after `loop_closed` (index already advanced), it
is attributed via a **pending-reward pointer** to the trial whose reward it
collects; it is blank on unrewarded trials / if no lick is seen. Licks are snooped
in `apply()` (center channel resolved from the shared `rig_config` pin map) and
routed through the actuation queue so the log stays single-writer (ADR-0043).

## Alternatives considered

- **Advance the trial on the hub-well lick (the "real" trial end).** More faithful
  to the behavior, but it makes the pure vision task core depend on a firmware
  lick signal and needs an unrewarded-trial fallback (the rat may not lick an
  empty well). Rejected for this cut: keep boundaries pure-vision and log the lick
  as a timestamp only.
- **Index each return schedule by a per-arm visit counter** (so the tones actually
  *played* on one arm are perfectly balanced). Rejected: it breaks the "row =
  trial" matrix the data file is built around. With row-indexing, each column is
  balanced across trials but the realized played subset on one arm is not — an
  accepted, documented consequence.
- **Keep the return cue random, only record it.** Rejected: the task wants the
  return cues generated the same principled (balanced, reproducible) way as the
  choice cue.
- **Reuse `rig_logger.py` / one combined file.** Rejected: that logger is a
  separate broker-only process logging raw lines; the Phase 3 data file is a
  task-level, self-describing table the tab owns. They are complementary.

## Consequences

- Return-arm cues are now balanced and reproducible from the seed, matching the
  choice cue; a session's full cue plan is knowable up front.
- Every Phase 3 session produces a self-describing CSV pairing the plan with the
  animal's choices and event timing, written live and crash-safe.
- The realized played return tones on a single arm are a subset of that arm's
  balanced column, so they are not guaranteed balanced (row = trial indexing).
- Host `*_iso` stamps are actuation-decision times and carry serial latency; the
  firmware `lick_fw_ms` is the hardware clock for the one firmware-reported event.
- No wire-protocol change: the return cue is still `CUE_ON,RETURN,<hz>`; only how
  the host picks `<hz>` changed. No reflash. `next_return_freq` is removed (only
  the Phase 3 controller used it).
- The controller now also reads firmware lick lines in `apply()` and does file
  I/O on the actuation thread (a few hundred rows per rewrite — negligible).

## Evidence / links

- ADR-0046 (host-owned Phase 3 this extends), ADR-0045 (`CueGate`), ADR-0043
  (actuation thread + routed lick edges), ADR-0021/0022 (shared rig-config lick
  pin map), ADR-0019 (`REWARD` pulse-duration field).
- `firmware/PROTOCOL.md` — `L,<i>,<state>` lick edges and `CUE_ON,RETURN,<hz>`.
- Tests: `host/test_trial_sequence.py`, `host/test_cue_gate.py`,
  `host/test_phase3_log.py`.
