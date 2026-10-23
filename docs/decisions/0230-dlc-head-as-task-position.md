# ADR 0230: Let DeepLabCut-Live's head centre drive the task, with the CoM as fallback

- **Status:** Accepted
- **Date:** 2026-10-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/task_position.py` (new), `host/tmaze/core/maze_state.py`
  (`_extract`), `host/tmaze/core/rules_engine.py` (`_make_frame`),
  `host/tmaze/vision/tracker.py` (`TrackResult.task_point`, `task_position()`),
  `host/tmaze/ui/camera_view.py` (track worker, sidecar, camera stats),
  `host/tmaze/io/dlc_head_client.py` (`newest`, two settings), `host/dlc_head.json`,
  `host/tmaze/ui/tracker_view.py` (the switch), `host/tmaze/ui/phase3_controller.py`
  + `host/tmaze/core/phase3/phase3_log.py` (trial-CSV header),
  `host/tmaze/core/phase3/bundle_format.py` (format 7); in `drieu_lab_analysis`:
  `phase3_format.FORMAT_VERSION`
- **Supersedes / Superseded by:** — (extends ADR-0224 / ADR-0225; implements FW-0085)

## Context

The task's position has always been the tracker's centre of mass (CoM), a point
on the body. The lab wants the CHOICE cue, and the rest of the task, to follow
the rat's head. ADR-0224 runs DeepLabCut-Live (DLC) in a helper process as a
preview. It answers ~99% of frames at 25 fps, each ~20 ms (about one camera
frame) after the frame it was given, and it sometimes has no trusted head point
(`low`, `skipped`, `no_blob`), or the helper can fail outright.

Every part of the task reads the position from one place: the camera's track
worker publishes each tracker result to the shared maze-state pipeline
(ADR-0041), whose two estimators (debounced, and fast for actuation; ADR-0042)
feed the cue trigger, the behaviour stats, the Maze state display and the state
log. The rules engine reads the same result.

## Decision

**A switch on the Tracking tab**, "Use head for task (CoM fallback)", next to
"Track head (DLC-Live)". It can only be turned on when the DLC helper is
`ready`, and it is locked while recording, so one recording runs under one mode.
Off (the default) changes nothing.

**One choice per frame, on the track worker.** After `tracker.process`, the
pure `task_position.TaskPositionSelector` picks the frame's point and the worker
attaches it to the result as `task_point` before publishing, so every consumer
and the video sidecar see the same choice:

- `dlc`: the newest DLC answer, if its `head_center` is trusted (likelihood >=
  pcutoff 0.6) and its `cam_block_id` is 0 to `task_max_lag_frames` (2, ~80 ms)
  frames behind this frame. The task does not wait for DLC; it uses the newest
  answer, which is usually one frame old.
- `dlc_hold`: no fresh trusted head point; the last one is kept for up to
  `task_hold_s` (0.15 s). This stops a single `low` or `skipped` frame from
  swapping the position between head and body, which would make zones flicker.
- `com_fallback`: after that, the CoM. This is also what happens when the helper
  dies; the tab warns the operator, and mid-recording the switch stays on so
  the rest of the file says `com_fallback`, not `com`.
- `com`: the switch is off.

Both settings live in `host/dlc_head.json`.

**Scope: everything.** `MazeStateEstimator._extract` and
`RulesEngine._make_frame` use the DLC point for `dlc` / `dlc_hold`. They read
the tracker as before for `com` / `com_fallback`, so the off path cannot drift.
A DLC point counts as tracked even while the blob tracker is coasting or lost:
DLC has already judged it with its own likelihood cutoff, and the crop then
comes from the last CoM or the ROI box (ADR-0225). Its likelihood is reported as
the frame's confidence, so the estimator's `min_confidence` (0.5) still applies
and sits below pcutoff.

**Logging.**

- The video sidecar gains trailing `task_src`, `task_x`, `task_y` columns (the
  point the task used, full-frame px) and `task_position*` header lines (mode,
  body part, max lag, hold time, pcutoff).
- The trial CSV gains `# position_source=com|dlc_head`. It is taken at arm and
  again at Start, since the switch only locks once recording starts.
- `<session>_camera_stats.json` gains `task_position`: the mode and the number
  of frames per source for that recording.
- The bundle `format_version` goes 6 -> 7 in both repos, and `format.json`
  documents the new columns as `sidecar_task_columns`.

## Alternatives considered

- **Wait for DLC's answer for each frame.** Rejected: it adds ~20 ms to every
  update and stalls the task whenever the GPU stalls (ADR-0224 saw 100+ ms
  spikes). Using the newest answer costs about one frame of lag instead.
- **Fall back to the CoM on every untrusted frame.** Rejected: the head and the
  CoM are several cm apart, so the position would jump between them and zone
  edges would flicker. The 0.15 s hold covers most short dropouts.
- **Only fall back when the helper dies; otherwise hold the head point.**
  Rejected: a long run of `low` frames (rat grooming, head out of view) would
  freeze the position.
- **Only the fast (actuation) estimator follows the head.** Rejected: the
  display, the behaviour stats and the state log would then describe a
  different point from the one that fired the cue.
- **Require the blob tracker to be tracking too.** Rejected: the lab chose to
  trust DLC on its own, since it already has a likelihood cutoff.
- **Log the source in a separate file, or in the maze-state log.** Rejected:
  the sidecar already has one row per frame with the track result, so it joins
  naturally. The cost is a format bump.

## Consequences

- The point that fires the cue is ~1 frame (~20-40 ms) older with the switch on
  than the CoM is. The half-size model (12 ms per crop, ADR-0224) is the option
  if that matters.
- Old bundles and recordings with the switch off read as before; analysis code
  that replays the cue rule from the CoM (`cue_latency`) is wrong for
  `dlc_head` sessions until FW-0089.
- A new setting, `position_source`, belongs to every analysis of a session's
  timing: sessions in the two modes should not be pooled without checking it.
- It needs a bench test before a real session: with the switch on, zone entries
  should follow the magenta head dot; killing the helper should turn the source
  to `com_fallback` while the task carries on.
- The MATLAB copy of the progress notebook (`drieu_lab_analysis` ADR-0165) has
  its own `format_version` guard to update by hand.

## Evidence / links

- Tests: `host/tests/test_task_position.py` (selector, sidecar columns/header,
  manifest, and the estimator / rules engine with each source); the full host
  suite (1855 tests) passes.
- Golden fixture re-issued: `format.json` (`format_version` 7,
  `sidecar_task_columns`) and the trial CSV's `# position_source=com` line;
  copied to `drieu_lab_analysis/tests/fixtures/`.
- DLC timing: ADR-0224's replay (helper 20.7 ms median, 1121/1199 frames `ok`).
