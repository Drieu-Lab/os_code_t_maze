# ADR 0057: Show the trial ribbon on the Rig view via a shared widget

- **Status:** Accepted
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/trial_ribbon_view.py` (new), `host/phase3_controller.py`, `host/experiment_control.py`
- **Supersedes / Superseded by:** —

## Context

The trial ribbon (the current trial flanked by a few recent and upcoming trials,
docs/decisions/0051) lived only on the **Phase 3** tab. During a run the operator
watches the **Rig view** tab (the maze schematic + camera + rig controls), so the
current/upcoming trials were only visible after switching tabs. The operator
asked to see the ribbon on the Rig view too.

The data was already reachable without new plumbing: the Phase 3 controller
publishes an immutable `Phase3StateSnapshot` for cross-tab readers
(docs/decisions/0050), and the Rig view already resolves it through the
`phase3_ref` holder to feed the behavior readout (the `phase3_state` closure in
`experiment_control.py`). The ribbon's "what to show" logic was also already a
pure, Tk-free core (`phase3_ribbon.ribbon_rows`). Only the "how to draw it" Tk
code was welded into `Phase3ControlView`.

## Decision

Extract the ribbon's Tk rendering into a small reusable widget,
`trial_ribbon_view.TrialRibbonView`, and place one on the Rig view's bottom-strip
right column (above the behavior readout). The widget:

- Renders from a `Phase3StateSnapshot` using the existing pure `ribbon_rows`
  helper; it owns the cell colours/short-labels and the pre-created fixed cells.
- Supports two drive modes:
  - **push** — the owner already polls and calls `render(state)` each tick (the
    Phase 3 tab, which also rebuilds its planned-trial list on the same change
    signature); and
  - **pull** — given a `state_getter`, it self-polls every 100 ms and only
    redraws when a cheap change-signature differs (the Rig view, which has no
    Phase 3 Tk poll of its own).
- Exposes the standard module contract (`widget()` / `apply()` no-op / `close()`)
  so the Rig view registers it like any other tab module and its self-poll is
  cancelled on exit.

`Phase3ControlView` now instantiates `TrialRibbonView` instead of building the
strip inline; the shared change-signature lives in
`trial_ribbon_view.progress_signature` and is reused by both callers. The
planned-trial list stays only on the Phase 3 tab.

## Alternatives considered

- **Duplicate the ribbon-drawing code on the Rig view.** Rejected: two copies of
  the same Tk cell logic is exactly the comprehension debt the repo guards
  against (ADR-0029); a shared widget is the second real caller that justifies
  the abstraction.
- **Drive the Rig-view ribbon from the serial line stream (`apply`).** Rejected:
  the ribbon reflects the published table snapshot, not raw serial lines; a
  snapshot self-poll (mirroring the Phase 3 tab's 100 ms poll) matches the data
  source and stays change-gated so it doesn't churn widgets.
- **Move the ribbon off the Phase 3 tab entirely (Rig view only).** Rejected: the
  Phase 3 tab keeps the ribbon next to its planned-trial list; both views now
  share one widget, so there is no divergence.

## Consequences

- The current/upcoming trials are visible on the default Rig view during a run,
  no tab switch needed.
- One source of truth for the ribbon's look and change-gating; the Phase 3 tab's
  behavior is unchanged (same widget, driven in push mode).
- A second Tk `after` self-poll runs while the app is open (pull mode on the Rig
  view). It is change-gated and cancelled in `close()`, so the cost is a cheap
  100 ms signature check when nothing changes.
- If the ribbon needs richer per-view configuration later (e.g. a different
  window size on the Rig view), the widget already takes constructor knobs.

## Evidence / links

- Builds on ADR-0050 (published snapshot contract) and ADR-0051 (in-tab ribbon +
  the pure `phase3_ribbon` core).
- Pure core unchanged; `python -m unittest discover -p "test_*.py"` from `host/`
  stays green (324 tests, 17 skipped).
- Code refs: `host/trial_ribbon_view.py`, `Phase3ControlView._build_progress_ui`
  / `_refresh_progress` in `host/phase3_controller.py`, and the Rig-view wiring in
  `experiment_control.py` (next to the `phase3_state` closure).
