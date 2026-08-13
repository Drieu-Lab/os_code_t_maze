# ADR 0103: Weekly-structure cues in the Records Manager Daily table

- **Status:** Accepted
- **Date:** 2026-08-13
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/records_manager.py`
- **Supersedes / Superseded by:** Builds on ADR-0102 (dashboard weekly view) and
  ADR-0100 (ad-lib water regime)

## Context

The Daily-entry tab's "recent rows" table showed the raw `daily_log` columns as
plain text. Reading the weekly husbandry rhythm off it was hard: nothing marked
which rows were weekends or ad-lib (off-restriction) days, a below-90% weight sat
in the table looking like any other number, day-over-day weight change had to be
computed by eye, and a *forgotten* day simply wasn't there - an absent row is
invisible, so a missed entry looked identical to a day that hadn't happened yet.
ADR-0102 added pure helpers (`weekday_label`, `weight_delta`, `missing_days`,
plus the ad-lib/weekend span logic) precisely so this table could surface the
same structure the dashboard trend now shows.

## Decision

Enrich the Daily table (display-only; the CSV/source of truth is unchanged):

- **Two derived columns.** A `Day` weekday label (from `weekday_label`) next to
  the date, and a `Δ g` day-over-day weight change (`weight_delta`, signed,
  compared to the last *weighed* day so blank days don't reset it). `% base` is
  now rendered as a percentage rather than a raw fraction.
- **Row tints.** Weekend rows get a faint grey background; ad-lib rows get an
  orange background (applied after weekend so ad-lib wins when a weekend day is
  also ad-lib); a below-90% weight sets a red foreground (an independent channel,
  so it composes with either background). A small legend row explains the colours.
- **Missing-entry placeholders.** When the table is filtered to a single rat,
  dates in the last `_DAILY_MISSING_LOOKBACK_DAYS` (14) with no row are inserted
  as pink "— no entry logged —" placeholder rows, interleaved in date order, so a
  forgotten day is visible. They carry no source row, so double-clicking one does
  nothing (there is nothing to edit yet).
- **Explicit column spec + item->row map.** Because derived columns break the old
  "Treeview values line up 1:1 with `daily_log.FIELDS`" assumption, the table is
  now driven by a `_DAILY_TREE_COLUMNS` spec, and each Treeview item is mapped
  back to its source row dict (`_daily_row_by_iid`) so editing loads the real
  record rather than re-parsing display strings.

## Alternatives considered

- **Show missing days for all rats at once.** Rejected: with the "All" filter,
  interleaving per-rat gaps is ambiguous and noisy; gaps are only meaningful in a
  single-rat view.
- **Flag missing days across all history.** Rejected: old history would fill with
  placeholders. A bounded recent window (14 days) catches the "did I log this
  week?" case without the noise.
- **Keep positional `FIELDS` <-> values mapping and append derived columns at the
  end.** Rejected: it puts `Day`/`Δ g` far from the date/weight they annotate,
  and still needs a lookup to load real rows once display strings diverge from
  stored values (the percent formatting alone breaks re-parsing).
- **Encode tint priority by overwriting a single style.** Rejected: using
  background for weekend/ad-lib and foreground for <90% lets the two compose
  (a below-90% ad-lib day reads as red-on-orange) without special-casing.

## Consequences

- The weekly rhythm, off-restriction days, low-weight flags, and forgotten
  entries are all visible in the table at a glance, matching the dashboard trend.
- Editing now flows through an explicit item->row map, which is also more robust
  than the previous positional zip (adding/reordering columns no longer risks
  loading the wrong field into a form box).
- Trade-off: the missing-day placeholders are single-rat only and bounded to 14
  days; a longer-range gap review still belongs on the dashboard/audit, not here.
- Frozen (pinned) identity columns were considered part of this weekly-cues work
  but are deferred to a follow-up, since a linked-Treeview freeze is a
  disproportionate amount of fragile GUI code; the identity columns are placed
  leftmost as a lighter mitigation.

## Evidence / links

- `host/tmaze/ui/records_manager.py`: `_DAILY_TREE_COLUMNS`,
  `_DAILY_MISSING_LOOKBACK_DAYS`, `_build_daily_tab` (columns/tints/legend),
  `_refresh_daily_table`, `_daily_cell`, `_daily_row_tags`, `_load_selected_daily`.
- Pure helpers reused here: `dashboard.weekday_label`, `dashboard.weight_delta`,
  `dashboard.missing_days` (ADR-0102), and `daily_log.is_ad_lib` /
  `compute_pct_baseline` (ADR-0100).
- Full host suite green (714 tests) after the change.
