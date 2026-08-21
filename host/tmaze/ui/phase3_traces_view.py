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
  - window (trials): the rolling-window size fed to ``build_traces`` for the rate
    series (accuracy / strategy / bias).
  - ITI window (trials): a SEPARATE rolling-window size for the ITI median, so
    the engagement signal can be smoothed over a shorter span than the rates
    (blank = follow the main window).
  - width (min) + scrollbar + Latest: a zoomable/scrollable time VIEWPORT over
    the shared x-axis (docs/decisions/0116). Width sets the visible window size
    (blank/0 = whole session); the scrollbar pans it; Latest re-pins to the live
    edge. Panning turns OFF auto-follow (follow-unless-zoomed) so the plot stops
    chasing new trials while you inspect an earlier stretch; the viewport arith-
    metic is the pure ``trace_viewport.compute_viewport``. View-only.
  - per-subplot show/hide toggles (ITI / accuracy / strategy / bias): hidden
    subplots are dropped so the rest gain height.
  - reference (ghost) overlay (docs/decisions/0117): pick a PAST session of the
    same rat and its traces are drawn behind the live ones as faded dashed lines,
    so a run can be compared against an earlier one. The dropdown lists the rat's
    recorded sessions (needs an ``animal_getter``); "None" turns the ghost off. A
    caption flags any run-config differences (cue mapping, generator, tones,
    return-cue, probe) between the reference and the current session. The
    reference is read from disk ONCE on selection (off the poll hot path) and its
    traces are cached, rebuilt only when the window sizes change.

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
from tmaze.core.phase3.trace_viewport import compute_viewport
from tmaze.core.phase3.phase3_log_reader import (
    SessionConfig, SessionRef, describe_config_mismatch, discover_sessions,
    session_config_from_snapshot, snapshot_from_csv)
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
# Shading + marker colour for unrewarded PROBE block spans (a teal, echoing the
# ribbon's probe cell fill; distinct from the purple corrective shade).
_PROBE_SHADE = "#1aa179"

_UNSET = object()

# The four subplot groups, in top-to-bottom order: (key, checkbox label).
_GROUPS: Tuple[Tuple[str, str], ...] = (
    ("iti", "ITI"),
    ("accuracy", "Accuracy"),
    ("strategy", "Strategy"),
    ("bias", "Bias"),
)

# The plotted line series per subplot key: (TraceResult attr, colour, linewidth,
# legend label). Kept as ONE table so the live lines and the faded reference
# (ghost) lines are drawn from the same definition and can't drift apart; the
# per-subplot decorations (threshold lines, y-limits, legend, probe markers) stay
# in _decorate. See docs/decisions/0114 (series) / 0117 (ghost).
_SERIES: dict = {
    "iti": (("iti_med_s", "#1f77b4", 1.6, None),),
    "accuracy": (("accuracy", "#1a7f37", 1.6, "all"),
                 ("high_acc", "#7f7fff", 1.0, "HIGH cue"),
                 ("low_acc", "#ff9f40", 1.0, "LOW cue")),
    "strategy": (("side_bias_left", "#1f77b4", 1.2, "P(left)"),
                 ("switch_rate", "#d62728", 1.2, "switch"),
                 ("win_stay", "#2ca02c", 1.0, "win-stay"),
                 ("lose_shift", "#9467bd", 1.0, "lose-shift"),
                 ("cue_follow", "#8c564b", 1.0, "cue-follow")),
    "bias": (("dir_bias", "#111111", 1.6, "dir bias"),
             ("sched_alt_rate", "#ff7f0e", 1.0, "sched alt rate")),
}


class Phase3TracesView:
    """Tk widget: stacked live trace subplots drawn from a Phase3StateSnapshot."""

    def __init__(self, parent: tk.Misc, *,
                 state_getter: Callable[[], Optional[Phase3StateSnapshot]],
                 animal_getter: Optional[Callable[[], str]] = None,
                 poll_ms: int = POLL_INTERVAL_MS,
                 text: str = "Session metric traces"):
        self._state_getter = state_getter
        # Optional: the current rat id, used to list its past sessions for the
        # reference (ghost) overlay. Without it the reference control is hidden.
        self._animal_getter = animal_getter
        self._poll_ms = poll_ms
        self._closed = False
        self._job: Optional[str] = None
        self._sig = _UNSET
        self._dirty = True                 # force the first draw
        self._last_state: Optional[Phase3StateSnapshot] = None

        # Reference (ghost) overlay state (docs/decisions/0117). The chosen past
        # session is read + reconstructed ONCE on selection; its build_traces
        # result is cached and rebuilt only when the window sizes change.
        self._refs: List[SessionRef] = []
        self._ref_snapshot: Optional[Phase3StateSnapshot] = None
        self._ref_config: Optional[SessionConfig] = None
        self._ref_selected: Optional[SessionRef] = None
        self._ref_traces: Optional[TraceResult] = None
        self._ref_cache_key: Optional[Tuple[int, Optional[int]]] = None

        # Interactive time viewport (docs/decisions/0116): the visible window is
        # width (zoom) + start (pan) + follow-latest flag. ``_total_min`` is the
        # data's full x-extent, refreshed each redraw so a scrollbar drag can map
        # its fraction back to minutes. Follow-unless-zoomed starts ON.
        self._view_start = 0.0
        self._follow_latest = True
        self._total_min = 0.0
        self._scrollbar: Optional[ttk.Scrollbar] = None

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
        self._build_reference_bar(self._frame)

        # Horizontal scrollbar + Latest button (the viewport's pan controls),
        # pinned under the canvas. Packed BEFORE the canvas so the expanding
        # canvas fills the space above it.
        navbar = ttk.Frame(self._frame)
        navbar.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 4))
        latest = ttk.Button(navbar, text="Latest", width=7,
                            command=self._snap_latest)
        latest.pack(side=tk.RIGHT, padx=(4, 0))
        tip(latest, "phase3.traces.latest")
        self._scrollbar = ttk.Scrollbar(navbar, orient=tk.HORIZONTAL,
                                        command=self._on_scroll)
        self._scrollbar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._scrollbar.set(0.0, 1.0)
        tip(self._scrollbar, "phase3.traces.scroll")

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

        # A separate, usually shorter window for the ITI engagement trace; blank
        # follows the main window.
        ttk.Label(bar, text="ITI window:").pack(side=tk.LEFT, padx=(0, 2))
        self._iti_win_var = tk.StringVar(value="")
        iti_win = ttk.Spinbox(bar, from_=2, to=200, increment=1, width=5,
                              textvariable=self._iti_win_var, command=_mark_dirty)
        iti_win.pack(side=tk.LEFT, padx=(0, 10))
        self._iti_win_var.trace_add("write", _mark_dirty)
        tip(iti_win, "phase3.traces.iti_window")

        # Viewport WIDTH (zoom): blank/0 = whole session. Changing it keeps the
        # current follow state (zooming while following stays pinned to the live
        # edge); only PANNING the scrollbar turns follow off. See _on_scroll.
        ttk.Label(bar, text="Width (min):").pack(side=tk.LEFT, padx=(0, 2))
        self._width_var = tk.StringVar(value="")   # blank = whole session
        width = ttk.Spinbox(bar, from_=0, to=600, increment=5, width=5,
                            textvariable=self._width_var, command=_mark_dirty)
        width.pack(side=tk.LEFT, padx=(0, 10))
        self._width_var.trace_add("write", _mark_dirty)
        tip(width, "phase3.traces.width")

        # Per-subplot show/hide toggles.
        self._show: dict[str, tk.BooleanVar] = {}
        for key, label in _GROUPS:
            var = tk.BooleanVar(value=True)
            self._show[key] = var
            cb = ttk.Checkbutton(bar, text=label, variable=var,
                                 command=_mark_dirty)
            cb.pack(side=tk.LEFT, padx=(0, 4))
            tip(cb, "phase3.traces.toggle", group=label)

        # Probe overlay toggle (not a subplot of its own): shades the unrewarded
        # probe spans across every subplot and overlays each block's probe accuracy
        # on the Accuracy subplot. See probe_blocks.py / docs/decisions/0115-probe.
        self._show_probe = tk.BooleanVar(value=True)
        pcb = ttk.Checkbutton(bar, text="Probe", variable=self._show_probe,
                              command=_mark_dirty)
        pcb.pack(side=tk.LEFT, padx=(0, 4))
        tip(pcb, "phase3.traces.toggle", group="Probe")

    def _build_reference_bar(self, parent: tk.Misc) -> None:
        """Reference (ghost) overlay controls: a session dropdown + refresh + the
        config-mismatch caption. Only shown when an ``animal_getter`` was wired
        (otherwise there's no rat id to list past sessions for)."""
        self._mismatch_var = tk.StringVar(value="")
        if self._animal_getter is None:
            return
        bar = ttk.Frame(parent)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 2))

        ttk.Label(bar, text="Reference:").pack(side=tk.LEFT, padx=(0, 2))
        self._ref_var = tk.StringVar(value="None")
        combo = ttk.Combobox(bar, textvariable=self._ref_var, state="readonly",
                             width=22, values=["None"],
                             postcommand=self._refresh_sessions)
        combo.pack(side=tk.LEFT, padx=(0, 4))
        combo.bind("<<ComboboxSelected>>", self._on_reference_selected)
        self._ref_combo = combo
        tip(combo, "phase3.traces.reference")

        refresh = ttk.Button(bar, text="\u21bb", width=3,
                            command=self._refresh_sessions)
        refresh.pack(side=tk.LEFT, padx=(0, 4))
        tip(refresh, "phase3.traces.ref_refresh")

        caption = ttk.Label(bar, textvariable=self._mismatch_var,
                            foreground="#8a6d00", font=("", 8))
        caption.pack(side=tk.LEFT, padx=(6, 0))
        tip(caption, "phase3.traces.mismatch")

    def _refresh_sessions(self) -> None:
        """Re-scan the current rat's recorded sessions into the dropdown (off the
        Tk poll - only on refresh / dropdown open). Keeps the current selection if
        it survived; drops the ghost if the picked session vanished."""
        animal = ""
        try:
            animal = self._animal_getter() if self._animal_getter else ""
        except Exception:
            animal = ""
        try:
            self._refs = discover_sessions(animal)
        except Exception:
            self._refs = []
        self._ref_combo["values"] = ["None"] + [r.label for r in self._refs]
        # If a selection is live but no longer listed, fall back to None.
        if self._ref_selected is not None:
            still = any(r.session_id == self._ref_selected.session_id
                        for r in self._refs)
            if not still:
                self._clear_reference()

    def _on_reference_selected(self, _evt=None) -> None:
        """Load the picked past session once (or clear on 'None') and force a
        redraw. Disk read happens here, never on the poll."""
        idx = self._ref_combo.current()
        if idx <= 0:                       # index 0 == "None"
            self._clear_reference()
            self._dirty = True
            return
        ref = self._refs[idx - 1] if 0 <= idx - 1 < len(self._refs) else None
        if ref is None:
            self._clear_reference()
            self._dirty = True
            return
        loaded = snapshot_from_csv(ref.csv_path)
        if loaded is None:
            self._clear_reference()
            self._mismatch_var.set(f"Reference {ref.label}: could not read file.")
            self._dirty = True
            return
        self._ref_snapshot, self._ref_config = loaded
        self._ref_selected = ref
        self._ref_traces = None            # rebuild against the current windows
        self._ref_cache_key = None
        self._dirty = True

    def _clear_reference(self) -> None:
        self._ref_selected = None
        self._ref_snapshot = None
        self._ref_config = None
        self._ref_traces = None
        self._ref_cache_key = None
        self._mismatch_var.set("")
        if getattr(self, "_ref_var", None) is not None:
            self._ref_var.set("None")

    def _ensure_ghost(self, window_n: int,
                      iti_window_n: Optional[int]) -> Optional[TraceResult]:
        """The reference session's traces, rebuilt via the SAME build_traces as the
        live line and cached; recomputed only when the window sizes change. None
        when no reference is selected."""
        if self._ref_snapshot is None:
            return None
        key = (window_n, iti_window_n)
        if self._ref_traces is None or self._ref_cache_key != key:
            self._ref_traces = build_traces(
                self._ref_snapshot, window_n=window_n,
                iti_window_n=iti_window_n, min_den=DEFAULT_MIN_DEN)
            self._ref_cache_key = key
        return self._ref_traces

    def _update_mismatch_caption(self,
                                 state: Optional[Phase3StateSnapshot]) -> None:
        """Set the caption to the config differences between the current session
        and the reference (quiet when they match, blank when no reference)."""
        if self._ref_selected is None or self._ref_config is None:
            self._mismatch_var.set("")
            return
        label = self._ref_selected.label
        if state is None:
            self._mismatch_var.set(f"Reference: {label}")
            return
        current = session_config_from_snapshot(state)
        diffs = describe_config_mismatch(current, self._ref_config)
        if diffs:
            self._mismatch_var.set(f"Reference {label} differs (now→ref): "
                                   + "; ".join(diffs))
        else:
            self._mismatch_var.set(f"Reference {label}: config matches.")

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
        # Blank ITI-window entry means "follow the main window" (None); a value
        # gives the ITI median its own, usually shorter, span.
        raw_iti = self._iti_win_var.get().strip()
        try:
            iti_window_n: Optional[int] = max(2, int(float(raw_iti))) if raw_iti else None
        except (TypeError, ValueError):
            iti_window_n = None
        res = build_traces(state, window_n=window_n, iti_window_n=iti_window_n,
                           min_den=DEFAULT_MIN_DEN)
        ghost = self._ensure_ghost(window_n, iti_window_n)
        self._update_mismatch_caption(state)

        self._fig.clear()
        visible = [key for key, _ in _GROUPS if self._show[key].get()]
        have_current = len(res) > 0
        have_ghost = ghost is not None and len(ghost) > 0
        if not visible or (not have_current and not have_ghost):
            self._total_min = 0.0
            if self._scrollbar is not None:
                self._scrollbar.set(0.0, 1.0)
            self._fig.text(0.5, 0.5,
                           "No completed trials yet." if state is not None
                           else "Not armed.",
                           ha="center", va="center", color="#888888")
            self._canvas.draw_idle()
            return

        # Shared x in MINUTES since each session's own t0; keep None-x points out
        # of the line data. The reference is aligned on ITS OWN elapsed minutes.
        t_min = [(v / 60.0) if v is not None else None for v in res.t]
        ghost_t_min = ([(v / 60.0) if v is not None else None for v in ghost.t]
                       if have_ghost else [])
        # Viewport extent spans both sessions so the full ghost arc can scroll in.
        cur_max = max((v for v in t_min if v is not None), default=0.0)
        ghost_max = max((v for v in ghost_t_min if v is not None), default=0.0)
        self._total_min = max(cur_max, ghost_max)

        axes = self._fig.subplots(len(visible), 1, sharex=True, squeeze=False)
        axes = [row[0] for row in axes]
        for ax, key in zip(axes, visible):
            if have_ghost:
                self._draw_ghost(ax, key, ghost, ghost_t_min)
            if have_current:
                self._draw_group_lines(ax, key, res, t_min)
            self._decorate(ax, key, gap_s, state, res, have_current)
            if have_current:
                self._shade_blocks(ax, res)
                self._shade_probes(ax, res)
        axes[-1].set_xlabel("session time (min)")

        self._apply_viewport(axes)
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

    def _draw_group_lines(self, ax, key: str, res: TraceResult,
                          t_min: List[Optional[float]]) -> None:
        """Plot the live line series for one subplot (from the shared _SERIES
        table); the decorations are added by _decorate."""
        for attr, color, lw, label in _SERIES[key]:
            self._line(ax, t_min, getattr(res, attr), color=color, lw=lw,
                       label=label)

    def _draw_ghost(self, ax, key: str, ref_res: TraceResult,
                    ref_t_min: List[Optional[float]]) -> None:
        """Plot the reference session's SAME series behind the live ones - faded,
        dashed, and thinner so they read as "past" - on the reference's own elapsed
        minutes. No labels (they'd double the legend) and no span shading. Same
        _SERIES table as the live line, so the two can't drift. See ADR-0117."""
        for attr, color, lw, _label in _SERIES[key]:
            self._line(ax, ref_t_min, getattr(ref_res, attr), color=color,
                       lw=max(0.8, lw * 0.6), ls="--", alpha=0.4, zorder=1)

    def _decorate(self, ax, key: str, gap_s: float,
                  state: Optional[Phase3StateSnapshot], res: TraceResult,
                  has_current: bool) -> None:
        """Per-subplot threshold lines, y-limits, legend, and probe markers. The
        legend + probe markers are live-only (skipped when only a ghost is shown)."""
        if key == "iti":
            ax.axhline(gap_s, color="#b3261e", lw=1.0, ls="--", alpha=0.8)
            ax.set_ylabel("ITI med (s)", fontsize=8)
            ax.set_ylim(bottom=0)
        elif key == "accuracy":
            ax.axhline(0.5, color="#999999", lw=0.8, ls=":", alpha=0.7)
            if has_current:
                self._plot_probe_accuracy(ax, res)
            ax.set_ylabel("accuracy", fontsize=8)
            ax.set_ylim(0, 1)
            if has_current:
                ax.legend(fontsize=6, loc="lower right", framealpha=0.5, ncol=3)
        elif key == "strategy":
            ax.axhline(0.5, color="#999999", lw=0.8, ls=":", alpha=0.7)
            ax.set_ylabel("strategy", fontsize=8)
            ax.set_ylim(0, 1)
            if has_current:
                ax.legend(fontsize=6, loc="lower right", framealpha=0.5, ncol=3)
        elif key == "bias":
            ax.axhline(0.0, color="#999999", lw=0.8, ls=":", alpha=0.7)
            self._trigger_lines(ax, state)
            ax.set_ylabel("bias / alt", fontsize=8)
            ax.set_ylim(-1.05, 1.05)
            if has_current:
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

    def _shade_probes(self, ax, res: TraceResult) -> None:
        """Shade the unrewarded probe spans (teal), gated on the Probe toggle."""
        if not self._show_probe.get():
            return
        for span in res.probe_blocks:
            ax.axvspan(span.start_t / 60.0, span.end_t / 60.0,
                       color=_PROBE_SHADE, alpha=0.12, lw=0, zorder=0)

    def _plot_probe_accuracy(self, ax, res: TraceResult) -> None:
        """Overlay each probe block's accuracy as a diamond at the span midpoint on
        the Accuracy subplot - the latent-knowledge read-out against the reinforced
        line. Gated on the Probe toggle. See docs/decisions/0115-probe."""
        if not self._show_probe.get():
            return
        xs: List[float] = []
        ys: List[float] = []
        for span in res.probe_blocks:
            if span.accuracy is None:
                continue
            xs.append((span.start_t + span.end_t) / 2.0 / 60.0)
            ys.append(span.accuracy)
        if xs:
            ax.plot(xs, ys, marker="D", ls="none", ms=6,
                    color=_PROBE_SHADE, markeredgecolor="#0b5", label="probe")

    # --------------------------------------------------------- viewport (x)
    def _parse_width(self) -> Optional[float]:
        """The width-control value in minutes, or None for whole-session
        (blank / 0 / unparseable = fully zoomed out)."""
        raw = self._width_var.get().strip()
        if not raw:
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        return v if v > 0.0 else None

    def _apply_viewport(self, axes) -> None:
        """Drive the shared x-axis (all subplots share x) from the viewport state
        and size the scrollbar thumb to match. Never re-autoscales x, so a manual
        zoom/pan survives the live redraws. See trace_viewport.compute_viewport."""
        xlo, xhi, lo, hi = compute_viewport(
            self._total_min, self._parse_width(), self._view_start,
            self._follow_latest)
        # When following, remember the resolved left edge so turning follow off
        # (a scrollbar drag) resumes from where the eye already is.
        if self._follow_latest:
            self._view_start = xlo
        axes[0].set_xlim(xlo, xhi)
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
        total = self._total_min
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
            step = width if args[2] == "pages" else max(0.5, width * 0.1)
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
