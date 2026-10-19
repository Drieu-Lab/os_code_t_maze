# ADR 0220: Record the learned head points as trailing camera-sidecar columns (format v5)

- **Status:** Accepted
- **Date:** 2026-10-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/head_columns.py` (new), `host/tmaze/ui/camera_view.py`
  (sidecar header + rows), `host/tmaze/vision/tracker.py` + `head_live.py`
  (the live head points, ADR-0219), `host/tmaze/core/phase3/bundle_format.py`
  (`FORMAT_VERSION` 4 -> 5, `sidecar_head_columns`), `host/head_models/`;
  analysis mirror `drieu_lab_analysis` `phase3_format` (`FORMAT_VERSION`,
  `SIDECAR_HEAD_COLUMNS`) and its MATLAB loader's version check
- **Supersedes / Superseded by:** —

## Context

ADR-0219 chose a small network for the head point; the tracker now produces,
per frame, six filtered points (head centre, neck, tail base, nose, both ears),
a head direction and a trusted / held / none status, but only while head
tracking is switched on for the session (off by default). The operator chose
to record all six points, the direction and the valid flag; to keep the model
file in the repo, versioned with the code, with each session saying which file
it ran; and, when head tracking is off, to leave the columns blank and record
that it was off. The task (zones, cue, reward) keeps using the centre of mass
for now.

## Decision

1. **Where:** the live camera sidecar (`video/<session_id>.csv`), one row per
   saved frame, gains `head_columns.HEAD_COLUMNS` at the **end** of the row,
   after `track_src_block_id`: `head_status`, `head_valid`, `<point>_x` /
   `<point>_y` for head, neck, tail_base, nose, right_ear, left_ear (full-frame
   px), `head_dir_deg` (neck -> head centre, degrees, image axes: 0 = right,
   90 = down).
2. **Values:** `head_status` is `ok` / `leap` / `none` and blank when head
   tracking is off; `head_valid` is 1 only for `ok`. During a `leap` the held
   (previous) points are still written, marked invalid; with `none` the points
   are blank. The values come from the same tracker result as the row's
   `track_*` columns, so they share its staleness (join on `track_src_block_id`,
   ADR-0040).
3. **Which model:** the sidecar `#` header records the state at record start:
   `head_tracking: on|off` and, when on, `head_model` (file name),
   `head_model_sha256` (a fingerprint of the exact file), `head_model_trained`
   and `head_model_sessions`. The model ships in `host/head_models/`
   (`head_net.onnx` + `head_net.json`), so the code commit in the session's
   provenance also identifies it.
4. **Contract:** `FORMAT_VERSION` 4 -> 5; `format.json` gains
   `sidecar_head_columns` (`{column: meaning}`, from `head_columns.column_doc`).
   The analysis mirror bumps its guard and lists the same column names; the
   golden fixture's `format.json` is re-issued.

## Alternatives considered

- **Insert the columns among the `track_*` columns** (extend `TRACK_COLUMNS`).
  Rejected: `track_src_block_id` and every later column would shift, which
  breaks any reader that counts column positions (spreadsheets, MATLAB scripts),
  and `TRACK_COLUMNS` is also the offline re-track file's layout, which does not
  produce head points.
- **A separate head CSV per session.** Rejected: a second per-frame file to keep
  aligned with the video, for data that already shares the sidecar's row and
  timing.
- **Record only the head centre and direction.** Rejected by the operator: the
  other points are nearly free (the network predicts them anyway) and are wanted
  for later analysis.
- **Model provenance only in the session manifest.** Not done here: the camera
  recorder is where the tracker and its model are known at record start, and the
  sidecar is the file the columns live in. Echoing it into the manifest can come
  with the GUI switch.
- **Leave out the held `leap` points.** Rejected: writing them with
  `head_valid = 0` keeps the row shape uniform and lets analysis see what was
  held; readers that want trusted points filter on `head_valid`.

## Consequences

- A reader that ignores unknown columns (pandas by name, the analysis loaders)
  is unaffected; the format bump makes older analysis code refuse v5 bundles
  until updated, as intended.
- Sessions recorded with head tracking off still carry the columns (blank) and
  `head_tracking: off`, so "off" and "on but lost" are distinguishable.
- The header records the state at record start; the GUI switch should not
  change it while recording (to be enforced when the switch is added).
- Offline re-track files (`<id>_offline_track*.csv`, ADR-0211) don't have the
  head columns; adding head points to the offline re-track is future work.
- Retraining (e.g. with rats 601 / 602) changes `head_model_sha256`, so old and
  new sessions stay distinguishable.

## Evidence / links

- ADR-0219 (method), ADR-0040 (sidecar staleness), ADR-0182 (format v4).
- Commit `c9019b1` (live head tracking in the tracker). On session
  `600_20260914_130417` (6000 frames) head tracking adds about 1.3 ms per
  frame (median 3.3 -> 4.6 ms) with 99.1% of frames valid.
- Tests: `host/tests/test_head_columns.py`; `test_golden_bundle` (fixture
  re-issued); analysis `tests/test_golden_fixture.py`.
