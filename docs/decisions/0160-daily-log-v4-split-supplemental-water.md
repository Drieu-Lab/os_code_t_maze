# ADR 0160: Split the free-text supplemental-water cell into millilitres and minutes (daily_log v4, registry v3)

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/records/daily_log.py` (`FIELDS`,
  `SCHEMA_VERSION`, `SUPP_FIELDS`, `supp_water_ml`, `supp_access_min`,
  `supp_unsplit`, `NUMERIC_ENTRY_FIELDS`, `validate_entry`),
  `host/tmaze/core/records/dashboard.py` (`collect_water`),
  `host/tmaze/core/records/records_audit.py` (`unsplit_supp_water` section),
  `host/tmaze/core/session/session_registry.py` (`SCHEMA_VERSION` 3, two
  columns), `host/tmaze/core/session/session_detail.py` (`supp_text`),
  `host/tmaze/ui/records_manager.py` (Daily form/table),
  resources `records/animal/build_daily_log.py` (schema, Entry sheet),
  `docs/records-schema.md`
- **Supersedes / Superseded by:** — (refines ADR-0076; extends ADR-0159)

## Context

The daily husbandry table (ADR-0076) has one cell for supplemental water,
`supp_water`, and it is free text. The paper form it mirrors records
supplemental water two different ways: sometimes as a **volume** the rat was
given by hand ("2.5 ml"), sometimes as an **access duration** at a bottle
("2 min", "30 min bottle"). A free-text column can hold either, so nothing
downstream can add it up: the dashboard's `collect_water` parsed `supp_water`
as millilitres when it happened to be a bare number and silently counted 0
otherwise. A "3" could be 3 mL or 3 minutes; nothing recorded which.

In the live file on 2026-09-11 the column is in fact **empty** (166 rows, no
`supp_water` value); the mixed volumes/notes the review found (34 cells) were
typed into `water_on`/`water_off` instead, where `suspect_water_cells`
already flags them. The problem is therefore the column's *definition*, not
yet its contents — the right moment to fix the definition, before the split
form starts being used and the ambiguity becomes data.

Everything downstream wants a number with a unit: the welfare dashboard's
daily water total, the session registry's water channels (ADR-0081), and
eventually the analysis repo's join onto neural data.

## Decision

**Two numeric cells replace one text cell, and the text cell is kept until a
human has split it.**

- `daily_log.csv` goes to **schema v4**: `supp_water_ml` (millilitres
  hand-given) and `supp_access_min` (minutes of free bottle access) are
  appended LAST, after `water_method`, so existing column letters in the
  Excel Entry sheet and any older reader are unaffected (the same rule as
  v2/v3). Both are validated like `weight_g`: blank is fine, non-blank must
  be a number ≥ 0 — on the GUI Save and on the builder's `--export` alike.
- The legacy `supp_water` column **stays in the file, verbatim**. No writer
  parses or rewrites it; the GUI shows it read-only next to the two new
  fields with a "Clear" button, and the Entry sheet greys it. An operator
  loads the row, types the value into the right numeric cell, clears the
  legacy text, saves. A later v5 drops the column once it is empty.
- **No reader guesses a split.** `daily_log.supp_water_ml(row)` reads only
  the v4 cell; `dashboard.collect_water` counts only that, so an unsplit
  legacy cell contributes 0 mL and the audit says so rather than the
  dashboard inventing a number. `session_detail.supp_text` shows the v4
  values when present, else the legacy text — display, not arithmetic.
- The **records audit** gains an `unsplit_supp_water` section
  (`daily_log.supp_unsplit`: legacy text present, both v4 cells blank) with
  a per-row stable key. It is the to-do list for the one-time split and
  shrinks to nothing as cells are moved.
- `session_registry.csv` goes to **schema v3**: `supp_water_ml` and
  `supp_access_min` are inserted right after the legacy `supp_water` column
  (the daily-table join fills all three), so the registry carries the same
  three views of the fact as the table.
- The resources builder `build_daily_log.py` moves to v4 in the **same
  change**: it projects every row onto its own `FIELDS` when it rewrites the
  CSV on `--export`, so a v3 builder running against a v4 file would have
  silently dropped the two new columns. Its Entry sheet gains the two
  numeric columns (with the ≥ 0 validation) and labels the legacy one
  "Supp. (legacy text)".

## Alternatives considered

- **Parse the legacy text heuristically ("2 min" → minutes, "2.5 ml" →
  mL).** Rejected: the ambiguous cases ("3", "1.5") are exactly the ones a
  heuristic gets wrong, and welfare records must not carry guessed numbers.
  A human split with the audit as checklist is small (today: zero cells).
- **One numeric cell plus a unit column.** Rejected: a day can have both a
  hand-given volume and bottle access; two cells record both, and every
  aggregate wants millilitres directly.
- **Replace `supp_water` in place (rename + convert in one migration).**
  Rejected: the conversion can't be automatic (above), and dropping the
  column before the split would lose the only record of those days.
- **Keep the registry at v2 and only add the columns.** Rejected: a column
  set change is exactly what `schema_version` is for; readers branch on it.

## Consequences

- `daily_log.csv` and `daily_log.xlsx` upgrade to v4 on their next natural
  write/`--rebuild`; no forced migration, no data changed. The other author's
  uncommitted workbook copies in the resources repo were not touched.
- Until a cell is split, that day's supplemental water reads as 0 mL on the
  dashboard — the same as before for non-numeric text, and now the audit
  lists every such day. A bare number in `supp_water` that was previously
  counted as mL would now read as 0 until split; deliberate (the number's
  unit was never recorded), and moot for the live file, whose legacy column
  is empty — the `unsplit_supp_water` section reports "none" today and the
  v5 drop of the column can follow as soon as the lab confirms no paper
  values remain to be back-filled.
- The next registry Generate rewrites the file at v3 (same session set, two
  new columns); the shrink check (ADR-0158) is unaffected.
- The Records Manager Daily form gains two fields and a read-only legacy
  display; the Daily table shows `Supp mL` / `Supp min` / `Supp (legacy)`.
- `date_of_birth` for `animals.json` (the other Phase 5 item) is a separate
  ADR.

## Evidence / links

- 2026-09-11 records review: `records_check_2026-09-11.txt` (34 water cells
  holding volumes/notes instead of clock times); `dashboard.collect_water`
  read `supp_water` with `_num()`. Live `daily_log.csv` on 2026-09-11: 166
  rows, `supp_water` blank in all of them; `records_check` after this change:
  `unsplit_supp_water` = none, total still 72 findings.
- ADR-0076 (daily table), ADR-0100/0104 (the v2/v3 append-last precedent),
  ADR-0081/0157 (registry), ADR-0097/0159 (audit), ADR-0085 (reversible
  CSV↔Excel surfaces).
- Records Manager hardening plan, Phase 5a/b.
