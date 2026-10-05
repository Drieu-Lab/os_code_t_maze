# ADR 0210: Tell an operator re-arm apart from a gate flicker

- **Status:** Accepted
- **Date:** 2026-10-05
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/phase3_controller.py` (`_Phase3Snapshot.disarm_count`,
  `_on_enable_write`, `_is_session_reopen`, `_plan_settings`,
  `_resume_after_reopen`, the return-cue display sync in `_process_frame`, the
  status line), `host/tmaze/ui/tooltip_text.py` (`phase3.arm`),
  `host/tests/test_session_phase3_wiring.py`
- **Supersedes / Superseded by:** amends ADR-0120 (gate reopen resumes the session)

## Context

ADR-0120 made a gate rising edge RESUME the run whenever the session id
matched the one the live log was armed with, so a tracking flicker or a brief
firmware mode change would not overwrite the session's data file. The gate,
though, folds together the Arm checkbox, tracking, Free Roam mode and a valid
zone map, so the controller could not tell a flicker from the operator
deliberately unticking and re-ticking **Arm Phase 3**. With no recording
running, both session ids are blank, so every re-arm counted as a "reopen" and
resumed.

Bug seen on the bench (2026-10-05): the operator armed with the return cue on,
disarmed, pressed **New** seed, turned the return cue off and re-armed. The run
resumed: the old seed and plan were kept (status `seed:1520527187` against a
seed field of `3100947903`, `resumes:2`), and the ribbon and planned-trials list
still showed `ret L:_ R:_`. The display's return-cue flag was fixed when the run
was first armed, while the cue gate reads the checkbox live on every frame. So
the screen and the speaker could disagree in either direction.

## Decision

1. **Count operator disarms on the Tk side.** A variable trace on the Arm
   checkbox increments `_disarm_count` on every untick, and the count rides the
   Tk-to-thread snapshot (`_Phase3Snapshot.disarm_count`). The actuation thread
   stores the count at each arm/resume (`_armed_disarm_count`). A rising edge
   whose count has moved is an operator re-arm. A counter rather than a flag
   means the Tk thread is the only writer and the actuation thread only
   compares. A trace rather than a per-frame check catches an untick made while
   tracking is off and no frames are flowing.
2. **Not recording: an operator re-arm is a fresh run.** `_is_session_reopen`
   returns False for it, so `_arm_session` builds a new plan from the current
   seed and settings. Nothing is lost: an unrecorded run writes no file
   (Increment 4b).
3. **Recording: it still resumes, but warns.** Re-arming fresh would rewrite
   the session's trial CSV at the same path, so resuming stays the safe default.
   The settings that only apply at arm (`_plan_settings`: seed, trial config,
   reward-incorrect, pulse width, max top-ups) are compared with the ones the
   run was armed with. If any changed, the `auto:resume` note says they were NOT
   applied, and the status line shows a persistent warning until the next arm or
   Restart. Restart (which writes a new `_r<n>` file) is the deliberate way to
   apply them.
4. **The return-cue display follows the checkbox.** Each armed frame copies
   `CueParams.return_enabled` into `Phase3Log.return_enabled`, which only feeds
   the published snapshot (ribbon + planned-trials list) and is not written to
   the CSV. Which return cue actually played is still recorded per trial
   (`return_cue_played`).

Tracking or firmware flickers are unchanged: they resume whether or not a
recording is running (ADR-0120).

## Alternatives considered

- **During a recording, treat an operator re-arm like Restart (new `_r<n>`
  file).** Rejected by the operator: one accidental untick/retick would silently
  split a session and reset the trial count to 1. Restart already exists as the
  deliberate path.
- **Re-arm fresh only when a setting changed while disarmed.** Rejected for the
  unrecorded case: the operator expects Arm to mean "start", and an unrecorded
  run has nothing to protect.
- **Lock the seed/sequence/reward widgets while armed.** Doesn't help: the edits
  in the bug were made while DISarmed.
- **Detect the untick from the snapshot at the gate's falling edge.** Misses an
  untick/retick made while tracking is off (no frames, so no edges).

## Consequences

- With no recording, unticking and re-ticking Arm now discards the in-memory
  run, its tallies and its trial index. That is the intended meaning of "arm",
  and nothing was on disk.
- During a recording, edits made while disarmed are never silently dropped: the
  operator is told, and Restart applies them.
- Programmatic `disarm()` also writes the checkbox, so it counts as an operator
  disarm. It already clears the session context, so a following arm was a fresh
  run anyway.
- A mid-run toggle of the Return-cue checkbox now shows on the ribbon at once,
  matching what the gate plays.

## Evidence / links

- Operator report + screenshot, 2026-10-05 (seed field vs status seed,
  `resumes:2`, return cues listed with the box unticked).
- Tests: `test_session_phase3_wiring` -
  `test_operator_rearm_when_not_recording_starts_fresh`,
  `test_tracking_flicker_when_not_recording_still_resumes`,
  `test_operator_rearm_while_recording_resumes_and_warns`,
  `test_operator_rearm_while_recording_without_edits_does_not_warn`,
  `test_ribbon_return_flag_follows_the_checkbox_mid_run`.
