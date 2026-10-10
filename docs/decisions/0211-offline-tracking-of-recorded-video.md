# ADR 0211: Re-track recorded session videos offline, into a separate file

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/offline_track.py`, `host/tmaze/vision/offline_tracker.py`,
  `host/tmaze/tools/offline_track.py`, `host/tests/test_offline_track.py`
- **Supersedes / Superseded by:** —

## Context

The rat tracker (`tmaze.vision.tracker.RatTracker`) only ran live, on the camera
grab path, so its output for a session was fixed at recording time. To test other
settings or models on a recorded session, to find where tracking is weak and
whether video quality is the cause, and to check tracker changes, we need to run
the same tracker again over a recorded video. The only video-file path so far was
the tuning window `blob_tracker_prototype`, which writes no session-ready output.

Facts that shaped the design:

- Each bundle already snapshots what the live tracker used: `config/model_<name>.npz`,
  `config/roi_polygon.json`, `config/landmarks.json`, the live `TrackerParams` in
  `config/runtime_params.json["tracker"]`, and `video/<id>_background.png` (ADR-0020).
  It does NOT snapshot the blocker model (ADR-0034).
- The recorded video is already undistorted and oriented (ADR-0015), so decoded
  frames are what the live tracker saw (after H.264 compression).
- The live tracker runs off the grab thread on a newest-frame-wins handoff
  (ADR-0040), so it can skip frames and its row can describe a slightly older
  frame (`track_src_block_id`).
- `RatTracker.load_model` re-applies the settings the model was built with
  (ADR-0027), and changing `proc_max_w` discards the background.

## Decision

Add `python -m tmaze.tools.offline_track` (pure decisions in
`tmaze.core.offline_track`, OpenCV in `tmaze.vision.offline_tracker`):

1. **New, additive output.** Each run writes `video/<id>_offline_track[_<tag>].csv`
   (one row per frame: `frame_idx, host_unix_s, cam_timestamp` + the live
   `TRACK_COLUMNS`, with the sidecar's `# key: value` header carried over and
   offline keys added) and a matching `.json` summary (every file used with its
   origin and sha256, the final settings, which layer set each one, the frame
   range, per-state counts, notes). The sidecar and manifest are never edited.
   `--tag` names a run so several can sit side by side; an existing run is not
   overwritten without `--overwrite`. The CSV is written under a `.partial` name
   and renamed when finished.
2. **Every frame, on itself.** No frame is skipped, and decoded frame *i* is
   matched to sidecar row *i* for its times.
3. **Replay the live setup by default.** Files come from the bundle snapshot.
   Settings are layered: the model's saved settings (or defaults) -> the
   session's snapshot (including enrichment, `adapt_enrich`, if it was on) ->
   `--params-file` -> `--set key=value`. Unknown keys in the last two are
   rejected with the list of valid names (`--list-params` prints them). Setup
   order: load the model, apply the settings, load blocker/ROI/landmarks, install
   the background LAST.
4. **Fallbacks for older bundles.**
   - No snapshot model: use the current shared rat model,
     `FALLBACK_MODEL_NAME = animal_ratmodel_messed_up_lamp_settings.npz`
     (rats 600/601/602). If that file is missing too, track without an
     appearance model.
   - Blocker model: never snapshotted, so the current
     `FALLBACK_BLOCKER_MODEL_NAME = animal_blockermodel.npz` is loaded when present
     (`--blocker-model`, `--no-blocker`).
   - No background PNG (or `--background-from-video`): per-pixel **median** of
     `BACKGROUND_SAMPLES = 50` frames spread evenly across the video.
   Each fallback is recorded in the summary `notes`.
5. **Frame ranges with warm-up.** `--start-frame` / `--end-frame` (inclusive) /
   `--warmup-frames`: warm-up frames are tracked but not written, because the
   tracker's smoothing and lock-on depend on earlier frames. `iter_track` is the
   reusable seam for the planned workbench and snippet export.

## Alternatives considered

- **Rewrite the sidecar's track columns.** Rejected: destroys the live record,
  and you can't compare runs.
- **Offline mode inside the experiment GUI's Tracking tab.** Rejected: touches
  code that runs during real sessions, and a long run could slow the rig GUI. An
  offline workbench is planned as a separate window (FW-0074).
- **Freeze the model (enrichment off) by default** for repeatable runs.
  Rejected by the operator: the default must reproduce the live setup.
  `--set adapt_enrich=false` freezes it when wanted.
- **Mean background.** Rejected: the rat is in the frame during a recording, so a
  mean keeps a faint rat ghost. The median of frames spread across the session
  drops it, because the rat is in any one spot for only a few samples.
- **Pick the newest model in `host/ratmodels/` per animal.** Rejected: saved
  model names don't reliably carry the animal, and the newest file is not
  necessarily the one in use. The operator named the current models explicitly.
- **Seek directly to `--start-frame`.** Deferred: seeking compressed video can
  land a few frames off. Skipping is exact (`grab()` without converting pixels)
  and fast (~1200 frames/s here). Seeking belongs with snippet export (FW-0076).

## Consequences

- Offline and live tracks legitimately differ: offline never skips frames, and it
  works on H.264-compressed frames (the bundles use crf 18). On session
  `602_20261009_132146`, frames 30000-31499, both were 100% TRACKING and their
  smoothed positions agreed to a median of 0.9 px (95th percentile 11.5 px).
- Speed on that session: ~270 frames/s tracked, so a 50-minute session takes
  about 5 minutes; the median background adds ~8 s.
- The blocker model replayed is the current default, not necessarily the one in
  use that day, until sessions snapshot it (FW-0079).
- When the current models change, update the two constants in
  `tmaze.core.offline_track`.
- Follow-ups: FW-0074 (workbench), FW-0075 (beam-break agreement; top priority),
  FW-0076 (overlay snippet export, incl. the task layer), FW-0077 (further
  evaluation), FW-0078 (retire analysis `render_overlay`), FW-0079 (snapshot the
  blocker model).

## Evidence / links

- Tests: `host/tests/test_offline_track.py` (pure resolution/layering/naming +
  synthetic-video end-to-end with and without a background PNG, and a warm-up
  sub-range).
- Smoke runs on `phase3_recordings/602_20261009_132146` (scratch output, not kept):
  snapshot model + ROI + landmarks + background used, the current blocker model as
  fallback. The median background matched the session PNG except for blinking
  board LEDs and compression noise (0.2% of pixels differ by > 30 grey levels).
- Related: ADR-0014 (track columns), ADR-0015 (undistort at source), ADR-0020
  (background PNG), ADR-0027 (model settings restore), ADR-0034 (blocker),
  ADR-0040 (tracking off the grab thread), ADR-0072 (config snapshot).
