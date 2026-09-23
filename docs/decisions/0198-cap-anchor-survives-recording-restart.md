# ADR 0198: Anchor the session cap once per arm, surviving a recording restart

- **Status:** Accepted
- **Date:** 2026-09-23
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/phase3_controller.py`
  (`set_session_context` anchor-once logic, `_arm_session` / `disarm` reset,
  new `_cap_anchored` / `_cap_t0` state, `_Phase3Snapshot.cap_t0`,
  `_publish_snapshot`, `_session_elapsed_s`, the `_process_frame` cap gate and
  the status countdown); tests `test_session_phase3_wiring`
- **Supersedes / Superseded by:** refines ADR-0190 (points 1 and 4); builds on
  ADR-0060, ADR-0124

## Context

ADR-0190 counts the session cap - `max_trials` scored trials and `max_minutes`
wall-clock - from **Session Start**, and re-takes that anchor on *every* Start:
`set_session_context(session_id, ...)` set `_cap_base_done = correct + incorrect`
and the clock used the current recording's `session_t0.mono`.

On 2026-09-23, rat 600's session `600_20260923_141558` ran **124 scored trials
with the 120-trial cap never firing** - the operator's own notes:

- trial 26: "Stopped and restarted session in between since video recording was
  in lossless"
- trial 28: "Stopped and restarted Session recording that is. Animal was not
  interrupted in task"
- trial 124: "120 trial cap did not trigger. Trials were still sounding cues.
  Ending session"

The video had been recording lossless (FFV1) instead of H.264, so the operator
stopped and restarted the **recording** at ~trial 28 to fix the codec - the rat
kept doing the task throughout. But stopping the recording released the Phase 3
context and the restart re-took the cap anchor: `_cap_base_done` jumped to ~28
and the clock re-zeroed, so the 120-trial / 45-min budget **restarted**. The cap
would not have fired until ~trial 148; the session ended at 124. The summary
confirms `session_cap.reached: null`.

The recording restart is operationally innocuous (same arm, same animal,
uninterrupted task) but the cap treated each Start as a brand-new window. Two
related consequences of the old code produced the miss: the trial base was
re-taken, and the time clock was tied to the *per-recording* `session_t0`, which
changes with each bundle.

## Decision

Anchor the cap **once, at the first Session Start after an arm**, and preserve
that anchor across a stop->restart of the recording within the same arm. The
operator chose: count continuously from the first Start (trials and clock),
including the brief stop->restart gap.

1. **Anchor-once.** New `_cap_anchored` flag. `set_session_context` with a
   `session_id` takes the anchor - `_cap_base_done = correct + incorrect`,
   `_cap_t0 = session_t0`, `_cap_anchored = True` - **only if not already
   anchored**. A subsequent Start (the restart) finds `_cap_anchored` true and
   leaves the base and clock untouched, so counting continues from the first
   Start.
2. **A dedicated, preserved clock anchor.** New `_cap_t0` (published as
   `_Phase3Snapshot.cap_t0`) holds the *first* Start's manifest t0. The cap's
   elapsed time (`_session_elapsed_s`) and the boundary gate now read `cap_t0`,
   not the per-recording `session_t0`. Because the monotonic clock is continuous
   across recordings in one process, elapsed runs continuously from the first
   Start, through the gap, across the restart.
3. **Reset only on a genuine new run.** `_arm_session` (a fresh arm or the
   Restart button, ADR-0058) and `disarm` clear `_cap_anchored` / `_cap_t0` /
   `_cap_base_done`, so the *next* arm+Start re-anchors a fresh budget. A gate
   flicker after Start is a reopen/resume (ADR-0120), not an arm, so it does not
   reset. This matches the real workflow: a new animal gets a fresh trial
   sequence + log via a re-arm (which now also re-anchors the cap); only a
   recording restart *without* a re-arm preserves the budget.

This refines ADR-0190 point 1 ("counts from Session Start") to "from the *first*
Session Start of the current arm", and point 4 (which said the next Start
re-takes the base) to "a recording restart preserves the base; only a re-arm /
Restart re-takes it".

## Alternatives considered

- **Count from ARM instead of Session Start.** The simplest anchor (no
  first-Start bookkeeping), but it reverses ADR-0190's deliberate "from Session
  Start" choice and would count the arm-before-Start warm-up trials. Rejected by
  the operator.
- **Pause the clock during the stop->restart gap** (sum only recorded stretches).
  More state to track and a long pause would stretch the wall-clock session;
  since the animal is not interrupted, a continuous clock is the honest measure.
  Rejected.
- **Reset the anchor on the gate FALLING edge / Stop instead of on arm.** A new
  animal started without an explicit re-arm would then inherit the previous
  budget. Keying the reset to the arm (which a new run already requires for its
  sequence + log) is the correct grain.
- **Do nothing (accept the reset).** Leaves a silent failure mode: any
  mid-session recording restart uncaps the rest of the run, exactly the case
  that occurred.

## Consequences

- A codec-driven (or any) recording restart mid-run no longer resets the cap;
  the rat is held to one 120-trial / 45-min budget across the restart.
- The cap now applies whenever a session has been Started this arm, including
  after Stop (the anchor is preserved until re-arm). A rat left running past the
  budget after Stop is silently frozen rather than cued indefinitely; no banner
  shows post-Stop (the banner is gated on an active session, ADR-0192).
- `_session_elapsed_s` is now "seconds since the cap anchor" (the first Start),
  not "since this recording's Start"; the two differ only across a restart.
- No on-disk format change: `cap_t0` is a live snapshot field, never written to
  a bundle (golden fixture / analysis mirror untouched). Provenance recorded by
  ADR-0190 is unchanged; a reader still sees `session_cap.reached`.
- The test harness must arm the way real usage does (a frame between opening the
  gate and Start) or the anchor is wiped by the first trial's arm edge; a new
  `_arm()` helper makes this explicit.

## Evidence / links

- Bundle `600_20260923_141558`: operator notes (quoted above) and
  `session_summary_*.json` with `session_cap.reached: null` at 124 scored trials.
- Code: `Phase3ControlView.set_session_context` (anchor-once), `_arm_session` /
  `disarm` (reset), `_cap_anchored` / `_cap_t0`, `_Phase3Snapshot.cap_t0`,
  `_publish_snapshot`, `_session_elapsed_s`, `_process_frame` cap gate,
  `_publish_status` countdown.
- Tests: `tests/test_session_phase3_wiring.py`
  (`test_cap_survives_a_recording_restart_and_counts_continuously`,
  `test_a_fresh_rearm_re_anchors_the_cap_budget`,
  `test_cap_banner_clears_on_stop_but_mute_persists`, plus the updated
  `_arm()` ordering for the existing trial/time-cap tests).
- Related: ADR-0190 (session cap), ADR-0192 (rig-view banner), ADR-0060
  (Start/Stop ownership), ADR-0124 (`session_t0`), ADR-0120 (gate reopen/resume).
