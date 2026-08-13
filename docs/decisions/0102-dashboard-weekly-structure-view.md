# ADR 0102: Show the weekly free-water rhythm on the dashboard trend

- **Status:** Accepted
- **Date:** 2026-08-13
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/dashboard.py`,
  `host/tmaze/ui/records_manager.py`, `host/tests/test_dashboard.py`
- **Supersedes / Superseded by:** Refines ADR-0087 (dashboard trend charts);
  builds on ADR-0100 (ad-lib water regime)

## Context

The welfare dashboard's combined % baseline trend (ADR-0087) plotted each rat's
points against a *categorical* x-axis: the union of dates that happened to have
data, evenly spaced regardless of the real calendar gap between them. That hides
the thing an operator most wants to see. Restriction runs on a weekly rhythm -
free water ("ad-lib") from roughly Friday evening to Sunday afternoon, weighing
resumes Monday - and weight/water swing predictably around that window
(ADR-0100 added the `water_regime` marking so the data even knows which days
were ad-lib). With a categorical axis, a missing Sunday and a logged Monday sit
one tick apart, indistinguishable from two consecutive logged days, so the
weekend recovery bump and any missed-day gap both disappear.

The old control was also a single "As of" date feeding a fixed 14-day look-back.
The operator could not frame an arbitrary week (e.g. "show me the block around
last weekend") without arithmetic, and the window length was not remembered.

## Decision

Two changes, kept in the pure core so they stay headlessly testable, plus the
thin GUI wiring.

1. **True date x-axis with two-layer weekend shading.**
   `_trend_chart_combined` now plots against real `datetime.date` values
   (`ax.xaxis_date()` + `AutoDateLocator`/`DateFormatter`), so calendar distance
   is honest and gaps read as gaps. Behind the lines it shades two layers:
   - a faint grey **calendar-weekend** reference (Sat-Sun, data-independent) -
     the *expected* free-water window; and
   - solid orange **ad-lib bands** from the data (`water_regime == ad_lib`),
     unioned across the enabled rats so the shared weekend window reads as one
     band while a lone per-rat deviation still shows.
   These come from new pure helpers `weekend_spans(start, end)` and
   `ad_lib_spans(log, animal_ids, start, end)`, which return inclusive
   `(start, end)` runs of contiguous `date`s. A caption spells out the two
   shadings so the chart is self-explanatory.

2. **Explicit From/To date range (replaces the single "As of").**
   The dashboard header now has From and To date entries plus 7/14/28-day
   quick-fill buttons. `_dash_window()` validates both are ISO dates, From <= To,
   and the span is within a `_DASH_MAX_SPAN_DAYS` (180) cap before drawing; the
   To date doubles as the "as of" anchor for each rat's status row. The pure
   trend helpers `weight_trend`/`water_ml_trend` gained an optional
   `start_date=` override (the old `days=` path is untouched, so existing callers
   and tests are unaffected).

   Only the *length* of the window persists (`dash_span_days` in the machine-local
   `records_manager.json`); on open we re-anchor To to today and set From =
   today - (span-1). Persisting absolute dates would reopen the dashboard on a
   stale week weeks later; persisting the length keeps the operator's preferred
   look-back while always framing the present.

Also added (same pure module, for the Daily-tab work that follows):
`weekday_label`, `weight_delta` (day-over-day change vs the last *weighed* day,
skipping blanks), and `missing_days` (gap dates in a range).

## Alternatives considered

- **Keep the categorical axis, just add tick labels for gaps.** Rejected: it
  still spaces days unevenly, so the weekly cadence never emerges; the whole
  point is even calendar spacing.
- **Shade only the data-driven ad-lib bands (no calendar-weekend layer).**
  Rejected: when a weekend row is simply missing (an unlogged Sunday), there is
  no ad-lib band to show, and the operator loses the "this is where free water
  *should* be" reference. The faint calendar layer is cheap and always present;
  the orange layer confirms what actually happened. (The user chose this
  "marked + reference" combination.)
- **Anchor the window by a day-count from an "As of" date (7/14/28).** This was
  the first pass; the user asked instead for an explicit start-end range so an
  arbitrary week can be framed directly. Quick-fill buttons preserve the
  convenience of the old day-count.
- **Persist absolute From/To dates.** Rejected: reopening days later would show a
  stale week. Persist the span length and re-anchor to today instead.

## Consequences

- The weekly rhythm, weekend recovery, and missed-day gaps are now visible at a
  glance; deviations from the norm (an off-schedule ad-lib day, a lone rat) show
  as bands that don't line up with the grey weekend reference.
- The span cap bounds the work: a mistyped or huge range is refused with a
  status message rather than trying to draw hundreds of days.
- The pure helpers (`*_spans`, `weekday_label`, `weight_delta`, `missing_days`)
  are reused by the Daily-tab cues in the next change, keeping that GUI logic
  thin.
- Trade-off: the date axis relies on matplotlib's date machinery; the existing
  text fallback (matplotlib absent) still works but is unshaded and range-only.

## Evidence / links

- Pure core + helpers: `host/tmaze/core/records/dashboard.py` (`weekend_spans`,
  `ad_lib_spans`, `weight_delta`, `missing_days`, `weekday_label`, and the
  `start_date=` override on `weight_trend`/`water_ml_trend`).
- GUI: `host/tmaze/ui/records_manager.py` (`_build_dashboard_tab`,
  `_dash_window`, `_dash_quick_fill`, `_dash_span_pref`, `_trend_chart_combined`).
- Tests: `host/tests/test_dashboard.py` (`TestExplicitStartWindow`,
  `TestWeeklySpans`, `TestTableHelpers`) - full suite 714 passing.
- Follows ADR-0100 (`water_regime`/`is_ad_lib`) and ADR-0087 (the combined chart
  this refines).
