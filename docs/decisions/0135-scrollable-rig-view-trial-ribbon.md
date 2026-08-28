# ADR 0135: Make the Rig-view trial ribbon scrollable over the whole plan

- **Status:** Accepted
- **Date:** 2026-08-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_ribbon.py`,
  `host/tmaze/ui/trial_ribbon_view.py`, `host/tmaze/ui/experiment_control.py`,
  `host/tests/test_phase3_ribbon.py`
- **Supersedes / Superseded by:** extends ADR-0057 (does not supersede it)

## Context

The trial ribbon on the Rig view (ADR-0057) is a fixed 7-cell window: the current
trial flanked by `RIBBON_BEFORE = 3` resolved trials behind and `RIBBON_AFTER = 3`
upcoming ahead (`phase3_ribbon.ribbon_rows`, ADR-0051). It auto-follows the
current trial, so during a run the operator can only see those seven trials on the
Rig view; to look further back or ahead they must switch to the Phase 3 tab (which
also carries a scrollable `Listbox` of the whole plan). The operator asked to be
able to scroll back/forward through the plan without leaving the Rig view.

The "what to show" logic is a pure core and the ribbon widget already anticipated
per-view configuration ("if the ribbon needs richer per-view configuration
later... the widget already takes constructor knobs", ADR-0057 Consequences), so
the change is contained.

## Decision

Give the Rig-view ribbon a horizontally scrollable strip that shows **all**
planned trials, opting in per view so the Phase 3 tab is unchanged.

- **Pure core.** Add `all_ribbon_rows(state)` in `phase3_ribbon.py` - the
  unwindowed twin of `ribbon_rows`: one `RibbonRow` per trial `0..total-1`,
  `is_current` set from the clamped `current_index` (so a completed session marks
  the last trial), `[]` when not armed / empty. Both builders now share a
  `_make_row` helper, so a row looks identical windowed or full.
- **Widget knob.** `TrialRibbonView(..., scrollable=False)`. When `False`, the
  fixed 7-cell strip is byte-for-byte unchanged (the Phase 3 tab's push mode).
  When `True`, the widget wraps a variable-length cell strip in a `tk.Canvas` +
  horizontal `ttk.Scrollbar`, renders `all_ribbon_rows`, grows the reusable cell
  list only when the plan size changes (so a steady session still doesn't churn
  widgets, per ADR-0051), and:
  - **auto-follows** the current trial (centres it after each render) while a
    `_following` flag is set;
  - **turns following off** on any manual scroll (scrollbar or Shift+wheel)
    unless the move leaves the current cell in view, and back on when the current
    cell scrolls back into view;
  - shows a small **">> current"** button (in the hint row) only while not
    following; clicking it re-centres and re-enables following.
- **Rig view opts in.** `experiment_control.py` constructs the Rig-view ribbon
  with `scrollable=True`; the Phase 3 tab keeps the default fixed strip beside its
  own trial list.

## Alternatives considered

- **Make scroll global (both views).** Rejected: the Phase 3 tab already has a
  scrollable `Listbox` of every trial next to the ribbon, so a scrollable ribbon
  there is redundant; opting in per view keeps ADR-0057's single-widget source of
  truth without changing the Phase 3 tab.
- **Always snap to the current trial on every update.** Rejected per the
  operator's choice: scrolling back to inspect an earlier trial would be undone by
  the next trial advance. Auto-follow-until-scrolled matches how they read it.
- **Bring the Phase 3 `Listbox` onto the Rig view instead.** Rejected: the
  operator wanted the ribbon's compact per-trial cells, not a text list, and a
  second list would duplicate the Phase 3 tab's widget.

## Consequences

- The operator can scroll the whole plan on the Rig view during a run; it still
  tracks the live trial until they choose to look away.
- The ribbon widget now carries a modest amount of Tk scroll/viewport code behind
  the `scrollable` flag. The fixed path (Phase 3 tab) is untouched, and the pure
  core stays headless-testable (`all_ribbon_rows` is covered in
  `test_phase3_ribbon.py`); the scroll behaviour is UI shell, left untested per
  the repo's pure-core test policy.
- Auto-follow centres the current cell using widget geometry, which needs the Tk
  layout to have settled (`update_idletasks`); it degrades gracefully (a
  best-effort `xview_moveto`) if geometry isn't ready yet.

## Evidence / links

- `phase3_ribbon.all_ribbon_rows` / `_make_row`; `test_phase3_ribbon.py`
  `TestAllRibbonRows`.
- `trial_ribbon_view.TrialRibbonView` `scrollable` mode
  (`_build_scrollable`, `_render_scrollable`, the scroll helpers); Rig-view wiring
  in `experiment_control.py`.
- Builds on ADR-0057 (shared ribbon widget on the Rig view) and ADR-0051 (the
  pure `phase3_ribbon` core + in-tab ribbon); reads the ADR-0050 published
  snapshot.
