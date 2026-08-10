# ADR 0085: Records Manager GUI over reversible CSV/Excel record surfaces

- **Status:** Accepted
- **Date:** 2026-08-07
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/records_manager.py`; `host/tmaze/core/records/daily_log.py`, `session_annotations.py` (new), `animals.py`, `dashboard.py` (new); resources repo `records/animal/build_daily_log.py`, `build_session_training_log.py`; `records/animal/session_annotations.csv` (new source of truth)
- **Supersedes / Superseded by:** — (builds on ADR-0075, 0076, 0082)

## Context

The records upgrade (ADR-0075/0076/0077/0081/0082) made the lab's data
machine-readable: `animals.json` (identity), `daily_log.csv` (daily husbandry),
`session_registry.csv` (generated session index), and `session_training_log.xlsx`
(a generated per-session view with a hand-edited "Annotations" sheet). But day to
day the operator still had to hand-edit CSVs or click through builder scripts, and
the one qualitative thing a human adds per session (common procedures + notes)
lived *only* inside an Excel sheet.

Two forces pulled in opposite directions. A GUI would make daily entry, roster
edits, and a welfare check-in fast and hard to get wrong. But the operator also
needs to keep records **remotely / without running the GUI on the rig PC** - i.e.
Excel must stay a working fallback. A naive GUI that treats its own writes as
authoritative (or a builder that blindly overwrites a CSV from a sheet) would make
the two surfaces clobber each other.

## Decision

Add a Tk desktop app, `tmaze.ui.records_manager` (launched
`python -m tmaze.ui.records_manager`), as a **thin shell** over pure record cores,
and make the two write surfaces **reversible and non-destructive**.

- **CSVs stay the single sources of truth.** `daily_log.csv`,
  `session_annotations.csv` (new - promoted out of the Excel-only Annotations
  sheet; keyed by `session_id`, `# schema_version=1`, mirrors `daily_log.csv`'s
  shape), and `animals.json`. The GUI writes these directly through the cores.
- **Every write is a keyed merge, never a blind overwrite.**
  `daily_log.upsert_rows` / `session_annotations.upsert_rows` read the current CSV
  and apply only the touched rows (by `(animal_id, date)` / `session_id`), so a row
  another surface added survives. `animals.write_records` validates
  (blank/ambiguous ids, alias collisions) before an atomic write.
- **Excel remains a hand-editable fallback, reconciled by merge.** The resources
  builders gained non-destructive directions: `build_daily_log.py --export`
  *upserts* the Entry sheet into the CSV (was a full overwrite); `--rebuild`
  regenerates the workbook as a CSV mirror; `build_session_training_log.py
  --import-annotations` upserts the Annotations sheet into
  `session_annotations.csv` (and migrates it one-time when the CSV is first
  absent). `--import-legacy` folds the legacy `water_restriction_log.xlsx` into the
  CSV filling only blanks and *reporting* conflicts.
- **Two one-click actions** wire truth <-> artifacts: **Generate files** (rebuild
  the registry in-process via `build_session_registry.main`, then run the two
  resources builders as subprocesses off the UI thread) and **Import from Excel**
  (the two `--export`/`--import-annotations` merges).
- **Dashboard reads, never writes.** Four welfare signals from a new pure
  `dashboard` core: % baseline + <90% flag + grams-to-90% deficit; a
  not-logged/not-weighed-today alarm; water intake by source (task/manual from the
  registry, supplemental/cage from the daily row; totals only, no target flag);
  and a 14-day weight trend.

## Alternatives considered

- **GUI writes Excel directly (no CSV middle).** Rejected: the rig already reads
  `daily_log.csv`, and a binary `.xlsx` is neither diffable nor stdlib-readable;
  keeping the CSV authoritative preserves ADR-0076.
- **Make Excel read-only (GUI is the only input).** Rejected: the operator
  explicitly needs remote/no-GUI record-keeping, so the workbook must stay
  hand-editable; hence the merge-both-ways design.
- **Blind overwrite on `--export` (simplest).** Rejected: it would drop rows the
  GUI wrote straight to the CSV since the workbook was last built. Upsert is the
  cost of dual surfaces; the one accepted gap is that a *deletion* in Excel isn't
  propagated (documented - clear the cells or edit the CSV to drop a row).
- **A web app / notebook.** Rejected: the rig is a single Windows PC already
  running Tk GUIs; a Tk app matches the deployment and needs no new stack.

## Consequences

- Daily entry, annotations, roster edits, and a welfare check-in are now
  point-and-click, while the CSVs stay authoritative and diffable and Excel stays
  a genuine fallback. The GUI degrades gracefully if the sibling resources repo is
  absent (saves still target the resolved path; Generate/Import report the missing
  builders rather than crashing).
- Reconciliation is upsert-based, so **deletions don't cross surfaces** and a
  human must resolve reported legacy-import conflicts. This is the deliberate
  trade for never silently losing an edit.
- New moving parts to keep in step: `session_annotations.csv`'s schema is now
  shared by a code-repo core and a resources builder (both carry
  `schema_version`); the GUI shells out to the resources builders, so their CLI
  flags (`--rebuild`, `--export`, `--import-annotations`, `--import-legacy`) are an
  interface.
- Because the GUI launches the resources builders with *this* env's interpreter
  (`sys.executable`), the `drieu_t_maze` env must carry the builders' dependency
  `openpyxl` even though the `tmaze` package never imports it. It is therefore
  declared in `environment.yml` and `host/requirements.txt`, and `_run_script`
  appends an install hint if a builder ever fails with a missing `openpyxl`.
- The pure cores are headlessly unit-tested (`host/tests/test_daily_log.py`,
  `test_session_annotations.py`, `test_animals.py`, `test_dashboard.py`); the
  resources builders are validated by running them (that repo has no unit-test
  harness), and the GUI by an import + headless construct/refresh smoke check.

## Evidence / links

- Cores + tests: `host/tmaze/core/records/{daily_log,session_annotations,animals,dashboard}.py`,
  `host/tests/test_{daily_log,session_annotations,animals,dashboard}.py`.
- Shell: `host/tmaze/ui/records_manager.py`.
- Builders: `os_code_t_maze_resources/records/animal/{build_daily_log,build_session_training_log}.py`.
- Builds on ADR-0075 (identity), ADR-0076 (daily table), ADR-0082 (session
  training view / Annotations sheet).
