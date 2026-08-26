"""
phase3_choice_raster_view.py - live Phase 3 trial-by-trial choice raster (Tk panel).

A companion to ``phase3_traces_view`` on the Metrics tab (docs/decisions/0127): a
single matplotlib axis that shows the session TRIAL BY TRIAL, one vertical bar per
completed trial along a trial-number x-axis, so an operator can read the raw
sequence of choices and outcomes at a glance (the rolling rates next to it smooth
that away). It reads the SAME immutable ``Phase3StateSnapshot`` the traces panel
and the trial ribbon do (docs/decisions/0050), through the same pure
``phase3_traces.build_traces`` reduction, so the raster and the rolling-accuracy
line drawn over it can't disagree with the traces panel.

Encoding (see docs/decisions/0127):
  - bar SIGN = choice side: RIGHT points up (+), LEFT points down (-).
  - bar HEIGHT + opacity = outcome: correct = full height, solid; incorrect =
    half height, faded.
  - bar COLOUR = side: right = purple, left = red.
  - PROBE and INTERVENTION (forced-arm block) trials keep their bar but get a
    distinct coloured outline so they read apart from free-choice trials.
  - a rolling-ACCURACY line (green) is overlaid on a twin right axis (0-1, with a
    dashed 0.5 reference), reusing ``build_traces``' windowed accuracy - a twin
    axis (rather than mapping accuracy onto the signed bar scale) so the 0-1
    accuracy reads honestly and doesn't masquerade as a signed measure.

Long sessions get a trial-window VIEWPORT (a "Width (trials)" zoom + a horizontal
scrollbar + a "Latest" button), reusing the unit-agnostic
``trace_viewport.compute_viewport`` (in trials instead of minutes) so hundreds of
trials don't collapse into unreadably thin bars. Follow-unless-panned, mirroring
the traces panel (docs/decisions/0116).

Pull-mode only: pass a ``state_getter`` and the widget self-polls, redrawing when
the published table changes OR when one of its own controls changes.

Module contract (so a tab can register it like any other module):
    widget() -> the Tk widget to place
    apply(info) -> None   no-op (it reads the snapshot, not serial lines)
    close() -> None       cancel the self-poll
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional

from tmaze.core.phase3.phase3_log import Phase3StateSnapshot
from tmaze.core.phase3.phase3_traces import (
    DEFAULT_MIN_DEN, TraceResult, build_traces)
from tmaze.core.phase3.phase3_stats import (
    DEFAULT_WINDOW, LEFT_LABEL, RIGHT_LABEL)
from tmaze.core.phase3.trace_viewport import compute_viewport
from tmaze.ui.trial_ribbon_view import progress_signature

try:
    from tmaze.ui.tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# matplotlib is an optional dependency (as in phase3_traces_view.py); guard the
# import so importing this module - and experiment_control.py - never crashes when
# it is absent. The panel then shows a one-line "install matplotlib" note.
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    from matplotlib.colors import to_rgba
    _MPL_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover - depends on host environment
    FigureCanvasTkAgg = None  # type: ignore
    Figure = None  # type: ignore
    to_rgba = None  # type: ignore
    _MPL_ERR = f"{type(e).__name__}: {e}"

# Poll cadence: matches the traces panel (a redraw repaints a matplotlib canvas).
POLL_INTERVAL_MS = 250

# Encoding constants (docs/decisions/0127). Side colour matches the traces panel's
# palette intent (right = the intervention blueviolet, left = the switch red); the
# special-trial outlines reuse the traces panel's probe teal / intervention orange
# so the two Metrics panels read with one colour vocabulary.
_RIGHT_COLOR = "#8a2be2"        # purple - right choice bars point UP
_LEFT_COLOR = "#d62728"         # red - left choice bars point DOWN
_ACC_COLOR = "#1a7f37"          # green - rolling accuracy line (matches traces)
_PROBE_EDGE = "#1aa179"         # teal outline for unrewarded probe trials
_INTERVENTION_EDGE = "#ff8c00"  # dark-orange outline for forced-arm block trials

# Bar heights: a correct trial reaches the full unit, an incorrect one half of it,
# so height (as well as opacity) separates the two at a glance.
_CORRECT_H = 2.0
_INCORRECT_H = 1.0
_CORRECT_ALPHA = 0.9
_INCORRECT_ALPHA = 0.4

_UNSET = object()


class Phase3ChoiceRasterView:
    """Tk widget: a live trial-by-trial choice raster from a Phase3StateSnapshot."""

    def __init__(self, parent: tk.Misc, *,
                 state_getter: Callable[[], Optional[Phase3StateSnapshot]],
                 poll_ms: int = POLL_INTERVAL_MS,
                 text: str = "Trial-by-trial choices"):
        self._state_getter = state_getter
        self._poll_ms = poll_ms
        self._closed = False
        self._job: Optional[str] = None
        self._sig = _UNSET
        self._dirty = True                 # force the first draw
        self._last_state: Optional[Phase3StateSnapshot] = None

        # Trial-window viewport (docs/decisions/0116, reused in trial units): the
        # visible window is width (zoom) + start (pan) + follow-latest flag.
        # ``_total`` is the data's full x-extent (max trial number), refreshed each
        # redraw so a scrollbar drag can map its fraction back to a trial.
        self._view_start = 0.0
        self._follow_latest = True
        self._total = 0.0
        self._scrollbar: Optional[ttk.Scrollbar] = None

        self._frame = ttk.LabelFrame(parent, text=text)
        tip(self._frame, "phase3.raster")

        if Figure is None:
            ttk.Label(
                self._frame,
                text=("matplotlib is not installed - the choice raster is "
                      f"unavailable.\n({_MPL_ERR})\nInstall it with: "
                      "pip install matplotlib"),
                foreground="#b3261e", justify="left").pack(
                    side=tk.TOP, anchor="w", padx=8, pady=8)
            self._canvas = None
            return

        self._build_controls(self._frame)

        # Horizontal scrollbar + Latest button (the viewport's pan controls),
        # pinned under the canvas (packed BEFORE the canvas so it fills above).
        navbar = ttk.Frame(self._frame)
        navbar.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))
        latest = ttk.Button(navbar, text="Latest", width=7,
                            command=self._snap_latest)
        latest.pack(side=tk.RIGHT, padx=(4, 0))
        tip(latest, "phase3.raster.latest")
        self._scrollbar = ttk.Scrollbar(navbar, orient=tk.HORIZONTAL,
                                        command=self._on_scroll)
        self._scrollbar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._scrollbar.set(0.0, 1.0)
        tip(self._scrollbar, "phase3.raster.scroll")

        self._fig = Figure(figsize=(5.2, 6.0), dpi=100)
        self._canvas = FigureCanvasTkAgg(self._fig, master=self._frame)
        self._canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                                          padx=4, pady=(0, 4))
        self._schedule_poll()

    # ------------------------------------------------------- module contract
    def widget(self) -> tk.Widget:
        return self._frame

    def apply(self, _info) -> None:
        return None

    def close(self) -> None:
        self._closed = True
        if self._job is not None:
            try:
                self._frame.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    # ------------------------------------------------------------- controls
    def _mark_dirty(self, *_a) -> None:
        """Force the next poll to redraw (a control changed; the snapshot didn't,
        so the change-gate alone would miss it)."""
        self._dirty = True

    def _build_controls(self, parent: tk.Misc) -> None:
        bar = ttk.Frame(parent)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)

        ttk.Label(bar, text="Window:").pack(side=tk.LEFT, padx=(0, 2))
        self._win_var = tk.StringVar(value=str(DEFAULT_WINDOW))
        win = ttk.Spinbox(bar, from_=2, to=200, increment=1, width=5,
                          textvariable=self._win_var, command=self._mark_dirty)
        win.pack(side=tk.LEFT, padx=(0, 10))
        self._win_var.trace_add("write", self._mark_dirty)
        tip(win, "phase3.raster.window")

        # Viewport WIDTH (zoom) in TRIALS: blank/0 = whole session.
        ttk.Label(bar, text="Width (trials):").pack(side=tk.LEFT, padx=(0, 2))
        self._width_var = tk.StringVar(value="")   # blank = whole session
        width = ttk.Spinbox(bar, from_=0, to=2000, increment=10, width=6,
                            textvariable=self._width_var,
                            command=self._mark_dirty)
        width.pack(side=tk.LEFT, padx=(0, 10))
        self._width_var.trace_add("write", self._mark_dirty)
        tip(width, "phase3.raster.width")

        self._show_acc = tk.BooleanVar(value=True)
        acc = ttk.Checkbutton(bar, text="Rolling accuracy",
                              variable=self._show_acc, command=self._mark_dirty)
        acc.pack(side=tk.LEFT, padx=(0, 4))
        tip(acc, "phase3.raster.accuracy")

    def _int_var(self, var: tk.StringVar, default: int) -> int:
        try:
            return int(float(var.get()))
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------ self-poll
    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            state = self._state_getter()
            sig = progress_signature(state)
            if sig != self._sig or self._dirty:
                self._sig = sig
                self._dirty = False
                self._last_state = state
                self._redraw(state)
        except tk.TclError:
            return
        try:
            self._job = self._frame.after(self._poll_ms, self._schedule_poll)
        except tk.TclError:
            self._job = None

    # ---------------------------------------------------------------- draw
    def _redraw(self, state: Optional[Phase3StateSnapshot]) -> None:
        if self._canvas is None:
            return
        window_n = max(2, self._int_var(self._win_var, DEFAULT_WINDOW))
        res = build_traces(state, window_n=window_n, min_den=DEFAULT_MIN_DEN)

        self._fig.clear()
        if len(res) == 0:
            self._total = 0.0
            if self._scrollbar is not None:
                self._scrollbar.set(0.0, 1.0)
            self._fig.text(0.5, 0.5,
                           "No completed trials yet." if state is not None
                           else "Not armed.",
                           ha="center", va="center", color="#888888")
            self._canvas.draw_idle()
            return

        ax = self._fig.subplots(1, 1)
        self._total = float(res.trial[-1]) if res.trial else 0.0

        self._draw_bars(ax, res)
        self._decorate(ax)
        if self._show_acc.get():
            self._draw_accuracy(ax, res)

        self._apply_viewport(ax)
        self._fig.subplots_adjust(left=0.12, right=0.88, top=0.96, bottom=0.08)
        self._canvas.draw_idle()

    def _draw_bars(self, ax, res: TraceResult) -> None:
        """One vertical bar per completed trial: sign = side, height + opacity =
        outcome, colour = side, with a distinct outline on probe / intervention
        trials. Batched into a single ``bar`` call with per-bar face/edge colours
        (baked-in alpha) so a long session still draws cheaply."""
        xs: List[int] = []
        heights: List[float] = []
        faces = []
        edges = []
        lws: List[float] = []
        for i, trial in enumerate(res.trial):
            side = res.choice[i]
            if side not in (LEFT_LABEL, RIGHT_LABEL):
                continue                    # unusable side -> nothing to point
            correct = res.correct[i]
            mag = _CORRECT_H if correct else _INCORRECT_H
            alpha = _CORRECT_ALPHA if correct else _INCORRECT_ALPHA
            base = _RIGHT_COLOR if side == RIGHT_LABEL else _LEFT_COLOR
            xs.append(trial)
            heights.append(mag if side == RIGHT_LABEL else -mag)
            faces.append(to_rgba(base, alpha))
            # Special trials (probe / intervention) keep their side colour but gain
            # a coloured outline so they separate from free-choice bars.
            if res.probe[i]:
                edges.append(to_rgba(_PROBE_EDGE, 1.0))
                lws.append(1.4)
            elif not res.oob[i]:
                edges.append(to_rgba(_INTERVENTION_EDGE, 1.0))
                lws.append(1.4)
            else:
                edges.append(to_rgba(base, 0.0))    # no visible edge
                lws.append(0.0)
        if xs:
            ax.bar(xs, heights, width=0.9, color=faces, edgecolor=edges,
                   linewidth=lws, zorder=2)

    def _decorate(self, ax) -> None:
        ax.axhline(0.0, color="#999999", lw=0.8, alpha=0.7)
        ax.set_ylim(-(_CORRECT_H + 0.3), _CORRECT_H + 0.3)
        ax.set_yticks([-_CORRECT_H, -_INCORRECT_H, 0, _INCORRECT_H, _CORRECT_H])
        ax.set_yticklabels(["L correct", "L error", "", "R error", "R correct"],
                           fontsize=7)
        ax.set_ylabel("LEFT  <-  choice  ->  RIGHT", fontsize=8)
        ax.set_xlabel("trial", fontsize=8)
        ax.tick_params(axis="both", labelsize=7)
        ax.grid(True, axis="y", alpha=0.15)

    def _draw_accuracy(self, ax, res: TraceResult) -> None:
        """Overlay the rolling accuracy (0-1) on a twin right axis, with a dashed
        0.5 reference. Its own axis keeps the 0-1 rate honest rather than mapping
        it onto the signed bar scale (docs/decisions/0127)."""
        xs: List[int] = []
        ys: List[float] = []
        for i, trial in enumerate(res.trial):
            a = res.accuracy[i]
            if a is not None:
                xs.append(trial)
                ys.append(a)
        ax2 = ax.twinx()
        ax2.set_ylim(0, 1)
        ax2.axhline(0.5, color="#999999", lw=0.8, ls=":", alpha=0.7)
        if xs:
            ax2.plot(xs, ys, color=_ACC_COLOR, lw=1.6, label="rolling accuracy",
                     zorder=3)
        ax2.set_ylabel("rolling accuracy", fontsize=8, color=_ACC_COLOR)
        ax2.tick_params(axis="y", labelsize=7, labelcolor=_ACC_COLOR)
        # The twin shares x with the base axis, so the viewport (set on ``ax``)
        # governs both.

    # --------------------------------------------------------- viewport (x)
    def _parse_width(self) -> Optional[float]:
        """The width-control value in TRIALS, or None for whole-session
        (blank / 0 / unparseable = fully zoomed out)."""
        raw = self._width_var.get().strip()
        if not raw:
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        return v if v > 0.0 else None

    def _apply_viewport(self, ax) -> None:
        """Drive the x-axis from the viewport state and size the scrollbar thumb.
        Never re-autoscales x, so a manual zoom/pan survives the live redraws. The
        +/-0.5 pads centre the integer-trial bars in the window. See
        trace_viewport.compute_viewport (reused in trial units)."""
        xlo, xhi, lo, hi = compute_viewport(
            self._total, self._parse_width(), self._view_start,
            self._follow_latest)
        if self._follow_latest:
            self._view_start = xlo
        ax.set_xlim(xlo + 0.5, xhi + 0.5)
        if self._scrollbar is not None:
            try:
                self._scrollbar.set(lo, hi)
            except tk.TclError:
                pass

    def _on_scroll(self, *args) -> None:
        """ttk.Scrollbar command: pan the viewport (a ``moveto`` fraction or a
        ``scroll`` step). Panning is a manual take-over, so it turns follow off
        (follow-unless-zoomed); Latest turns it back on."""
        width = self._parse_width()
        total = self._total
        if width is None or total <= 0.0:
            return                          # whole session shown; nothing to pan
        if not args:
            return
        how = args[0]
        if how == "moveto" and len(args) >= 2:
            try:
                frac = float(args[1])
            except (TypeError, ValueError):
                return
            self._view_start = frac * total
        elif how == "scroll" and len(args) >= 3:
            try:
                number = float(args[1])
            except (TypeError, ValueError):
                return
            step = width if args[2] == "pages" else max(1.0, width * 0.1)
            self._view_start = self._view_start + number * step
        else:
            return
        self._follow_latest = False
        self._dirty = True

    def _snap_latest(self) -> None:
        """Latest button: re-pin the viewport to the live edge and resume
        auto-following the newest trials."""
        self._follow_latest = True
        self._dirty = True
