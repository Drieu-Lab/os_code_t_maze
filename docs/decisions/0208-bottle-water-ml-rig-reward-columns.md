# ADR 0208: Daily log v5 - `bottle_water_ml`, rig-reward columns, always-sum water total

- **Status:** Accepted
- **Date:** 2026-10-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/daily_log.py` (`FIELDS`, `SCHEMA_VERSION`,
  `LEGACY_FIELD_NAMES`), `host/tmaze/core/records/dashboard.py`
  (`collect_water`, `WATER_SOURCES`), `host/tmaze/core/records/records_audit.py`
  (`bottle_water_on_task_day`), `host/tmaze/core/records/animal_state.py`,
  `host/tmaze/ui/records_manager.py` (Daily tab), `docs/records-schema.md`;
  resources repo `records/animal/build_daily_log.py` + `daily_log.csv`; analysis
  repo `src/tmaze_analysis/daily_log.py`
- **Supersedes / Superseded by:** Supersedes ADR-0106. Builds on ADR-0084 (the
  four water channels), ADR-0104 (`water_method`), ADR-0155 (header guard,
  change log), ADR-0160 (the v4 numeric supplement cells)

## Context

The daily table's `water_ml` column meant two different things depending on
the day:

- on a `measured_bottle` day it was the measured bottle volume (e.g. 7 / 10 /
  5 mL on 2026-09-05..07);
- on a `task_earned` day operators had typed the rig's task water into it (rat
  602, 2026-08-10..13). That is the same water the rig already records per
  session in `session_registry.csv` (`task_reward_ml` / `manual_reward_ml`).

ADR-0106 handled the second case by letting the typed number "win" on a
`task_earned` day and dropping the rig reward from the total. That kept the
total right only when the method was set correctly. It missed rat 600 on
2026-08-12 (8.24 mL typed, method blank, rig total 8.239 mL), which was counted
twice. It also meant the Records Manager's Daily table never showed the rig's
own figure, so typing it in by hand looked like the only way to record it.
From September on, every `task_earned` row has a blank `water_ml`, so in
practice the column had already become bottle-only.

The operator asked for the earned water (scored + manual) in the Daily table,
plus a total, and for the column to be renamed to say what it holds.

## Decision

1. **Rename `water_ml` to `bottle_water_ml` in place (schema v5).** Same
   position in `FIELDS`, so the Excel Entry sheet's column letters don't move.
   It means water given **outside the task only** (a measured bottle or cage
   water); task water is never typed there. `daily_log.LEGACY_FIELD_NAMES`
   maps the old header name to the new one on read, BEFORE the ADR-0155 header
   guard, so a v1-v4 file still loads (rather than being refused as a renamed
   column) and is rewritten as v5 on its next save. The analysis repo's reader
   maps the same name.
2. **Show the rig's water in the Daily table.** New read-only columns Task mL
   and Man mL (after % base) and Total mL (after the supplement columns),
   computed from the session registry through `dashboard.collect_water`, so
   the table and the dashboard bars always agree.
3. **Always add all four sources.** `collect_water` no longer looks at
   `water_method`: task + manual + supplemental + bottle. The source formerly
   called `cage` is now `bottle`.
4. **Flag the mistake instead of guessing around it.** A new records-audit
   section, `bottle_water_on_task_day`, lists any numeric `bottle_water_ml` on
   a day whose method is `task_earned`, or that is within
   `BOTTLE_MATCHES_RIG_ML` = 0.05 mL of that day's rig reward (task + manual).
   0.05 mL is below any real bottle measurement but covers the rounding of a
   hand copy.
5. **One-time cleanup of the five known cells**, done through the normal save
   path and logged in `records_changes.csv` under operator RJ (operator
   confirmed all five were task water):

   | Animal | Date | `water_ml` cleared | rig task + manual |
   | --- | --- | --- | --- |
   | 602 | 2026-08-10 | 4.52 | 4.40 + 0.12 |
   | 602 | 2026-08-11 | 6.40 | (session registry) |
   | 602 | 2026-08-12 | 4.57 | (session registry) |
   | 602 | 2026-08-13 | 4.23 | 4.68 + 0.78 |
   | 600 | 2026-08-12 | 8.24 | 8.239 |

   Rat 600's 2026-08-12 row also gets `water_method = task_earned`, which it
   had been missing.

## Alternatives considered

- **Keep ADR-0106's "entered value wins" rule.** Rejected by the operator: it
  depends on the method being set (it missed 600 on 08-12), and it hides the
  rig's figure behind a hand copy.
- **Rename only the column heading, keep the stored name `water_ml`.** Smaller,
  but the CSV, the Excel Entry sheet and the analysis frame would keep a name
  that doesn't say what the column holds. The operator chose the full rename.
- **Append a new `bottle_water_ml` column and retire `water_ml` later** (the
  ADR-0160 pattern). Rejected: the values don't need splitting, only renaming,
  and a second column would leave two places to type the same number until the
  old one is dropped.
- **Store the rig's task/manual water in the daily CSV.** Rejected: a second
  copy of a number the session registry already owns, which can drift from it.
- **Leave the August cells for the operator to clear** (the ADR-0097 default).
  The operator asked for it to be done in this change; the audit flag stays to
  catch the same mistake in future.

## Consequences

- The Daily table shows where each day's water came from and its total, read
  from the same source as the dashboard.
- A day's total can be counted twice if someone types task water into Bottle
  mL; the audit flags it rather than the code silently correcting it.
- Readers of the daily CSV outside these three repos (if any appear) must
  accept both names until every copy has been saved as v5.
- `animal_state.json`'s `measured_water_ml` (frozen at Session Start) now
  comes from `bottle_water_ml`; the bundle key is unchanged.
- Weight and water trends across August are still affected by the weekend
  ad-lib days (marked `ad_lib`, so left out of the water trend but still on
  the weight trend); this change does not alter that.

## Evidence / links

- Live `daily_log.csv` read on 2026-10-01: from 2026-09-01 every
  `task_earned` row has a blank `water_ml`; the only numbers are on
  `measured_bottle` days plus the five August cells above.
- `python -m tmaze.tools.records_check` before the cleanup: the new section
  listed exactly those five rows (600 on 08-12: "equals the rig reward 8.239
  mL").
- Tests: `host/tests/test_daily_log.py` (`TestBottleWaterV5`),
  `test_dashboard.py` (`test_four_sources_always_sum_whatever_the_method`),
  `test_records_audit.py` (`TestBottleWaterOnTaskDay`), `test_animal_state.py`;
  analysis repo `tests/test_analysis_daily_log.py`.
