# ADR 0015: Apply lens-distortion correction at the camera source

- **Status:** Accepted
- **Date:** 2026-06-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/camera_view.py`, `host/calibration.py` (shared loader + `Undistorter`, also used by `blob_tracker_prototype.py`); `os_code_t_maze_resources/rig-hardware/camera/` (`export_calibration_to_json.m`, `camera_calibration.json`); relates to ADR-0011/0013
- **Supersedes / Superseded by:** —

## Context

The overhead Basler acA1300-60gc lens introduces radial/tangential distortion:
straight maze edges bow near the frame border. Markerless position tracking
(planned) measures positions, paths, and speeds in pixels, so uncorrected
distortion biases those metrics, especially toward the edges where the rat runs
between the start box and reward ports.

The camera was calibrated offline with MATLAB's `cameraCalibrator` (checkerboard
images + result `.mat` live in the resources repo under `rig-hardware/camera/`).
MATLAB and OpenCV use different conventions, so the parameters cannot be copied
verbatim.

The rig records lossless FFV1 specifically to preserve image data
(ADR-0011/0013). Undistortion resamples pixels, so applying it to the recording
is in tension with that "bit-exact to the sensor" goal — a deliberate choice was
required about *where* in the pipeline to correct.

## Decision

Apply undistortion **at the camera source**, in the grab thread, in the order
**debayer -> undistort -> orient**, so the corrected frame is what the preview,
the (future) tracking pipeline, and the recording all see. The undistorted frame
keeps the same width/height (via `getOptimalNewCameraMatrix` with `alpha=0`, which
scales out the invalid border at the cost of a small crop), so recording size and
downstream coordinates are unaffected.

Calibration is **load-only**: `camera_view.py` reads an OpenCV-convention JSON
(`CALIBRATION_PATH`, defaulting via `__file__` to the sibling resources repo at
`rig-hardware/camera/camera_calibration.json`), auto-loaded on startup, with a
"Load calibration..." button and an "Undistort" enable toggle. Maps are built
once per frame size with `cv2.initUndistortRectifyMap` and applied with
`cv2.remap`. If the calibration `image_size` does not match the live frame
(AOI/.pfs differences) the app warns once and skips correction rather than apply
a wrong map.

The MATLAB result is converted by `rig-hardware/camera/export_calibration_to_json.m`,
which handles the convention differences:

- transpose `IntrinsicMatrix` -> OpenCV `K` (or use newer `Intrinsics.K`);
- shift the principal point for 1-based -> 0-based indexing (`cx-1`, `cy-1`);
- reorder distortion to `[k1, k2, p1, p2, k3]` (MATLAB splits radial/tangential);
- swap `ImageSize [h w]` -> `image_size [w, h]`.

Example `camera_calibration.json`:

```json
{
  "source": "matlab",
  "image_size": [1280, 1024],
  "camera_matrix": [[fx, 0, cx], [0, fy, cy], [0, 0, 1]],
  "distortion": [k1, k2, p1, p2, k3],
  "rms_reprojection_error": 0.21,
  "date": "2026-06-29"
}
```

The recording sidecar header records provenance (`undistorted`, `calibration`,
`calib_rms`) so a saved video is self-describing.

## Alternatives considered

- **Record raw; undistort only preview + tracking.** Preserves bit-exact sensor
  data and lets the video be re-undistorted later, but tracking coordinates would
  live in a different (corrected) space than the saved (distorted) video,
  complicating overlay/analysis alignment. Rejected for simplicity: with
  correction at the source, the video and all coordinates share one geometry.
- **Undistort in analysis only (keep the whole live pipeline raw).** Cleanest for
  data preservation, but the live preview/tracking would still be distorted, and
  every downstream consumer would have to re-implement the correction. Rejected.
- **In-app checkerboard calibration helper.** Unnecessary: calibration is a
  one-time offline step and was already done in MATLAB; the app only needs to
  load the result.

## Consequences

- The recorded video is **no longer bit-exact to the sensor** — it is resampled
  (lens-corrected) before encoding. FFV1 is still a lossless codec (ADR-0011/0013
  hold), but its *input* frames are undistorted. Raw sensor pixels are not
  recoverable from the recording; the calibration is documented in the header and
  versioned in the resources repo so the transform is reproducible.
- Per-frame `cv2.remap` adds a small cost in the grab thread (maps precomputed
  once). It has been negligible relative to debayer + encode; if it ever competes
  at full frame rate, the toggle disables it, or tracking could be throttled.
- The "Undistort" toggle and size-mismatch guard make it safe to run before a
  matching calibration exists (it simply passes frames through).

## Evidence / links

- `host/calibration.py` (`load_calibration`, `Undistorter` - the shared map
  build + remap, reused by the tracker prototype so tuning matches this pipeline).
- `host/camera_view.py` (`_maybe_undistort`, `_load_calibration`,
  `CALIBRATION_PATH`, `UNDISTORT_ALPHA`; delegates to `calibration.Undistorter`).
- `os_code_t_maze_resources/rig-hardware/camera/` (`README.md`,
  `export_calibration_to_json.m`, `calibration_images/`).
- Relates to ADR-0011 (free-run FFV1) and ADR-0013 (selectable lossless/lossy).
