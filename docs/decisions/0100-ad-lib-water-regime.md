# ADR 0100: Mark ad-lib (off-restriction) days in the daily table + teach the dashboard

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/daily_log.py` (new `water_regime`
  column, schema v1 -> v2, `normalize_regime` / `is_ad_lib`, `validate_entry`),
  `host/tmaze/core/records/dashboard.py` (`weight_status` / `not_logged_today` /
  `weight_trend` / `water_ml_trend` interpretation),
  `host/tmaze/ui/records_manager.py` (Regime control + "Mark ad-lib span" dialog +
  dashboard status rendering), `os_code_t_maze_resources/records/animal/build_daily_log.py`
  (Entry-sheet column + dropdown + schema bump). Implements FW-0030.
- **Supersedes / Superseded by:** — (a follow-up to
  [ADR-0076](0076-daily-husbandry-table-source-of-truth.md) and
  [ADR-0087](0087-dashboard-trend-charts.md))

## Context

Water is available **ad libitum on weekends** (typically Friday evening through
Sunday afternoon): the rats are off water restriction for that span. The daily
husbandry table ([ADR-0076](0076-daily-husbandry-table-source-of-truth.md)) and
the welfare dashboard ([ADR-0087](0087-dashboard-trend-charts.md)) had no way to
model this, so an ad-lib weekend day misread two ways:

1. It looked like a **"not logged / not weighed" alarm** — there is no
   restriction data to enter, yet the dashboard flagged the blank as a forgotten
   entry.
2. Once the optional water trace (ADR-0087) is on, a weekend showed either a
   spuriously large water bar or a gap that **distorts the read of the
   maintenance water level** — the amount that holds an animal at target
   *during restriction*. Scientifically, maintenance-level inference should be
   computed over restriction days only; an ad-lib day is a different regime.

The clinically relevant detail is not just *which* days are ad-lib but *when
within the boundary days* free water started/ended (Fri ~5pm on, Sun ~2pm off).

## Decision

Give the daily table an explicit **`water_regime`** column (daily_log schema
**v1 -> v2**), appended last so v1 readers that project onto their own `FIELDS`
ignore it and no existing Excel column letters / formulas shift. Blank or any
restriction synonym means the default (on restriction); the single recognised
off-restriction token is **`ad_lib`** (loosely matched: `ad-lib`, `Ad Lib`,
`adlib`). `normalize_regime` canonicalises a cell to `"ad_lib"` or `""` so the
column stays blank except on ad-lib days; `is_ad_lib(row)` is the one predicate
the dashboard and builder share. `validate_entry` rejects an unrecognised
non-blank regime (a typo) rather than silently treating it as restriction.

The **within-day boundary time reuses the existing `water_on` / `water_off`
columns** rather than adding new fields: a Friday row is `water_regime=ad_lib,
water_on="5pm"`; the Sunday row is `water_regime=ad_lib, water_off="2pm"`; fully
ad-lib middle days carry neither. This lines up with FW-0029 (real HH:MM water
timestamps) later.

The dashboard treats ad-lib days distinctly (all four signals):

- **not-logged/not-weighed alarm** — an `ad_lib` *row* never raises "not
  weighed" (an off-restriction day is expected to have no restriction data). A
  day with *no row at all* still reads "not logged", so the operator marks the
  weekend for the alarm to fall silent.
- **water-maintenance trace** — ad-lib days are **excluded** entirely (they are
  not maintenance-level intake).
- **% baseline trend** — ad-lib days **still plot** (weight is meaningful when
  recorded) and carry an `ad_lib` flag per point so the GUI can style them.
- **today's status line** — renders "ad-lib (off restriction)" (neutral blue)
  instead of a red no-weight alarm; shows % baseline too if a weight exists.

Entry ergonomics: the Records Manager adds a **Regime** dropdown on the Daily
form and a **"Mark ad-lib span"** dialog (animal + start/end date + optional
on-time on the first day / off-time on the last), which upserts one `ad_lib` row
per day across the span — preserving any existing weight — so a whole weekend is
marked in one action instead of a row-per-day chore. The Excel Entry sheet gains
the matching `restriction`/`ad_lib` dropdown; `--export` normalises it the same
way, keeping the CSV<->Excel round-trip reversible (ADR-0085).

## Alternatives considered

- **A recognised note flag (`#adlib`) in the free-text `notes` column** — no
  schema change, but fragile to parse, easy to typo silently, and it overloads a
  human free-text field with machine semantics. Rejected in favour of an
  explicit, validated column.
- **A separate span/interval record (start+end datetime) instead of per-day
  rows** — matches the "set once for the weekend" intent but needs a second
  keyed store and a join to answer "is this date ad-lib?"; the per-(animal,date)
  daily row is already the table's grain, and the span *dialog* gives the
  one-action ergonomics without a new data shape. Chosen: per-row column + span
  helper.
- **New dedicated `adlib_on` / `adlib_off` time columns** — the existing
  `water_on` / `water_off` already mean exactly "when water went on/off"; adding
  parallel columns would duplicate that and complicate FW-0029. Reused instead.
- **Drop ad-lib days from the % baseline trend too** — weight is still a valid
  welfare signal on a weekend, so only the *water* trace excludes them.

## Consequences

- Makes it easy to mark a weekend and have the dashboard read it correctly; the
  maintenance-water trace (ADR-0087) is no longer distorted by free-water days,
  which is a prerequisite for turning that overlay on by default later.
- The schema bump is backward-compatible in both directions: a v1 CSV (no
  column) loads with `water_regime=""` (restriction); a v2 CSV read by older code
  simply ignores the extra column. Existing data files upgrade to the v2 header
  on their next natural write/export — no forced migration is performed here.
- The Excel Entry sheet only gains the new column when the workbook is
  regenerated (`build_daily_log.py --rebuild`); until then the GUI is the surface
  that writes the column. That matches the current "GUI is primary, Excel is a
  fallback" posture.
- We would revisit this if the lab needs sub-day regime changes that the two
  boundary-time columns can't express, or if ad-lib turns out to warrant its own
  first-class span record for analysis.

## Evidence / links

- Backlog item: `docs/future-work.md` FW-0030 (now Done).
- Related: [ADR-0076](0076-daily-husbandry-table-source-of-truth.md) (the daily
  table + schema), [ADR-0077](0077-pre-session-animal-state-in-bundle.md)
  (pre-session state), [ADR-0085](0085-records-manager-gui-reversible-surfaces.md)
  (reversible CSV<->Excel surfaces), [ADR-0087](0087-dashboard-trend-charts.md)
  (the water trace this qualifies).
- Follow-up: FW-0029 (real HH:MM water timestamps -> `hours_since_water`), which
  will make `water_on` / `water_off` real times and reach into `animal_state` /
  Session Start.
- Tests: `host/tests/test_daily_log.py` (`TestWaterRegime`, regime cases in
  `TestValidateEntry`), `host/tests/test_dashboard.py` (`TestAdLibRegime`).
