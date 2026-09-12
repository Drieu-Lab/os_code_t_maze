# ADR 0162: Resources builders import the `tmaze` package (delete the mirrored copies)

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/records/operators.py` (`require_operator`),
  resources `records/animal/build_daily_log.py`,
  `records/animal/build_session_training_log.py`,
  `records/animal/records_changelog_mirror.py` (deleted),
  resources `AGENTS.md` / `README.md`
- **Supersedes / Superseded by:** — (completes the Phase 6 deferral in ADR-0155)

## Context

The two Excel round-trip builders live in the resources repo so they sit
next to the files they edit. Until now they could not import the code
repo's `tmaze` package, so every schema bump, every normaliser, the
header/schema guard, the change-log writer and the `--operator` check were
**copied** into `build_daily_log.py` / `build_session_training_log.py` and
into a dedicated `records_changelog_mirror.py`. Phase 2d of the hardening
plan landed that mirror as a stop-gap (ADR-0155): same on-disk lines as
the core, to be deleted the moment the builders could import `tmaze`.

Keeping two copies was already costing: daily_log schema v4 (ADR-0160) had
to land in both repos in the same change, because a v3 builder projecting
onto its own `FIELDS` would have silently dropped the new columns from a
v4 CSV. The next schema bump would have the same trap.

The agreed decision in the hardening plan: the resources builders **may**
import `tmaze`. The package is already installed on the rig
(`pip install -e .` in conda env `drieu_t_maze`).

## Decision

**The builders depend on the `tmaze` package for every records-file
contract. Excel-only code stays in the builders. The mirror is deleted.**

- `build_daily_log.py` imports `tmaze.core.records.daily_log` for
  `FIELDS`, `SCHEMA_VERSION`, `WATER_METHODS`, `NUMERIC_ENTRY_FIELDS`,
  `normalize_regime` / `normalize_method`, `validate_entry`, `upsert_rows`,
  `write_rows`, `load_rows_for_write`; and `records_changelog.record` +
  `operators.require_operator` for the write-and-log path.
- `build_session_training_log.py` imports `session_annotations` for
  `FIELDS` / `SCHEMA_VERSION` / `write_rows` / `load_rows_for_write`, and
  the same changelog + operator helpers.
- A missing `tmaze` is a **clear `SystemExit`** at import time (how to
  install it, nothing written). No silent `sys.path` hack, no fallback
  copy.
- `operators.require_operator(name, what)` moves into the code-repo core
  (was only in the mirror): blank name is refused; when
  `host/operators.json` is readable the name must be on the list; when
  the file is absent or unreadable any non-blank name is accepted, so a
  builder run on a machine that can import `tmaze` but whose `host/`
  folder is not the live one still writes. The builders catch the
  `ValueError` and exit.
- Change-log writes pass an explicit path next to the script
  (`records/animal/records_changes.csv`) so a non-default resources
  checkout still logs beside its own CSVs.
- Entry / View / Annotations sheet layout, openpyxl styling, the legacy
  seed/import, and the session-training View assembly stay in the
  builders: those are Excel surfaces, not records contracts.

## Alternatives considered

- **Keep the mirror, bump it by hand.** Rejected: ADR-0160 already showed
  the two copies drift, and a drifted builder silently drops columns.
- **Move the builders into the code repo.** Rejected: they belong next to
  the workbooks they regenerate; importing the package is enough coupling.
- **Add the code repo to `sys.path` from the builder.** Rejected: that
  hides a missing install and picks up whatever checkout happens to sit
  next door, dirty or not. An editable install is the one documented
  setup.
- **Require `operators.json` even when it is unreadable.** Rejected:
  keeps the Phase 2d behaviour so a laptop that can import `tmaze` from
  site-packages but does not have the live `host/` tree can still
  attribute an export under any non-blank name.

## Consequences

- A schema bump in `daily_log.py` or `session_annotations.py` is picked up
  by the next builder run; no second edit in the resources repo except
  when the Entry sheet itself needs a new column letter or header label.
- Running a builder without `tmaze` installed fails immediately with
  install instructions, instead of writing a silently-wrong CSV.
- `records_changelog_mirror.py` is gone; anyone who still has a local
  import of it must use the package.
- The Records Manager's "Import from Excel" subprocess inherits the same
  env (it already launches with the `drieu_t_maze` interpreter).

## Evidence / links

- ADR-0155 (the mirror as a temporary writer), ADR-0160 (the lockstep
  schema-v4 landing that motivated this).
- Records Manager hardening plan, Phase 6.
