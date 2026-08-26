# ADR 0120: A mid-session cue-gate reopen resumes the run, it does not restart it

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/phase3_controller.py` (`_process_frame` gate edges, new `_is_session_reopen` / `_resume_after_reopen`), `host/tmaze/core/session/session_notes.py` (new `AUTO_RESUME` tag), `host/tests/test_session_phase3_wiring.py`
- **Supersedes / Superseded by:** —

## Context

In a host-owned Phase 3 run (ADR-0046) the cue gate's **rising edge** meant
"start a fresh session": `_process_frame` called `_arm_session`, which
regenerates the sequence from the seed at trial 1, builds a new `Phase3Log`, and
(re)writes the data file. That is right for the *first* arm.

But the gate also closes and reopens for mundane reasons **mid-session** — the
operator pauses tracking to reposition the rat or clear the camera view, or the
firmware briefly changes mode. When that happened during an active session,
`_arm_session` ran again with `restart_index=0`, recomputed the *same*
`phase3_<session_id>.csv` name, and overwrote the session's file with a blank
plan at trial 1. The trials already run were lost from disk, the trial index
reset, and the operator got no warning. Captured as FW-0026; made more damaging
by ADR-0066 (Start now reliably binds the file into the session folder, so a
reopen clobbers the *session's* record rather than a stray `host/data/` file).

This matters ahead of neural-recording integration: the behavior CSV is a stream
we align spikes against, and a silently truncated mid-session file is a silent
hole in that alignment.

## Decision

Distinguish "first/new arm" from "the gate flickered mid-session" on the rising
edge, and **resume** on a flicker (the option FW-0026 recommended and the
operator confirmed):

- `_is_session_reopen(snap)` is true when a session is already armed
  (`self._log is not None`) AND `snap.session_id == self._log.session_id` — the
  SAME session is still active. A different or absent `session_id` (a new
  coordinated session, or a re-arm after Stop cleared the context) is a genuinely
  new run and still `_arm_session`s. A manual run (both ids blank) resumes its own
  file; a clean manual run is the explicit Restart button (ADR-0058).
- `_resume_after_reopen(snap)` keeps the task, sequence, trial index, and data
  file exactly as they were. It only resets the task-event edges, runs the
  existing `_silence_and_rearm` (cut any stale tone; reset the per-trip
  cue/gate/well state), and re-sends `CUE,VISION` so the host re-owns the DAC.
  The next trial then waits for a fresh hub→up-stem run, exactly like any
  silence-and-rearm.
- The reopen is **surfaced** so it is never mistaken for a new run: a
  per-run `_reopen_resumes` counter shows `resumes:N` in the ARMED status line,
  and an `auto:resume` operator-intervention note lands on the session timeline
  (docs/decisions/0111).

## Alternatives considered

- **Keep re-arming on every rising edge (status quo).** Rejected — it is the bug:
  a mundane tracking flicker overwrites the session's data with a blank plan and
  resets the trial index, silently.
- **Explicitly restart on a reopen via the `_r<n>` path (ADR-0058).** Preserves
  the prior run's file as `_r<n>` rather than overwriting it, but still resets to
  trial 1 and splits one continuous run into two files. Rejected as the default:
  a brief tracking pause is not a new run, and Restart already exists for when the
  operator truly wants a clean run.

## Consequences

- A mid-session tracking pause/resume (or brief firmware mode change) no longer
  loses data or resets the trial index; the run continues where it left off, with
  the flicker visibly recorded.
- A gate reopen for the *same* session is now a no-data-loss operation; the only
  side effects are reclaiming the DAC and clearing stale cue state — the same
  things a Skip/Repeat/Back already do.
- Manual-mode semantics change slightly: a reopen of a manual run resumes its
  file instead of starting a new timestamped one. This is the deliberate
  zero-data-loss default; Restart gives a clean manual run.
- Reopen detection keys on `session_id` identity, so it depends on Stop clearing
  the session context (`set_session_context()` with no args) — which it does, and
  which the existing `test_second_session_does_not_inherit_the_first_ones_file`
  regression continues to guard.

## Evidence / links

- FW-0026 (now Done); builds on ADR-0066 (rebind on Start), ADR-0058 (Restart /
  `_r<n>`, the `auto:*` note tags), ADR-0047 (the data file), ADR-0111 (operator
  intervention notes).
- Test: `test_session_phase3_wiring.test_mid_session_reopen_resumes_and_preserves_data`
  (asserts the SAME log object, file path, table, and index survive a
  close→reopen, and the resume is surfaced); the four pre-existing wiring tests
  stay green, including the post-Stop re-arm case. Full host suite green.
