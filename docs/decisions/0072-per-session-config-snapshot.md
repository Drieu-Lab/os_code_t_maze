# ADR 0072: Snapshot the session's config into the session folder, and version the rig geometry files

- **Status:** Accepted
- **Date:** 2026-07-31
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_config.py` (new pure core), `host/test_session_config.py`
  (new tests), `host/session_controller.py` (writes the snapshot on Start +
  manifest streams), `host/session_log.py` (`SCHEMA_VERSION` 1 -> 2),
  `host/tracker_view.py` (`active_model_path()`), `host/experiment_control.py`
  (`config_inputs` provider wiring), `.gitignore` (un-ignore the geometry files)
- **Supersedes / Superseded by:** partially supersedes the *gitignore consequence*
  recorded in [ADR-0014](0014-markerless-position-tracking.md),
  [ADR-0018](0018-landmark-annotations.md),
  [ADR-0023](0023-position-based-rules-engine.md),
  [ADR-0025](0025-maze-state-module.md) (see below). Those decisions otherwise
  stand; only "keep this file gitignored" changes, so their Status is unchanged.
  Builds on [ADR-0048](0048-host-session-orchestration-and-manifest.md) /
  [ADR-0049](0049-per-session-recording-folder-tree.md) (session folder +
  manifest) and relates to [ADR-0031](0031-gui-edit-rules-json.md).

## Context

A session's recorded data (video + per-frame track, Phase 3 CSV, serial) is only
reproducible if you also know the **definitions that shaped it**: the maze zone
map, the tracker ROI and landmarks, the cm/px scale, the lens calibration, the
Phase 3 / rules config, and the live tuning that was applied. Several of these
live in single-path files under `host/` that the GUI **overwrites in place** when
re-drawn (`zones.json`, `roi_polygon.json`, `landmarks.json`, `rig_scale.json`),
and some are not files at all (`camera_view.ORIENTATION` / `UNDISTORT_ALPHA`, the
live `TrackerParams` / `MazeStateParams`). So once someone nudges the camera and
re-draws a zone map, there was **no record of which geometry a past session
used** — the reproducibility gap.

Two facts shaped the fix. First, the session already writes a self-contained
folder tree + manifest (ADR-0049), so a `config/` sibling folder is the natural
home. Second, the geometry files were originally gitignored as "per-rig,
regenerated" artifacts tied to the camera pose (ADR-0014/0018/0023/0025). In
practice the rig geometry has stabilized (untouched for days) and is intended to
be frozen once rig/task development completes — i.e. they behave like **fixed
parameters**, not churny per-run state.

## Decision

**1. Snapshot config per session.** On Session Start, copy the reproducibility-
critical config into `<session_id>/config/`:

- Host-local files, found by the new pure core `session_config.py` itself
  (`zones.json`, `roi_polygon.json`, `landmarks.json`, `rig_scale.json`,
  `phase3.json`, `rules.json`).
- Extra files supplied by the injected `config_inputs` provider: the lens
  calibration JSON (`camera_view.CALIBRATION_PATH`) and the active tracker model
  `.npz` (`tracker_view.active_model_path()`).
- A `runtime_params.json` of the non-file scalars: camera transform
  (`ORIENTATION`, `UNDISTORT_ALPHA`), live `TrackerParams`, live
  `MazeStateParams`.

`session_config.py` also writes a `config_snapshot.json` index recording, per
expected source, whether it was present, its copied filename, and a **SHA-256**
(so an offline reader can verify integrity and detect drift against the live
`host/*.json`). The snapshot is registered in the manifest as the `config_dir` /
`config_snapshot` streams; `SessionManifest.SCHEMA_VERSION` bumps 1 -> 2. The
whole thing is best-effort: a missing/unreadable source is recorded
`present: false` and never blocks Start.

**2. Version the rig geometry files as fixed parameters.** Remove
`host/roi_polygon.json`, `host/landmarks.json`, `host/zones.json`,
`host/rig_scale.json` from `.gitignore` and commit them. Git history becomes the
record of the (now rare) geometry change; the per-session snapshot remains the
authoritative per-session record. The learned appearance model
(`host/ratmodels/*.npz`) stays gitignored (binary, per-animal; external backup).

## Alternatives considered

- **Snapshot only; keep the geometry files gitignored.** The snapshot alone
  closes the reproducibility gap, so this was viable. Rejected because, with the
  geometry now effectively frozen, versioning the live files is simple, gives a
  shared starting point + change history, and has negligible churn — the churn
  worry behind ADR-0014/0018/0023/0025 no longer applies.
- **A separate committed `rig_geometry_example/` baseline** (copies, live files
  stay ignored). Considered; rejected as redundant duplication once the live
  files are stable enough to track directly — two sources of truth for the same
  geometry with no upside here.
- **Import the GUI path constants into `session_config.py`** so the filename list
  isn't duplicated. Rejected: those constants live in Tk/OpenCV shells
  (`tracker_view`, `rules_view`, `camera_view`), and a stdlib-only pure core must
  not import them. The filenames are duplicated deliberately, with a comment
  cross-referencing each owning module.
- **Re-poll / re-snapshot on config changes mid-session.** Rejected for now:
  geometry is expected stable across a session; a one-shot snapshot at Start is
  simpler. A mid-session edit is out of scope (future work).

## Consequences

- Every session folder is now self-describing: the geometry, calibration, task
  config, and live tuning that produced its data travel with it, and the SHA-256
  index lets an analyst confirm a session used the committed geometry.
- `host/zones.json` / `roi_polygon.json` / `landmarks.json` / `rig_scale.json`
  now show up in `git status` / `git diff` after a re-draw. That is intended
  (they are fixed parameters); a genuinely per-rig divergence would revisit this
  (cf. ADR-0031's per-rig-override alternative).
- One new injected provider (`config_inputs`) on the Session panel, mirroring the
  existing `behavior_summary` seam; the Tracking view is now captured in a ref so
  its active-model path is reachable.
- Manifest readers must handle `schema_version` 2 (new streams are additive).
- The tracker model `.npz` is only snapshotted when a model was loaded/saved from
  a file; a live Register-rat-only model has no path and is skipped (its
  `TrackerParams` are still captured, and the session sidecar already records the
  resulting per-frame track).

## Evidence / links

- Code: `host/session_config.py`, `host/test_session_config.py`,
  `host/session_controller.py` (`_write_config_snapshot`, `CONFIG_SUBDIR`),
  `host/session_log.py` (`SCHEMA_VERSION`), `host/tracker_view.py`
  (`active_model_path`), `host/experiment_control.py` (`config_inputs`),
  `.gitignore`.
- Tests: `python -m unittest test_session_config` (+ the full host suite).
- Related: ADR-0048/0049 (session orchestration + folder tree), ADR-0031
  (versioned `rules.json`), ADR-0014/0018/0023/0025 (the geometry files whose
  gitignore consequence this revisits).
