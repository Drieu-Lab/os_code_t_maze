"""
experiment_control.py - extensible experiment-control GUI for the T-maze rig.

Connects to the rig broker (rig_broker.py) via RigClient and hosts a set of
modules in a tabbed notebook. The first module is a live maze illustration
(rig_view.RigView). The rig/firmware controls live at the bottom of the Rig view
tab: a "Solenoid test" bar fires R<port><sol>,<ms> commands (the pulse width is
configurable, default DEFAULT_PULSE_MS) so you can map each solenoid by watching
which physical valve clicks and which marker flashes in Rig view, and a "Training
mode" bar switches the firmware between Free Roam and the Full task. Both the
tmaze_test and cue-guided-task sketches ack with SOL lines and honor the
per-pulse duration, so the buttons work under either (cue-guided-task fires
non-blocking and in any mode). Future modules (reward control, trial
monitor, ...) drop in by adding an entry to TABS; each module only needs:

    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None  to handle each serial line
    close() -> None           (optional) to release resources on app exit

A TABS entry is (title, [factory, ...]); modules listed together share one tab,
packed left-to-right. The first tab places the live maze schematic
(rig_view.RigView) beside the camera preview/recording (camera_view.CameraView).
The Camera module uses the optional close() hook to stop its threads and finalize
any in-progress recording when the window closes.

NOTE: rig_broker.py must already be running. Use start_rig.bat to launch the
broker and GUIs together, or run "python rig_broker.py" first.

Run from os_code_t_maze/host/:
    python experiment_control.py
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

import protocol
from rig_io import RigClient
from rig_view import RigView
from camera_view import CameraView
from reward_volume import (reward_volume_ul, format_volume,
                           DEFAULT_REWARD_PULSE_MS)
from rig_config import load_rig_config, RigConfig
from rig_process import find_rig_pids, rig_session_id, taskkill
from tooltips import tip

# Markerless tracker + its control tab are optional (need opencv/numpy/pillow);
# guard the imports so the GUI still runs if they're missing.
try:
    from tracker import RatTracker
    _TRACKER_ERR = None
except Exception as e:  # pragma: no cover - depends on host environment
    RatTracker = None  # type: ignore
    _TRACKER_ERR = f"{type(e).__name__}: {e}"
try:
    from tracker_view import TrackerControlView
except Exception as e:  # pragma: no cover
    TrackerControlView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Position-based rules engine tab (consumes the tracker + landmarks; actuates
# reward via the broker). Optional; guarded so a missing dep never breaks the
# GUI. See docs/decisions/0023.
try:
    from rules_view import RulesControlView
except Exception as e:  # pragma: no cover
    RulesControlView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"
# Maze-state tab (consumes the tracker; reduces the centroid to a discrete
# per-tick maze state + zone editor). Optional; guarded like the tabs above.
# See docs/decisions/0025.
try:
    from maze_state_view import MazeStateControlView
except Exception as e:  # pragma: no cover
    MazeStateControlView = None  # type: ignore
    _TRACKER_ERR = _TRACKER_ERR or f"{type(e).__name__}: {e}"

# ================= CONFIG =================
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5555
DEFAULT_PULSE_MS = 150   # matches REWARD_PULSE_MS in firmware/common/rig_config.h

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


def build_solenoid_panel(parent: tk.Misc, client: RigClient) -> None:
    """Solenoid mapping helper: fire R<port><sol> and watch Rig view markers."""
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

    def make_camera(parent):
        cam = CameraView(parent, tracker=tracker)
        camera_ref["cam"] = cam
        return cam

    modules = []

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
    if tracker is not None and TrackerControlView is not None:
        tabs.append(("Tracking", [
            lambda parent: TrackerControlView(
                parent, tracker=tracker, camera=camera_ref.get("cam"))]))
    # Rules tab: the position-based rules engine (off by default). It is injected
    # with the shared tracker, the camera (for frames + still-frame calibration),
    # and a send callback so it can actuate reward via the broker. The send
    # closure resolves `client` at call time (created below). See ADR-0023.
    if tracker is not None and RulesControlView is not None:
        tabs.append(("Rules", [
            lambda parent: RulesControlView(
                parent, tracker=tracker, camera=camera_ref.get("cam"),
                send=lambda cmd: client.send(cmd))]))
    # Maze state tab: reduces the tracker centroid to a discrete per-tick maze
    # state (zone + direction + loops) and hosts the zone editor. Read-only
    # consumer of the shared tracker; needs the camera for still-frame editing +
    # preview. See docs/decisions/0025.
    if tracker is not None and MazeStateControlView is not None:
        tabs.append(("Maze state", [
            lambda parent: MazeStateControlView(
                parent, tracker=tracker, camera=camera_ref.get("cam"))]))

    # Build modules into tabs. Modules grouped in one tab are packed
    # left-to-right (schematic on the left, camera on the right). The Rig view
    # tab gets a small toolbar on top carrying the "Quit rig" button, and a
    # bottom strip that hosts the rig/firmware controls (Solenoid test, Training
    # mode) - these belong with Rig view (you watch the valve markers here) and
    # keeping them off the Tracking tab lets that tab use its full height.
    rig_controls_parent = root  # fallback; reassigned to the Rig view bottom strip
    for title, factories in tabs:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text=title)
        if title == "Rig view":
            toolbar = ttk.Frame(tab)
            toolbar.pack(side=tk.TOP, fill=tk.X, padx=2, pady=(2, 0))
            quit_btn = ttk.Button(toolbar, text="Quit rig (close all windows)",
                                  command=quit_rig)
            quit_btn.pack(side=tk.RIGHT)
            tip(quit_btn, "quit.rig")
            # Pack the bottom strip BEFORE the body so it reserves its space and
            # the body (schematic + camera) fills whatever remains.
            rig_controls_parent = ttk.Frame(tab)
            rig_controls_parent.pack(side=tk.BOTTOM, fill=tk.X)
            body = ttk.Frame(tab)
            body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        else:
            body = tab
        for i, factory in enumerate(factories):
            module = factory(body)
            # The first module keeps its natural size; the rest expand to fill.
            module.widget().pack(side=tk.LEFT, fill=tk.BOTH,
                                 expand=(i > 0), padx=2, pady=2)
            modules.append(module)

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

    def on_line(line: str) -> None:
        mode = protocol.mode_name(line)
        lick = protocol.lick_edge(line)
        reward = protocol.reward_info(line)
        if mode is not None:
            mode_var.set(f"Mode: {MODE_LABELS.get(mode, mode)}")
            reward_tally.reset()
            lick_tally.reset()
        elif lick is not None:
            index, rising = lick
            if rising:
                lick_tally.add(index)
        elif reward is not None:
            manual, dur = reward
            reward_tally.add(
                dur if dur is not None else DEFAULT_REWARD_PULSE_MS, manual)
        for module in modules:
            module.apply(line)

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

    build_mode_controls(rig_controls_parent, client, mode_var)
    build_solenoid_panel(rig_controls_parent, client)
    build_reward_panel(rig_controls_parent, reward_tally)
    build_lick_panel(rig_controls_parent, lick_tally)

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
