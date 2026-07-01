# ADR 0014: Markerless rat position tracking (shared CV core + appearance model)

- **Status:** Accepted
- **Date:** 2026-06-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (new), `host/tracker_view.py` (new),
  `host/blob_tracker_prototype.py`, `host/camera_view.py`,
  `host/experiment_control.py`; recording sidecar CSV schema; `host/ratmodels/`
  and `host/roi_polygon.json` (gitignored)
- **Supersedes / Superseded by:** — (relates to ADR-0011/0012/0013 recording,
  ADR-0015 undistort-at-source)

## Context

The cue-guided T-maze task needs the rat's position over time (which arm it
enters, dwell at ports, trajectories) to relate behavior to the recorded neural
data. We want this without head markers/LEDs (markerless), cheaply, and running
live alongside the existing free-run camera recording (ADR-0011/0012/0013). The
rig is static and well-lit from above, so a classic computer-vision approach is
enough to start; deep pose estimation (DLC/SLEAP) is heavier than needed for a
single centroid and can come later.

Two practical problems shaped the design:

1. **Intruders.** An experimenter's hand/arm often enters frame to intervene. A
   naive "largest moving blob" tracker jumps to the hand. We need to keep the
   lock on the *rat* through such events.
2. **A resting rat must stay visible.** Adaptive background models (MOG2) learn a
   motionless rat into the background and lose it - unacceptable for a task with
   long pauses at ports.

The tracker also has to be usable two ways: a standalone tuning tool
(`blob_tracker_prototype.py`) and inside the experiment GUI, without the two
implementations diverging.

## Decision

**One UI-free core, `host/tracker.py` (`RatTracker`), shared by the prototype and
the GUI.** It takes BGR frames and returns a `TrackResult`; it contains no Tk and
no OpenCV-window code, so the same algorithm runs in the prototype loop and in the
camera grab thread.

Algorithm (classic CV):
- **Fixed reference-frame background subtraction** (`|frame - empty-rig|`), not
  MOG2, so a stationary rat is still detected (a resting animal never melts into
  the background). The reference is grabbed on demand per session.
- **Polygon ROI** stored as **normalized (0..1) vertices** (survives proc/preview/
  full-frame scaling), persisted separately from the model as rig geometry in
  `host/roi_polygon.json`.
- **Appearance model `RatModel`** kept as cheap **sufficient statistics**: a
  pooled Hue-Saturation colour histogram, plus a **multivariate Gaussian** over a
  rotation-invariant FORM vector `[log(area), log(elongation), solidity]`. Size
  and shape are modelled *jointly* (one Gaussian) so their natural correlation is
  captured (a rearing/grooming rat is smaller AND rounder; running is larger AND
  elongated). Built during a **registration window** from the dominant, non-clipped
  in-ROI blob over many frames so it captures the animal's pose range.
- **Scoring association**: distance-to-prediction + colour-histogram +
  FORM (Mahalanobis). Colour and size are the strong cues; **shape is a soft,
  low-weight term and never a hard gate** (it's the most variable / mask-sensitive
  signal). The only hard FORM gate is a size band derived from the log-area
  marginal. On a fresh (re)acquisition (no prediction) the distance term is
  dropped so appearance decides which blob is the rat - this is what rejects an
  intruder.
- **State machine** `NO_REFERENCE -> ACQUIRING -> TRACKING -> COASTING -> LOST`
  (+ `REGISTERING`). `COASTING` is **bounded** (`coast_max_frames`): after the
  timeout the stale track is dropped (`LOST`) so a lock on a departed hand is
  released and the rat can be re-acquired. `ACQUIRING` requires `confirm_frames`
  good frames before `TRACKING`. A constant-velocity **Kalman filter** smooths and
  predicts. Each frame carries a **confidence** in [0,1].
- **Online enrichment (accumulate, not drift)**: an optional mode adds confident,
  unambiguous tracking observations to the model's running statistics, growing the
  captured colour/size/shape *range* without forgetting registration. Chosen over
  an EMA blend, which would chase the latest frame and discard registration.

**GUI integration:** one shared `RatTracker` is created in
`experiment_control.py` and injected into `CameraView` (producer - runs
`process()` on its grab thread, default OFF) and a new `TrackerControlView`
"Tracking" tab (controller - register/tune/persist + live readout). The camera
preview overlays the path/centroids/ROI/state.

**Recording sidecar schema change:** every recording's CSV gains
`TRACK_COLUMNS` (`track_state, track_detected, track_cx, track_cy,
track_smooth_cx, track_smooth_cy, track_area, track_elongation, track_solidity,
track_confidence`), blank when tracking is off, so the per-frame track stays
frame-aligned with the video.

**Model persistence:** `RatModel` saves to a compact `.npz` of the sufficient
statistics (pooled histogram counts + form sample count/sum/sum-of-outer-
products + metadata), versioned (`MODEL_VERSION`). The **reference background is
NOT persisted** (session/lighting-specific - re-grab per session). Models live in
`host/ratmodels/` (gitignored, per-animal/per-rig).

## Alternatives considered

- **MOG2 / adaptive background** — rejected: learns a motionless rat into the
  background; the fixed reference avoids that on a static rig.
- **Largest-blob tracking (no appearance model)** — rejected: steals onto an
  experimenter's hand. Still available as the model-less fallback for quick tests.
- **Rectangular ROI** — rejected: the maze isn't axis-aligned/rectangular; a
  normalized polygon masks it precisely and survives scaling.
- **Independent per-feature size/shape bands** — rejected in favour of one
  multivariate Gaussian, which captures the size-shape correlation and yields a
  single principled Mahalanobis score.
- **EMA "drift" adaptation** — rejected: recency-weighted forgetting drifts onto
  slowly-changing distractors and discards registration; accumulation (enrichment)
  grows the range and converges instead.
- **Deep pose estimation (DeepLabCut/SLEAP/DANNCE)** — deferred: heavier than a
  centroid needs now; the shared-core design leaves room to add it later.
- **Heading/body-orientation output** — deferred (FW-0002): the body axis is only
  known mod 180 deg and head/tail disambiguation needs real rat video to validate.
- **Tracking in a separate process / every-frame at full res** — kept in the grab
  thread at a downscaled `proc_max_w` for simplicity; a throttle (every Nth frame)
  is the escape hatch if it competes with encoding.

## Consequences

- The prototype and GUI cannot diverge (one core). Tuning in either tool, plus the
  shared `roi_polygon.json` and `ratmodels/`, carry over.
- Tracking is opt-in and runs on the grab thread, so with it OFF the camera tab is
  unchanged; a tracker exception can't corrupt a recording (guarded), and the
  sidecar columns are simply blank.
- The sidecar CSV format changed (added columns) - readers must tolerate the new
  columns; older recordings lack them.
- Coordinates are emitted in full-frame px (overlay scales down) so the CSV aligns
  with the recorded FFV1/H.264 video.
- A model is tied to its `proc_max_w` (area/shape are measured at proc
  resolution); changing it invalidates the reference and should prompt
  re-registration. `MODEL_VERSION` was bumped to 2 with the Gaussian/sufficient-
  statistics format; older `.npz` models raise a clear "re-register" error.
- Revisit if: lighting is IR/low-saturation (colour weakens - lean on
  distance/size/shape; see future work), multiple animals are tracked (needs
  Hungarian assignment), or grab-thread tracking competes with encoding at full
  frame rate.

## Evidence / links

- Code: `host/tracker.py`, `host/tracker_view.py`, `host/camera_view.py`
  (`_track_row`, `_draw_track_overlay`, `TRACK_COLUMNS`),
  `host/experiment_control.py` (shared tracker + Tracking tab),
  `host/blob_tracker_prototype.py`.
- Validated headlessly (synthetic frames): registration builds the model;
  rotation-invariant elongation; shape stays soft (round blob still accepted);
  wrong-colour intruder rejected; `COASTING -> LOST -> re-acquire`; enrichment
  widens the captured range (n grows, log-elongation variance grows, round-pose
  form score rises); model `.npz` (v2) and ROI JSON persistence round-trip; and
  the sidecar track row matches `TRACK_COLUMNS`. Live rig/rat tuning to follow.
- Related: ADR-0011/0012/0013 (camera recording), ADR-0015 (undistort-at-source -
  the tracker receives already-undistorted frames), FW-0002 (deferred heading).
