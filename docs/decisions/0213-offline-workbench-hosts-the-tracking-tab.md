# ADR 0213: The offline workbench hosts the live Tracking tab

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/tracker_view.py` (`TrackerTabOptions`,
  `active_blocker_model_path`, `set_active_paths`, `refresh_from_tracker`),
  `host/tmaze/vision/tracker.py` (`draw_overlay` toggles),
  `host/tests/test_tracker_view_options.py`; later the workbench window (FW-0074)
- **Supersedes / Superseded by:** — (builds on ADR-0211, ADR-0212)

## Context

The offline workbench (FW-0074) must offer every control the live Tracking tab
offers, now and as the tab grows. The tab (`TrackerControlView`) only needs a
camera-like object (`get_latest_frame`, `tracking_enabled`, `enable_tracking`,
`get_output_dir`), so a video player can feed it. But it was written for the rig:

- At start it loads `host/roi_polygon.json` and `host/landmarks.json` (the rig's
  current files), not the session's own copies.
- Save ROI and Define landmarks write those files without asking; Clear
  landmarks deletes `host/landmarks.json`; Define LED board region writes
  `host/led_board_search.json`.
- Enable tracking, Grab background (on a recording, the rat is in every frame)
  and the standalone track log make no sense offline.
- Its "Enrich while tracking" box always started unticked and pushed
  `adapt_enrich=False`, which would undo a session snapshot that had it on.

## Decision

Host the real tab in the workbench, with a frozen `TrackerTabOptions`:

- `load_host_files` (skip the start-up loads), `ask_before_saving` (Save ROI and
  a new "Save landmarks..." go through Save-as dialogs; defining landmarks keeps
  them in memory; Clear never deletes a file), `hide` (control groups:
  `enable`, `grab_bg`, `led_board`, `track_log`), `show_preview` (the workbench
  draws its own larger view), `file_dir` / `model_dir` (where dialogs open).
- **The defaults are exactly the live behaviour**, so `experiment_control` is
  unchanged. Tests check both sides with the host files redirected to a temp
  folder.
- The enrich box now starts from the tracker's own setting. Live, that is the
  `TrackerParams` default (`False`), so nothing changes there.
- The tab now also remembers the blocker model's path
  (`active_blocker_model_path`, also needed by FW-0079), a host can tell it which
  files it loaded (`set_active_paths`), and can ask it to re-read its sliders
  after changing settings directly (`refresh_from_tracker`).
- `draw_overlay` gains `draw_roi` / `draw_trail` / `draw_candidates` switches for
  the workbench's display toggles; defaults draw everything.

The workbench puts its offline-only controls (files, every setting, marked-frame
registration, ghosts, runs) in its own panel beside the tab.

## Alternatives considered

- **A new workbench panel built from widgets pulled out of the tab.** Rejected:
  a larger restructuring of rig code, and each new live control would need adding
  twice.
- **Copy the tab's controls into the workbench.** Rejected: the two would drift.
- **Host the tab unchanged.** Rejected: it would load and overwrite the rig's
  live `host/` files from an offline window.

## Consequences

- One tab serves both windows; a control added to the live tab appears in the
  workbench too. It must stay free of assumptions that only hold live, or put
  them behind an option.
- The live tab's behaviour is unchanged, but this is code the rig runs: the
  option defaults are the contract, guarded by `test_tracker_view_options`.

## Evidence / links

- `host/tests/test_tracker_view_options.py`.
- FW-0074 (workbench), FW-0079 (snapshot the blocker model), ADR-0211, ADR-0212,
  ADR-0018 (landmarks), ADR-0195 (LED board region).
