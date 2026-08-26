"""
experiment_control.py - extensible experiment-control GUI for the T-maze rig.

Connects to the rig broker (rig_broker.py) via RigClient and hosts a set of
modules in a tabbed notebook. The first module is a live maze illustration
(rig_view.RigView). The rig/firmware controls live at the bottom of the Rig view
tab: a "Solenoid test" bar fires R<port><sol>,<ms> commands (the pulse width is
configurable, default DEFAULT_PULSE_MS) so you can map each solenoid by watching
which physical valve clicks and which marker flashes in Rig view, an "Audio cue
test" bar plays host-driven tones (CUE_ON / CUE_OFF) through the speakers so they
can be checked without running a trial, a "Training
mode" bar switches the firmware between Free Roam and the Full task, and a
"Session" panel (bottom-right, under the behavior readout) starts/stops recording
everything (camera + Phase 3) under one shared session id + manifest (ADR-0048).
Both the
tmaze_test and cue-guided-task sketches ack with SOL lines and honor the
per-pulse duration, so the buttons work under either (cue-guided-task fires
non-blocking and in any mode). Future modules (reward control, trial
monitor, ...) drop in by adding an entry to TABS; each module only needs:

    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None  to handle each serial line
    close() -> None           (optional) to release resources on app exit

A TABS entry is (title, [factory, ...]); modules listed together share one tab,
packed left-to-right. An optional third element - a list of sub-tab labels
parallel to the factories - nests those modules as sub-tabs of an inner notebook
instead (the "Setup" tab groups Tracking + Maze state this way; ADR-0053). The
first tab places the live maze schematic
(rig_view.RigView) beside the camera preview/recording (camera_view.CameraView).
The Camera module uses the optional close() hook to stop its threads and finalize
any in-progress recording when the window closes.

NOTE: rig_broker.py must already be running. Use start_rig.bat to launch the
broker and GUIs together, or run "python rig_broker.py" first.

Run from os_code_t_maze/host/:
    python experiment_control.py
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from tmaze.core import protocol
from tmaze.core import cue_volume
from tmaze.io.rig_io import RigClient
from tmaze.ui.rig_view import RigView
from tmaze.ui.camera_view import (CameraView, DEFAULT_OUTPUT_DIR, ORIENTATION,
                         UNDISTORT_ALPHA, CALIBRATION_PATH)
from tmaze.core.reward.reward_volume import (reward_volume_ul, format_volume,
                           DEFAULT_REWARD_PULSE_MS)
from tmaze.core.rig_config import load_rig_config, RigConfig
from tmaze.io.rig_process import find_rig_pids, rig_session_id, taskkill
from tmaze.core.line_router import LineRouter
from tmaze.ui.tooltips import tip

# Markerless tracker + its control tab are optional (need opencv/numpy/pillow);
# guard the imports so the GUI still runs if they're missing.
try:
    from tmaze.vision.tracker import RatTracker
    _TRACKER_ERR = None
except Exception as e:  # pragma: no cover - depends on host environment
    RatTracker = None  # type: ignore
    _TRACKER_ERR = f"{type(e).__name__}: {e}"
try:
    from tmaze.ui.tracker_view import TrackerControlView
except Exception as e:  # pragma: no cover
    TrackerControlView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Position-based rules engine tab (consumes the tracker + landmarks; actuates
# reward via the broker). Optional; guarded so a missing dep never breaks the
# GUI. See docs/decisions/0023.
try:
    from tmaze.ui.rules_view import RulesControlView
except Exception as e:  # pragma: no cover
    RulesControlView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Maze-state tab (consumes the tracker; reduces the centroid to a discrete
# per-tick maze state + zone editor). Optional; guarded like the tabs above.
# See docs/decisions/0025.
try:
    from tmaze.ui.maze_state_view import MazeStateControlView
except Exception as e:  # pragma: no cover
    MazeStateControlView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Cue-volume calibration subtab (Setup): balance HIGH vs LOW speaker loudness and
# save it to host/cue_calibration.json (auto-applied to Phase 3 + the speaker
# test). Pure Tk + the broker send; no tracker/camera. See docs/decisions/0125.
try:
    from tmaze.ui.cue_volume_view import CueVolumeView
except Exception:  # pragma: no cover
    CueVolumeView = None  # type: ignore
# Phase 3 tab: the host-owned cued task (firmware = actuator). Composes the cue
# gate + trial FSM over the shared pipeline; drives CUE_ON,<kind>,<hz> / reward.
# Optional; guarded like the tabs above. See docs/decisions/0046.
try:
    from tmaze.ui.phase3_controller import Phase3ControlView
except Exception as e:  # pragma: no cover
    Phase3ControlView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Task tab: groups the Rules + Phase 3 controllers under one tab with an
# exclusive selector and the single shared Animal ID field (ADR-0053 phase 4).
# Pure Tk container; guarded like the tabs above. See docs/decisions/0053.
try:
    from tmaze.ui.task_view import TaskControlView
except Exception as e:  # pragma: no cover
    TaskControlView = None  # type: ignore
# Shared reward seam: one RewardDispatcher owns the global rate backstop, the
# pulse/volume record, and the R<port> send for BOTH task controllers (Rules +
# Phase 3), so a reward is handled identically whichever task drove it. Stdlib
# only; guarded like the tabs above. See docs/decisions/0054 (FW-0019).
try:
    from tmaze.core.reward.reward_dispatcher import RewardDispatcher
except Exception:  # pragma: no cover
    RewardDispatcher = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Session tab: a thin orchestrator that starts/stops the recorders together and
# writes a shared session manifest (docs/decisions/0048). Needs no tracker (just
# tk + the recorders' public seams), so it is guarded on its own import only.
try:
    from tmaze.ui.session_controller import SessionControlView
except Exception:  # pragma: no cover
    SessionControlView = None  # type: ignore
# Live Phase 2a behavior readout: a read-only tracker consumer that fills the
# empty bottom-right of the Rig view tab. Optional; guarded like the tabs above.
# See docs/decisions/0032.
try:
    from tmaze.ui.behavior_view import BehaviorView
except Exception as e:  # pragma: no cover
    BehaviorView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Trial ribbon (recent / current / upcoming trials): the shared Phase 3 strip,
# shown on the Rig view too so the current trial is visible during a run without
# switching tabs. Reads the published Phase 3 snapshot. Optional; guarded like
# the tabs above. See docs/decisions/0057.
try:
    from tmaze.ui.trial_ribbon_view import TrialRibbonView
except Exception:  # pragma: no cover
    TrialRibbonView = None  # type: ignore
# Trial-control buttons (Skip / Repeat / Back / +Trials / Restart / Pause): the
# operator's steering controls for a live Phase 3 run, in the empty area beside
# the ribbon + live-stats panels on the Rig view. Wired to the live Phase 3
# controller. Optional; guarded like the tabs above. See docs/decisions/0058.
try:
    from tmaze.ui.trial_control_bar import TrialControlBar
except Exception:  # pragma: no cover
    TrialControlBar = None  # type: ignore
# Live session-metric trace panel: stacked matplotlib subplots (rolling ITI /
# accuracy / strategy / bias) over wall-clock session time, on its own top-level
# tab. Reads the published Phase 3 snapshot (pull mode). Optional; guarded like
# the tabs above (matplotlib is guarded inside the view itself). See ADR-0114.
try:
    from tmaze.ui.phase3_traces_view import Phase3TracesView
except Exception:  # pragma: no cover
    Phase3TracesView = None  # type: ignore
# Trial-by-trial choice raster: a companion panel beside the trace plots on the
# Metrics tab (one bar per completed trial, side/outcome encoded, rolling accuracy
# overlaid). Reads the same published snapshot; guarded like the traces view
# (matplotlib is guarded inside it). See ADR-0127.
try:
    from tmaze.ui.phase3_choice_raster_view import Phase3ChoiceRasterView
except Exception:  # pragma: no cover
    Phase3ChoiceRasterView = None  # type: ignore
# One shared maze-state pipeline (pure stdlib): a single estimator driven on the
# camera tracking worker that fans a RatState stream out to the three tabs above,
# replacing their per-tab estimators. Optional; guarded like the tabs above.
# See docs/decisions/0041.
try:
    from tmaze.vision.maze_pipeline import MazeStatePipeline
    from tmaze.core.maze_state import (
        MazeStateParams, ZoneMap, ZONES_PATH, load_zone_map,
    )
except Exception as e:  # pragma: no cover
    MazeStatePipeline = None  # type: ignore
    MazeStateParams = ZoneMap = None  # type: ignore
    ZONES_PATH = ""  # type: ignore
    load_zone_map = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"

# ================= CONFIG =================
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5555
DEFAULT_PULSE_MS = 150   # matches REWARD_PULSE_MS in firmware/common/rig_config.h

# Audio-cue speaker-test tones. The HIGH/LOW frequencies mirror the Phase 3
# choice tones and the clamp range guards the DAC; all four match
# firmware/common/rig_config.h (TONE_HIGH_HZ / TONE_LOW_HZ / CUE_HZ_MIN /
# CUE_HZ_MAX). DEFAULT_CUE_BURST_MS is a host-side auto-off so a test tone
# doesn't play forever (it plays for the burst, then the host sends CUE_OFF).
CUE_TEST_HIGH_HZ = 8000   # TONE_HIGH_HZ
CUE_TEST_LOW_HZ = 4000    # TONE_LOW_HZ
CUE_HZ_MIN, CUE_HZ_MAX = 500, 20000
DEFAULT_CUE_BURST_MS = 500

# Firmware pin map (single source of truth) - used to label the lick counters by
# port role. Degrades gracefully if the header can't be parsed. NOTE: trusts
# flashed == source (see rig_config.py) - reflash the Due after editing it.
_RIG_CFG = load_rig_config()
N_LICK = len(_RIG_CFG.lick_pins) if _RIG_CFG.ok and _RIG_CFG.lick_pins else 3

MODE_LABELS = {"FREEROAM": "Free Roam", "FULLTASK": "Full task"}


class RewardTally:
    """Running reward-volume total for the current session (see ADR-0019)."""

    def __init__(self) -> None:
        self._totals = {"pulses": 0, "ul": 0.0,
                        "auto_pulses": 0, "auto_ul": 0.0,
                        "man_pulses": 0, "man_ul": 0.0}
        self.total_var = tk.StringVar()
        self.detail_var = tk.StringVar()
        self.reset()

    def reset(self) -> None:
        for k in self._totals:
            self._totals[k] = 0 if k.endswith("pulses") else 0.0
        self._render()

    def add(self, dur_ms: int, manual: bool) -> None:
        vol = reward_volume_ul(dur_ms)
        self._totals["pulses"] += 1
        self._totals["ul"] += vol
        prefix = "man" if manual else "auto"
        self._totals[f"{prefix}_pulses"] += 1
        self._totals[f"{prefix}_ul"] += vol
        self._render()

    def _render(self) -> None:
        self.total_var.set(
            f"{format_volume(self._totals['ul'])}  "
            f"({self._totals['pulses']} pulses)")
        self.detail_var.set(
            f"auto {format_volume(self._totals['auto_ul'])} "
            f"({self._totals['auto_pulses']})  /  "
            f"manual {format_volume(self._totals['man_ul'])} "
            f"({self._totals['man_pulses']})")


class LickTally:
    """Per-port rising-edge lick counts for the current session."""

    def __init__(self, n_lick: int, rig_cfg: RigConfig) -> None:
        self._n = n_lick
        self._rig_cfg = rig_cfg
        self._counts = [0] * n_lick
        self.vars = [tk.StringVar() for _ in range(n_lick)]
        self.reset()

    def _name(self, i: int) -> str:
        role = self._rig_cfg.lick_role(i) if self._rig_cfg.ok else None
        return f"L{i} ({role})" if role else f"L{i}"

    def reset(self) -> None:
        for i in range(self._n):
            self._counts[i] = 0
        self._render()

    def add(self, i: int) -> None:
        if 0 <= i < self._n:
            self._counts[i] += 1
            self._render()

    def _render(self) -> None:
        for i in range(self._n):
            self.vars[i].set(f"{self._name(i)}: {self._counts[i]}")


def build_mode_controls(parent: tk.Misc, client: RigClient,
                        mode_var: tk.StringVar) -> None:
    """Training-mode switch bar (Free Roam / Full task)."""
    mode_frame = ttk.LabelFrame(
        parent,
        text="Training mode  -  switch the firmware between Free Roam and the Full task")
    mode_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)

    def confirm_and_send_mode(label: str, cmd: str) -> None:
        if messagebox.askyesno(
                "Switch training mode",
                f"Switch to {label}?\n\nThis (re)starts the session on the Due "
                "(a new trial sequence is generated for the Full task)."):
            client.send(cmd)

    free_btn = ttk.Button(
        mode_frame, text="Free Roam",
        command=lambda: confirm_and_send_mode("Free Roam", "MODE,FREE"))
    free_btn.grid(row=0, column=0, padx=4, pady=4)
    tip(free_btn, "mode.free")
    full_btn = ttk.Button(
        mode_frame, text="Full task",
        command=lambda: confirm_and_send_mode("Full task", "MODE,FULL"))
    full_btn.grid(row=0, column=1, padx=4, pady=4)
    tip(full_btn, "mode.full")
    mode_lbl = ttk.Label(mode_frame, textvariable=mode_var)
    mode_lbl.grid(row=0, column=2, sticky="w", padx=(12, 4), pady=4)
    tip(mode_lbl, "mode.label")


def build_solenoid_panel(parent: tk.Misc, client: RigClient,
                         on_reward_fire=None) -> None:
    """Solenoid mapping helper: fire R<port><sol> and watch Rig view markers.

    ``on_reward_fire`` (callable(ms)) is notified whenever the operator fires the
    REWARD SPOUT here (sol == the reward solenoid index), so the Session panel can
    attribute that operator-triggered water at its source - the firmware echoes it
    identically to task reward on the wire, so this is the only place the two can
    be told apart (docs/decisions/0084)."""
    reward_sol = _RIG_CFG.reward_sol_index if _RIG_CFG.reward_sol_index is not None else 1

    sol_frame = ttk.LabelFrame(
        parent,
        text="Solenoid test  -  fire R<port><sol>, watch the valve and its marker")
    sol_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)

    pulse_var = tk.StringVar(value=str(DEFAULT_PULSE_MS))

    def send_sol(port: int, sol: int) -> None:
        raw = pulse_var.get().strip()
        try:
            ms = int(raw)
        except ValueError:
            ms = DEFAULT_PULSE_MS
        ms = max(1, min(5000, ms))
        pulse_var.set(str(ms))
        client.send(f"R{port}{sol},{ms}")
        # Attribute an operator reward-spout fire at its source (docs/decisions/
        # 0084). The port is not constrained: any reward-spout fire is operator
        # water, and the central port is the reward well (docs/decisions/0007).
        if sol == reward_sol and on_reward_fire is not None:
            try:
                on_reward_fire(ms)
            except Exception:
                pass

    headers = ("", "reward", "reward", "reward", "drain", "rinse")
    header_tips = (None, "sol.header.reward", "sol.header.reward",
                   "sol.header.reward", "sol.header.drain", "sol.header.rinse")
    for col, text in enumerate(headers):
        hdr = ttk.Label(sol_frame, text=text, font=("TkDefaultFont", 8))
        hdr.grid(row=0, column=col, padx=2, pady=(2, 0))
        if header_tips[col]:
            tip(hdr, header_tips[col])
    port_rows = (("Left", 0), ("Right", 1), ("Center / start", 2))
    for row, (port_name, port) in enumerate(port_rows, start=1):
        ttk.Label(sol_frame, text=f"{port_name} (port {port}):").grid(
            row=row, column=0, sticky="w", padx=(6, 8), pady=2)
        for sol in range(5):
            btn = ttk.Button(sol_frame, text=f"R{port}{sol}", width=5,
                             command=lambda p=port, s=sol: send_sol(p, s))
            btn.grid(row=row, column=sol + 1, padx=2, pady=2)
            tip(btn, "sol.button", port=port, sol=sol)

    ttk.Label(sol_frame, text="Pulse (ms):").grid(
        row=4, column=0, sticky="e", padx=(6, 8), pady=(6, 4))
    pulse_spin = ttk.Spinbox(sol_frame, from_=1, to=5000, increment=10, width=6,
                             textvariable=pulse_var)
    pulse_spin.grid(row=4, column=1, columnspan=2, sticky="w", pady=(6, 4))
    tip(pulse_spin, "sol.pulse")
    ttk.Label(sol_frame, text="per click (<= 5000)", font=("TkDefaultFont", 8)).grid(
        row=4, column=3, columnspan=3, sticky="w", pady=(6, 4))


def build_cue_panel(parent: tk.Misc, client: RigClient) -> None:
    """Speaker-test helper: play a host-driven cue tone (CUE_ON / CUE_OFF).

    Mirrors the solenoid test bar but for the audio cues: each button plays a
    tone through the rig speakers so the speakers (and the matching Open Ephys
    tone TTL) can be checked without running a trial. Uses PROTOCOL v6's
    host-supplied tone identity (CUE_ON,<kind>,<hz>), which works in any mode.
    """
    cue_frame = ttk.LabelFrame(
        parent,
        text="Audio cue test  -  play a tone through the speakers (CUE_ON / CUE_OFF)")
    cue_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)

    kind_var = tk.StringVar(value="CHOICE")
    burst_var = tk.StringVar(value=str(DEFAULT_CUE_BURST_MS))
    custom_var = tk.StringVar(value=str(CUE_TEST_HIGH_HZ))
    # One pending auto-off timer id, so a new press (or Stop) cancels the old one
    # instead of leaving a stale CUE_OFF that would cut a fresh tone short.
    pending = {"after_id": None}

    def stop() -> None:
        if pending["after_id"] is not None:
            parent.after_cancel(pending["after_id"])
            pending["after_id"] = None
        client.send("CUE_OFF")

    def play(hz: int) -> None:
        hz = max(CUE_HZ_MIN, min(CUE_HZ_MAX, hz))
        # The firmware no-ops CUE_ON while a tone is already playing, so stop
        # any current/pending tone first, then start the new one. Apply the saved
        # per-cue volume balance so the test bar previews what Phase 3 will play
        # (the "Cue volume" Setup subtab is where that balance is tuned; ADR-0125).
        stop()
        client.send(f"CUE_ON,{kind_var.get()},{hz},{cue_volume.gain_pct_for(hz)}")
        raw = burst_var.get().strip()
        try:
            ms = int(raw)
        except ValueError:
            ms = DEFAULT_CUE_BURST_MS
        ms = max(1, min(5000, ms))
        burst_var.set(str(ms))
        pending["after_id"] = parent.after(ms, stop)

    def play_custom() -> None:
        raw = custom_var.get().strip()
        try:
            hz = int(raw)
        except ValueError:
            hz = CUE_TEST_HIGH_HZ
        hz = max(CUE_HZ_MIN, min(CUE_HZ_MAX, hz))
        custom_var.set(str(hz))
        play(hz)

    # Row 0: preset tones + Stop.
    high_btn = ttk.Button(cue_frame, text=f"HIGH ({CUE_TEST_HIGH_HZ} Hz)",
                          command=lambda: play(CUE_TEST_HIGH_HZ))
    high_btn.grid(row=0, column=0, padx=4, pady=(4, 2))
    tip(high_btn, "cue.high")
    low_btn = ttk.Button(cue_frame, text=f"LOW ({CUE_TEST_LOW_HZ} Hz)",
                         command=lambda: play(CUE_TEST_LOW_HZ))
    low_btn.grid(row=0, column=1, padx=4, pady=(4, 2))
    tip(low_btn, "cue.low")
    stop_btn = ttk.Button(cue_frame, text="Stop", command=stop)
    stop_btn.grid(row=0, column=2, padx=4, pady=(4, 2))
    tip(stop_btn, "cue.stop")

    # Row 1: custom frequency.
    ttk.Label(cue_frame, text="Custom (Hz):").grid(
        row=1, column=0, sticky="e", padx=(6, 8), pady=2)
    custom_spin = ttk.Spinbox(cue_frame, from_=CUE_HZ_MIN, to=CUE_HZ_MAX,
                              increment=100, width=7, textvariable=custom_var)
    custom_spin.grid(row=1, column=1, sticky="w", pady=2)
    tip(custom_spin, "cue.custom")
    play_btn = ttk.Button(cue_frame, text="Play", command=play_custom)
    play_btn.grid(row=1, column=2, padx=4, pady=2)
    tip(play_btn, "cue.play")

    # Row 2: cue kind + burst duration (the two shared settings).
    ttk.Label(cue_frame, text="Cue kind:").grid(
        row=2, column=0, sticky="e", padx=(6, 8), pady=(2, 4))
    kind_combo = ttk.Combobox(cue_frame, textvariable=kind_var, width=8,
                              state="readonly", values=("CHOICE", "RETURN"))
    kind_combo.grid(row=2, column=1, sticky="w", pady=(2, 4))
    tip(kind_combo, "cue.kind")
    ttk.Label(cue_frame, text="Burst (ms):").grid(
        row=3, column=0, sticky="e", padx=(6, 8), pady=(2, 4))
    burst_spin = ttk.Spinbox(cue_frame, from_=1, to=5000, increment=50, width=6,
                             textvariable=burst_var)
    burst_spin.grid(row=3, column=1, sticky="w", pady=(2, 4))
    tip(burst_spin, "cue.burst")
    ttk.Label(cue_frame, text="auto-off (<= 5000)", font=("TkDefaultFont", 8)).grid(
        row=3, column=2, sticky="w", pady=(2, 4))


def build_reward_panel(parent: tk.Misc, tally: RewardTally) -> None:
    reward_frame = ttk.LabelFrame(
        parent,
        text="Reward dispensed  -  running total this session (resets on a mode switch)")
    reward_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)
    ttk.Label(reward_frame, text="Total:").grid(
        row=0, column=0, sticky="e", padx=(6, 4), pady=4)
    reward_total_lbl = ttk.Label(reward_frame, textvariable=tally.total_var,
                                 font=("TkDefaultFont", 10, "bold"))
    reward_total_lbl.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=4)
    tip(reward_total_lbl, "reward.total")
    reward_detail_lbl = ttk.Label(reward_frame, textvariable=tally.detail_var,
                                  font=("TkDefaultFont", 8))
    reward_detail_lbl.grid(row=0, column=2, sticky="w", padx=(0, 16), pady=4)
    tip(reward_detail_lbl, "reward.detail")
    reward_reset_btn = ttk.Button(reward_frame, text="Reset", command=tally.reset)
    reward_reset_btn.grid(row=0, column=3, sticky="e", padx=4, pady=4)
    tip(reward_reset_btn, "reward.reset")


def build_lick_panel(parent: tk.Misc, tally: LickTally) -> None:
    lick_frame = ttk.LabelFrame(
        parent,
        text="Lick counts  -  rising edges this session (resets on a mode switch)")
    lick_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)
    for i in range(tally._n):
        lick_lbl = ttk.Label(lick_frame, textvariable=tally.vars[i],
                             font=("TkDefaultFont", 10, "bold"))
        lick_lbl.grid(row=0, column=i, sticky="w", padx=(6, 16), pady=4)
        tip(lick_lbl, "lick.count", index=i)
    lick_reset_btn = ttk.Button(lick_frame, text="Reset", command=tally.reset)
    lick_reset_btn.grid(row=0, column=tally._n, sticky="e", padx=4, pady=4)
    tip(lick_reset_btn, "lick.reset")


def main() -> int:
    root = tk.Tk()
    root.title("T-Maze Experiment Control")

    notebook = ttk.Notebook(root)
    notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # One shared RatTracker, injected into the camera (producer: runs process()
    # on its grab thread) and the Tracking tab (controller). The camera reference
    # is captured so the Tracking tab can read still frames / toggle tracking.
    tracker = None
    if RatTracker is not None:
        try:
            tracker = RatTracker()
        except Exception as e:
            print(f"[experiment_control] tracker unavailable: {e}", file=sys.stderr)
            tracker = None
    camera_ref = {}
    # The Task tab + its Phase 3 controller are captured (like the camera) so the
    # Session panel can read the shared animal id and arm/disarm Phase 3 / read
    # its data-file path for the manifest (ADR-0048/0053).
    task_ref = {}
    phase3_ref = {}
    # The shared reward dispatcher is captured (created inside make_task) so the
    # Session panel can push the "Reward" field's liquid to it as the active liquid
    # for per-fire volume accounting (ADR-0091).
    dispatcher_ref = {}
    # The Session panel is captured too, so the behavior panel can read its
    # Start/Stop session clock for the Phase 3 session line (ADR-0060).
    session_ref = {}
    # The behavior panel is captured so the Session panel can read its Phase 3
    # locomotor/compliance snapshot for the end-of-session summary (ADR-0071).
    behavior_ref = {}

    # Cross-tab seam (docs/decisions/0050): the live Phase 3 controller's
    # immutable published snapshot, resolved through the shared holder (populated
    # when the Task tab is built). Defined here at the top level so BOTH the Rig
    # view's ribbon/behavior readout AND the Metrics trace tab can read it
    # (null-safe until the Phase 3 tab exists / arms). See ADR-0114.
    def phase3_state():
        p3 = phase3_ref.get("p3")
        return p3.published_state() if p3 is not None else None

    # The current animal id from the Task tab's single shared field (ADR-0053,
    # FW-0005), resolved through the shared holder. Defined here (not only beside
    # the Session panel below) so the Metrics tab can list the rat's past sessions
    # for its reference (ghost) overlay (ADR-0117); null-safe until the Task tab
    # is built.
    def task_animal_id():
        tv = task_ref.get("task")
        return tv.animal_id() if tv is not None else ""

    # One shared maze-state pipeline (a single estimator on the camera tracking
    # worker), consumed by the Maze state / Rules / Behavior tabs instead of a
    # per-tab estimator (ADR-0041). Seed it with a previously drawn zone map so
    # state is available immediately; the Maze state tab owns editing it.
    pipeline = None
    if tracker is not None and MazeStatePipeline is not None:
        zone_map = ZoneMap()
        if load_zone_map is not None and ZONES_PATH and os.path.exists(ZONES_PATH):
            try:
                zone_map = load_zone_map(ZONES_PATH)
            except Exception:
                zone_map = ZoneMap()
        pipeline = MazeStatePipeline(zone_map, MazeStateParams())

    def make_camera(parent):
        # Pass the shared maze pipeline so the Rig view preview can draw zones +
        # the current-zone readout (display-only; the Maze state tab owns editing).
        cam = CameraView(parent, tracker=tracker, pipeline=pipeline)
        camera_ref["cam"] = cam
        # Drive the shared pipeline from the camera's off-thread track worker so
        # estimation runs once, at frame rate, off the Tk poll (ADR-0040/0041).
        if pipeline is not None:
            cam.subscribe_track(pipeline.on_track)
        return cam

    def make_task(parent):
        # One shared reward dispatcher for both controllers (docs/decisions/0054).
        # The send closure resolves `client` at fire time (created below).
        dispatcher = (RewardDispatcher(lambda cmd: client.send(cmd))
                      if RewardDispatcher is not None else None)
        dispatcher_ref["disp"] = dispatcher
        tv = TaskControlView(
            parent, tracker=tracker, camera=camera_ref.get("cam"),
            pipeline=pipeline, send=lambda cmd: client.send(cmd),
            dispatcher=dispatcher,
            phase3_cls=Phase3ControlView, rules_cls=RulesControlView)
        task_ref["task"] = tv
        # Keep the cross-tab holder pointing at the live Phase 3 controller (the
        # Session panel + behavior readout reach it through here; ADR-0048/0050).
        phase3_ref["p3"] = tv.phase3
        return tv

    # Registered modules + the single fan-out/reset seam (docs/decisions/0056).
    # Every module is both kept in ``modules`` (for close()) and added to the
    # router, which parses each line once and hands the pre-parsed LineInfo to
    # apply(info), plus fires one mode-changed reset to subscribers (FW-0021/22).
    modules = []
    router = LineRouter()

    def add_module(module) -> None:
        modules.append(module)
        router.add_module(module)

    def on_close() -> None:
        client.stop()
        # Let modules release any resources (e.g. the Camera tab's threads and
        # camera, finalizing an in-progress recording) before the GUI tears down.
        for module in modules:
            closer = getattr(module, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        root.destroy()

    def quit_rig() -> None:
        """Close every window start_rig.bat opened, then this GUI itself."""
        sid = rig_session_id()
        if sid:
            scope = ("all rig windows started by start_rig.bat\n"
                     "(serial broker, session logger, lick tuner, and this "
                     "control GUI)")
        else:
            scope = ("the running rig windows it can find\n"
                     "(serial broker, session logger, lick tuner, and this "
                     "control GUI)")
        if not messagebox.askyesno(
                "Quit rig",
                f"Close {scope}?\n\nThis stops recording and releases the "
                "serial port and camera."):
            return
        # Kill the other session processes first (broker/logger run in their own
        # cmd windows; /T in taskkill closes those windows too), then close this
        # GUI cleanly so the camera recording is finalized.
        me = os.getpid()
        for pid in find_rig_pids(sid):
            if pid != me:
                taskkill(pid)
        on_close()

    # Tab registry: (tab title, [factory(parent) -> module, ...]). Modules listed
    # together share one tab, packed left-to-right. The first tab puts the live
    # maze schematic (RigView) beside the camera preview/recording (CameraView).
    tabs = [("Rig view", [RigView, make_camera])]
    # Setup tab (ADR-0053 phase 3): the calibration/definition surface done
    # before a run. Tracking (tracker tuning + ROI/landmark editors) and Maze
    # state (zone editor + discrete-state readout) are nested as sub-tabs of one
    # top-level tab (an inner ttk.Notebook), grouping the two still-frame-editing
    # workflows together. Only the sub-views whose deps are present are added.
    setup_subtabs = []
    # The Tracking view is captured (like camera/task) so the Session panel can
    # read the active appearance-model path for the config snapshot (ADR-0072).
    tracker_view_ref = {}
    if tracker is not None and TrackerControlView is not None:
        def make_tracking(parent):
            tvw = TrackerControlView(
                parent, tracker=tracker, camera=camera_ref.get("cam"))
            tracker_view_ref["tv"] = tvw
            return tvw
        setup_subtabs.append(("Tracking", make_tracking))
    # Maze state: reduces the tracker centroid to a discrete per-tick maze state
    # (zone + direction + loops) and hosts the zone editor. Read-only consumer of
    # the shared tracker; needs the camera for still-frame editing + preview.
    # See docs/decisions/0025.
    if tracker is not None and MazeStateControlView is not None:
        setup_subtabs.append(("Maze state", lambda parent: MazeStateControlView(
            parent, tracker=tracker, camera=camera_ref.get("cam"),
            pipeline=pipeline)))
    # Cue volume: a pre-run speaker-balance calibration (ADR-0125). Needs only the
    # broker (to play test tones), resolved lazily like make_task since subtab
    # factories run before the client exists; no tracker/camera, so it is offered
    # even when the vision deps are missing.
    if CueVolumeView is not None:
        setup_subtabs.append(("Cue volume", lambda parent: CueVolumeView(
            parent, send=lambda cmd: client.send(cmd))))
    if setup_subtabs:
        labels = [label for label, _ in setup_subtabs]
        factories = [factory for _, factory in setup_subtabs]
        tabs.append(("Setup", factories, labels))
    # Task tab (ADR-0053 phase 4): the host-owned task controllers under ONE tab
    # with an exclusive controller selector and the single shared Animal ID field
    # (the one host-side id source feeding the session manifest; FW-0005). It
    # groups the Rules engine (position-based reward shaping; ADR-0023) and the
    # Phase 3 cued task (host owns the trial sequence, cue identity, scoring, and
    # reward, driving CUE_ON,<kind>,<hz> / R<port> via the broker; ADR-0046). The
    # send closure resolves `client` at call time (created below). Present when
    # the tracker + at least one controller are available.
    if tracker is not None and TaskControlView is not None and (
            Phase3ControlView is not None or RulesControlView is not None):
        tabs.append(("Task", [make_task]))
    # Metrics tab (ADR-0114): live within-session trace plots read from the
    # published Phase 3 snapshot (pull mode, like the Rig-view ribbon). Its own
    # top-level tab since the stacked subplots need vertical room the crowded
    # Rig-view bottom strip doesn't have. Present whenever the view imported
    # (matplotlib is guarded inside it); shows "Not armed." until a run starts.
    # A trial-by-trial choice raster (ADR-0127) rides alongside it as a SECOND
    # module: packed left-to-right, so - being not the first - it expands to fill
    # the tab space the fixed-width traces panel leaves empty on its right.
    if Phase3TracesView is not None:
        metrics_factories = [lambda parent: Phase3TracesView(
            parent, state_getter=phase3_state, animal_getter=task_animal_id)]
        if Phase3ChoiceRasterView is not None:
            metrics_factories.append(lambda parent: Phase3ChoiceRasterView(
                parent, state_getter=phase3_state))
        tabs.append(("Metrics", metrics_factories))
    # NOTE: the Session control is NOT a tab - it lives in the Rig view bottom
    # strip's RIGHT column, under the live behavior readout (filling the dead
    # space there so it doesn't lengthen the left column / the window). It is
    # built after the tab loop (so camera_ref/phase3_ref are populated) and after
    # the broker client exists; see the bottom-strip wiring below (ADR-0048).

    # Build modules into tabs. Modules grouped in one tab are packed
    # left-to-right (schematic on the left, camera on the right). The Rig view
    # tab gets a small toolbar on top carrying the "Quit rig" button, and a
    # bottom strip that hosts the rig/firmware controls (Solenoid test, Training
    # mode in the left column; the Session start/stop panel under the behavior
    # readout in the right) - these belong with Rig view (you watch the valve
    # markers here) and keeping them off the Tracking tab lets that tab use its
    # full height.
    rig_controls_parent = root  # fallback; reassigned to the Rig view bottom strip
    rig_controls_left = root     # left column: the existing rig/firmware controls
    rig_controls_right = None    # right column: the live behavior readout (or None)
    for entry in tabs:
        title, factories = entry[0], entry[1]
        # A third element (parallel sub-tab labels) marks a NESTED group: the
        # factories become sub-tabs of an inner notebook instead of being packed
        # left-to-right (ADR-0053 phase 3, the Setup tab).
        subtitles = entry[2] if len(entry) > 2 else None
        tab = ttk.Frame(notebook)
        notebook.add(tab, text=title)
        if subtitles is not None:
            inner = ttk.Notebook(tab)
            inner.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            for label, factory in zip(subtitles, factories):
                sub = ttk.Frame(inner)
                inner.add(sub, text=label)
                module = factory(sub)
                module.widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                                     padx=2, pady=2)
                add_module(module)
            continue
        if title == "Rig view":
            toolbar = ttk.Frame(tab)
            toolbar.pack(side=tk.TOP, fill=tk.X, padx=2, pady=(2, 0))
            quit_btn = ttk.Button(toolbar, text="Quit rig (close all windows)",
                                  command=quit_rig)
            quit_btn.pack(side=tk.RIGHT)
            tip(quit_btn, "quit.rig")
            # Pack the bottom strip BEFORE the body so it reserves its space and
            # the body (schematic + camera) fills whatever remains. The strip is
            # split into a LEFT column (the existing rig/firmware controls, which
            # are left-aligned and leave the right half empty) and a RIGHT column
            # that fills that dead space with the live behavior readout.
            rig_controls_parent = ttk.Frame(tab)
            rig_controls_parent.pack(side=tk.BOTTOM, fill=tk.X)
            rig_controls_left = ttk.Frame(rig_controls_parent)
            rig_controls_left.pack(side=tk.LEFT, fill=tk.Y, anchor="n")
            rig_controls_right = ttk.Frame(rig_controls_parent)
            rig_controls_right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                                    padx=(8, 4), pady=4)
            body = ttk.Frame(tab)
            body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        else:
            body = tab
        for i, factory in enumerate(factories):
            module = factory(body)
            # The first module keeps its natural size; the rest expand to fill.
            module.widget().pack(side=tk.LEFT, fill=tk.BOTH,
                                 expand=(i > 0), padx=2, pady=2)
            add_module(module)

    # Status bar.
    status_var = tk.StringVar(value="Connecting...")
    ttk.Label(root, textvariable=status_var, anchor="w").pack(
        side=tk.BOTTOM, fill=tk.X)

    # Current training mode, reflected from the firmware's 0,MODE,<name> lines.
    mode_var = tk.StringVar(value="Mode: (unknown)")
    reward_tally = RewardTally()
    lick_tally = LickTally(N_LICK, _RIG_CFG)

    # Connect to the broker (must exist before wiring callbacks).
    client = RigClient(root, host=BROKER_HOST, port=BROKER_PORT)

    # The bottom-strip tallies are stateful consumers of a MODE switch, so they
    # subscribe to the ONE reset seam (docs/decisions/0056) alongside the modules
    # that define on_mode_changed - no more per-consumer "if mode: reset" checks.
    router.subscribe_mode(
        lambda mode: mode_var.set(f"Mode: {MODE_LABELS.get(mode, mode)}"))
    router.subscribe_mode(lambda mode: reward_tally.reset())
    router.subscribe_mode(lambda mode: lick_tally.reset())

    def on_line(line: str) -> None:
        # Parse ONCE, then fan the pre-parsed line out. The reward/lick tallies
        # ADD here; their MODE reset (and the mode label) run via the router's
        # mode seam, which fires before modules see the line (FW-0021/0022).
        info = protocol.classify(line)
        if info.lick is not None:
            index, rising = info.lick
            if rising:
                lick_tally.add(index)
        elif info.reward is not None:
            manual, dur = info.reward
            reward_tally.add(
                dur if dur is not None else DEFAULT_REWARD_PULSE_MS, manual)
        router.dispatch(info)

    client.on_line(on_line)
    client.on_status(status_var.set)

    try:
        client.start()
    except Exception as e:
        err = (f"Could not connect to rig broker on {BROKER_HOST}:{BROKER_PORT}: {e}\n\n"
               "Is rig_broker.py running? Try start_rig.bat.")
        print(f"[experiment_control] {err}", file=sys.stderr, flush=True)
        messagebox.showerror("Experiment Control - broker not reachable", err)
        root.destroy()
        return 1

    build_mode_controls(rig_controls_left, client, mode_var)

    # Attribute operator reward-spout feeds (the Solenoid-test reward button) to
    # the Session panel at their source, so its manual_water counts operator water
    # only and stays disjoint from the task estimate (docs/decisions/0084). The
    # session is resolved lazily via session_ref (built below) at click time.
    def on_operator_reward(ms: int) -> None:
        s = session_ref.get("session")
        if s is not None and hasattr(s, "note_operator_reward"):
            s.note_operator_reward(ms)
    build_solenoid_panel(rig_controls_left, client,
                         on_reward_fire=on_operator_reward)
    build_cue_panel(rig_controls_left, client)
    build_reward_panel(rig_controls_left, reward_tally)
    build_lick_panel(rig_controls_left, lick_tally)

    # Live Phase 2a behavior readout in the freed bottom-right column. It is a
    # read-only consumer of the shared tracker + camera; registered as a module
    # so it gets each serial line (to reset on a MODE switch) and close().
    if (tracker is not None and BehaviorView is not None
            and rig_controls_right is not None):
        # ``phase3_state`` (the published-snapshot getter) is defined at the top
        # of main() so the Metrics tab can share it (docs/decisions/0050/0114).
        # The Phase 3 session line is driven by the Session panel's Start/Stop
        # button (ADR-0060); resolve it through the shared holder (the Session
        # panel is built just below), so the behavior panel needn't reach across.
        def session_timing():
            s = session_ref.get("session")
            return s.session_timing() if s is not None else None
        # Split the right column so the operator trial-control buttons can fill the
        # empty area that spans BOTH the ribbon and the live-stats panels
        # (docs/decisions/0058): ribbon + behavior stack in a LEFT sub-column, the
        # button bar sits in a RIGHT sub-column of the same height. The Session
        # panel stays pinned to the bottom of the column (built after this block).
        upper = ttk.Frame(rig_controls_right)
        upper.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        left_col = ttk.Frame(upper)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Trial ribbon at the TOP of the left sub-column (above the behavior
        # readout): the same strip as the Phase 3 tab, so the current/upcoming
        # trials are visible on the default Rig view during a run (docs/decisions/
        # 0057). It self-polls the published snapshot (pull mode) since the Rig view
        # has no Phase 3 poll; registered as a module so its self-poll is cancelled
        # on exit.
        if TrialRibbonView is not None:
            ribbon = TrialRibbonView(left_col, state_getter=phase3_state)
            ribbon.widget().pack(side=tk.TOP, fill=tk.X, padx=4, pady=(4, 0))
            add_module(ribbon)
        behavior = BehaviorView(left_col, tracker=tracker,
                                camera=camera_ref.get("cam"), pipeline=pipeline,
                                phase3_state=phase3_state,
                                session_timing=session_timing)
        behavior.widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        add_module(behavior)
        # Expose the behavior panel's Phase 3 locomotor snapshot to the Session
        # panel for the end-of-session summary (ADR-0071).
        behavior_ref["behavior"] = behavior
        # Operator trial-control buttons on the RIGHT, wired to the live Phase 3
        # controller through the shared holder (each call null-safe if the Phase 3
        # tab is absent). Restart confirms first (it discards the current run's
        # progress). Registered as a module so its self-poll is cancelled on exit.
        if TrialControlBar is not None:
            def _p3_call(method_name):
                def call():
                    p3 = phase3_ref.get("p3")
                    fn = getattr(p3, method_name, None) if p3 is not None else None
                    if fn is not None:
                        fn()
                return call

            def _restart():
                p3 = phase3_ref.get("p3")
                if p3 is None:
                    return
                if messagebox.askyesno(
                        "Restart session",
                        "Restart this session at trial 1?\n\nThe current run's "
                        "progress is discarded (its data file is kept); a fresh "
                        "plan is generated from the same seed."):
                    p3.restart_session()

            def _toggle_pause():
                p3 = phase3_ref.get("p3")
                if p3 is not None:
                    p3.toggle_paused()

            def _paused():
                p3 = phase3_ref.get("p3")
                return bool(p3.is_paused()) if p3 is not None else False

            controls = {
                "skip": _p3_call("skip_current_trial"),
                "repeat": _p3_call("repeat_current_trial"),
                "back": _p3_call("step_back_trial"),
                "extend": _p3_call("extend_trials"),
                "restart": _restart,
                "pause": _toggle_pause,
                # Manual override of the closed-loop debias scheduler (no-op unless
                # the debias_blocks / intervention_blocks generator is active). See
                # debias.py / ADR.
                "debias": _p3_call("toggle_debias_block"),
                # Manual override of the de-alternation block (no-op unless
                # intervention_blocks is active with the alternation child enabled).
                # See alternation.py / intervention_stack.py / ADR.
                "alt": _p3_call("toggle_alternation_block"),
                # Manual unrewarded probe block (works under any generator; no-op
                # unless probe blocks are enabled this run). See probe_blocks.py.
                "probe": _p3_call("toggle_probe_block"),
            }
            ctrl_bar = TrialControlBar(
                upper, callbacks=controls, state_getter=phase3_state,
                paused_getter=_paused)
            ctrl_bar.widget().pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 4), pady=4)
            add_module(ctrl_bar)

    # Session start/stop panel: pinned to the BOTTOM of the right column, filling
    # the dead space under the behavior readout (keeps it off the left column so
    # the strip - and the window - stays short). Built here (not as a tab) so
    # camera_ref/phase3_ref + the broker client exist; registered as a module so
    # it gets each serial line (animal-id prefill) + close(). ADR-0048. Falls back
    # to the left column only if the right one somehow wasn't created.
    if SessionControlView is not None:
        session_parent = (rig_controls_right if rig_controls_right is not None
                          else rig_controls_left)
        # Each session gets its own folder tree under <recordings>/phase3_recordings
        # (ADR-0049). Use the camera's fixed default recordings folder as the base
        # (not the live Output: box), so sessions always land in one place.
        recordings_root = os.path.join(DEFAULT_OUTPUT_DIR, "phase3_recordings")
        # ``task_animal_id`` (the Task tab's shared id field; ADR-0053/FW-0005) is
        # defined at the top of main() so the Metrics tab can share it; the session
        # slugs its value into the session_id + manifest.
        # The behavior panel's Phase 3 locomotor snapshot for the end-of-session
        # summary (ADR-0071); resolved through the shared holder, null-safe if the
        # behavior panel wasn't built (no tracker).
        def behavior_summary():
            b = behavior_ref.get("behavior")
            return b.behavior_summary() if b is not None else None
        # Runtime bits for the per-session config snapshot (ADR-0072) that don't
        # live in a host/*.json: extra files to copy (the lens calibration JSON,
        # the active tracker model) + live scalars (camera transform, tracker /
        # maze params). session_config finds the host/*.json itself. All reads
        # are guarded so a snapshot never breaks Session Start.
        def config_inputs():
            files = {}
            params = {}
            try:
                params["camera"] = {"orientation": ORIENTATION,
                                    "undistort_alpha": UNDISTORT_ALPHA}
                if CALIBRATION_PATH:
                    files["camera_calibration.json"] = CALIBRATION_PATH
            except Exception:
                pass
            if tracker is not None:
                try:
                    params["tracker"] = dataclasses.asdict(tracker.get_params())
                except Exception:
                    pass
            if pipeline is not None:
                try:
                    params["maze_state"] = dataclasses.asdict(pipeline.params)
                except Exception:
                    pass
            tvw = tracker_view_ref.get("tv")
            if tvw is not None:
                try:
                    mp = tvw.active_model_path()
                    if mp and os.path.isfile(mp):
                        files["model_" + os.path.basename(mp)] = mp
                except Exception:
                    pass
            return {"files": files, "params": params}
        session = SessionControlView(
            session_parent, camera=camera_ref.get("cam"),
            phase3=phase3_ref.get("p3"), send=lambda cmd: client.send(cmd),
            recordings_root=recordings_root, animal_id=task_animal_id,
            behavior_summary=behavior_summary, config_inputs=config_inputs,
            dispatcher=dispatcher_ref.get("disp"))
        session.widget().pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)
        add_module(session)
        # Expose the session clock to the behavior panel's Phase 3 line (ADR-0060).
        session_ref["session"] = session

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
