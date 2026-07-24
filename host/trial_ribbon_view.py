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

from phase3_ribbon import RIBBON_AFTER, RIBBON_BEFORE, RibbonRow, ribbon_rows
from phase3_log import Phase3StateSnapshot
from phase3_task import ABANDONED, CORRECT

try:
    from tooltips import tip
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
            sum(1 for r in state.rows if r.outcome is not None))


class TrialRibbonView:
    """Tk widget: the trial ribbon strip, drawn from a Phase3StateSnapshot."""

    def __init__(self, parent: tk.Misc, *,
                 state_getter: Optional[Callable[[], Optional[Phase3StateSnapshot]]] = None,
                 poll_ms: int = POLL_INTERVAL_MS,
                 text: str = "Trial ribbon (recent / current / upcoming)",
                 tooltip_key: str = "phase3.ribbon"):
        self._state_getter = state_getter
        self._poll_ms = poll_ms
        self._closed = False
        self._job: Optional[str] = None
        self._sig = _UNSET

        self._frame = ttk.LabelFrame(parent, text=text)
        tip(self._frame, tooltip_key)
        self._hint = ttk.Label(self._frame, text="", foreground=_FG_MUTED)
        self._hint.grid(row=1, column=0, sticky="w", padx=4, pady=2)
        strip = ttk.Frame(self._frame)
        strip.grid(row=0, column=0, sticky="w", padx=4, pady=2)
        # Pre-create the fixed cells once and update them in place each render, so
        # a steady session doesn't churn widgets (extra cells near the session
        # ends are hidden with grid_remove).
        self._cells = [self._make_cell(strip, i) for i in range(RIBBON_CELLS)]

        # Pull mode: no external poll, so drive our own off the published getter.
        if self._state_getter is not None:
            self._schedule_poll()

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
        rows = ribbon_rows(state)
        for i, cell in enumerate(self._cells):
            if i < len(rows):
                self._fill_cell(cell, rows[i])
                cell["frame"].grid()
            else:
                cell["frame"].grid_remove()
        self._hint.configure(
            text="" if rows else "Not armed - arm to generate the trial plan.")

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
                         padx=5, pady=3)
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
        bg = _CELL_CURRENT_BG if row.is_current else _CELL_BG
        for key in ("frame", "trial", "choice", "ret"):
            cell[key].configure(bg=bg)
        cell["trial"].configure(text=f"#{row.trial}")
        cell["choice"].configure(
            text=f"{row.choice_cue}->{_ARM_SHORT.get(row.choice_side, '?')}")
        cell["ret"].configure(
            text=f"ret L:{_CUE_SHORT.get(row.return_cue_left, '?')} "
                 f"R:{_CUE_SHORT.get(row.return_cue_right, '?')}")
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
