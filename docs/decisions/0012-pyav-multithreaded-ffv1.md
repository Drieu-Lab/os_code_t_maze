# ADR 0012: Encode the camera's FFV1 video with PyAV slice multithreading

- **Status:** Accepted
- **Date:** 2026-06-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/camera_view.py`, `host/requirements.txt`
- **Supersedes / Superseded by:** refines the writer choice in ADR-0011 (which used OpenCV `VideoWriter`)

## Context

ADR-0011 shipped the camera recorder using OpenCV's `VideoWriter` with the FFV1
codec. On the bench this could not keep up: at 1280x1024 color, ~42 fps
(~157 MB/s of raw BGR), the status bar showed the bounded encode queue pinned at
60/60 and `q-drop` climbing while `saved` lagged far behind `frames`. The cause
is that OpenCV's `VideoWriter` runs FFV1 **single-threaded**, so one core cannot
encode full-resolution color at the capture rate. The bounded queue then drops
frames (by design - a controlled, counted drop rather than stalling acquisition
or freezing the GUI), but the recording loses frames.

FFV1 is intra-only and supports splitting each frame into independent **slices**
that encode in parallel, which OpenCV's writer does not expose but FFmpeg
(via PyAV) does.

## Decision

Replace the OpenCV `VideoWriter` with a PyAV-backed `_FFV1Writer` in
`camera_view.py` that enables FFV1 slice multithreading:

- container/stream via `av.open(...)` + `add_stream("ffv1", ...)` with options
  `slices=24`, `slicecrc=1`;
- `thread_type="AUTO"` and `thread_count=0` (all cores);
- `pix_fmt="bgr0"` (BGR + ignored padding byte), bit-exact lossless for the 8-bit
  BGR frames. Note: FFmpeg's FFV1 does **not** offer plain 8-bit `gbrp` (only
  `gbrp9/10/12...`), so `bgr0` is the correct 8-bit lossless RGB format; the
  constant pad plane compresses away. (`yuv420p`/`yuv444p` were rejected as not
  bit-exact for RGB input.)

Encode errors are surfaced to the status bar + stderr and stop the recording,
rather than being swallowed - an early `bgr0`-vs-`gbrp` bug otherwise produced an
empty video plus a header-only sidecar with no visible error.

PyAV (`av`) is an additional dependency, **required only for recording** - live
preview still works without it, and the record button is disabled (with a note)
when `av` is missing. Output stays lossless FFV1 in `.mkv`, full color.

## Alternatives considered

- **Keep OpenCV, just enlarge the queue** - only delays the drops; the encoder is
  fundamentally slower than capture, so a larger queue fills anyway (and costs
  RAM). Rejected as a real fix (a modest queue bump was kept only to absorb
  encoder spin-up / transient stalls).
- **Record raw Bayer mono instead of color** - ~3x less data and removes the
  debayer from the hot path; a valid future option, but it changes the saved
  format (offline debayer needed). Kept in reserve; not needed once encoding is
  multithreaded.
- **Lower frame rate / crop ROI** - works but sacrifices data the experiment
  wants. Rejected as the default.
- **GPU (NVENC) lossless** - fast, but H.264/HEVC "lossless" is not bit-exact
  FFV1 and adds a hardware/codec dependency. Rejected for a scientific record.

## Consequences

- Recording keeps up with full-res color at the rig's frame rate on a multi-core
  machine; `q-drop` should stay at 0 in steady state.
- New dependency `av`; preview degrades gracefully without it.
- If a future camera/run still saturates all cores, fall back to raw-Bayer
  recording (already scoped) or reduce fps/ROI.

## Evidence / links

- Bench symptom: status bar `frames: 4196 saved: 453 q-drop: 245 queue: 60/60`
  at 1280x1024 / ~42 fps color (2026-06-24).
- FFV1 slice multithreading: FFmpeg FFV1 encoder `slices`/`slicecrc` options.
- Related: ADR-0011 (camera module + free-run + FFV1 decision).
