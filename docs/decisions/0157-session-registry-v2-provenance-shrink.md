# ADR 0157: Session registry v2 - a self-describing build that can be refused

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/session/session_registry.py` (`SCHEMA_VERSION`
  2, `BuildMeta`, `ShrinkReport`/`shrink_check`, `registry_ids`,
  `registry_meta`, `scan_roots_with_root`, `bundle_path`, `root` column),
  `host/tmaze/tools/build_session_registry.py` (`build_meta`, `_file_sha`,
  `_code_commit`), `host/tmaze/ui/records_manager.py` (reads bundle paths via
  `bundle_path`), `docs/records-schema.md`, `host/tests/test_session_registry.py`,
  `host/tests/test_build_session_registry.py`
- **Supersedes / Superseded by:** — (extends ADR-0081 and ADR-0113)

## Context

`session_registry.csv` is a full rebuild every time (ADR-0081): whatever the
scan finds *is* the registry. That is what makes it trustworthy - no stale rows,
no double counting - but it also means the file has **no memory**. Two things
followed from that during the 2026-09-11 records review:

1. **Nothing said what a build was made from.** The v1 file carried only a
   `# schema_version=1` line. Holding a copy, you could not tell when it was
   built, which folders were scanned (the rig folder alone, or the rig folder
   plus the `Z:` archive; ADR-0113/0156), which `daily_log.csv` shaped its
   husbandry join, or which `session_ignore.json` dropped rows. When the
   committed registry turned out to index only a third of the ~110 real
   sessions, the file itself gave no clue why.
2. **A build that LOSES sessions is accepted silently.** If the archive drive
   is not mounted, or a root is mistyped, the scan simply finds fewer bundles
   and the rebuild writes a smaller file over the larger one. The downstream
   View sheets and the Records Manager's session list then shrink too, with no
   warning. For a scientific record this is the wrong default: a session that
   vanishes from the index should be a *question*, not an outcome.

A smaller wart: `bundle` was one absolute path. With sessions spread over two
roots, "which root did this come from" - useful to spot a copy that is still
sitting on the rig disk after being archived, or to re-point a root that moved -
was recoverable only by string-prefix guesswork.

## Decision

Bump `session_registry.csv` to **schema v2** (the tags file's version number
follows; its columns are unchanged) with three changes:

1. **A provenance header.** After `# schema_version=2` the file carries
   `# key=value` comment lines written from a `BuildMeta`: `built_at` (local
   ISO time), `code_commit` (the code repo's short sha, `+dirty` when the tree
   had uncommitted changes, or `unknown` without git), `roots` (a **JSON list**
   of the folders scanned, in scan order - JSON so Windows backslashes and any
   separator inside a path round-trip unambiguously), and one `<input>_sha`
   line per joined input (`daily_log`, `note_tags`, `session_ignore`; a 12-hex
   content hash or `absent`). Every existing reader already skips leading `#`
   lines (the shared `read_commented_csv`, the resources builder's
   `read_registry`), so adding lines there is backward-compatible;
   `registry_meta(text)` parses them back for tools that want them.

2. **`bundle` becomes root-relative and a `root` column is appended.** Each row
   records (which root, which folder under it) rather than one absolute path.
   The core's `scan_roots_with_root` keeps the ADR-0113 rule (first-listed root
   wins a duplicate id) and now also reports *which* root won. A pure helper
   `bundle_path(row)` re-joins the two - and still returns a v1 row's absolute
   `bundle` untouched - so the Records Manager's summary panel and "Open bundle
   folder" work on either version. `root` is appended after `bundle` so a
   v1-shaped reader that indexes by column name is unaffected.

3. **A pure shrink check.** `shrink_check(previous_ids, new_ids)` returns a
   `ShrinkReport` (`vanished`, `added`, `.ok` = nothing vanished);
   `registry_ids(text)` extracts the id column from the previous file. The core
   only *reports*; deciding to refuse is the shell's job (Phase 3c wires the
   command-line tool to stop unless `--allow-shrink`, and the GUI to show the
   vanishing ids and ask). Growth and no-change are always fine; the only
   suspicious direction is loss.

## Alternatives considered

- **Put the provenance in a sidecar JSON next to the CSV.** Rejected: a copy of
  the CSV would travel without it, which is exactly the situation the header is
  for. The comment-line convention already exists (`# schema_version`), and the
  file stays a plain CSV for spreadsheets and pandas (`comment="#"`).
- **Timestamps instead of content hashes for the inputs.** Rejected: mtimes
  change on a git checkout or a copy without the content changing; a hash says
  whether the *data* the build joined against was the same.
- **Keep `bundle` absolute and add `root` only.** Rejected: the same fact twice,
  and a root that is re-pointed (a drive letter change, ADR-0156's UNC switch)
  would leave every row's absolute path stale while `root` was right.
- **Make the registry append-only / incremental instead of a full rebuild.**
  Rejected: that trades the shrink problem for the stale-row and double-count
  problems ADR-0081 chose the full rebuild to avoid. The shrink check gives the
  full rebuild the one thing it lacked - a memory of what it last found.
- **Have the core refuse the shrink itself.** Rejected: whether to refuse is a
  policy of the caller (a CLI flag, a GUI dialog); the core stays a pure report
  so it is testable and reusable.

## Consequences

- The next build writes a v2 file. `docs/records-schema.md` documents the header
  and the `bundle`/`root` pair; the resources `build_session_training_log.py`
  needs no change (it skips `#` lines and does not read `bundle`).
- `code_commit` makes an uncommitted-code build visible as `+dirty` in the file
  - a gentle nudge to commit before generating the records artifacts.
- The shell now spends a few ms hashing three small files and running two git
  commands per build; both are best-effort and never fail the build.
- The offline analysis repo (`drieu_lab_analysis`) does not read the registry
  (it scans bundles directly), so nothing there changes; the `bundle`/`root`
  semantics are recorded in the shared schema doc for when it does.
- Phase 3c builds on this: shrink refusal in the tool and GUI, a "N bundles
  newer than the registry" banner using `built_at`, and the supervised Generate
  that first indexes the whole ~110-session history.

## Evidence / links

- The committed registry at review time indexed ~1/3 of the real sessions
  because the archive root was configured in neither the GUI nor the CLI
  (ADR-0156 has the counts).
- ADR-0081 (full idempotent rebuild), ADR-0088 (scan/output split), ADR-0113
  (archive roots, first-listed wins), ADR-0155 (records history), ADR-0156
  (tracked roots config).
- Records Manager hardening plan, Phase 3b.
