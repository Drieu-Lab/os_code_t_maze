# ADR 0013: Let the GUI pick lossless FFV1 or compressed H.264 per recording

- **Status:** Accepted
- **Date:** 2026-06-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/camera_view.py`
- **Supersedes / Superseded by:** builds on ADR-0011 / ADR-0012 (does not supersede)

## Context

ADR-0011/0012 record the camera as bit-exact lossless FFV1. On the bench this
produces a ~350 MB file for a 5 s clip at 1280x1024 / ~42 fps color, i.e. a
sustained ~70 MB/s (~250 GB/hour). That is correct and expected for lossless
(FFV1 gives ~2-3x over the ~3.9 MB/frame raw BGR), but it is heavy on disk for
long behavioral sessions.

For most T-maze use the video is scored for position/behavior, not analyzed
pixel-exactly, so visually-lossless **H.264** (libx264, CRF ~18) is the lab
standard and is ~25-100x smaller. But some uses (or a cautious default) still
want true lossless. The user asked to choose between the two **in the GUI**
rather than committing to one at build time.

## Decision

Generalize the PyAV writer (`_FFV1Writer` -> `_VideoWriter`) to support two
codecs, selected per recording from a **Format** dropdown in the camera
controls:

- **Lossless (FFV1)** -> `add_stream("ffv1", slices=24, slicecrc=1)`,
  `pix_fmt="bgr0"`, container `.mkv` (unchanged from ADR-0012).
- **Compressed (H.264)** -> `add_stream("libx264", crf=<GUI>, preset="superfast")`,
  `pix_fmt="yuv420p"`, container `.mp4`. A **quality (CRF)** spinbox (0-51,
  default 18; lower = better/larger) appears only when this codec is selected.

The chosen codec/CRF are captured in `_RecParams` and written into the sidecar
CSV header (`codec`, `crf`) for provenance. The Format/CRF controls are locked
while a recording is in progress (no mid-clip format change) and re-enabled on
stop or on a failed start. `libx264` is verified present in the bundled PyAV
FFmpeg.

`preset="superfast"` was chosen over `veryfast`/`ultrafast` for safe encode
headroom: on realistic (compressible) frames all presets exceed 200 fps encode
at full res (>> the 42 fps capture rate), so `superfast` keeps a wide margin
even under high-motion/noise without meaningfully growing files.

## Alternatives considered

- **Single fixed format (lossless only)** — simplest, but forces ~250 GB/hour on
  every session; rejected because most runs don't need bit-exact frames.
- **Single fixed format (H.264 only)** — small files, but throws away the
  lossless option some analyses may want; rejected — make it the user's call.
- **Lossless H.264/HEVC (CRF 0) as the "lossless" choice** — not bit-exact like
  FFV1 and still large; rejected — keep FFV1 as the true-lossless path.
- **Raw Bayer mono lossless** (~3x smaller than lossless color) — still scoped as
  a future option (ADR-0012); orthogonal to this color lossless-vs-lossy toggle.

## Consequences

- Per-recording choice: lossless `.mkv` (bit-exact, ~250 GB/hr) or compressed
  `.mp4` (lossy, ~2-10 GB/hr at CRF 18) without a code change.
- File extension/container now depends on the selected codec; downstream tooling
  must handle both `.mkv` and `.mp4` (the sidecar `.csv` is unchanged).
- H.264 needs an x264-enabled FFmpeg; the standard pip/conda-forge `av` builds
  include it. If a build lacks it, recording in H.264 fails (surfaced via the
  ADR-0012 error path); lossless FFV1 still works.

## Evidence / links

- Bench: 5 s lossless clip ≈ 350 MB (~70 MB/s) at 1280x1024 / ~42 fps (2026-06-24).
- Preset bench (realistic frames, full res): ultrafast/superfast/veryfast all
  ~205-235 fps encode; libx264 confirmed available in bundled FFmpeg.
- Related: ADR-0011 (camera module), ADR-0012 (PyAV multithreaded FFV1).
