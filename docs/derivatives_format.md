# Derived-data folder format (version 1)

The layout and rules for [derived data](glossary.md#derived-data-pipeline-manifest-stale):
results computed from the session bundles after recording. The decision and
its reasons are in [ADR-0226](decisions/0226-derived-data-folder.md). This page
is the spec other repos (`drieu_lab_analysis`) mirror; the rig repo's
implementation is `host/tmaze/core/derivatives.py` (rules) and
`host/tmaze/io/derivatives_io.py` (reading/writing).

## Where

The root comes from the `TMAZE_DERIVATIVES_DIR` environment variable, else
`host/derivatives_root.json` (`"root"`). On the lab share it is
`Z:\Ravi\derivatives`.

## Layout: pipeline first

```text
<root>/
  <pipeline>/                     e.g. dlc_full_iter1, movement_derivatives_v1
    <session_id>/                 e.g. 602_20260916_145627 (the bundle folder's name)
      manifest.json               written LAST
      <session_id>_<what>.<ext>   the pipeline's own files
    _logs/  _stop  .lock          the pipeline's housekeeping (names start with _ or .)
```

- **Pipeline names**: lowercase letters, digits and `_`, starting with a letter.
  The name carries the version (`_iter1`, `_v1`). Change the model, a
  setting that changes results, or a column's definition: use a new name. The
  old folder stays as it was.
- **Each pipeline writes only inside its own folder.** No tool creates or
  deletes another pipeline's folders. A pipeline needs no registration: its
  first run creates its folder.
- **Session folders** are named exactly like the bundle folder (the
  `session_id`), so a result joins to its bundle by name.
- Folders whose names start with `_` or `.` are not sessions.

## Session states

A session's state for a pipeline is decided from its folder alone:

| State | Meaning |
| --- | --- |
| pending | no session folder yet |
| partial | a folder but no valid, complete manifest (a run was interrupted, or is running) |
| stale | a complete manifest whose recorded inputs no longer match the bundle |
| done | a complete manifest whose inputs still match |

"Pending" is never stored anywhere. It is the bundles the scan finds (the
recordings roots, minus `host/session_ignore.json`) minus the session folders,
so a newly recorded session is pending for every pipeline at once. A pipeline
that only applies to some sessions (e.g. it needs a video) filters that list
itself.

## manifest.json

Written to `manifest.json.tmp`, then renamed into place, so it is either
complete or absent. A tool re-running a session deletes the old manifest first,
so an interrupted re-run shows as partial, not as done with mixed files.

```json
{
  "derivatives_format_version": 1,
  "pipeline": "dlc_full_iter1",
  "session_id": "602_20260915_151743",
  "status": "complete",
  "adopted": false,
  "inputs": [
    {"role": "video", "kind": "file",
     "path": "Z:\\Ravi\\phase3_recordings\\602_20260915_151743\\video\\602_20260915_151743.mp4",
     "bundle_rel": "video/602_20260915_151743.mp4",
     "size": 14404165837, "mtime_s": 1789503470.669},
    {"role": "upstream:dlc_full_iter1", "kind": "upstream",
     "pipeline": "dlc_full_iter1", "session_id": "602_20260915_151743",
     "finished_at": "2026-10-21T23:05:31-04:00"}
  ],
  "outputs": [{"file": "602_20260915_151743_dlc.csv", "size": 48162855}],
  "params": {},
  "model": {},
  "software": {},
  "started_at": "2026-10-21T22:00:00-04:00",
  "finished_at": "2026-10-21T23:05:31-04:00"
}
```

Required keys: `derivatives_format_version`, `pipeline`, `session_id`,
`status` (`"complete"`), `inputs`, `outputs`, `finished_at`. A manifest with
any of them missing, a different format version, or a `pipeline` /
`session_id` that doesn't match its folder counts as partial.

**Inputs** (`kind`):

- `file`: a bundle file, identified by `size` and `mtime_s` (seconds since
  1970). Matches when the size is equal and the modified time is within 2 s
  (copies between drives can round it). `path` is for people and is not
  compared, so archiving a bundle to another drive doesn't make results
  stale. `bundle_rel` (the path inside the bundle, `/`-separated) lets any
  tool re-check the file without knowing the pipeline.
- `upstream`: another pipeline's result for the same session. Matches when
  that session's upstream manifest still has the same `finished_at`, so
  re-running the upstream pipeline makes this result stale.

The inputs match when each role is present in both lists and each pair
matches. A role added or dropped counts as changed.

`outputs`: each file the pipeline wrote, relative to the session folder,
with its size. `params`, `model` and `software` are free-form per pipeline
(settings, model files and their checksums, library versions). `adopted` is
true when the result was made before this folder existed and taken over.

## Locks

`<session folder>/.lock` (JSON: `pid`, `host`, `since`) marks a session being
worked on; a second run skips it. A run's pipeline-wide lock is
`<pipeline>/.lock`. A lock whose process no longer runs on the same computer
is taken over. A lock from another computer is never taken over automatically:
delete it by hand once that run is known to be dead.

## Pipelines

| Pipeline | Written by | Files per session |
| --- | --- | --- |
| `dlc_full_iter1` | `python -m tmaze.tools.dlc_offline` (ADR-0227) | `<sid>_dlc.csv` (DeepLabCut's standard CSV: three header rows, then one row per video frame; the first column is the frame index = the sidecar's `frame_idx`), `<sid>_dlc.h5` (the same table) |
