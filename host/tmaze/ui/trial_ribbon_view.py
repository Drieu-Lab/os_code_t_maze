"""
trial_ribbon_view.py - the Phase 3 "trial ribbon" as a reusable Tk widget.

A thin Tk shell around the pure ``phase3_ribbon.ribbon_rows`` core: a compact
strip of the current trial flanked by a few resolved trials behind it and a few
upcoming trials ahead of it (docs/decisions/0051). It is the "how to draw it"
half; ``phase3_ribbon.py`` is the "what to show" half.

It was lifted out of ``phase3_controller.py`` so the same strip can appear both
on the Phase 3 tab and on the Rig view (the default experiment view), reading the
one immutable ``Phase3StateSnapshot`` the Phase 3 controller publishes for
cross-tab readers (docs/decisions/0050). See docs/decisions/0057.

Two drive modes:
  - push: the owner already polls and calls ``render(state)`` on each tick (the
    Phase 3 tab, which also rebuilds its planned-trial list on the same tick).
  - pull: pass a ``state_getter`` and the widget self-polls every ``poll_ms`` and
    only redraws when the published table actually changes (the Rig view, which
    has no Phase 3 poll of its own).

Module contract (so the Rig view can register it like any other tab module):
    widget() -> the Tk widget to place
    apply(info) -> None   no-op (it reads the snapshot, not serial lines)
    close() -> None       cancel the self-poll (pull mode)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from tmaze.core.phase3.phase3_ribbon import (
    RIBBON_AFTER, RIBBON_BEFORE, RibbonRow, all_ribbon_rows, ribbon_rows)
from tmaze.core.phase3.phase3_log import Phase3StateSnapshot
from tmaze.core.phase3.phase3_task import ABANDONED, CORRECT

try:
    from tmaze.ui.tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# The ribbon holds a fixed strip of RIBBON_BEFORE + 1 + RIBBON_AFTER cells (the
# helper clamps near the session ends, so unused cells are hidden). Colours are
# cosmetic only (docs/decisions/0051).
RIBBON_CELLS = RIBBON_BEFORE + 1 + RIBBON_AFTER
_CELL_BG = "#f2f2f2"            # a ribbon cell at rest
_CELL_CURRENT_BG = "#ffe9a8"    # the current trial (ribbon cell + list row)
_FG = "#222222"
_FG_MUTED = "#888888"           # not-yet-resolved trials
_FG_CORRECT = "#1a7f37"         # a correct choice
_FG_INCORRECT = "#b3261e"       # an incorrect choice
# A closed-loop debias corrective block: a distinct coloured outline around the
# cell (purple, so it reads apart from the current-trial fill and the OK/X colours).
_CORRECTIVE_OUTLINE = "#8a2be2"
# An unrewarded probe trial (probe_blocks.py): a distinct teal cell fill, so it
# reads apart from the normal grey/current-yellow cells and the purple corrective
# outline. See docs/decisions/0115-probe.
_PROBE_BG = "#cdeede"

# Compact one-letter labels for the strip (arms and cue tones).
_ARM_SHORT = {"LEFT": "L", "RIGHT": "R"}
_CUE_SHORT = {"HIGH": "H", "LOW": "L"}

# How often the pull-mode self-poll checks the published snapshot (matches the
# Phase 3 controller's Tk poll cadence).
POLL_INTERVAL_MS = 100

# Sentinel for "no render yet": distinct from any real signature (including
# ``None`` for the not-armed state), so the first poll always draws.
_UNSET = object()


def progress_signature(state: Optional[Phase3StateSnapshot]):
    """A cheap change-signature of the published table.

    Lets a poll skip redrawing the ribbon (and, for the Phase 3 tab, the
    planned-trial list) unless the table actually changed - so a steady session
    doesn't churn widgets every 100 ms (docs/decisions/0051). ``None`` when there
    is nothing armed yet (itself a valid, distinct signature)."""
    if state is None:
        return None
    return (state.seed, state.total, state.current_index, state.armed,
            sum(1 for r in state.rows if r.outcome is not None),
            # so a NORMAL<->INTERVENTION toggle (incl. an operator override) redraws
            state.debias_state,
            sum(1 for r in state.rows if r.sched_state == "INTERVENTION"),
            # so a probe appearing / the probe state changing redraws too
            getattr(state, "probe_state", "NORMAL"),
            sum(1 for r in state.rows if r.probe == "1"),
            # so the provisional upcoming-block markers (docs/decisions/0152) redraw
            # when only the prediction changes - e.g. a block about to exit while
            # its sched_state is still INTERVENTION, or a probe running down
            getattr(state, "intervention_next", False),
            getattr(state, "probe_upcoming", 0))


class TrialRibbonView:
    """Tk widget: the trial ribbon strip, drawn from a Phase3StateSnapshot."""

    def __init__(self, parent: tk.Misc, *,
                 state_getter: Optional[Callable[[], Optional[Phase3StateSnapshot]]] = None,
                 poll_ms: int = POLL_INTERVAL_MS,
                 scrollable: bool = False,
                 text: str = "Trial ribbon (recent / current / upcoming)",
                 tooltip_key: str = "phase3.ribbon"):
        self._state_getter = state_getter
        self._poll_ms = poll_ms
        self._scrollable = scrollable
        self._closed = False
        self._job: Optional[str] = None
        self._sig = _UNSET

        self._frame = ttk.LabelFrame(parent, text=text)
        tip(self._frame, tooltip_key)
        if scrollable:
            self._build_scrollable()
        else:
            self._build_fixed()

        # Pull mode: no external poll, so drive our own off the published getter.
        if self._state_getter is not None:
            self._schedule_poll()

    # -------------------------------------------------------------- layouts
    def _build_fixed(self) -> None:
        """Fixed 7-cell window strip (the Phase 3 tab; docs/decisions/0051)."""
        self._hint = ttk.Label(self._frame, text="", foreground=_FG_MUTED)
        self._hint.grid(row=1, column=0, sticky="w", padx=4, pady=2)
        strip = ttk.Frame(self._frame)
        strip.grid(row=0, column=0, sticky="w", padx=4, pady=2)
        # Pre-create the fixed cells once and update them in place each render, so
        # a steady session doesn't churn widgets (extra cells near the session
        # ends are hidden with grid_remove).
        self._strip = strip
        self._cells = [self._make_cell(strip, i) for i in range(RIBBON_CELLS)]

    def _build_scrollable(self) -> None:
        """Horizontally scrollable strip of ALL planned trials (the Rig view).

        A ``tk.Canvas`` + horizontal scrollbar wrapping a variable-length cell
        strip, so the operator can scroll back/forward through the whole plan
        during a run. It auto-follows the current trial until the operator scrolls
        away, then a "jump to current" button reappears (docs/decisions/0135)."""
        self._frame.columnconfigure(0, weight=1)
        # Follow the current trial by default; a manual scroll away turns this off
        # (and shows the jump button) until the current cell is back in view.
        self._following = True
        self._current_col: Optional[int] = None
        self._strip_h = 0

        wrap = ttk.Frame(self._frame)
        wrap.grid(row=0, column=0, sticky="ew", padx=4, pady=2)
        wrap.columnconfigure(0, weight=1)
        self._canvas = tk.Canvas(wrap, highlightthickness=0, height=1)
        self._canvas.grid(row=0, column=0, sticky="ew")
        self._hbar = ttk.Scrollbar(wrap, orient="horizontal",
                                    command=self._on_scrollbar)
        self._hbar.grid(row=1, column=0, sticky="ew")
        self._canvas.configure(xscrollcommand=self._hbar.set)
        self._strip = ttk.Frame(self._canvas)
        self._canvas.create_window((0, 0), window=self._strip, anchor="nw")
        self._strip.bind("<Configure>", self._on_strip_configure)
        # Shift+wheel horizontal scroll (Windows/macOS); treated as a manual scroll.
        for w in (self._canvas, self._strip):
            w.bind("<Shift-MouseWheel>", self._on_wheel)
        self._cells: list = []   # grown to match the plan size on render

        bar = ttk.Frame(self._frame)
        bar.grid(row=1, column=0, sticky="ew", padx=4, pady=2)
        bar.columnconfigure(0, weight=1)
        self._hint = ttk.Label(bar, text="", foreground=_FG_MUTED)
        self._hint.grid(row=0, column=0, sticky="w")
        self._jump_btn = ttk.Button(bar, text=">> current", width=11,
                                     command=self._jump_to_current)
        # Shown only when not following (starts hidden).

    # ------------------------------------------------------- module contract
    def widget(self) -> tk.Widget:
        return self._frame

    def apply(self, _info) -> None:
        # The ribbon reads the published snapshot, not serial lines; the Rig view
        # registers it as a module so its close() runs, but apply() is a no-op.
        return None

    def close(self) -> None:
        self._closed = True
        if self._job is not None:
            try:
                self._frame.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    # ---------------------------------------------------------------- render
    def render(self, state: Optional[Phase3StateSnapshot]) -> None:
        """Redraw every cell from ``state`` (the caller decides when to call)."""
        if self._scrollable:
            self._render_scrollable(state)
            return
        rows = ribbon_rows(state)
        for i, cell in enumerate(self._cells):
            if i < len(rows):
                self._fill_cell(cell, rows[i])
                cell["frame"].grid()
            else:
                cell["frame"].grid_remove()
        self._hint.configure(
            text="" if rows else "Not armed - arm to generate the trial plan.")

    def _render_scrollable(self, state: Optional[Phase3StateSnapshot]) -> None:
        """Redraw the full-plan scrollable strip (docs/decisions/0135)."""
        rows = all_ribbon_rows(state)
        self._sync_cell_count(len(rows))
        current_col: Optional[int] = None
        for i, cell in enumerate(self._cells):
            if i < len(rows):
                self._fill_cell(cell, rows[i])
                cell["frame"].grid()
                if rows[i].is_current:
                    current_col = i
            else:
                cell["frame"].grid_remove()
        self._current_col = current_col
        self._hint.configure(
            text="" if rows else "Not armed - arm to generate the trial plan.")
        try:
            self._canvas.update_idletasks()
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except tk.TclError:
            return
        if self._following and current_col is not None:
            self._scroll_cell_into_view(current_col)
        self._update_jump_visibility()

    # -------------------------------------------------------- scroll helpers
    def _sync_cell_count(self, n: int) -> None:
        """Grow the reusable cell list to at least ``n`` cells (extras beyond the
        live plan are hidden with grid_remove in the render loop)."""
        while len(self._cells) < n:
            cell = self._make_cell(self._strip, len(self._cells))
            for key in ("frame", "trial", "choice", "ret", "outcome"):
                cell[key].bind("<Shift-MouseWheel>", self._on_wheel)
            self._cells.append(cell)

    def _on_strip_configure(self, _event=None) -> None:
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
            h = self._strip.winfo_reqheight()
            if h > 1 and h != self._strip_h:
                self._strip_h = h
                self._canvas.configure(height=h)
        except tk.TclError:
            pass

    def _on_scrollbar(self, *args) -> None:
        """Scrollbar drag/step: a manual scroll, so stop auto-following unless the
        move happens to leave the current cell in view."""
        try:
            self._canvas.xview(*args)
        except tk.TclError:
            return
        self._following = self._current_cell_visible()
        self._update_jump_visibility()

    def _on_wheel(self, event) -> str:
        try:
            self._canvas.xview_scroll(-1 if event.delta > 0 else 1, "units")
        except tk.TclError:
            return "break"
        self._following = self._current_cell_visible()
        self._update_jump_visibility()
        return "break"

    def _jump_to_current(self) -> None:
        self._following = True
        if self._current_col is not None:
            self._scroll_cell_into_view(self._current_col)
        self._update_jump_visibility()

    def _scroll_cell_into_view(self, col: int) -> None:
        """Scroll so cell ``col`` is centred in the viewport (best effort)."""
        try:
            self._canvas.update_idletasks()
            bbox = self._canvas.bbox("all")
            if not bbox:
                return
            total_w = bbox[2] - bbox[0]
            if total_w <= 0:
                return
            frame = self._cells[col]["frame"]
            x = frame.winfo_x()
            w = frame.winfo_width()
            canvas_w = self._canvas.winfo_width()
            target = x + w / 2 - canvas_w / 2
            self._canvas.xview_moveto(max(0.0, min(1.0, target / total_w)))
        except tk.TclError:
            pass

    def _current_cell_visible(self) -> bool:
        """Whether the current-trial cell is fully within the visible viewport."""
        if self._current_col is None:
            return False
        try:
            self._canvas.update_idletasks()
            view_lo, view_hi = self._canvas.xview()
            bbox = self._canvas.bbox("all")
            if not bbox:
                return False
            total_w = bbox[2] - bbox[0]
            if total_w <= 0:
                return False
            frame = self._cells[self._current_col]["frame"]
            x = frame.winfo_x()
            w = frame.winfo_width()
            return (x / total_w) >= view_lo and ((x + w) / total_w) <= view_hi
        except tk.TclError:
            return False

    def _update_jump_visibility(self) -> None:
        if self._following:
            self._jump_btn.grid_remove()
        else:
            self._jump_btn.grid(row=0, column=1, sticky="e", padx=(6, 0))

    # ------------------------------------------------------------ self-poll
    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            state = self._state_getter() if self._state_getter else None
            sig = progress_signature(state)
            if sig != self._sig:              # change-gated, like the Phase 3 tab
                self._sig = sig
                self.render(state)
        except tk.TclError:
            return
        try:
            self._job = self._frame.after(self._poll_ms, self._schedule_poll)
        except tk.TclError:
            self._job = None

    # ---------------------------------------------------------------- cells
    def _make_cell(self, parent: tk.Misc, col: int) -> dict:
        """One ribbon cell: a coloured frame of stacked labels, updated in place."""
        frame = tk.Frame(parent, bg=_CELL_BG, bd=1, relief="solid",
                         padx=5, pady=3, highlightthickness=0,
                         highlightbackground=_CORRECTIVE_OUTLINE,
                         highlightcolor=_CORRECTIVE_OUTLINE)
        frame.grid(row=0, column=col, padx=2, sticky="n")
        trial = tk.Label(frame, bg=_CELL_BG, fg=_FG, font=("TkDefaultFont", 8, "bold"))
        choice = tk.Label(frame, bg=_CELL_BG, fg=_FG, font=("TkDefaultFont", 8))
        ret = tk.Label(frame, bg=_CELL_BG, fg=_FG, font=("TkDefaultFont", 8))
        outcome = tk.Label(frame, bg=_CELL_BG, fg=_FG_MUTED, font=("TkDefaultFont", 8))
        for w in (trial, choice, ret, outcome):
            w.pack(anchor="w")
        return {"frame": frame, "trial": trial, "choice": choice,
                "ret": ret, "outcome": outcome}

    def _fill_cell(self, cell: dict, row: RibbonRow) -> None:
        """Update one pre-made ribbon cell in place (text, colours, highlight)."""
        # The current trial's yellow fill wins; else a probe trial gets its teal
        # fill; else the neutral grey. Probe and corrective are orthogonal (a probe
        # suspends interventions), so a probe cell never also shows the outline.
        if row.is_current:
            bg = _CELL_CURRENT_BG
        elif row.probe:
            bg = _PROBE_BG
        else:
            bg = _CELL_BG
        for key in ("frame", "trial", "choice", "ret"):
            cell[key].configure(bg=bg)
        # Ring corrective-block trials in a distinct outline (and mark the number);
        # tag probe trials in the number line instead.
        cell["frame"].configure(
            highlightthickness=2 if row.corrective else 0)
        if row.probe:
            cell["trial"].configure(text=f"#{row.trial} prb")
        elif row.corrective:
            cell["trial"].configure(text=f"#{row.trial} blk")
        else:
            cell["trial"].configure(text=f"#{row.trial}")
        cell["choice"].configure(
            text=f"{row.choice_cue}->{_ARM_SHORT.get(row.choice_side, '?')}")
        # Show the planned return-arm cues only when the return cue is actually
        # played this run; otherwise drop the line (pack_forget shrinks the cell).
        # Cells are reused across renders, so this toggles on every fill.
        # See docs/decisions/0141.
        if row.return_enabled:
            cell["ret"].configure(
                text=f"ret L:{_CUE_SHORT.get(row.return_cue_left, '?')} "
                     f"R:{_CUE_SHORT.get(row.return_cue_right, '?')}")
            cell["ret"].pack(anchor="w", before=cell["outcome"])
        else:
            cell["ret"].pack_forget()
        if row.outcome == ABANDONED:
            # Operator-abandoned trial (Skip / Repeat): neutral, not a scored
            # correct/incorrect (docs/decisions/0058).
            cell["outcome"].configure(bg=bg, fg=_FG_MUTED, text="SKIP")
        elif row.resolved:
            ok = row.outcome == CORRECT
            cell["outcome"].configure(
                bg=bg, fg=_FG_CORRECT if ok else _FG_INCORRECT,
                text=f"{_ARM_SHORT.get(row.chosen_arm, '?')} {'OK' if ok else 'X'}")
        else:
            cell["outcome"].configure(bg=bg, fg=_FG_MUTED, text="-")
