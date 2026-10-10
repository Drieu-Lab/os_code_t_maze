# ADR 0217: Importing pre-bundle recordings for offline tracking

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/legacy_import.py`,
  `host/tmaze/tools/import_legacy_recordings.py`, `host/tests/test_legacy_import.py`
- **Supersedes / Superseded by:** — (builds on ADR-0211)

## Context

Before session bundles existed (ADR-0048/0049), the camera wrote loose files
straight into `t-maze_recordings/`: `<stem>.csv` (the sidecar), a video named
`<stem>[_<label>].mp4|.mkv`, and `<stem>_background[_<label>].png`, where
`<stem>` is `animal_YYYYMMDD_HHMMSS` and the label was typed by hand afterwards
(e.g. `rat600_lowlight`). The offline tracker (ADR-0211) only reads bundles:
`<id>/video/<id>.<ext>` + `<id>.csv`, with the model, ROI and landmarks in
`<id>/config/`. These recordings have no `config/` at all, the session id is
`animal` instead of the rat, and the background was renamed after the sidecar
header named it, so the tracker would not find it.

The operator confirmed the camera and rig have not moved relative to each other
since then, so today's ROI, landmarks and zones apply.

## Decision

- **An import tool, not a second reader.** `import_legacy_recordings` builds a
  pseudo-bundle per recording under `t-maze_recordings/legacy_bundles/`, so the
  offline tracker and workbench work on them unchanged. The video, sidecar and
  background are **hard links** (a second name for the same file on disk: no
  extra space, the original names stay where they were); the model and host
  config files are small copies.
- **Two steps through a reviewed table.** `--draft` writes
  `legacy_bundles/legacy_recordings.csv`, one row per recording with a proposed
  include, session id, rat, model and model quality, and a reason in `notes`; a
  human edits it; the build step validates it and builds only the `include=yes`
  rows. The draft never overwrites an edited table, and the build leaves an
  existing bundle folder alone. The table lives beside the bundles (it describes
  those files, not the code), so it is not versioned.
- **What is proposed for import.** Videos of at least 1 GB that were recorded
  with undistortion on. Smaller files are tests or short clips (all the real
  2026-07 sessions are larger). The two 2026-06-24 recordings were made before
  undistortion (ADR-0015), so the ROI, landmarks and models do not line up with
  them.
- **Rat from the label.** `rat<N>` in the label gives the rat; otherwise the
  session id starts `unk_`. A rat taken from other evidence (the training log)
  is typed into the table by hand with the reason in `notes`.
- **Model and its quality.** For the rat and the lighting in the label, the
  preferred model is `animal_ratmodel_rat<N>[_lowlight].npz` (two-lamp sessions
  fall back to the lowlight model, then the plain one). Quality is `ok` if that
  exact model was saved no later than the recording ended (so the live tracker
  could have used it), `approximate` if it was saved later or is another
  lighting's, and `none` for no model (the tracker's current fallback model is
  used). The quality is written to `config/legacy_import.json` with the source
  file names and the hashes of the copied files, so an `approximate` bundle can
  be found and re-registered in the workbench later.
- **Today's host files.** `roi_polygon.json`, `landmarks.json` and `zones.json`
  are copied from `host/` at import time. No `runtime_params.json` is written,
  so the tracker runs with its default settings.
- **Outside `phase3_recordings/`.** The bundles have no task log and must not be
  picked up by the session registry or the analysis repo as Phase 3 sessions.

## Alternatives considered

- **Teach the offline tracker to read loose recordings.** Rejected: every reader
  (tracker, workbench, later the analysis) would need a second path, and the
  rat / model / background guesses would be made silently on each run instead
  of once, reviewed.
- **Rename or move the originals into bundles.** Rejected: destructive, and the
  original names carry the hand-typed labels.
- **Copy the videos.** Rejected: some are 15 GB; hard links cost nothing. (They
  need the bundles on the same drive as the recordings.)
- **Guess the unlabelled rats from the training-log times automatically.**
  Rejected: only one (2026-07-02 15:31, rat 602) is unambiguous; the others stay
  `unk_` with the likely rat in `notes`.

## Consequences

- The blocker model is always the tracker's current fallback (saved 2026-07-10);
  for earlier recordings this is an approximation, as the tracker cannot say
  "no blocker" per bundle (`--no-blocker` applies to a whole run).
- The model quality is judged from file save times on this PC, so a model
  restored from elsewhere with a new save time reads as `approximate`.
- Re-importing a bundle means deleting its folder first.

## Evidence / links

- `host/tests/test_legacy_import.py`: grouping the loose files, the rat and
  session id, the model rule (ok / approximate / none, two lamps falling back to
  lowlight), the draft's skip reasons, table checks, and an end-to-end draft then
  build whose bundle `offline_track.resolve_settings` reads from the bundle.
- First import (2026-10-10): 28 loose recordings found, 14 imported (11 with a
  rat, 3 `unk_`); a 300-frame `offline_track` run on `600_20260714_131656` was
  79% TRACKING and 0% LOST.
