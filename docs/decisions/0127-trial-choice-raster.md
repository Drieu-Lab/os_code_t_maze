# ADR 0127: Trial-by-trial choice raster on the Metrics tab

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_traces.py` (per-trial marks on
  `TraceResult`), `host/tmaze/ui/phase3_choice_raster_view.py` (new panel),
  `host/tmaze/ui/experiment_control.py` (Metrics-tab wiring),
  `host/tmaze/ui/tooltip_text.py`, `host/tests/test_phase3_traces.py`
- **Supersedes / Superseded by:** —

## Context

The Metrics tab (ADR-0114) plots ROLLING measures - accuracy, strategy rates,
ITI - over session time. Those windows deliberately smooth away the raw
sequence, but when watching a run it is often the un-smoothed trial-by-trial
pattern an operator wants to see: did the rat just do `L R L R ...`, is it
stuck on one side, are the errors clustered? A single rolling number can't show
that.

The tab also had visible dead space: its one panel (`Phase3TracesView`) is
packed non-expanding (the Metrics factory list had a single entry, and the tab
builder packs the first module at its natural size - `expand=(i > 0)` in
`experiment_control.main`), so the tab's right half sat empty.

All the per-trial facts needed for a raster (choice side, correct/incorrect,
probe, intervention) are already reduced inside `phase3_traces.build_traces`
(the `_Point` first pass), but `TraceResult` only exposed the windowed RATES
built from them, not the raw per-trial values.

## Decision

Add a **trial-by-trial choice raster** as a second panel on the Metrics tab,
filling the empty region beside the trace plots.

- **Pure core:** expose the raw per-trial facts on `TraceResult` as four new
  parallel lists (`choice`, `correct`, `probe`, `oob`), populated in the same
  `build_traces` loop from the existing `_Point`. No second pass, and the raster
  is computed from the SAME reduction as the rolling lines, so the two Metrics
  panels can't drift.
- **View:** a new thin Tk shell `phase3_choice_raster_view.Phase3ChoiceRasterView`
  (matplotlib guarded + text fallback, 250 ms `progress_signature`-gated
  self-poll, pull-mode `state_getter`), drawing one vertical bar per completed
  trial on a **trial-number** x-axis:
  - bar SIGN = side (RIGHT up, LEFT down),
  - bar HEIGHT + opacity = outcome (correct = full-height solid; incorrect =
    half-height faded),
  - bar COLOUR = side (right purple, left red),
  - probe / intervention trials keep their bar but gain a distinct coloured
    outline (probe teal, intervention orange) so coerced/unrewarded trials read
    apart from free choices,
  - a rolling-accuracy line (green) overlaid on a **twin right axis** (0-1, with
    a dashed 0.5 reference).
  A trial-window viewport (a `Width (trials)` zoom + scrollbar + `Latest`) reuses
  the unit-agnostic `trace_viewport.compute_viewport` (in trials, not minutes) so
  long sessions stay readable; follow-unless-panned as in ADR-0116.
- **Wiring:** register the raster as the SECOND Metrics-tab module, so the
  existing left-to-right packing gives it `expand=True` and it fills the space.

## Alternatives considered

- **Map the rolling accuracy onto the signed bar scale** (e.g. `y = (2*acc-1)*2`
  so 50% sits at zero) instead of a twin axis - rejected: it visually matches the
  inspiring example better but is misleading, since accuracy is a 0-1 rate, not a
  signed L/R quantity, and a zero-crossing "accuracy" would be read as a side
  bias. A twin 0-1 axis keeps the rate honest.
- **Add the raster as a 5th stacked subplot inside `Phase3TracesView`** - rejected:
  the raster's natural x-axis is trial number, but that figure's subplots
  `sharex` on session-time minutes; and the whole point was to use the empty
  horizontal space, not add more vertical stacking. A separate panel keeps each
  figure's x-axis coherent.
- **A dedicated per-trial core module** rather than extending `TraceResult` -
  rejected for now (no second caller): the facts are already computed in
  `build_traces`; exposing them there is a smaller diff and keeps one source of
  truth (the repo's "no new abstraction until 2-3 callers" convention).
- **x-axis in session minutes** (to align with the traces panel) - rejected:
  trial number matches the inspiring example and reads more naturally for a
  raster; the panel stands alone (its own figure) so it need not share the axis.

## Consequences

- The Metrics tab now shows both the smoothed rates and the raw sequence side by
  side, and no longer wastes half its width.
- `TraceResult` grew four additive fields (all defaulting empty), so existing
  readers/tests are unaffected; the round-trip symmetry ADR-0117 relies on still
  holds (the new fields are derived the same way live and from a reconstructed
  snapshot).
- The raster does not (yet) draw a reference/ghost overlay or shade block/probe
  spans the way the traces panel does; only per-bar outlines mark special trials.
  A ghost raster or span shading could be added later if wanted.
- Read-only: no new logging or protocol change (a snapshot consumer only).

## Evidence / links

- Builds on ADR-0114 (live metric traces), ADR-0116 (viewport), ADR-0115 (probe
  marker), ADR-0118 (intervention blocks), ADR-0050 (published snapshot seam),
  ADR-0083 (package layout / pure core + thin shell).
- Code: `phase3_traces.TraceResult` (new `choice`/`correct`/`probe`/`oob`),
  `phase3_choice_raster_view.py`, the Metrics-tab block in
  `experiment_control.main`.
- Tests: `host/tests/test_phase3_traces.py` (per-trial mark coverage).
