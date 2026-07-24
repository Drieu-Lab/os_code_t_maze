# ADR 0060: The Session button owns the Phase 3 session boundary

- **Status:** Accepted
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_controller.py`, `host/phase3_controller.py`, `host/behavior_view.py`, `host/experiment_control.py`, `host/tooltip_text.py`
- **Supersedes / Superseded by:** Supersedes the "arm Phase 3 on Start / disarm on Stop" part of ADR-0048

## Context

The Rig-view "Session" panel (`session_controller.py`, ADR-0048) is a one-click
"record everything for this session" control: it mints a `session_id`, creates
the per-session folder tree (ADR-0049), starts the camera, opens the filtered
serial file, and writes the manifest. As built, Start also *armed* Phase 3
(`phase3.arm(session_id, out_dir)`) and Stop *disarmed* it.

Two problems surfaced in use:

1. **Start could record a session with the task off.** Arming Phase 3 from the
   Session button meant the operator never had to think about the task's own Arm
   checkbox; but arming only opens the gate once Free Roam + tracking + a zone map
   are also true, so it was easy to start "a session" that wasn't actually running
   the cued task.
2. **Stop killed the task and hid its stats.** Disarming on Stop closed the Phase 3
   gate, which cut the tone/DAC and - because the behavior panel only shows the
   Phase 3 readout while the published snapshot is `armed` (ADR-0050/0052) - made
   the live Phase 3 stats vanish (the panel reverted to Phase 2a). The operator
   wanted to stop *recording* without ending the *task*.

Separately, the Phase 3 stats panel had no session clock (start/stop/duration)
like the Phase 2a readout's (ADR-0036). The natural owner of that clock is the
Session button, not Phase 3's arm/gate: the operator asked for the times to track
the Session Start/Stop button.

## Decision

Make the Session panel's Start/Stop button the canonical Phase 3 **session
boundary**, and stop coupling it to Phase 3's arm state:

- **Start requires Phase 3 to already be armed.** `Phase3ControlView.is_armed()`
  reports the Arm checkbox; `SessionControlView._start_session` refuses (a warning
  dialog + status line, no folders created, no camera started) if a Phase 3
  controller is present but not armed. Enforced only when a controller exists, so
  standalone/headless use is unaffected.
- **Start no longer arms Phase 3; it only sets the session context.** A new
  `Phase3ControlView.set_session_context(session_id, out_dir)` records the shared
  id + `task/` folder *without* touching the Arm checkbox (unlike `arm()`), so the
  Phase 3 data file still correlates with the video + manifest.
- **Stop no longer disarms Phase 3.** The `phase3.disarm()` call is removed from
  `_stop_session`; the task keeps running and its live stats stay visible.
- **The button drives the Phase 3 stats session clock.** `SessionControlView` owns
  a `session_timer.SessionTimer` (reset+start on a committed Start, stop on Stop)
  exposed via `session_timing()`. `BehaviorView` renders a `Session: start - stop
  (duration)` line at the top of the Phase 3 sub-panel from an injected
  `session_timing` provider (wired through a `session_ref` holder in
  `experiment_control.py`, mirroring the `phase3_state` seam). Phase 2a keeps its
  own local timer + Start/Stop button unchanged.

The two halves reinforce each other: because Stop leaves Phase 3 armed, the
behavior panel stays on the Phase 3 readout after Stop, so the session line's stop
time and final duration are actually visible (they wouldn't be if Stop disarmed
and the panel flipped back to Phase 2a).

## Alternatives considered

- **Keep Start arming Phase 3, just skip disarm on Stop.** Rejected: it leaves the
  "recorded a session with the task off" hazard, and makes the Arm checkbox and the
  Session button fight over one piece of state.
- **Drive the Phase 3 session clock from Phase 3's own arm/gate** (e.g. a
  `SessionTimer` inside `Phase3ControlView`, started on `_arm_session`). Rejected:
  the operator asked for the times to track the Session button; and since the panel
  hides the Phase 3 readout the instant the gate closes, a gate-driven stop time
  would never be visible.
- **Keep showing the Phase 3 panel after the session ends** (persist the frozen
  final stats regardless of `armed`) so a stop time could show under the old
  disarm-on-Stop behavior. Rejected as a larger change to the ADR-0052 phase-switch
  logic; leaving Phase 3 armed after Stop achieves the same visibility more simply.

## Consequences

- The operator's mental model is explicit: arm the task (Phase 3 tab), then
  Start/Stop the recording; Stop ends the recording, not the task. Ending the task
  is a deliberate, separate Arm-checkbox action.
- A session can no longer be started without the task armed, removing a class of
  "empty" recordings.
- The `Phase3ControlView.arm()` / `disarm()` seams remain for direct/programmatic
  use, but are no longer called by the Session panel.
- The behavior panel now depends on one more injected provider (`session_timing`);
  it degrades to "Session: not started" when none is wired.
- Follow-up left open: the Phase 3 session line reflects the recording window, not
  necessarily the exact span the gate was open; if a distinct "task-active" clock
  is ever wanted, it would be a separate measure.

## Evidence / links

- Supersedes the arm/disarm coupling in ADR-0048 (host session orchestration);
  builds on ADR-0049 (per-session folder tree), ADR-0050 (published snapshot),
  ADR-0052 (phase-aware behavior panel), and ADR-0036 (Phase 2a session timer,
  whose `SessionTimer` core is reused).
- Code refs: `SessionControlView._start_session` / `_stop_session` /
  `session_timing` (`host/session_controller.py`), `Phase3ControlView.is_armed` /
  `set_session_context` (`host/phase3_controller.py`),
  `BehaviorView._p3_session_text` (`host/behavior_view.py`), the `session_timing`
  wiring (`host/experiment_control.py`).
