# ADR 0104: Record HOW water was delivered as an orthogonal water_method field

- **Status:** Accepted
- **Date:** 2026-08-13
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/daily_log.py`,
  `host/tmaze/ui/records_manager.py`, `host/tests/test_daily_log.py`,
  and the sibling `os_code_t_maze_resources/records/animal/build_daily_log.py`
- **Supersedes / Superseded by:** Builds on ADR-0100 (ad-lib water regime) and
  ADR-0076/0085 (daily table + reversible surfaces)

## Context

Water restriction on this rig is delivered three different ways, and which one
was used on a given day is worth recording (it changes how a day's water intake
should be read):

- **timed access** - free water at the bottle for a set duration (e.g. 30 min);
- **measured bottle** - a measured volume provided in a bottle;
- **task earned** - the animal gets its water by running the task (reward only).

The daily table already has a `water_regime` column (ADR-0100), but that answers a
*different* question: whether the day was ON restriction or OFF it (ad-lib, e.g. a
weekend). The welfare dashboard keys real behaviour off `is_ad_lib` - an ad-lib
day suppresses the not-weighed alarm and is excluded from the water-maintenance
trace. Folding the three delivery methods into `water_regime`'s vocabulary would
conflate two independent axes and break that logic: "timed access" is an
on-restriction delivery method, not an off-restriction day, so treating it like
ad-lib would wrongly silence alarms and drop the day from the trace.

## Decision

Add `water_method` as an **orthogonal** column, not a new regime value.

- **Schema v3.** `daily_log.py` appends `water_method` LAST (after
  `water_regime`), bumps `SCHEMA_VERSION` to 3, and adds `WATER_METHODS`
  (`timed_access`, `measured_bottle`, `task_earned`) with a loose
  `normalize_method` (synonyms like `timed`/`bottle`/`task` fold to canonical;
  blank/unknown -> `""`), mirroring `normalize_regime`. `validate_entry` rejects a
  non-blank method that doesn't normalise. As with v2, old readers ignore the new
  column and a v1/v2 CSV reads `water_method` as `""` (no forced migration).
- **Records Manager.** The Daily form gains a `Water method` dropdown; Save
  stores the canonical token. The "Mark ad-lib span" dialog is generalised to
  "Mark span": it can set the regime AND/OR the method across a date range (each
  independently leave-unchanged), so it still one-click-marks a weekend ad-lib
  and can now bulk-tag a training block's delivery method. The Daily table shows
  a `Method` column.
- **Sibling builder mirror.** `build_daily_log.py` mirrors the schema (v3 header,
  `water_method` column + Entry-sheet dropdown), a `_method` normaliser, and a
  `validate_entry_rows` method check, so the Excel `--export` round-trip stores
  the same normalised token and rejects the same garbage (ADR-0085 reversibility).

## Alternatives considered

- **Extend `water_regime`'s vocabulary with the three methods.** Rejected: it
  conflates "on vs off restriction" with "how water was delivered." A restriction
  day delivered by timed access is still on restriction; giving it a regime value
  would break the ad-lib alarm/trace semantics (ADR-0100). (The user chose the
  orthogonal-column option.)
- **Infer the method from existing columns** (e.g. treat any `water_ml` as
  measured-bottle). Rejected: lossy and wrong - task-earned days also have a
  volume, and timed-access days may not record one. The method is a distinct fact
  the operator knows and should state.
- **Make `timed_access` mean ad-lib.** Rejected explicitly: timed access is an
  on-restriction delivery, so it must not flip the ad-lib semantics.

## Consequences

- The delivery method is captured without disturbing any existing signal: the
  dashboard alarms/traces still key on `water_regime`/`is_ad_lib` only.
- `water_method` is descriptive for now (no automatic volume/intake inference);
  a future change could use it to interpret water accounting per method.
- Both write surfaces (GUI Save, Excel `--export`) normalise and validate the new
  field, so the CSV stays canonical and the round-trip reversible.
- The Daily table is now 14 columns, which reinforces the deferred frozen-column
  idea (FW-0034).

## Evidence / links

- Core: `host/tmaze/core/records/daily_log.py` (`WATER_METHODS`,
  `normalize_method`, `validate_entry`, `FIELDS`/`SCHEMA_VERSION`).
- GUI: `host/tmaze/ui/records_manager.py` (`_build_daily_tab` method dropdown +
  `Method` column, `_save_daily` normalisation, `_mark_span`/`_apply_span`).
- Tests: `host/tests/test_daily_log.py` (`TestWaterMethod`, method validation) -
  full suite 721 passing.
- Sibling mirror: `os_code_t_maze_resources/records/animal/build_daily_log.py`
  (schema v3, `_method`, Entry dropdown, `--export` normalisation).
