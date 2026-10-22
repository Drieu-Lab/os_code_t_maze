# ADR 0226: Derived data lives in its own folder, pipeline first, with a manifest per session

- **Status:** Accepted
- **Date:** 2026-10-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/derivatives.py`, `host/tmaze/io/derivatives_io.py`,
  `host/tmaze/paths.py` (`derivatives_root`), `host/derivatives_root.json`,
  `host/tmaze/tools/derivatives_status.py`, `docs/derivatives_format.md`;
  later `drieu_lab_analysis` (mirrors the spec)
- **Supersedes / Superseded by:** —

## Context

Results are starting to be computed from the session bundles after recording:
first DeepLabCut over every video (ADR-0227), then movement measures (head
direction, speed, maze position) in `drieu_lab_analysis`. Until now such
results were either written into the bundle (`sync/clock_fit_*.json` from
`sync_fit`) or left in scratch folders. Writing into the bundle mixes what the
rig recorded with what was computed later, and a scratch folder gives no way
to tell which sessions are done, which were interrupted and which are out of
date. The rig records ~3 sessions a day, and a full DLC pass takes about an
hour per video, so this has to be tracked by the files themselves, not by memory.

Several pipelines will be written by different repos, so the layout must not
depend on any one tool or on central bookkeeping.

## Decision

1. **A separate tree**, `<root>/<pipeline>/<session_id>/`, outside the bundles.
   The root is `TMAZE_DERIVATIVES_DIR`, else `host/derivatives_root.json`
   (`Z:\Ravi\derivatives`), on the lab share next to the archived bundles.
2. **Pipeline first.** Each pipeline owns one folder and writes only inside it,
   so a new pipeline (in either repo) needs no change anywhere else, and
   deleting or redoing one pipeline can't touch another. The name carries the
   version (`dlc_full_iter1`); a changed model or definition gets a new name
   instead of overwriting.
3. **State from the folder alone.** A `manifest.json` per session, written last
   and atomically (written to a temporary file, then renamed), records the
   inputs (file size + modified time, or the upstream pipeline's
   `finished_at`) and outputs. No folder = pending, no valid manifest = partial,
   inputs changed = stale, else done. "Pending" is computed from the bundle
   scan each time, never stored, so new sessions need no registration.
4. **Inputs are compared by content stamp, not path** (size, modified time
   within 2 s), so archiving a bundle from the rig disk to `Z:` doesn't make
   its results stale. Each file input also records `bundle_rel`, so a generic
   status tool can re-check any pipeline's inputs.
5. **A `.lock` file** per session (and per pipeline run), so two runs never
   work on one session; a lock left by a dead process on the same computer is
   taken over.

The spec others mirror is `docs/derivatives_format.md` (format version 1).
`drieu_lab_analysis` must not import `tmaze`, so it mirrors the spec in its
own code, as it already does for the bundle format.

## Alternatives considered

- **Write results into each bundle** (as `sync_fit` does): it mixes recorded and
  computed data, breaks the "bundle = what the rig wrote" rule for archived
  bundles, and each pipeline would need write access to the archive.
- **Session first** (`<session_id>/<pipeline>/`, BIDS-like): it makes "everything
  about one session" easy, but every new pipeline has to touch every session
  folder, and "how far is pipeline X" means walking the whole tree. Pipeline
  first keeps ownership simple and is what BIDS itself does for derivatives
  (`derivatives/<pipeline>/sub-*/`).
- **A central database or index file** of what was run: one more thing to keep in
  sync, which goes wrong when a run is killed or a folder is copied by hand.
  The manifests are the record.
- **Content hashes of the inputs**: hashing a 14 GB video takes minutes per
  session on the share; size + modified time is what `rsync`/`robocopy` use and
  is enough here.
- **Matching inputs by path**: archiving a bundle would make every result look
  stale.

## Consequences

- A tool that re-runs a session deletes its manifest first, so an interrupted
  re-run shows as partial rather than as done with mixed files.
- If someone edits a bundle file (e.g. re-encodes a video), its results show as
  stale and are redone on the next run. `sync_fit` rewriting `sync/` doesn't
  affect pipelines that don't list those files as inputs.
- A lock from another computer is never taken over automatically; it has to be
  deleted by hand once that run is known to be dead.
- `sync_fit` still writes into the bundle; moving it here is not planned.

## Evidence / links

- `docs/derivatives_format.md` - the spec.
- Tests: `host/tests/test_derivatives.py` (states, staleness by stamp not
  path, moved bundles, upstream re-runs, locks, atomic manifest).
- First use: 7 DLC results adopted into `Z:\Ravi\derivatives\dlc_full_iter1`
  on 2026-10-21; `python -m tmaze.tools.derivatives_status` reports 7 done,
  194 with no result.
