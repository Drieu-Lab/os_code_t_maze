# ADR 0018: Annotate reward wells and body sensors as image-space landmarks

- **Status:** Accepted
- **Date:** 2026-06-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (landmark state/API + overlay), `host/tracker_view.py`
  (`_LandmarkEditor` + controls), `host/landmarks.json` (new, gitignored),
  `.gitignore`
- **Supersedes / Superseded by:** — (extends ADR-0014 markerless tracking;
  relates to ADR-0015 undistort-at-source, FW-0004 deferred rule engine)

## Context

The tracker (ADR-0014) gives the rat's position over time, and the GUI already
lets the experimenter draw a normalized-polygon ROI for the maze extent. To
actually drive cue-guided task logic from position we need *anchors*: where the
rig's reward wells and body-sensor beams physically are in the camera image. The
rig has a fixed, known set of these - 3 reward wells (lick ports `L,i`:
0=left, 1=right, 2=center/start) and 6 body-sensor beams (`S,i` in `SENSOR_PINS`
order: ENTRY, EXIT, L_CHOICE, R_CHOICE, L_RETURN, R_RETURN) - already enumerated
in `host/rig_view.py` and `firmware/PROTOCOL.md`.

We want to *mark* these now (the foundation), so that later a task-rule engine can
reason about the animal's location relative to them (FW-0004). The marking has to
survive frame scaling (preview vs proc vs full-frame) and lens correction
(ADR-0015), like the ROI does.

## Decision

Add **single-point landmark annotations** to the existing still-frame tooling,
modeled exactly on the ROI:

- **Geometry: single normalized (0..1) points** (not zones). "Relative to defined
  points" is the requirement; any proximity threshold is a *rule-time* parameter
  for the future engine, not baked into the marker. Normalized coords survive all
  scaling and the undistort-at-source pipeline.
- **Fixed named set + optional custom points.** `FIXED_LANDMARKS` in
  `host/tracker.py` is the single source of truth: each fixed entry is
  `(kind, index, label)` where `index` ties the marker to its firmware event
  (`well` -> `L,i`, `sensor` -> `S,i`). Custom points (`kind="custom"`,
  `index=None`) can be added freely for anything not in the fixed set.
- **Tracker owns the data, but does not use it yet.** `RatTracker` gains
  `set_landmarks/get_landmarks/clear_landmarks/save_landmarks/load_landmarks`
  (lock-guarded), mirroring the ROI API. `process()` is unchanged - landmarks are
  held for the overlay and for FW-0004.
- **Persistence:** `host/landmarks.json`
  (`{"version": 1, "landmarks": [{kind, index, label, x, y}, ...]}`), separate
  from the ROI and the appearance model, gitignored as per-rig regenerated
  geometry (like `roi_polygon.json`). Auto-loaded on Tracking-tab startup.
- **Editor + overlay:** a modal `_LandmarkEditor` (still frame, side list of
  landmarks, select-then-click to place, auto-advance, add-custom/clear/save)
  in `host/tracker_view.py`; the shared `draw_overlay` renders each placed point
  as a colour-coded marker + label, so it appears on **both** the camera tab and
  the Tracking tab previews.

The **task-rule engine is explicitly out of scope** here and deferred to FW-0004.

## Alternatives considered

- **Point + proximity radius ("zones")** — rejected for now: the requirement is
  position *relative to points*; a per-marker radius bakes a threshold into
  geometry that really belongs to the rule layer. Easy to add later if needed.
- **Per-landmark polygon regions** — rejected: most flexible but the most
  clicking, and unnecessary when a centroid-to-point distance suffices.
- **Freeform points only (no fixed set)** — rejected: the rig's wells/beams are a
  known, fixed set tied to firmware indices; pre-seeding them (and keeping the
  `index` link) is what lets a rule map "near reward well" to lick port `L,i`.
  Custom points are still allowed on top.
- **Store on a new module / in the ROI file** — rejected: the tracker already
  owns the ROI and the overlay reads from it, so co-locating landmarks there is
  consistent; a *separate* `landmarks.json` keeps the ROI file backward-compatible.
- **Reuse `_RoiEditor`** — rejected: polygon-vertex clicking and labeled-point
  placement are different interactions; a dedicated editor is clearer and leaves
  the proven ROI editor untouched.

## Consequences

- The camera and Tracking previews now show the wells/sensors once marked; both
  get it free via the shared overlay. With no `landmarks.json` present, behavior
  is unchanged.
- A clean foundation for FW-0004 (proximity/zone task rules): the `index` link
  means a rule can correlate "animal near landmark" with the firmware `L`/`S`
  events for that same port/beam.
- Like the ROI/model, landmarks are tied to the camera geometry (orientation +
  undistort): re-mark if the camera is moved or the calibration changes.
- One more gitignored per-rig artifact (`host/landmarks.json`).
- Revisit if rules need zones rather than points (add an optional per-marker
  radius), or if landmarks should be emitted into the recording sidecar.

## Evidence / links

- Code: `host/tracker.py` (`FIXED_LANDMARKS`, `LANDMARK_COLORS`, landmark API,
  `draw_overlay`), `host/tracker_view.py` (`_LandmarkEditor`, `LANDMARKS_PATH`,
  `_define_landmarks`/`_clear_landmarks`/auto-load), `.gitignore`.
- Validated headlessly: `set/get/clear/save/load_landmarks` round-trip through
  `landmarks.json`; `draw_overlay` renders placed points on a synthetic frame
  without error.
- Related: ADR-0014 (markerless tracking + ROI this mirrors), ADR-0015
  (undistort-at-source - landmarks are marked on already-corrected frames),
  `host/rig_view.py` / `firmware/PROTOCOL.md` (the `L,i`/`S,i` index meanings),
  FW-0004 (deferred task-rule engine that consumes these).
