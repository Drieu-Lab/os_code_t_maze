# ADR 0192: Surface the session cap on the Rig view (top banner + red Stop button)

- **Status:** Accepted
- **Date:** 2026-09-21
- **Author / Agent:** Agent (Cursor) on behalf of RJ
- **Affects:** `host/tmaze/core/phase3/phase3_log.py`
  (`Phase3StateSnapshot.cap_reached`, `Phase3Log.snapshot(..., cap_reached=)`),
  `host/tmaze/ui/phase3_controller.py` (`_publish_state` passes the cap record),
  `host/tmaze/ui/rig_view.py` (`RigView(state_getter=)` + the cap banner),
  `host/tmaze/ui/experiment_control.py` (injects the snapshot getter into
  `RigView`), `host/tmaze/ui/session_controller.py` (the Start/Stop button
  alert); tests `test_rig_view`, `test_phase3_log`
- **Supersedes / Superseded by:** extends ADR-0190 (session cap)

## Context

ADR-0190 makes a Phase 3 session a soft stop when the cap fires: cues and reward
go silent and a `CAP REACHED - remove rat, Stop session` banner appears. That
banner lives only on the **Task tab's** status line. In practice the operator
watches the **Rig view** tab during a session (the live maze schematic, the
camera, the trial ribbon and the Start/Stop button all live there), so the one
signal telling them the session is over was on a tab they were not looking at -
they would keep waiting while the rat sat in a silent maze.

Hard constraint from the operator: the Rig view already fills the monitor's full
vertical height, so the alert must add **no vertical span** - no new rows, no
taller panels. It has to reuse pixels already on screen.

## Decision

Surface the cap on the Rig view through two existing surfaces, both
height-neutral, driven off the published snapshot:

1. **`cap_reached` on the published snapshot.** `Phase3StateSnapshot` gains a
   `cap_reached` field (the controller's ADR-0190 record dict, or `None`);
   `Phase3Log.snapshot()` takes it as a keyword and `_publish_state` passes a
   copy each publish. Cross-tab readers now learn the cap fired without reaching
   into the controller - the same pull-mode pattern the trial ribbon / Metrics
   tab already use (ADR-0050). The publisher gates it on an **active recording
   session** (`_session_id` set): ADR-0190 keeps the controller's `_cap_reached`
   past Stop so the still-armed task stays muted for a rat left in the maze, but
   the "remove rat, Stop session" instruction is stale once Stop was pressed, so
   the banner must clear on Stop - it reappears only if a new capped session
   starts. (The Stop button, gated separately on the Session panel's own active
   flag, already cleared on Stop.)

2. **A (banner on the maze schematic).** `RigView` optionally takes the Phase 3
   snapshot getter (`state_getter`, injected in `experiment_control`) and
   self-polls it every 500 ms. While `cap_reached` is set it paints a loud,
   full-width red bar with `CAP REACHED (<limit>) - remove rat, Stop session`
   across the **top margin of the 480x500 maze canvas** - a canvas item over
   otherwise-empty pixels, so the pane never changes size. It blinks between two
   reds to catch the eye and clears the instant the cap record clears (a fresh
   arm / Restart / next Start). `close()` cancels the poll.

3. **C (red Stop button).** The Session panel already holds the controller
   reference, so it polls `cap_reached()` every 500 ms and, while a session is
   active and capped, switches the Start/Stop button to
   `STOP SESSION - cap reached` in a red, bold `Cap.TButton` style - pointing at
   the exact control to click. The reliable signal is the button **text** (some
   Windows `ttk` themes ignore button colour); the style is best-effort. It
   reverts on Stop / next session.

## Alternatives considered

- **B (recolour the "live stats" LabelFrame).** Lowest effort and it already
  polls the snapshot, but it sits low-left in the operator's gaze - weaker than a
  banner where the eyes already are. Kept as a fallback, not built.
- **F (paint into the camera HUD).** The largest, most central surface (the
  preview already draws `ACQUIRING` / `Zone:` text), but it is owned by
  `CameraView` and drawn by the preview/tracker pipeline, so it only shows while
  the preview renders and couples the alert to the camera - the most invasive
  option for no gain over A.
- **A new banner widget / status row.** The obvious approach, rejected outright
  by the no-added-height constraint.
- **Read the controller directly for the banner (skip the snapshot field).**
  Works, but every Rig-view reader would then depend on the controller's private
  cap state; the snapshot field keeps the one published contract and lets the
  Metrics tab reuse it later.
- **`tk.Button` for a guaranteed-colour Stop button.** Would make the red
  reliable on every theme, but swapping the widget type is more invasive than a
  loud text label; the text already carries the signal.

## Consequences

- The operator sees the cap on the tab they actually watch, and is pointed at the
  Stop button, without the window growing. The Task-tab status line is unchanged.
- `RigView` now optionally depends on the Phase 3 snapshot (a read-only pull); a
  standalone `RigView()` with no `state_getter` behaves exactly as before (no
  poll, no banner).
- Two 500 ms Tk `after` polls (one per surface) run while the app is open; both
  are cheap (a reference read + at most one reconfigure) and cancelled on close.
- The blink and the button both key off the single `cap_reached` record, so they
  can never disagree, and both clear together when the cap state clears.
- No `format_version` / manifest / summary schema change: `cap_reached` is a
  live UI field on the in-memory snapshot only, never written to disk, so the
  golden fixture and the analysis mirror are untouched.

## Evidence / links

- Operator requirement + Rig-view screenshot: 2026-09-21 conversation ("I am
  usually looking at the Rig View ... no elements are to be added which increase
  the vertical span").
- Code: `RigView._refresh_cap` / `_cap_banner_text`,
  `SessionControlView._update_cap_alert`, `Phase3Log.snapshot`,
  `Phase3ControlView._publish_state`.
- Tests: `tests/test_rig_view.py` (banner text for both limits),
  `tests/test_phase3_log.py::...test_snapshot_carries_cap_reached`; a Tk smoke
  check exercised the live canvas (hidden -> shown -> blink -> hidden -> close).
- Related: ADR-0190 (session cap), ADR-0050 (published snapshot / pull mode),
  ADR-0057/0135 (the ribbon's same pull pattern on the Rig view), ADR-0060
  (operator-owned Stop), FW-0070 (editable cap fields - separate).
