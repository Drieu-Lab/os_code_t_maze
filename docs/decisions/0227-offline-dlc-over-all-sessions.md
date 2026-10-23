# ADR 0227: Offline DeepLabCut over all sessions (`dlc_offline`)

- **Status:** Accepted
- **Date:** 2026-10-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/tools/dlc_offline.py`, `host/tmaze/tools/dlc_offline_worker.py`,
  `host/dlc_offline.json`, `host/tmaze/tools/head_point_dataset.py` (`prediction_csvs`)
- **Supersedes / Superseded by:** — (uses [ADR-0226](0226-derived-data-folder.md)); the
  stitched track and the deleted pickles are superseded by [ADR-0229](0229-dlc-offline-best-skeleton-per-frame.md)

## Context

The iteration-1 full DeepLabCut model (`snapshot-best-140`, ADR-0224) tracks
the head much better than the rig's markerless tracker, and its offline output
is what the small head network is retrained on and what the movement measures
will be built from. Until now it was run on 9 videos by a scratch script. There
are ~200 session videos (1.75 TB, most on `Z:`), each taking ~1 h on the rig
PC's GPU, and ~3 new ones a day. The same PC runs the rig, so a long job
must never slow or block a session.

## Decision

`python -m tmaze.tools.dlc_offline` runs the full model over every session
video that has no up-to-date result, newest first, into the `dlc_full_iter1`
pipeline folder (ADR-0226).

- **Same call as the scratch run**: `analyze_videos(shuffle=1,
  snapshot_index="best", device="cuda", auto_track=True, n_tracks=1)` (one rat
  per video, tracks stitched into one). Settings in `host/dlc_offline.json`.
- **Files**: `<sid>_dlc.csv` in DeepLabCut's standard CSV layout, plus
  `<sid>_dlc.h5`. The first column is the video frame index, which equals the
  sidecar's `frame_idx` (checked: DLC rows = sidecar rows on all 7 adopted
  sessions). DeepLabCut's pickles (~250 MB per video) are deleted unless
  `--keep-raw`.
- **Two processes**: the command (rig env) chooses sessions and keeps the
  manifests; each video runs in a worker process in the `DEEPLABCUT` env (the
  only env with DeepLabCut + the GPU torch). The command never imports
  DeepLabCut, and it can stop the worker at any moment.
- **The rig comes first**: it won't start while the rig GUI
  (`tmaze.ui.experiment_control`) is running, checks every `rig_check_s`
  (30 s) during a video, and stops the worker if the GUI opens. The stopped
  session is left partial and redone next run. The worker runs at below-normal
  priority, pinned off logical processors 10 and 11 (the rig's).
- **Stop on request**: `--stop` (or the Records Manager's Stop button) drops a
  `_stop` file the job checks on the same 30 s cycle.
- **Model version guard**: the pipeline name must end in `_iter<N>` matching
  the DLC project's `iteration`. Retraining (iteration 2) makes the command
  refuse until `dlc_offline.json` names `dlc_full_iter2`, so results from two
  models never share a folder. The manifest records the snapshot file and its
  sha256.
- **Adopt**: `--adopt DIR` takes over stitched `_sk.h5` files made earlier by the
  same snapshot (the 9 scratch-run videos) instead of spending 9 GPU-hours
  again. It is accepted only if the DLC project's copy of the video has the
  bundle video's size, or (no copy) the frame count equals the sidecar's.
  Marked `"adopted": true`.

## Alternatives considered

- **Run DeepLabCut in the rig env**: DeepLabCut's dependencies would have to be
  installed into `drieu_t_maze`, the env the rig runs in; not worth the risk.
- **Analyse many videos in one DeepLabCut call**: faster start-up (~20 s per
  video saved), but a stop would lose the whole batch's progress and the parent
  couldn't tell which video failed.
- **A scheduled task overnight**: the operator would have less control and it
  couldn't see whether the rig is about to be used; the Records Manager button
  plus the rig guard covers the need. It can still be scheduled later.
- **Pause the worker (suspend) instead of killing it when the rig opens**: the
  GPU memory it holds would stay allocated during the session.
- **Write the CSV with `cam_block_id`**: the DLC-standard layout keeps
  `head_point_dataset` and DeepLabCut's own tools working unchanged; the join
  to the sidecar is by frame index.

## Consequences

- A full pass is ~200 GPU-hours; at a few hours a night it takes weeks, newest
  sessions first, so the data being analysed now is done first.
- A video stopped by the rig opening loses up to that video's work (~1 h).
- `head_point_dataset --predictions Z:\Ravi\derivatives\dlc_full_iter1` reads
  the finished sessions directly (only those with a manifest).
- The `DEEPLABCUT` env path is per-machine, in `host/dlc_offline.json`.

## Evidence / links

- Scratch run this replaces: `t-maze_recordings/_head_point_eval/dlc_analyze_iter1.py`.
- Adoption on 2026-10-21: 7 sessions, ~30 s each (loading DeepLabCut), DLC rows
  = sidecar rows on all 7 (e.g. `602_20260915_151743`: 89,938 / 89,938).
- Tests: `host/tests/test_dlc_offline.py` (version guard, snapshot choice,
  session choice, rig detection, adopt rule, a full run with a fake worker,
  stop leaves the session partial, `head_point_dataset` reads only finished
  sessions).
