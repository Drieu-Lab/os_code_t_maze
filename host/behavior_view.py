"""
behavior_view.py - live "Phase 2a behavior" readout panel for experiment_control.py.

The thin Tk shell around the pure behavior-stats core (behavior_stats.BehaviorStats).
Like the Maze state / Rules tabs, it runs a UI-free consumer of the shared tracker
on the Tk poll loop (single-threaded, no locks): each tick it reduces the tracker's
latest result to a discrete RatState (maze_state.MazeStateEstimator), turns that
into task-semantic events (task_events.TaskEvents), and folds those - plus the
smoothed centroid - into the Phase 2a training measures, shown both cumulatively
and over a rolling window.

It lives in the empty bottom-right of the Rig view tab (beside the solenoid /
reward / lick controls) so the experimenter can watch shaping progress while
hand-rewarding and recording. It is a pure OBSERVER: it never actuates reward.

Distance reuses rules_engine.Scale (rig_scale.json, the Rules tab's "Measure
scale") - the single canonical px<->cm conversion. Depends on tracking being
enabled and zones.json defined; degrades to a quiet "waiting" state otherwise
(distance additionally needs the scale). See docs/decisions/0032 and FW-0007.
"""

from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

import protocol
from preview import safe_after
from tooltips import tip

from behavior_stats import BehaviorStats
from maze_state import (
    MazeStateEstimator, MazeStateParams, ZoneMap, ZONES_PATH, load_zone_map,
)
from task_events import TaskEvents

try:
    from rules_engine import Scale, load_scale
except Exception:  # pragma: no cover - rules engine is a pure sibling core
    Scale = None  # type: ignore
    load_scale = None  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
SCALE_PATH = os.path.join(_HERE, "rig_scale.json")   # shared with rules_view.py

POLL_INTERVAL_MS = 150   # readout / estimator refresh (~ the Maze state tab rate)


class BehaviorView:
    """Tk module: live Phase 2a training-readout panel (widget/apply/close)."""

    def __init__(self, parent: tk.Misc, tracker=None, camera=None):
        self._tracker = tracker
        self._camera = camera
        self.frame = ttk.LabelFrame(parent, text="Phase 2a behavior  -  live readout")
        self._closed = False
        self._running = False

        if tracker is None:
            ttk.Label(self.frame, padding=16, anchor="center", justify="center",
                      text="Behavior readout unavailable - the tracker core is "
                           "unavailable.").pack(fill=tk.BOTH, expand=True)
            return

        self._estimator = MazeStateEstimator(ZoneMap(), MazeStateParams())
        self._events = TaskEvents()
        self._stats = BehaviorStats()
        self._scale = Scale() if Scale is not None else None

        self._vars = {k: tk.StringVar(value="-") for k in (
            "status", "compliance", "lr", "nonstem", "run", "throughput", "distance")}
        self._window = tk.IntVar(value=self._stats.snapshot().window_n)

        self._build_ui()
        self._reload()          # zones + scale from disk, if present
        self._refresh()
        self._schedule_poll()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, line: str) -> None:
        """Reset the session counters on a firmware MODE switch (a new session),
        matching the Reward/Lick tallies on this tab."""
        if self._tracker is None:
            return
        if protocol.mode_name(line) is not None:
            self._stats.reset()
            self._stats.mark_gap()
            self._refresh()

    def close(self) -> None:
        self._closed = True

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = dict(padx=6, pady=1)
        # Controls row.
        ctl = ttk.Frame(self.frame)
        ctl.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(4, 2))
        self._run_btn = ttk.Button(ctl, text="Start", width=7, command=self._toggle_run)
        self._run_btn.pack(side=tk.LEFT, padx=(0, 4))
        tip(self._run_btn, "behavior.start")
        reset_btn = ttk.Button(ctl, text="Reset", width=7, command=self._reset)
        reset_btn.pack(side=tk.LEFT, padx=4)
        tip(reset_btn, "behavior.reset")
        ttk.Label(ctl, text="Window:").pack(side=tk.LEFT, padx=(12, 2))
        win_spin = ttk.Spinbox(ctl, from_=1, to=100, width=5,
                               textvariable=self._window, command=self._sync_window)
        win_spin.pack(side=tk.LEFT)
        tip(win_spin, "behavior.window")
        reload_btn = ttk.Button(ctl, text="Reload zones/scale", command=self._reload)
        reload_btn.pack(side=tk.LEFT, padx=(12, 0))
        tip(reload_btn, "behavior.reload")

        status_lbl = ttk.Label(self.frame, textvariable=self._vars["status"],
                               foreground="#666666")
        status_lbl.pack(side=tk.TOP, anchor="w", **pad)
        tip(status_lbl, "behavior.status")

        # Measures - one bold value line each. The var key doubles as the tooltip
        # key suffix, except the same-side run (var "run" -> tip "sameside").
        _TIP_KEY = {"run": "sameside"}
        for key in ("compliance", "lr", "nonstem", "run", "throughput", "distance"):
            lbl = ttk.Label(self.frame, textvariable=self._vars[key], anchor="w",
                            font=("TkDefaultFont", 10))
            lbl.pack(side=tk.TOP, anchor="w", **pad)
            tip(lbl, f"behavior.{_TIP_KEY.get(key, key)}")

    # ------------------------------------------------------------- controls
    def _toggle_run(self) -> None:
        self._running = not self._running
        if self._running:
            self._stats.mark_gap()   # fresh continuity when (re)starting
        self._run_btn.configure(text="Stop" if self._running else "Start")
        self._refresh()

    def _reset(self) -> None:
        self._stats.reset()
        self._stats.mark_gap()
        self._refresh()

    def _sync_window(self) -> None:
        try:
            self._stats.set_window(int(self._window.get()))
        except (tk.TclError, ValueError):
            return
        self._refresh()

    def _reload(self) -> None:
        """(Re)load the zone map and the px<->cm scale from disk."""
        if os.path.exists(ZONES_PATH):
            try:
                self._estimator.set_zone_map(load_zone_map(ZONES_PATH))
            except Exception:
                pass
        if load_scale is not None and os.path.exists(SCALE_PATH):
            try:
                self._scale = load_scale(SCALE_PATH)
            except Exception:
                pass
        self._refresh()

    # --------------------------------------------------------------- poll
    def _tracking_running(self) -> bool:
        if self._camera is not None:
            try:
                return bool(self._camera.tracking_enabled())
            except Exception:
                return False
        # No camera handle: best-effort - treat a fresh result as "tracking".
        return self._tracker.snapshot_result() is not None

    def _has_zones(self) -> bool:
        try:
            return self._estimator.zone_map.valid()
        except Exception:
            return False

    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._poll()
        finally:
            safe_after(self.frame, POLL_INTERVAL_MS, self._schedule_poll)

    def _poll(self) -> None:
        if self._running and self._tracking_running() and self._has_zones():
            result = self._tracker.snapshot_result()
            s = self._estimator.update(result, time.time())
            te = self._events.update(s)
            pos = None
            if result is not None:
                pos = result.smoothed if result.smoothed is not None else result.centroid
            self._stats.update(te, pos_px=pos, scale=self._scale)
        elif self._running:
            # Running but not measuring (tracking off / no zones): break
            # continuity so the idle stretch is not counted as active time.
            self._stats.mark_gap()
        self._refresh()

    # --------------------------------------------------------------- render
    def _status_text(self) -> str:
        if not self._running:
            return "stopped - press Start"
        if not self._tracking_running():
            return "waiting for tracking (enable it in the Tracking tab)"
        if not self._has_zones():
            return "waiting for zones (draw them in the Maze state tab)"
        return "running"

    def _refresh(self) -> None:
        if self._tracker is None:
            return
        snap = self._stats.snapshot()
        n = snap.window_n
        self._vars["status"].set(self._status_text())
        self._vars["compliance"].set(
            f"Stem-gated: {_pct(snap.compliance_cum)}  "
            f"(last {n}: {_pct(snap.compliance_win)})   "
            f"[{snap.stem_choices}/{snap.total_choices}]")
        self._vars["lr"].set(
            f"L/R choice: {_lr(snap.lr_cum)}  (last {n}: {_lr(snap.lr_win)})")
        self._vars["nonstem"].set(
            f"Non-stem (denied): {snap.nonstem_choices}")
        self._vars["run"].set(
            f"Same-side run: {snap.same_side_run_cur}{_side(snap.same_side_run_side)}  "
            f"(max {snap.same_side_run_max}{_side(snap.same_side_run_max_side)})")
        self._vars["throughput"].set(
            f"Trials: {snap.stem_choices}   "
            f"{_rate(snap.trials_per_min)}/min")
        self._vars["distance"].set(self._distance_text(snap))

    def _distance_text(self, snap) -> str:
        scale_ok = self._scale is not None and self._scale.valid()
        if not (scale_ok or snap.distance_contributed):
            return "Distance: --  (needs scale - set it in the Rules tab)"
        return (f"Distance: {snap.distance_cm / 100.0:.1f} m   "
                f"{_rate(snap.distance_m_per_min)} m/min")


def _pct(x: Optional[float]) -> str:
    return "--" if x is None else f"{x * 100:.0f}%"


def _lr(t) -> str:
    return "--" if t is None else f"{t[0] * 100:.0f}/{t[1] * 100:.0f}"


def _rate(x: Optional[float]) -> str:
    return "--" if x is None else f"{x:.1f}"


def _side(side: Optional[str]) -> str:
    return {"left": "L", "right": "R"}.get(side, "")
