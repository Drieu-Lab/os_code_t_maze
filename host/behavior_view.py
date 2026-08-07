"""
behavior_view.py - phase-aware live behavior readout panel for experiment_control.py.

The thin Tk shell around the pure behavior-stats core (behavior_stats.BehaviorStats).
Like the Maze state / Rules tabs, it is a UI-free consumer of the shared maze-state
pipeline (maze_pipeline; ADR-0041): each poll it DRAINS every RatState the shared
worker produced since last poll, turns each into task-semantic events
(task_events.TaskEvents), and folds those - plus the smoothed centroid - into the
Phase 2a training measures, shown both cumulatively and over a rolling window.
Draining (not sampling the latest) is required because the choice/loop events are
one-frame edges that would otherwise be missed between polls.

Phase-aware (docs/decisions/0052). The one panel hosts two switched sub-panels:
the Phase 2a shaping readout above (default), and a Phase 3 cued-task stats panel
shown WHILE a Phase 3 session is armed. The Phase 3 view is driven not by the
local estimator but by the immutable Phase3StateSnapshot the Phase 3 controller
publishes (docs/decisions/0050), resolved each poll through the injected
``phase3_state`` provider and reduced by the pure phase3_stats.compute core. When
no Phase 3 session is armed the panel falls back to the Phase 2a readout, and the
LabelFrame title tracks the active phase.

The Phase 3 sub-panel also shows a session start/stop/duration line, driven by the
Session panel's Start/Stop button via the injected ``session_timing`` provider
(docs/decisions/0060) - distinct from the Phase 2a session line, which is driven by
this panel's own local Start/Stop button.

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

from preview import safe_after
from tooltips import tip

from tmaze.core.phase3 import phase3_stats
from tmaze.core.behavior_stats import BehaviorStats
from tmaze.core.session.session_timer import SessionTimer
from tmaze.core.task_events import TaskEvents

try:
    from tmaze.core.rules_engine import Scale, load_scale
except Exception:  # pragma: no cover - rules engine is a pure sibling core
    Scale = None  # type: ignore
    load_scale = None  # type: ignore

_HERE = os.path.dirname(os.path.abspath(__file__))
SCALE_PATH = os.path.join(_HERE, "rig_scale.json")   # shared with rules_view.py

POLL_INTERVAL_MS = 150   # readout / estimator refresh (~ the Maze state tab rate)

# Sub-panel titles - the LabelFrame text tracks the active phase (ADR-0052).
P2A_TITLE = "Phase 2a behavior  -  live readout"
P3_TITLE = "Phase 3 cued task  -  live stats"


class BehaviorView:
    """Tk module: live Phase 2a training-readout panel (widget/apply/close)."""

    def __init__(self, parent: tk.Misc, tracker=None, camera=None, pipeline=None,
                 phase3_state=None, session_timing=None):
        self._tracker = tracker
        self._camera = camera
        # Shared maze-state pipeline (ADR-0041): consume its RatState stream
        # instead of running a private estimator. Draining every bundle keeps the
        # one-frame choice/loop edges (folded into TaskEvents/BehaviorStats).
        self._pipeline = pipeline
        # Cross-tab Phase 3 provider: a zero-arg callable returning the latest
        # phase3_log.Phase3StateSnapshot (or None), so this panel can go phase-aware
        # (docs/decisions/0050). Stored here; consumed by later work.
        self._phase3_state = phase3_state
        # Cross-panel session-clock provider (docs/decisions/0060): a zero-arg
        # callable returning the Session panel's session_timer.SessionTimerSnapshot
        # (or None). Drives the Phase 3 sub-panel's session line off the Session
        # panel's Start/Stop button (Phase 2a keeps its own local timer/button).
        self._session_timing = session_timing
        self._sub = None
        self.frame = ttk.LabelFrame(parent, text=P2A_TITLE)
        self._closed = False
        self._running = False
        # Which sub-panel is currently packed ("2a" or "3"); _show_phase() only
        # re-packs when this changes, so a steady session doesn't churn widgets.
        self._shown_phase = "2a"

        if tracker is None or pipeline is None:
            ttk.Label(self.frame, padding=16, anchor="center", justify="center",
                      text="Behavior readout unavailable - the tracker core is "
                           "unavailable.").pack(fill=tk.BOTH, expand=True)
            return

        self._sub = pipeline.register()
        self._events = TaskEvents()
        self._stats = BehaviorStats()
        # A second accumulator for the SAME locomotor/compliance measures during a
        # Phase 3 session (docs/decisions/0071): driven automatically whenever a
        # Phase 3 session is armed (not this panel's local Start button) and reset
        # on each Phase 3 arm, so behavior_summary() reports the current session.
        # It is what the end-of-session summary reads for the "excursions" group.
        self._p3_stats = BehaviorStats()
        self._p3_active = False
        self._timer = SessionTimer()
        self._scale = Scale() if Scale is not None else None

        self._vars = {k: tk.StringVar(value="-") for k in (
            "status", "session", "compliance", "lr", "nonstem", "run",
            "throughput", "distance")}
        # Phase 3 stat lines (filled from phase3_stats.compute while a Phase 3
        # session is armed); the key doubles as the tooltip-key suffix. "session"
        # is the Session-button-driven clock line at the top (docs/decisions/0060).
        # The left column holds the cued-task lines; the right column shows the
        # live locomotor/compliance group from self._p3_stats (docs/decisions/0071).
        self._p3_vars = {k: tk.StringVar(value="-") for k in (
            "session", "trials", "accuracy", "bycue", "bias", "reward",
            "strategy", "rt",
            "compliance", "nonstem", "run", "throughput", "distance")}
        self._window = tk.IntVar(value=self._stats.snapshot().window_n)

        self._build_ui()
        self._reload()          # zones + scale from disk, if present
        self._refresh()
        self._schedule_poll()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, info) -> None:
        """No per-line work: this panel is driven by the shared pipeline poll,
        and its MODE-switch reset now runs via on_mode_changed (the shared reset
        seam; docs/decisions/0056)."""

    def on_mode_changed(self, mode: str) -> None:
        """Reset the session counters on a firmware MODE switch (a new session),
        matching the Reward/Lick tallies on this tab."""
        if self._tracker is None:
            return
        self._stats.reset()
        self._stats.mark_gap()
        self._timer.reset()
        self._refresh()

    def behavior_summary(self):
        """The Phase 3 session's locomotor/compliance snapshot
        (behavior_stats.BehaviorSnapshot) for the end-of-session summary
        (docs/decisions/0071), or None if this panel has no accumulator (the
        tracker was unavailable). Read on the Tk thread like the rest of the panel,
        so no locking is needed."""
        p3 = getattr(self, "_p3_stats", None)
        return p3.snapshot() if p3 is not None else None

    def close(self) -> None:
        self._closed = True
        if self._sub is not None:
            self._sub.close()

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

        # Two switched sub-panels share the strip (ADR-0052): the Phase 2a readout
        # (default, packed now) and a Phase 3 cued-task stats panel shown while a
        # Phase 3 session is armed. _show_phase() packs one and forgets the other.
        self._p2a_box = ttk.Frame(self.frame)
        self._p2a_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._p3_box = ttk.Frame(self.frame)

        session_lbl = ttk.Label(self._p2a_box, textvariable=self._vars["session"],
                                anchor="w", font=("TkDefaultFont", 10))
        session_lbl.pack(side=tk.TOP, anchor="w", **pad)
        tip(session_lbl, "behavior.session")

        # Phase 2a measures - one bold value line each. The var key doubles as the
        # tooltip key suffix, except the same-side run (var "run" -> tip "sameside").
        _TIP_KEY = {"run": "sameside"}
        for key in ("compliance", "lr", "nonstem", "run", "throughput", "distance"):
            lbl = ttk.Label(self._p2a_box, textvariable=self._vars[key], anchor="w",
                            font=("TkDefaultFont", 10))
            lbl.pack(side=tk.TOP, anchor="w", **pad)
            tip(lbl, f"behavior.{_TIP_KEY.get(key, key)}")

        # Phase 3 stat lines (filled by _refresh_phase3). Two columns so the
        # panel uses the empty right-hand space without adding height: the
        # cued-task lines on the left, the live locomotor/compliance group
        # (docs/decisions/0071) on the right. The "session" clock spans the top.
        session3_lbl = ttk.Label(self._p3_box, textvariable=self._p3_vars["session"],
                                 anchor="w", font=("TkDefaultFont", 10))
        session3_lbl.grid(row=0, column=0, columnspan=3, sticky="w", **pad)
        tip(session3_lbl, "behavior.phase3.session")

        for r, key in enumerate(("trials", "accuracy", "bycue", "bias", "reward",
                                 "strategy", "rt"), start=1):
            lbl = ttk.Label(self._p3_box, textvariable=self._p3_vars[key], anchor="w",
                            font=("TkDefaultFont", 10))
            lbl.grid(row=r, column=0, sticky="w", **pad)
            tip(lbl, f"behavior.phase3.{key}")

        ttk.Separator(self._p3_box, orient="vertical").grid(
            row=1, column=1, rowspan=7, sticky="ns", padx=8, pady=1)

        # Right column: the locomotor/compliance "excursions" group. A static
        # heading distinguishes these all-excursion counts from the cued trials.
        excursions_lbl = ttk.Label(self._p3_box, text="Excursions",
                                   foreground="#666666", anchor="w")
        excursions_lbl.grid(row=1, column=2, sticky="w", **pad)
        for r, key in enumerate(("compliance", "nonstem", "run", "throughput",
                                 "distance"), start=2):
            lbl = ttk.Label(self._p3_box, textvariable=self._p3_vars[key], anchor="w",
                            font=("TkDefaultFont", 10))
            lbl.grid(row=r, column=2, sticky="w", **pad)
            tip(lbl, f"behavior.phase3.{key}")
        self._p3_box.grid_columnconfigure(2, weight=1)

    # ------------------------------------------------------------- controls
    def _toggle_run(self) -> None:
        self._running = not self._running
        now = time.time()
        if self._running:
            self._stats.mark_gap()   # fresh continuity when (re)starting
            self._timer.start(now)   # wall-clock session span (see docs/decisions/0036)
        else:
            self._timer.stop(now)
        self._run_btn.configure(text="Stop" if self._running else "Start")
        self._refresh()

    def _reset(self) -> None:
        self._stats.reset()
        self._stats.mark_gap()
        self._timer.reset()
        self._refresh()

    def _sync_window(self) -> None:
        try:
            n = int(self._window.get())
        except (tk.TclError, ValueError):
            return
        self._stats.set_window(n)
        self._p3_stats.set_window(n)   # keep the Phase 3 accumulator in step
        self._refresh()

    def _reload(self) -> None:
        """(Re)load the px<->cm scale from disk.

        The zone map is now owned by the shared pipeline (edited in the Maze state
        tab), so only the distance scale is reloaded here; zone changes take
        effect immediately via the shared estimator.
        """
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
            return self._pipeline.zones_valid()
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
        # A Phase 3 session drives the second accumulator automatically (no local
        # Start needed; docs/decisions/0071); detect its arm/disarm edges so the
        # accumulator stays scoped to the current session.
        p3_active = self._phase3_snapshot() is not None
        self._track_phase3_lifecycle(p3_active)
        measuring = self._tracking_running() and self._has_zones()
        if measuring and (self._running or p3_active):
            # Drain EVERY RatState the shared worker produced since last poll and
            # fold each in order: the choice/loop edges are one-frame flags, so
            # sampling only the latest would drop trials landing between polls.
            bundles = self._sub.drain() if self._sub is not None else []
            for b in bundles:
                te = self._events.update(b.state)
                pos = None
                r = b.track_result
                if r is not None:
                    smoothed = getattr(r, "smoothed", None)
                    pos = smoothed if smoothed is not None \
                        else getattr(r, "centroid", None)
                # Feed whichever accumulator(s) are live: the Phase 2a readout
                # (this panel's Start) and/or the Phase 3 session accumulator.
                if self._running:
                    self._stats.update(te, pos_px=pos, scale=self._scale)
                if p3_active:
                    self._p3_stats.update(te, pos_px=pos, scale=self._scale)
        elif self._running or p3_active:
            # Live but not measuring (tracking off / no zones): break continuity
            # so the idle stretch is counted as neither active time nor distance.
            if self._sub is not None:
                self._sub.drain()   # discard stale bundles so we don't fold a backlog
            if self._running:
                self._stats.mark_gap()
            if p3_active:
                self._p3_stats.mark_gap()
        self._refresh()

    def _track_phase3_lifecycle(self, active: bool) -> None:
        """Reset the Phase 3 locomotor accumulator on each arm edge so its counts
        cover the current session and not a prior one (docs/decisions/0071). A
        rising edge (no session -> armed) starts fresh; the falling edge just
        breaks continuity."""
        if active and not self._p3_active:
            self._p3_stats.reset()
        elif not active and self._p3_active:
            self._p3_stats.mark_gap()
        self._p3_active = active

    # --------------------------------------------------------------- render
    def _status_text(self) -> str:
        if not self._running:
            return "stopped - press Start"
        if not self._tracking_running():
            return "waiting for tracking (enable it in the Tracking tab)"
        if not self._has_zones():
            return "waiting for zones (draw them in the Maze state tab)"
        return "running"

    def _phase3_snapshot(self):
        """The active Phase 3 session snapshot (armed), or None. Reading the
        injected provider is cheap and thread-safe: it returns the controller's
        latest immutable Phase3StateSnapshot (docs/decisions/0050). A snapshot
        whose gate has closed (armed=False) is treated as inactive, so the panel
        falls back to the Phase 2a readout between sessions."""
        if self._phase3_state is None:
            return None
        try:
            snap = self._phase3_state()
        except Exception:
            return None
        return snap if (snap is not None and snap.armed) else None

    def _show_phase(self, phase: str) -> None:
        """Pack the requested sub-panel ("2a"/"3") and forget the other, retitling
        the frame. A no-op when that panel is already shown (avoids re-packing on
        every poll)."""
        if phase == self._shown_phase:
            return
        self._shown_phase = phase
        if phase == "3":
            self._p2a_box.pack_forget()
            self._p3_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.frame.configure(text=P3_TITLE)
        else:
            self._p3_box.pack_forget()
            self._p2a_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.frame.configure(text=P2A_TITLE)

    def _window_n(self) -> int:
        try:
            return int(self._window.get())
        except (tk.TclError, ValueError):
            return self._stats.snapshot().window_n

    def _refresh(self) -> None:
        if self._tracker is None:
            return
        snap3 = self._phase3_snapshot()
        if snap3 is not None:
            self._show_phase("3")
            self._refresh_phase3(snap3)
        else:
            self._show_phase("2a")
            self._refresh_phase2a()

    def _refresh_phase3(self, snap3) -> None:
        st = phase3_stats.compute(snap3, window_n=self._window_n())
        n = st.window_n
        self._vars["status"].set(
            f"Phase 3 session active - trial "
            f"{min(st.current_index + 1, st.total)}/{st.total}")
        self._p3_vars["session"].set(self._p3_session_text())
        self._p3_vars["trials"].set(
            f"Completed: {st.completed}/{st.total}   "
            f"correct {st.correct}  incorrect {st.incorrect}")
        self._p3_vars["accuracy"].set(
            f"Accuracy: {_pct(st.accuracy_cum)}  (last {n}: {_pct(st.accuracy_win)})"
            f"   [{st.correct}/{st.completed}]")
        self._p3_vars["bycue"].set(
            f"By cue: HIGH {_pct(st.high_accuracy)} [{st.high_completed}]   "
            f"LOW {_pct(st.low_accuracy)} [{st.low_completed}]")
        self._p3_vars["bias"].set(
            f"Side bias (chose): {_lr(st.lr_bias)}   "
            f"[{st.left_choices}L/{st.right_choices}R]")
        self._p3_vars["reward"].set(
            f"Reward rate: {_pct(st.reward_rate)}   [{st.rewarded}/{st.completed}]")
        self._p3_vars["strategy"].set(
            f"Win-stay: {_pct(st.win_stay)}   Lose-shift: {_pct(st.lose_shift)}")
        self._p3_vars["rt"].set(f"Choice RT: {_rt(st.mean_rt_s)}   [n={st.rt_n}]")
        # Right column: the live locomotor/compliance group over ALL excursions
        # during the Phase 3 session (denied non-stem trips included), from the
        # session-scoped accumulator (docs/decisions/0071). Same formatting as
        # the Phase 2a panel, via the shared helpers below.
        loco = self._p3_stats.snapshot()
        n_loco = self._window_n()
        self._p3_vars["compliance"].set(self._compliance_text(loco, n_loco))
        self._p3_vars["nonstem"].set(self._nonstem_text(loco))
        self._p3_vars["run"].set(self._run_text(loco))
        self._p3_vars["throughput"].set(self._throughput_text(loco))
        self._p3_vars["distance"].set(self._distance_text(loco))

    def _refresh_phase2a(self) -> None:
        snap = self._stats.snapshot()
        n = snap.window_n
        self._vars["status"].set(self._status_text())
        self._vars["session"].set(self._session_text(self._timer.snapshot(time.time())))
        self._vars["compliance"].set(self._compliance_text(snap, n))
        self._vars["lr"].set(
            f"L/R choice: {_lr(snap.lr_cum)}  (last {n}: {_lr(snap.lr_win)})")
        self._vars["nonstem"].set(self._nonstem_text(snap))
        self._vars["run"].set(self._run_text(snap))
        self._vars["throughput"].set(self._throughput_text(snap))
        self._vars["distance"].set(self._distance_text(snap))

    @staticmethod
    def _compliance_text(snap, n) -> str:
        return (f"Stem-gated: {_pct(snap.compliance_cum)}  "
                f"(last {n}: {_pct(snap.compliance_win)})   "
                f"[{snap.stem_choices}/{snap.total_choices}]")

    @staticmethod
    def _nonstem_text(snap) -> str:
        return f"Non-stem (denied): {snap.nonstem_choices}"

    @staticmethod
    def _run_text(snap) -> str:
        return (f"Same-side run: {snap.same_side_run_cur}"
                f"{_side(snap.same_side_run_side)}  "
                f"(max {snap.same_side_run_max}"
                f"{_side(snap.same_side_run_max_side)})")

    @staticmethod
    def _throughput_text(snap) -> str:
        return f"Trials: {snap.stem_choices}   {_rate(snap.trials_per_min)}/min"

    def _distance_text(self, snap) -> str:
        scale_ok = self._scale is not None and self._scale.valid()
        if not (scale_ok or snap.distance_contributed):
            return "Distance: --  (needs scale - set it in the Rules tab)"
        return (f"Distance: {snap.distance_cm / 100.0:.1f} m   "
                f"{_rate(snap.distance_m_per_min)} m/min")

    def _p3_session_text(self) -> str:
        """The Phase 3 session line, driven by the Session panel's Start/Stop
        button (docs/decisions/0060) rather than a local timer. Reuses the same
        formatting as the Phase 2a session line; shows 'not started' when no
        Session panel is wired or none has been started."""
        if self._session_timing is None:
            return "Session: not started"
        try:
            snap = self._session_timing()
        except Exception:
            snap = None
        if snap is None:
            return "Session: not started"
        return self._session_text(snap)

    @staticmethod
    def _session_text(snap) -> str:
        if snap.start_ts is None:
            return "Session: not started"
        start = _clock(snap.start_ts)
        dur = _duration(snap.elapsed_s)
        if snap.running:
            return f"Session: started {start}   -   {dur}"
        return f"Session: {start} - {_clock(snap.stop_ts)}   ({dur})"


def _pct(x: Optional[float]) -> str:
    return "--" if x is None else f"{x * 100:.0f}%"


def _lr(t) -> str:
    return "--" if t is None else f"{t[0] * 100:.0f}/{t[1] * 100:.0f}"


def _rate(x: Optional[float]) -> str:
    return "--" if x is None else f"{x:.1f}"


def _rt(x: Optional[float]) -> str:
    return "--" if x is None else f"{x:.2f} s"


def _side(side: Optional[str]) -> str:
    return {"left": "L", "right": "R"}.get(side, "")


def _clock(ts: Optional[float]) -> str:
    return "--" if ts is None else time.strftime("%H:%M:%S", time.localtime(ts))


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--"
    total = int(max(0.0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"
