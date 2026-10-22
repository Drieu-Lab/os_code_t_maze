# ADR 0225: DLC-Live crop fallback - hold the last CoM, then crop the ROI

- **Status:** Accepted
- **Date:** 2026-10-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/dlc_head.py` (`roi_box`, `CropPlanner`, trial-file
  columns), `host/tmaze/io/dlc_head_client.py` (`submit`, config), `host/tmaze/ui/camera_view.py`
  (passes the ROI), `host/dlc_head.json` (`hold_s`, `roi_fallback`)
- **Supersedes / Superseded by:** — (extends [ADR-0224](0224-dlc-live-head-preview.md))

## Context

The DeepLabCut-Live head preview (ADR-0224) crops a 448 px square around the
tracker's centre of mass (CoM). When the tracker has no CoM on a frame it sends
nothing and records `no_blob`. In the first sessions with the preview on
(2026-10-21) that was 4-10% of frames. Most of those gaps were short: in
602's session, 325 of 349 gaps lasted under 1 s. But in that session the
appearance model had not been loaded, so the tracker on default settings lost
the rat for longer stretches. With the CoM gone, DLC-Live, which could have
found the rat on its own, was idle too.

Timing on the rig GPU (full network, 2 CPU threads, paced at 25 fps):
a 448 x 448 crop ~12 ms, the rig ROI's bounding box (~736 x 950 px) ~43 ms,
the whole 1280 x 1024 frame ~80 ms. A camera frame arrives every 40 ms.

## Decision

When the tracker has no CoM, the rig chooses the crop in this order
(`dlc_head.CropPlanner`, pure, the time passed in):

1. **`com`**: the tracker's CoM on this frame, as before.
2. **`hold`**: the same 448 px square around the last good CoM while it is
   at most `hold_s` old (default **1.0 s**, set in `host/dlc_head.json`).
   It costs nothing extra and covers most dropouts.
3. **`roi`**: the bounding box of the tracker's ROI polygon (`roi_box`, clipped
   to the frame), when `roi_fallback` is on (default **on**) and a ROI is set.
   It takes ~42 ms per crop, so the helper answers about every other frame. The
   one-crop-in-flight rule marks the rest `skipped`, as it does for any busy
   frame. The first ROI-sized crop takes ~140 ms (the GPU sets up for the new
   size), well inside the 2 s "helper has hung" limit.
4. Otherwise `no_blob`, as before.

The task is unchanged: the CoM still drives the rules, and a held or ROI crop
only feeds the preview overlay and the trial file.

The trial file `<session>_dlc_head.csv` gains three columns after `crop_y0`:
`crop_w`, `crop_h` and `crop_src` (`com` / `hold` / `roi`). It also gains the header
lines `crop_src`, `hold_s` and `roi_fallback`. It is still a separate trial file, not
part of the bundle format, so there is no `format_version` bump. The three files
written on 2026-10-21 have the old layout; nothing reads them yet.

## Alternatives considered

- **ROI crop on every frame instead of the CoM square**: rejected in ADR-0224
  (~43 ms > the 40 ms frame interval, so half the frames would be skipped all
  the time).
- **Hold only, no ROI**: it covers the common short dropouts but leaves
  DLC-Live idle exactly when it could help most, when the tracker has lost
  the rat for seconds.
- **ROI only, no hold**: it would halve DLC's answer rate during every
  sub-second dropout when a 448 crop at the last CoM almost always still
  contains the rat.
- **Centre the next crop on DLC-Live's own last answer**: self-tracking would
  return to the full rate sooner after a ROI crop finds the rat. It is not
  needed yet and couples the crop to the network's mistakes. Revisit with
  FW-0085.
- **The whole frame as the fallback**: 80 ms, about one answer in three frames,
  and the ROI already covers everywhere the rat can be.

## Consequences

- While the rat is out of the maze (before it is placed, after it is removed)
  the helper runs ROI crops continuously, using the GPU more than before.
  During today's sessions the 448 crops already ran without measurable effect
  on the camera. If that ever matters, set `roi_fallback: false`.
- `hold` crops can show a stale head position for up to `hold_s`. The
  `crop_src` column lets analysis drop them.
- Adds 0.16 ms (median) to the track worker per frame (measured on 750 frames).

## Evidence / links

- Crop timing: `t-maze_recordings/_head_point_eval/roi_crop_timing.py`
  (2026-10-21): 448: 11.9 ms, ROI box 755 x 963: 42.8 ms, full frame: 79.7 ms
  (median, paced 25 fps).
- Real helper + real frames: `roi_fallback_replay.py`, 750 frames of
  `602_20261021_142829` from frame 20200 (the tracker had lost the rat), every
  frame forced to the ROI fallback: 374 answered, 278 `ok`, 96 `low`, 376
  `skipped`; first ROI crop 138 ms, then median 42.3 ms, max 46.4 ms.
- Dropout lengths from the 2026-10-21 `_dlc_head.csv` files: 600 (21 gaps, 18
  under 1 s), 601 (64 gaps, 59 under 1 s), 602 (349 gaps, 325 under 1 s).
- Tests: `host/tests/test_dlc_head.py` (`TestRoiBox`, `TestCropPlanner`),
  `host/tests/test_dlc_head_client.py` (`test_lost_rat_holds_last_com_then_uses_roi`).
