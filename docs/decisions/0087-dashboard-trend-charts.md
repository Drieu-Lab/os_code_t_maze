# ADR 0087: One combined matplotlib dashboard trend chart (per-rat toggles, optional water overlay)

- **Status:** Accepted
- **Date:** 2026-08-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/records_manager.py` (`_trend_chart_combined`, `_dashboard_status_row`, `_rat_color`, `_sync_dash_toggles`, `_enabled_dash_rats`, `_trend_text_fallback`, Dashboard toggles); `host/tmaze/core/records/dashboard.py` (`water_ml_trend`); `host/tests/test_dashboard.py`
- **Supersedes / Superseded by:** — (builds on ADR-0085, 0081)

## Context

The Records Manager welfare dashboard (ADR-0085) drew each rat's weight trend as
a hand-rolled `tk.Canvas` polyline that autoscaled to the window's min/max, one
per-rat card. That made small gram fluctuations look like dramatic swings, had no
reference line for the protocol's 90%-baseline restriction floor (RA23A232),
wasn't antialiased (jagged), and - being one separate chart per rat, each with its
own auto-scaled axis - made it hard to *compare* rats at a glance. The operator
asked for the trend to be "more slick", to optionally show the daily water the
animal received (to judge the maintenance water level), and then to consolidate
the per-rat charts into **one plot overlaying all rats**, with per-rat show/hide
toggles.

Two data facts shaped the water side. First, matplotlib is already a project
dependency (`environment.yml`, `host/requirements.txt`), so a real chart costs no
new stack. Second, per-day water is only quantifiable from the numeric channels
`collect_water` reads (`daily_log`'s `water_ml`/`supp_water` and the session
registry's `task_reward_ml`/`manual_reward_ml`; ADR-0081/0084). In the current
records the rig water is still free text in the `water_on` cell and the registry
has no sessions yet, so a water trace would be almost entirely empty until the
operator goes back and enters numeric values. There is also an unresolved
modelling question - water is available ad libitum on weekends (roughly Friday
evening to Sunday afternoon), which a naive intake/maintenance reading shouldn't
treat as restriction days (deferred to future work, FW-0030).

## Decision

Replace the per-rat sparkline cards with **one shared embedded matplotlib chart**
(`_trend_chart_combined`) that overlays every enabled rat, built with
`matplotlib.figure.Figure` + `FigureCanvasTkAgg` (not `pyplot`, to avoid the global
figure registry / leak bookkeeping - destroying the Tk widget on refresh is enough,
plus a per-refresh list reference so the canvas isn't GC'd early).

- **One combined % baseline chart.** Each rat's % baseline over 14 days is an
  antialiased line with markers in a stable per-rat colour (`_rat_color`, keyed on
  roster position), plotted against a **shared x-axis** = the union of the enabled
  rats' dates. A single dashed reference line at `dashboard.BELOW_THRESHOLD` (90%)
  serves all traces; any point `< 90%` is marked red, and each trace's latest value
  is annotated in its colour. A legend appears when >1 rat is shown. The y-range is
  anchored to always include the 90% line (`[min(all, 90) - pad, max(all, 100) +
  pad]`) so the comparison reads honestly and calmly.
- **Per-rat show/hide toggles + a status strip.** Above the chart, one compact row
  per rat (`_dashboard_status_row`) pairs a checkbox (default on; `dash_rat_show`
  vars persist across refreshes) and a colour swatch matching the trace with that
  rat's *today* welfare line - % baseline + `<90%` flag + grams-to-90% deficit, or
  the not-logged/not-weighed alarm - and its water-by-source total. These per-rat
  facts can't live inside a shared plot, so they sit beside it and double as the
  legend/what-am-I-looking-at key.
- **Optional water overlay, OFF by default, single-rat only.** A "Show water trace
  (single rat only)" checkbox (`dash_show_water`, default `False`) gates a twin-axis
  bar overlay of each day's total mL from a new pure core `water_ml_trend` (per-day
  sum via the existing `collect_water`/`water_breakdown`; no free-text parsing). It
  draws **only when exactly one rat is enabled** - water bars for several rats on a
  twin axis would be unreadable - and stays off by default because it isn't
  informative until the water records are gap-free and correctly sourced.
- **Fallback:** the matplotlib import is lazy inside `_trend_chart_combined`; on
  `ImportError` it falls back to a per-rat text summary (`_trend_text_fallback`), so
  the GUI still works without matplotlib.

## Alternatives considered

- **One chart per rat (the first cut of this ADR).** Rejected after review: stacked
  per-rat cards each auto-scale their own axis, so comparing rats means eyeballing
  three differently-scaled plots. A single overlaid chart with a shared axis and one
  90% line makes cross-rat comparison direct; for a 3-rat colony it isn't crowded.
- **Polish the hand-drawn `tk.Canvas` sparkline.** Rejected as the primary path:
  `tk.Canvas` lines aren't antialiased and reimplementing axes/gridlines/threshold
  lines by hand is more code for a worse result than matplotlib, which is already a
  dependency. Kept only as the no-matplotlib fallback (now a text summary).
- **Water trace bars for all rats at once.** Rejected: overlapping twin-axis bars
  for several rats are unreadable, so the overlay is gated on an exact single-rat
  selection (solo) instead.
- **Water trace on by default.** Rejected: with today's records it would render
  empty or misleading (rig water is free text in `water_on`, registry empty), and
  the weekend ad-lib days would distort a maintenance reading (FW-0030). Off by
  default avoids shipping a chart that looks broken.
- **Parse `"<n> ml"` out of the free-text `water_on`/`notes`.** Rejected: the
  operator plans to enter the numbers into the numeric cells during a records
  clean-up, so a heuristic parser would be throwaway complexity.
- **`pyplot`-based figures.** Rejected: `pyplot` keeps a global figure registry
  that must be `close()`d to avoid leaks across refreshes; using `Figure` directly
  sidesteps that.

## Consequences

- The dashboard's default view is now a single, honest % baseline chart overlaying
  all rats with the 90% flag line always visible, above a per-rat status/toggle
  strip; the water overlay is one click away (once a rat is soloed) for when the
  records support it. `water_ml_trend` is implemented and headlessly tested now, so
  enabling the toggle later needs no further core work.
- The combined chart scales to a small colony (a handful of rats reads fine); a much
  larger roster would crowd the legend/colours and would want revisiting (e.g.
  small-multiples or a rat picker), but that's well beyond the current 3 rats.
- A labelling caveat carries over from ADR-0081/0084: rig water typed into the daily
  `water_ml` cell is counted in the total (so the trace stays accurate) but is
  labelled "cage", not "task" - the "task" label only comes via the registry. This
  is a per-source label detail, not a total-accuracy one.
- matplotlib is now a hard runtime dependency of the dashboard tab (with a graceful
  polyline fallback). It was already declared for the rig, so no new install burden.
- Follow-up: weekend ad-libitum water accounting (FW-0030) and the water-entry
  clean-up remain open; revisit the overlay's default and its intake interpretation
  once those land.

## Evidence / links

- Core + tests: `host/tmaze/core/records/dashboard.py` (`water_ml_trend`),
  `host/tests/test_dashboard.py` (`TestWaterMlTrend`).
- Shell: `host/tmaze/ui/records_manager.py` (`_trend_chart_combined`,
  `_dashboard_status_row`, `_rat_color`, `_sync_dash_toggles`, `_enabled_dash_rats`,
  `_trend_text_fallback`, `dash_show_water` + `dash_rat_show` toggles).
- Builds on ADR-0085 (Records Manager GUI + dashboard), ADR-0081 (session registry
  water channels), ADR-0084 (operator reward water). Sibling future work: FW-0030.
