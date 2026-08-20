# ADR 0113: Recordings may live in several locations (primary + archive roots)

- **Status:** Accepted
- **Date:** 2026-08-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/paths.py` (`phase3_recordings_archives`, `phase3_recordings_roots`); `host/tmaze/core/session/session_registry.py` (`scan_roots`, `build`); `host/tmaze/tools/build_session_registry.py` (repeatable `--root`); `host/tmaze/analysis/bundle_reader.py` (`discover_bundles`, `load_animals`); `host/tmaze/ui/records_manager.py` (archive roots pref + "Archives..." dialog)
- **Supersedes / Superseded by:** — (extends ADR-0088; builds on ADR-0049, 0081, 0083, 0112)

## Context

The rig PC's `C:` drive is filling up. The operator has started moving older
Phase 3 recording folders off it - out of
`C:\Users\Celine\Documents\t-maze_recordings\phase3_recordings` and onto a
network/second drive (`Z:\Ravi\phase3_recordings`) - and will keep doing so as
space runs out. New sessions are still written to the `C:` folder first and only
later moved to `Z:`, so at any moment the session history is **split across two
locations**: recent sessions on `C:`, archived ones on `Z:`.

Everything that *discovers* sessions was built around a single root (ADR-0088,
`tmaze.paths.phase3_recordings_root()`):

- `session_registry.scan(root)` and `bundle_reader.discover_bundles(root, ...)`
  each do a non-recursive `os.listdir` of exactly **one** folder.

So with the history split, whichever consumer scans one root sees only half of
it. Concretely:

- The Records Manager "Generate files" (and the `build_session_registry` CLI)
  builds `session_registry.csv` from a single operator-picked folder, so the
  Sessions tab / registry / integrity report / resources workbook all lose
  whichever half isn't scanned.
- The `animal_progress` analysis notebook (`bundle_reader.load_animals`,
  ADR-0112) silently analyses only the sessions in the default `C:` folder,
  dropping the archived ones - a correctness bug with no error.

A secondary, related fact: the registry stores each bundle's **absolute** path in
a `bundle` column. Moving a folder from `C:` to `Z:` leaves that row's path stale
(pointing at the now-missing `C:` location) until the registry is regenerated
with a scan that actually covers `Z:` - at which point the path re-points.

What is *not* a problem: a bundle is self-contained with only relative internal
paths (ADR-0049), so it works wherever it sits; the rig write-side always targets
the `C:` primary (matching the workflow); and the id/date-keyed records
(`daily_log.csv`, `animals.json`, `session_annotations.csv`) are
location-independent, so the Daily/Roster/Dashboard tabs and a moved session's
annotations survive a move untouched.

## Decision

Model recordings as a **search path of roots** - one primary write location plus
zero or more read-only archive locations - resolved once in pure `tmaze.paths`
and consumed everywhere discovery happens.

- **`tmaze.paths` is the single source for the root list.** Keep
  `phase3_recordings_root()` (singular) as the **primary** root the rig writes to
  and the default scan target. Add `phase3_recordings_archives()` (read from a
  new `TMAZE_RECORDINGS_ARCHIVES` env var, `os.pathsep`-separated full
  `phase3_recordings` folders) and `phase3_recordings_roots()` =
  `[primary, *archives]`, order-preserving de-duplicated.
- **De-duplicate by session_id, first root wins.** `session_registry.scan_roots`
  and `bundle_reader.discover_bundles` merge across roots and keep a session only
  once, from the earliest-listed root. During an in-progress archive copy a
  session can momentarily exist in both places; preferring the earlier-listed
  (primary/source) copy means an incomplete destination copy can't shadow the
  good source. After the move completes the session exists only on `Z:` and is
  picked up there.
- **Thread the list through, keep single-root callers working.**
  `session_registry.build` and `bundle_reader.discover_bundles`/`load_animals`
  accept a string OR a sequence. `build_session_registry`'s `--root` becomes
  repeatable and defaults to `phase3_recordings_roots()`. `load_animals(root=None)`
  defaults to the same list, so the notebook (`RECORDINGS_ROOT = None`) picks up
  both locations with no edit.
- **Operator-pickable, persisted archives in the Records Manager.** Alongside the
  existing pickable primary folder (ADR-0088), a new "Archives..." dialog manages
  a list of archive roots, persisted in git-ignored `host/records_manager.json`
  (`recordings_archives`). "Generate files" scans the primary plus every archive,
  so `session_registry.csv` (and every downstream consumer) covers the full
  split history and each row's `bundle` path re-points to wherever the folder now
  lives.

## Alternatives considered

- **Recursive scan of one common parent.** Rejected: the two locations are on
  different drives with different layouts (`...\t-maze_recordings\phase3_recordings`
  vs `Z:\Ravi\phase3_recordings`), so there is no single parent; and a deep walk
  would wander into each bundle's subfolders. An explicit list of roots is
  simpler and predictable.
- **A symlink/junction from `C:` to `Z:`.** Rejected: fragile across machines and
  network mounts, invisible in the tooling, and it wouldn't help the analysis
  reader reason about provenance. Config beats a filesystem trick here.
- **Copy the moved folders back for each analysis.** Rejected: defeats the point
  of freeing disk space.
- **Point the single existing `--root`/GUI folder at `Z:` and lose `C:`.**
  Rejected: that just moves the blind spot - you'd miss the newest sessions still
  on `C:`. The split is inherent to the workflow, so discovery must span both.

## Consequences

- The registry build, the Records Manager Sessions tab / integrity report, the
  resources workbook, and the `animal_progress` notebook all see the complete
  history even while it is split across drives; moved rows' `bundle` paths correct
  themselves on the next Generate.
- Two ways to configure archives: `TMAZE_RECORDINGS_ARCHIVES` (handy for the CLI /
  notebook / other machines) or the Records Manager "Archives..." dialog (pref in
  `host/records_manager.json`). The GUI list and the env var are independent
  inputs; the GUI passes its own primary + archives explicitly to the build.
- **Operational rule:** after moving a bundle to an archive, re-run "Generate
  files" so the registry re-points its `bundle` path. Until then a moved-but-not-
  rescanned session degrades gracefully (its summary/"Open bundle folder" fall
  back rather than erroring), because those read the stored absolute path and
  guard on existence.
- The rig write-side is unchanged (still `C:`-only), so recording is unaffected.
- If the same session_id ever legitimately differs between roots (it shouldn't -
  a session is copied verbatim), the first-root-wins rule silently hides the
  later copy; that is the intended safety behaviour for a mid-copy window.

## Evidence / links

- Code: `host/tmaze/paths.py` (`phase3_recordings_archives`,
  `phase3_recordings_roots`), `host/tmaze/core/session/session_registry.py`
  (`scan_roots`, multi-root `build`), `host/tmaze/tools/build_session_registry.py`
  (repeatable `--root`), `host/tmaze/analysis/bundle_reader.py`
  (`discover_bundles`/`load_animals` accept a root list),
  `host/tmaze/ui/records_manager.py` (archive pref + "Archives..." dialog).
- Tests: `host/tests/test_build_session_registry.py` (two roots merge + de-dup),
  `host/tests/test_analysis_bundle_reader.py` (multi-root discover/load).
- Extends ADR-0088 (single-source recordings root), builds on ADR-0049 (per-session
  bundle tree), ADR-0081 (session registry), ADR-0083 (tmaze package + landmark
  path resolution), ADR-0112 (analysis layer / notebook).
