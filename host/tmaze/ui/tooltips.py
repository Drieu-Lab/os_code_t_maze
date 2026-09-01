"""tooltips.py - shared hover-tooltip engine for the host Tk GUIs.

Tkinter has no built-in tooltip widget, so this module provides one small,
reusable mechanism plus a keyed API so call sites carry no prose:

    tip(widget, "reward.total")               # a normal widget
    canvas_tip(canvas, item_id, "sol.fire",   # a canvas item (rig_view markers)
               port="L", role="reward R1", cmd="R00")

The text for a key lives in ONE place, tooltip_text.py (the single source of
tooltip strings). tip()/canvas_tip() resolve the key through that registry and
then bind the low-level popup; an unknown/blank key logs a warning and attaches
nothing, so text/key drift is caught rather than crashing the GUI.

Design: a single hidden Toplevel per tooltip, shown after a short hover delay
and hidden on leave / button press. Every Tk call is guarded so a torn-down
widget (window closing) never raises out of an <Enter>/<Leave> callback.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Optional

from tmaze.ui.tooltip_text import resolve

# How long the pointer must rest before the tip shows, and the tip's look.
SHOW_DELAY_MS = 450
_WRAPLENGTH = 320
_BG = "#ffffe0"       # pale yellow, the conventional tooltip colour
_FG = "#222222"


class _Tip:
    """A single tooltip popup bound to one anchor widget (or canvas).

    Holds a hidden Toplevel that is created lazily on first show and reused.
    Positioning is supplied per-show so the same class serves both widget
    tooltips (anchored below-right of the widget) and canvas tooltips
    (anchored near the pointer).
    """

    def __init__(self, anchor: tk.Misc):
        self._anchor = anchor
        self._tipwin: Optional[tk.Toplevel] = None
        self._after_id: Optional[str] = None

    def schedule(self, text: str, x: int, y: int) -> None:
        self.hide()
        if not text:
            return
        try:
            self._after_id = self._anchor.after(
                SHOW_DELAY_MS, lambda: self._show(text, x, y))
        except tk.TclError:
            self._after_id = None

    def _show(self, text: str, x: int, y: int) -> None:
        self._after_id = None
        if self._tipwin is not None:
            return
        try:
            win = tk.Toplevel(self._anchor)
            win.wm_overrideredirect(True)   # no title bar / border chrome
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(
                win, text=text, justify="left", background=_BG, foreground=_FG,
                relief="solid", borderwidth=1, wraplength=_WRAPLENGTH,
                font=("TkDefaultFont", 8), padx=5, pady=3,
            ).pack()
            self._tipwin = win
        except tk.TclError:
            self._tipwin = None

    def hide(self) -> None:
        if self._after_id is not None:
            try:
                self._anchor.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._tipwin is not None:
            try:
                self._tipwin.destroy()
            except tk.TclError:
                pass
            self._tipwin = None


# ------------------------------ low-level API ------------------------------
def add_tooltip(widget: tk.Misc, text: str) -> None:
    """Attach a hover tooltip carrying literal ``text`` to ``widget``.

    Prefer tip(widget, key) so the text stays in the registry; this is the
    escape hatch for one-off dynamic strings built at the call site.
    """
    if not text:
        return
    state = _Tip(widget)

    def on_enter(_event: "tk.Event") -> None:
        try:
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 4
        except tk.TclError:
            return
        state.schedule(text, x, y)

    widget.bind("<Enter>", on_enter, add="+")
    widget.bind("<Leave>", lambda _e: state.hide(), add="+")
    widget.bind("<ButtonPress>", lambda _e: state.hide(), add="+")


def add_dynamic_tooltip(widget: tk.Misc, text_fn) -> None:
    """Attach a hover tooltip whose text is computed at show time by ``text_fn``.

    Like add_tooltip but for LIVE values (e.g. camera transport stats): the
    callback runs on each hover and may return "" to show nothing. Guarded so a
    torn-down widget or a raising callback never escapes the <Enter> handler.
    """
    state = _Tip(widget)

    def on_enter(_event: "tk.Event") -> None:
        try:
            text = text_fn() or ""
        except Exception:
            text = ""
        if not text:
            return
        try:
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 4
        except tk.TclError:
            return
        state.schedule(text, x, y)

    widget.bind("<Enter>", on_enter, add="+")
    widget.bind("<Leave>", lambda _e: state.hide(), add="+")
    widget.bind("<ButtonPress>", lambda _e: state.hide(), add="+")


def add_canvas_tooltip(canvas: tk.Canvas, item_id: int, text: str) -> None:
    """Attach a hover tooltip to a single canvas item (e.g. a rig_view marker)."""
    if not text:
        return
    state = _Tip(canvas)

    def on_enter(_event: "tk.Event") -> None:
        try:
            px, py = canvas.winfo_pointerxy()
        except tk.TclError:
            return
        state.schedule(text, px + 14, py + 12)

    canvas.tag_bind(item_id, "<Enter>", on_enter, add="+")
    canvas.tag_bind(item_id, "<Leave>", lambda _e: state.hide(), add="+")


# ------------------------------- keyed API ---------------------------------
def _warn_missing(key: str) -> None:
    print(f"[tooltips] no tooltip text for key {key!r}", file=sys.stderr,
          flush=True)


def tip(widget: tk.Misc, key: str, **fmt) -> None:
    """Attach the registry tooltip for ``key`` (formatted with ``fmt``) to a widget."""
    text = resolve(key, **fmt)
    if not text:
        _warn_missing(key)
        return
    add_tooltip(widget, text)


def canvas_tip(canvas: tk.Canvas, item_id: int, key: str, **fmt) -> None:
    """Attach the registry tooltip for ``key`` (formatted) to a canvas item."""
    text = resolve(key, **fmt)
    if not text:
        _warn_missing(key)
        return
    add_canvas_tooltip(canvas, item_id, text)
