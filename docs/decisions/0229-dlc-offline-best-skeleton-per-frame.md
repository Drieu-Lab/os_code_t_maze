# ADR 0229: `dlc_offline` keeps the best skeleton on each frame, not DeepLabCut's stitched track

- **Status:** Accepted
- **Date:** 2026-10-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/dlc_best.py` (new), `host/tmaze/tools/dlc_offline_worker.py`,
  `host/tmaze/tools/dlc_offline.py`, `host/dlc_offline.json`, `docs/derivatives_format.md`;
  downstream `drieu_lab_analysis` `movement_derivatives` (its upstream is now `dlc_best_iter1`)
- **Supersedes / Superseded by:** Supersedes the "stitched track" and "pickles deleted"
  parts of [ADR-0227](0227-offline-dlc-over-all-sessions.md); changes how
  [ADR-0228](0228-dlc-offline-no-animal-is-done.md) detects an empty video

## Context

Our DeepLabCut model is a multi-animal one. On each video frame it proposes a
few candidate skeletons (DeepLabCut calls them *assemblies*: an x, y and
confidence score per body part). `analyze_videos(auto_track=True, n_tracks=1)`
then links them over time in two steps: a tracker cuts them into short pieces
(*tracklets*), and a *stitcher* chains the pieces into one track per animal.

The first movement results showed the head valid on only 16% of frames of
`600_20261021_163521`, although the live DeepLabCut preview of the same session
saw it on 87%. Across the 10 `dlc_full_iter1` sessions the stitched track kept
17-98% of frames (6 of 10 below 65%). Measured on `600_20260914_130417`, whose
pre-stitching files survived:

- before linking, the head is found (likelihood >= 0.6) on 93% of frames;
- the tracker's 2,739 pieces cover 96% of frames and overlap on 0.2%;
- the stitched track keeps 40%, with no warning.

The stitcher solves a cheapest-path problem in which a track may jump gaps up
to 1.5x the longest gap in the video, so a path through fewer pieces is often
cheaper and the skipped pieces are dropped (our reading of DeepLabCut 3.0.1's
`stitch.py`). When that fails it falls back to a heuristic ("Employing black
magic"), seen on 2 of the 10 sessions. On every frame the best candidate
has all 10 body parts and the others score 0, so with one rat in the maze
there is nothing for the linking to tell apart.

A script written on the office PC (`stitch_video.py`) reruns the same stitching
with a larger recursion limit; it fixes a crash on long videos, not this loss.

## Decision

- The worker runs only DeepLabCut's pose step (`auto_track=False`), which
  writes `<video>DLC..._assemblies.pickle`. The pure `tmaze.core.dlc_best`
  keeps, on each frame, the candidate with the highest total score (sum of its
  body parts' scores; parts DeepLabCut marks missing count 0; first on a tie),
  and writes it in DeepLabCut's standard CSV layout, one row per video frame.
  A frame with no candidate is a blank row. No smoothing, no likelihood
  cut-off: readers apply their own (e.g. `movement_derivatives` uses 0.6).
- New pipeline name `dlc_best_iter1`, so the old `dlc_full_iter1` results stay
  untouched for comparison. The manifest says `params.track: "best_per_frame"`
  and `params.found_frames` (frames with any body part found).
- DeepLabCut's pickles (`_full`, `_meta`, `_assemblies`, ~220 MB per video)
  are kept under `raw/` by default (`keep_raw` in `dlc_offline.json`,
  `--no-raw` to drop), so a change to this rule is a few minutes of CPU, not
  another ~200 GPU-hours.
- No `.h5` copy any more; the CSV is the result.
- No-animal (ADR-0228) is now "no frame has any body part found", not
  DeepLabCut's "Tracklets are empty" error (the tracker no longer runs).
- `--adopt DIR` now builds results from `_assemblies.pickle` files of the same
  snapshot (with the same video-size / frame-count check as before).

## Alternatives considered

- **Tune the stitcher** (`max_gap`, `min_length`, the tracker settings): the
  cost still rewards skipping pieces; each try costs a run, and the result
  would depend on settings nobody here can explain.
- **Concatenate all tracklets**: 96% coverage on the measured session, about
  the same as best-per-frame, but it still depends on the tracker's settings
  and code for no gain with one animal.
- **The office `stitch_video.py`**: it fixes a different problem (the stitcher's
  recursion crash on long videos).
- **Keep the name `dlc_full_iter1` and overwrite**: it would destroy the
  before/after comparison, and readers couldn't tell which rule made a file.

## Consequences

- On the 9 adopted sessions the head is found on 89-97% of frames (was 38-96%),
  every video frame has a row (the table no longer stops early), and the head
  moves more than 40 px between consecutive found frames on at most 0.03% of
  frames (99th percentile 18-28 px), so the best candidate does not hop to
  another object.
- A frame where the network wrongly scores something else highest is kept as
  is. With the current model the second candidate scores 0 on the median
  frame; if a future model or a second animal changes that, this rule must be
  revisited (and `n_tracks` would matter again).
- Each session folder on `Z:` grows by ~220 MB (~45 GB for ~200 sessions).
- The empty clip `600_20260826_132348` (ADR-0228's example) is no longer
  "no animal": without the tracker, 53 of its 623 frames have a candidate,
  46 with a part at likelihood >= 0.6 (tail base up to 0.96). Whether that is
  a brief rat or something else in the maze is unchecked; its movement table
  has the head on 2% of frames, so it is easy to spot and filter.
- `movement_derivatives_v1` (analysis repo) now reads `dlc_best_iter1`.

## Evidence / links

- Coverage comparison, 2026-10-22: stitched vs best for the 9 adopted sessions
  (e.g. `600_20260914_130417` 0.376 -> 0.928, `602_20260914_140500` 0.386 ->
  0.947, `602_20260915_151743` 0.962 -> 0.969); script kept outside the repos at
  `t-maze_recordings/_head_point_eval/compare_best_vs_stitched.py`.
- Movement results after the change: head valid 90-98% on all 9 sessions.
- Tests: `host/tests/test_dlc_best.py`, `host/tests/test_dlc_offline.py`.
