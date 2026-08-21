"""
phase3_traces_view.py - live Phase 3 within-session metric traces as a Tk panel.

A thin Tk shell around the pure ``phase3_traces.build_traces`` core: a stack of
matplotlib subplots that plot how the session's behavioural measures move over
wall-clock time, so an operator can watch a rat drift toward disengagement (its
rolling inter-trial interval climbing) or a strategy emerge during a run instead
of only reading the latest scalar in the text panel (docs/decisions/0114). It is
the plotted twin of the behavior readout (``behavior_view`` / ``phase3_stats``)
and reads the SAME immutable ``Phase3StateSnapshot`` the trial ribbon does
(docs/decisions/0050).

Pull-mode only: pass a ``state_getter`` and the widget self-polls every
``poll_ms``, redrawing when the published table changes OR when one of its own
controls changes (the snapshot is unchanged then, so the change-gate alone would
miss it).

Controls (the configurable aspects):
  - disengaged-gap line (s): the horizontal ITI threshold; default 45 s, matching
    ``analysis/mode_label.ModeThresholds.disengaged_gap_s``.
  - window (trials): the rolling-window size fed to ``build_traces``.
  - last N min: clamps the shared x-axis to the most recent N minutes (blank/0 =
    whole session); view-only.
  - per-subplot show/hide toggles (ITI / accuracy / strategy / bias): hidden
    subplots are dropped so the rest gain height.

Module contract (so the Rig view / a tab can register it like any other module):
    widget() -> the Tk widget to place
    apply(info) -> None   no-op (it reads the snapshot, not serial lines)
    close() -> None       cancel the self-poll
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, List, Optional, Tuple

from tmaze.core.phase3.phase3_log import Phase3StateSnapshot
from tmaze.core.phase3.phase3_traces import (
    DEFAULT_DISENGAGED_GAP_S, DEFAULT_MIN_DEN, TraceResult, build_traces)
from tmaze.core.phase3.phase3_stats import DEFAULT_WINDOW
from tmaze.ui.trial_ribbon_view import progress_signature

try:
    from tmaze.ui.tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# matplotlib is an optional dependency (as in records_manager.py); guard the
# import so importing this module - and experiment_control.py - never crashes
# when it is absent. The panel then shows a one-line "install matplotlib" note.
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    _MPL_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover - depends on host environment
    FigureCanvasTkAgg = None  # type: ignore
    Figure = None  # type: ignore
    _MPL_ERR = f"{type(e).__name__}: {e}"

# Poll cadence: a touch slower than the ribbon's 100 ms since a redraw repaints a
# matplotlib canvas; still well within a trial, and gated so a steady session
# doesn't churn.
POLL_INTERVAL_MS = 250

# Shading colour for INTERVENTION (corrective) block spans (a faint purple, to
# echo the ribbon's corrective outline).
_BLOCK_SHADE = "#8a2be2"

_UNSET = object()

# The four subplot groups, in top-to-bottom order: (key, checkbox label).
_GROUPS: Tuple[Tuple[str, str], ...] = (
    ("iti", "ITI"),
    ("accuracy", "Accuracy"),
    ("strategy", "Strategy"),
    ("bias", "Bias"),
)


class Phase3TracesView:
    """Tk widget: stacked live trace subplots drawn from a Phase3StateSnapshot."""

    def __init__(self, parent: tk.Misc, *,
                 state_getter: Callable[[], Optional[Phase3StateSnapshot]],
                 poll_ms: int = POLL_INTERVAL_MS,
                 text: str = "Session metric traces"):
        self._state_getter = state_getter
        self._poll_ms = poll_ms
        self._closed = False
        self._job: Optional[str] = None
        self._sig = _UNSET
        self._dirty = True                 # force the first draw
        self._last_state: Optional[Phase3StateSnapshot] = None

        self._frame = ttk.LabelFrame(parent, text=text)
        tip(self._frame, "phase3.traces")

        if Figure is None:
            ttk.Label(
                self._frame,
                text=("matplotlib is not installed - the trace panel is "
                      f"unavailable.\n({_MPL_ERR})\nInstall it with: "
                      "pip install matplotlib"),
                foreground="#b3261e", justify="left").pack(
                    side=tk.TOP, anchor="w", padx=8, pady=8)
            self._canvas = None
            return

        self._build_controls(self._frame)

        self._fig = Figure(figsize=(6.6, 6.0), dpi=100)
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
    def _build_controls(self, parent: tk.Misc) -> None:
        bar = ttk.Frame(parent)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)

        def _mark_dirty(*_a) -> None:
            self._dirty = True

        ttk.Label(bar, text="Disengaged (s):").pack(side=tk.LEFT, padx=(0, 2))
        self._gap_var = tk.StringVar(value=str(int(DEFAULT_DISENGAGED_GAP_S)))
        gap = ttk.Spinbox(bar, from_=1, to=600, increment=5, width=5,
                          textvariable=self._gap_var, command=_mark_dirty)
        gap.pack(side=tk.LEFT, padx=(0, 10))
        self._gap_var.trace_add("write", _mark_dirty)
        tip(gap, "phase3.traces.gap")

        ttk.Label(bar, text="Window:").pack(side=tk.LEFT, padx=(0, 2))
        self._win_var = tk.StringVar(value=str(DEFAULT_WINDOW))
        win = ttk.Spinbox(bar, from_=2, to=200, increment=1, width=5,
                          textvariable=self._win_var, command=_mark_dirty)
        win.pack(side=tk.LEFT, padx=(0, 10))
        self._win_var.trace_add("write", _mark_dirty)
        tip(win, "phase3.traces.window")

        ttk.Label(bar, text="Last N min:").pack(side=tk.LEFT, padx=(0, 2))
        self._xwin_var = tk.StringVar(value="")   # blank = whole session
        xwin = ttk.Spinbox(bar, from_=0, to=600, increment=5, width=5,
                           textvariable=self._xwin_var, command=_mark_dirty)
        xwin.pack(side=tk.LEFT, padx=(0, 10))
        self._xwin_var.trace_add("write", _mark_dirty)
        tip(xwin, "phase3.traces.xwindow")

        # Per-subplot show/hide toggles.
        self._show: dict[str, tk.BooleanVar] = {}
        for key, label in _GROUPS:
            var = tk.BooleanVar(value=True)
            self._show[key] = var
            cb = ttk.Checkbutton(bar, text=label, variable=var,
                                 command=_mark_dirty)
            cb.pack(side=tk.LEFT, padx=(0, 4))
            tip(cb, "phase3.traces.toggle", group=label)

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
        gap_s = float(max(1, self._int_var(self._gap_var, int(DEFAULT_DISENGAGED_GAP_S))))
        res = build_traces(state, window_n=window_n, min_den=DEFAULT_MIN_DEN)

        self._fig.clear()
        visible = [key for key, _ in _GROUPS if self._show[key].get()]
        if not visible or len(res) == 0:
            self._fig.text(0.5, 0.5,
                           "No completed trials yet." if state is not None
                           else "Not armed.",
                           ha="center", va="center", color="#888888")
            self._canvas.draw_idle()
            return

        # Shared x in MINUTES since t0; keep None-x points out of the line data.
        t_min = [(v / 60.0) if v is not None else None for v in res.t]
        axes = self._fig.subplots(len(visible), 1, sharex=True, squeeze=False)
        axes = [row[0] for row in axes]
        for ax, key in zip(axes, visible):
            self._draw_group(ax, key, res, t_min, gap_s, state)
            self._shade_blocks(ax, res)
        axes[-1].set_xlabel("session time (min)")

        self._apply_xlimit(axes, t_min)
        self._fig.subplots_adjust(left=0.10, right=0.90, top=0.96, bottom=0.08,
                                  hspace=0.25)
        self._canvas.draw_idle()

    def _line(self, ax, t_min: List[Optional[float]],
              ys: List[Optional[float]], **kw) -> None:
        """Plot one series, dropping None-x points and rendering None-y as gaps."""
        xs: List[float] = []
        vals: List[float] = []
        for x, y in zip(t_min, ys):
            if x is None:
                continue
            xs.append(x)
            vals.append(float("nan") if y is None else float(y))
        if xs:
            ax.plot(xs, vals, **kw)

    def _draw_group(self, ax, key: str, res: TraceResult,
                    t_min: List[Optional[float]], gap_s: float,
                    state: Optional[Phase3StateSnapshot]) -> None:
        if key == "iti":
            self._line(ax, t_min, res.iti_med_s, color="#1f77b4", lw=1.6)
            ax.axhline(gap_s, color="#b3261e", lw=1.0, ls="--", alpha=0.8)
            ax.set_ylabel("ITI med (s)", fontsize=8)
            ax.set_ylim(bottom=0)
        elif key == "accuracy":
            self._line(ax, t_min, res.accuracy, color="#1a7f37", lw=1.6,
                       label="all")
            self._line(ax, t_min, res.high_acc, color="#7f7fff", lw=1.0,
                       label="HIGH cue")
            self._line(ax, t_min, res.low_acc, color="#ff9f40", lw=1.0,
                       label="LOW cue")
            ax.axhline(0.5, color="#999999", lw=0.8, ls=":", alpha=0.7)
            ax.set_ylabel("accuracy", fontsize=8)
            ax.set_ylim(0, 1)
            ax.legend(fontsize=6, loc="lower right", framealpha=0.5, ncol=3)
        elif key == "strategy":
            self._line(ax, t_min, res.side_bias_left, color="#1f77b4", lw=1.2,
                       label="P(left)")
            self._line(ax, t_min, res.switch_rate, color="#d62728", lw=1.2,
                       label="switch")
            self._line(ax, t_min, res.win_stay, color="#2ca02c", lw=1.0,
                       label="win-stay")
            self._line(ax, t_min, res.lose_shift, color="#9467bd", lw=1.0,
                       label="lose-shift")
            self._line(ax, t_min, res.cue_follow, color="#8c564b", lw=1.0,
                       label="cue-follow")
            ax.axhline(0.5, color="#999999", lw=0.8, ls=":", alpha=0.7)
            ax.set_ylabel("strategy", fontsize=8)
            ax.set_ylim(0, 1)
            ax.legend(fontsize=6, loc="lower right", framealpha=0.5, ncol=3)
        elif key == "bias":
            self._line(ax, t_min, res.dir_bias, color="#111111", lw=1.6,
                       label="dir bias")
            self._line(ax, t_min, res.sched_alt_rate, color="#ff7f0e", lw=1.0,
                       label="sched alt rate")
            ax.axhline(0.0, color="#999999", lw=0.8, ls=":", alpha=0.7)
            self._trigger_lines(ax, state)
            ax.set_ylabel("bias / alt", fontsize=8)
            ax.set_ylim(-1.05, 1.05)
            ax.legend(fontsize=6, loc="lower right", framealpha=0.5, ncol=2)
        ax.tick_params(axis="both", labelsize=7)
        ax.grid(True, axis="y", alpha=0.15)

    def _trigger_lines(self, ax, state: Optional[Phase3StateSnapshot]) -> None:
        """Draw the closed-loop auto-trigger thresholds the snapshot reports, so
        the live bias / alt-rate can be read against the level that fires a
        block. No-op for the balanced generator (thresholds 0 / disabled)."""
        if state is None:
            return
        if getattr(state, "debias_enabled", False) and state.trigger_bias > 0:
            for y in (state.trigger_bias, -state.trigger_bias):
                ax.axhline(y, color="#111111", lw=0.8, ls="--", alpha=0.5)
        if getattr(state, "alt_enabled", False) and state.alt_trigger_rate > 0:
            ax.axhline(state.alt_trigger_rate, color="#ff7f0e", lw=0.8,
                       ls="--", alpha=0.5)

    def _shade_blocks(self, ax, res: TraceResult) -> None:
        for span in res.blocks:
            ax.axvspan(span.start_t / 60.0, span.end_t / 60.0,
                       color=_BLOCK_SHADE, alpha=0.10, lw=0, zorder=0)

    def _apply_xlimit(self, axes, t_min: List[Optional[float]]) -> None:
        """Clamp the shared x-axis to the most recent N minutes if requested."""
        xs = [v for v in t_min if v is not None]
        if not xs:
            return
        xmax = max(xs)
        n = self._int_var(self._xwin_var, 0)
        if n > 0:
            axes[0].set_xlim(max(0.0, xmax - n), xmax if xmax > 0 else 1.0)
        else:
            axes[0].set_xlim(0.0, xmax if xmax > 0 else 1.0)
