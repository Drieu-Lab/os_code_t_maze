# ADR 0215: Workbench video player - one worker thread, exact OpenCV seeking

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/vision/video_player.py`, `host/tests/test_video_player.py`;
  the workbench window (FW-0074)
- **Supersedes / Superseded by:** — (builds on ADR-0211, ADR-0213, ADR-0214)

## Context

The workbench needs to play, pause, jump and step through a recorded video with
the tracker running, while the Tk window stays responsive and the hosted
Tracking tab changes settings at any moment. Three questions:

- **Who calls `RatTracker.process()`?** The tracker keeps a frame history, so
  two callers at once (playback plus a re-track, or model building) would mix
  their histories.
- **Is seeking exact?** ADR-0211 avoided seeking in the command-line tool for
  fear it lands a few frames off in compressed video. Here frame numbers matter:
  they appear in `--register-frames`, the copied command line, and the trial
  markers.
- **How does a whole-video run fit beside playback?**

## Decision

- **One worker thread owns the frame loop** (`VideoPlayer`), the same model as
  `camera_view` on the rig. The Tk thread only queues requests (play, pause,
  seek, step, re-track, process the current frame once, mode, speed, warm-up,
  A-B loop); the newest seek wins. Anything else that changes the tracker's
  history - building models from marked frames, installing a background - is
  submitted as a job and runs on the same thread between frames, then the shown
  frame is re-tracked.
- **Seeking uses OpenCV's `CAP_PROP_POS_FRAMES`.** A forward jump of up to 300
  frames is decoded instead. Verified exact: on `602_20261009_132146` (H.264,
  70,727 frames), seeks to frames 12345, 39990, 40000 and 70000 returned images
  identical, pixel for pixel, to reading the video in order; also checked on a
  synthetic mp4 in the tests.
- **Jumps re-track the warm-up** (ADR-0214); a forward step no longer than the
  warm-up keeps tracking instead. A 150-frame jump on the real session takes
  ~0.7 s.
- **The player answers the Tracking tab's camera calls** (`get_latest_frame`,
  `tracking_enabled`, `enable_tracking`, `get_output_dir`), so the tab is hosted
  unchanged (ADR-0213). "View only" mode shows the video without tracking.
- **Whole-video runs** (`RunJob`) use their own tracker on their own thread via
  `offline_tracker.run`, so playback continues. Cancel is checked at each
  progress callback (every 1000 frames), and `run` removes the partial file.
  `prepare_run` builds the run's settings the ADR-0214 way and saves in-memory
  models beside the outputs.

## Alternatives considered

- **Track on the Tk thread.** Rejected: decoding plus tracking is several ms
  per frame and a jump is ~0.7 s, which would freeze the window.
- **A lock around the tracker instead of one owner thread.** Rejected: a lock
  keeps calls from overlapping but not histories from interleaving.
- **Decode forward from the start for every jump** (exact by construction).
  Rejected: ~1 minute to reach the end of a session; seeking is exact and
  verified.
- **PyAV for decoding.** Rejected: the run path decodes with OpenCV, and
  matching pixels between the window and the run matter more.

## Consequences

- The command-line tool could now seek to `--start-frame` instead of skipping
  (FW-0076 snippet export will rely on it).
- If a future OpenCV build or codec setting made seeking inexact, the window
  would show slightly wrong frames; `test_seeked_frame_matches_sequential_read`
  guards the synthetic case, and the check above can be rerun on real video.

## Evidence / links

- `host/tests/test_video_player.py` (seek vs in-order read, warm-up counts, view
  mode, end of video and A-B loop, model building, window run vs copied command
  give identical rows, Cancel leaves no output).
- Real-video seek check on `602_20261009_132146` (scratch script, not kept).
