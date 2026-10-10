# ADR 0216: Offline workbench window - layout, file origins, and when to re-track

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/offline_workbench.py`, `host/tmaze/ui/tracker_view.py`
  (`TrackerTabOptions.on_event`), `host/tests/test_offline_workbench_ui.py`
- **Supersedes / Superseded by:** — (completes FW-0074; builds on ADR-0211..0215)

## Context

ADR-0213..0215 settled the pieces: host the live Tracking tab, express the
window's settings as the command line's `--set` values, and play the video on
one worker thread. The window itself still had to decide:

- how to lay out three groups of controls (the hosted tab, the video with its
  transport, the offline-only controls) without hiding any of them - the
  operator uses a 1920-px-wide monitor and asked for no collapsing panels;
- how the window knows where each input file now comes from. The Files panel,
  the copied command line and a run all need it, but the operator can change a
  file in two places: the window's own Files panel, or the hosted tab (Load
  model, Define region, Register rat, ...);
- what to re-track after a change while paused.

## Decision

- **Three fixed columns.** Left: the hosted tab. Centre: the video, display
  toggles above it, and the transport bar below (scrub bar, a strip of trial
  markers coloured by outcome, play / step / jump / frame box / speed, A-B
  loop, trial jumps, Track vs View-only, warm-up, Reset tracker). Right: tabs
  for Files, Settings, Marks (model building and the earlier-run ghost), and
  Run (tag, range, progress, Cancel, Copy command line). The transport is packed
  before the video so a short window shrinks the picture, not the controls.
- **The tab reports file changes through a callback.** `TrackerTabOptions`
  gains `on_event(kind, action, path)`, called after a load, save, edit,
  register, clear or blocker capture. It is unset on the rig, so the live tab
  is unchanged. The window keeps one `FileChoice` per input, updated from its
  own panel and from these events. A file matching the session's default keeps
  its source (`bundle` / `fallback`); any other file is `override`; drawn and
  unsaved is `edited`; built from frames is `registered`.
- **File swaps run on the player's worker** (`VideoPlayer.submit`), then the
  shown frame is re-tracked, so a swap never races the frame loop.
- **When to re-track while paused.** A settings change (the window compares the
  settings every 250 ms) or a file change re-tracks the warm-up up to the shown
  frame, about 300 ms after the last change so dragging a slider doesn't queue
  dozens of re-tracks. A blocker capture or "finish registration" from the tab
  processes the shown frame once instead: the tracker applies those requests on
  the next frame it sees, and re-tracking the warm-up would feed those frames
  into the registration.
- **Nothing is saved without asking** (ADR-0213). "Save as" opens in
  `host/ratmodels/` for models and in the bundle's `video/` for the rest. A run
  writes `video/<id>_offline_track_<tag>.csv` + `.json` and saves any model built
  here beside them first (`prepare_run`). A run is refused while an ROI,
  landmarks or background is changed and not saved, because the command line
  could not repeat it.
- **Ghosts**: the live track (from the video sidecar's smoothed centroid) as a
  cyan cross, and one earlier offline run (picked by tag) as a magenta circle,
  each with its pixel distance from the current tracking.

## Alternatives considered

- **Poll the tab's state instead of a callback** (e.g. compare
  `active_model_path()` and the ROI every few hundred ms). Rejected: polling
  cannot tell "loaded a file" from "drew one", nor see a registration finish,
  and would need more of the tab's internals.
- **Collapsible or tabbed centre controls.** Rejected at the operator's request
  (wide monitor, everything visible).
- **Re-track the warm-up for every change, including captures.** Rejected for
  captures / finishing a registration (see above).

## Consequences

- The live tab now has one more optional hook; any new file action added to the
  tab should call `_notify` so the workbench's Files panel stays accurate.
- While building this, a live-tab bug surfaced: "Register rat" after "Load
  model" keeps reporting the loaded file, so the session snapshot copies the old
  model. Not fixed here (it changes what the rig snapshots at Start); recorded as
  FW-0080.
- The window's first open of a bundle without a background PNG takes a few
  seconds (median background), as on the command line.

## Evidence / links

- `host/tests/test_offline_workbench_ui.py`: the window follows seeks; a drawn
  ROI shows as unsaved and blocks a run until saved; a setting changed in the
  window shows as layer "you" and in the copied `--set`; a rat model built from
  the A-B marks becomes `--register-frames`; a range run from the window and the
  copied command give identical rows.
- `host/tests/test_tracker_view_options.py::test_on_event_reports_file_changes`.
- Smoke run on `602_20261009_132146`: opens in 0.4 s (70,727 frames, 54 trials,
  zones and live track found), a jump to a trial takes 0.65 s, and the copied
  command after one slider change was just `--set thresh=28`.
