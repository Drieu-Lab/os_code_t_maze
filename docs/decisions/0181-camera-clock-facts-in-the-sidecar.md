# ADR 0181: Stamp the camera clock honestly and record its facts in the sidecar

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/camera_view.py`, `host/tmaze/core/camera_health.py` (`fps_measured`), `host/tests/test_camera_health.py`
- **Supersedes / Superseded by:** — (builds on ADR-0124 one-anchor clocks, ADR-0147 camera health/stats)

## Context

Three camera-clock facts a future reader needs were wrong or missing (review
findings S4, S5, S6, L9):

- **S4 — `host_unix_s` was stamped too late.** The sidecar's per-frame `host_unix_s`
  is the reliable cross-stream timeline (ADR-0124), and the column's comment says it
  is taken "at grab". But the value was `time.time()` captured at ENQUEUE — after
  debayer + `Convert` + undistort + orient + the tracker handoff — so it carried the
  variable per-frame processing latency as if it were the frame's real time.
- **S5 — the video's own timestamps are not real time.** The mp4 container stamps
  frames at the NOMINAL rate (`pts += 1` at `round(fps)`), so a reader can't recover
  the true effective frame rate from the video; nothing in the bundle stated the
  measured rate.
- **S6 / L9 — the sidecar header lacked two clock facts.** The raw `cam_timestamp`
  ticks can't be converted to seconds without the camera clock's tick rate, and the
  header carried `session_t0_iso`/`_wall` but not the monotonic partner
  `session_t0_mono`, so the sidecar didn't carry the same three-part session zero as
  the trial CSV and manifest.

## Decision

- **S4**: capture `grab_wall = time.time()` in `_grab_loop` the instant the buffer is
  retrieved and confirmed complete — BEFORE any conversion/undistort/orient — and use
  it as the frame's `host_unix_s`. The "at grab" comment is now true.
- **S6/L9**: add three header lines to every recording's sidecar:
  `session_t0_mono` (from the manifest anchor, blank for a manual Record-button run),
  `cam_timestamp_tick_hz`, and `tick_hz_source`. A new `_read_tick_hz(camera)` reads
  `GevTimestampTickFrequency` at open (source `"node"`), falling back to the GigE
  nominal `1_000_000_000` Hz (`CAM_TICK_HZ_DEFAULT`, source `"default"`). An offline
  fit (`tools/led_sync_prototype.py`, which measures the tick rate empirically) can
  still supersede it.
- **S5**: `<id>_camera_stats.json` gains `fps_nominal` (the configured rate) and
  `fps_measured` — the mean rate over the saved span, computed by a new pure helper
  `camera_health.fps_measured(saved, first_host_unix_s, last_host_unix_s)` =
  `(N-1) intervals / (last - first grab time)`, or `null` when < 2 frames were saved.
  The encode loop tracks the first/last grab stamp of the saved frames (reset per
  recording).

No `format_version` bump: the three header additions are `#`-comment key/value lines
(readers parse the header into a tolerant dict) and `fps_*` are new keys in the
best-effort stats JSON — nothing changes the sidecar COLUMN set. The prose notes that
tell a reader WHY to prefer the sidecar over the container (S5/S6) and the coordinated
FORMAT_VERSION bump live in Increment 3e.

## Alternatives considered

- **Also add a `host_unix_s_written` sidecar column** (grab-vs-write latency). — A new
  per-frame COLUMN changes the sidecar contract, which is coordinated with the analysis
  mirror in the 3e format bump; the plan marks it optional. Deferred to avoid a column
  change outside 3e.
- **Use `saved / span` for `fps_measured`.** — N frames define N-1 intervals; `(N-1)/span`
  is the honest mean rate (the difference is immaterial at session length but correct for
  short clips). Chosen over the plan's shorthand.
- **Measure the tick rate on the host and write that.** — The empirical fit belongs to
  the offline `led_sync_prototype` (Theil–Sen over `cam_timestamp` vs `host_unix_s`); the
  sidecar should record the camera's DECLARED rate (node or nominal) with its source, and
  let the fit supersede it. Hence `tick_hz_source` rather than a single unlabelled number.
- **Read the tick frequency on the encode thread at header-write time.** — The camera is
  owned by the grab thread; reading nodes there (like `_read_fps`) at open is the safe
  place. Cached onto the view and copied into the header.

## Consequences

`host_unix_s` now reflects the true grab instant, so cross-stream alignment (video ↔
serial ↔ ephys) no longer folds in processing jitter. A bundle is self-describing enough
to convert `cam_timestamp` ticks to seconds, to carry the full three-part anchor, and to
answer "what frame rate did this session actually run at" straight from the stats JSON.
Costs: one extra `time.time()` per grabbed frame (negligible), a one-time node read at
open, and two floats tracked per recording. A healthy recording's video + sidecar COLUMNS
are unchanged; only header lines and stats keys are added.

## Evidence / links

- Review findings S4/S5/S6/L9 and Increment 3c in
  `sensor_polarity_hand_check_5b3798c1.plan.md`.
- Clocks contract: ADR-0124 (one canonical anchor). Camera health/stats: ADR-0147.
- Offline tick-rate fit: `host/tmaze/tools/led_sync_prototype.py` (`cam_ts_tick_hz`).
- Tests: `host/tests/test_camera_health.py::TestFpsMeasured`.
