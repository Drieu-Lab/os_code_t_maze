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
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from rig_io import RigClient
from rig_view import RigView
from camera_view import CameraView
from reward_volume import (reward_volume_ul, format_volume,
                           DEFAULT_REWARD_PULSE_MS)

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

# ================= CONFIG =================
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5555
DEFAULT_PULSE_MS = 150   # matches REWARD_PULSE_MS in firmware/common/rig_config.h

# Scripts start_rig.bat launches; used as a fallback for the "Quit rig" button
# when this GUI was started without a session id (e.g. run standalone).
RIG_SCRIPTS = ("rig_broker.py", "rig_logger.py", "lick_tuner.py",
               "experiment_control.py")

# Flag to hide the transient PowerShell/taskkill console windows the Quit button
# spawns (only defined on Windows).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ============== "Quit rig": close every window start_rig.bat opened ============
# start_rig.bat tags each program it launches with "-X rigsid=<id>" (see ADR-0016
# and start_rig.bat). That id rides along in each process's command line, so we
# can enumerate exactly this rig session's processes and terminate them. When no
# id is present we fall back to matching the known rig script names.
def rig_session_id() -> str | None:
    """The session id this process was tagged with, or None if untagged."""
    sid = sys._xoptions.get("rigsid")
    if sid:
        return str(sid)
    return os.environ.get("RIG_SESSION_ID") or None


def find_rig_pids(session_id: str | None) -> list[int]:
    """PIDs of this rig session's processes (broker, logger, tuner, GUIs).

    Uses WMI via PowerShell to match each process's command line. When a session
    id is given we match "rigsid=<id>"; the literal is split in the query so the
    query's *own* PowerShell process can't match itself. Otherwise we match the
    known rig script filenames. PowerShell itself is always excluded.
    """
    if session_id:
        # Split "rigsid" so this query's command line never contains the
        # contiguous token it searches for (which would match itself).
        cond = "$_.CommandLine -like ('*rig' + 'sid=" + session_id + "*')"
    else:
        likes = " -or ".join(
            f"$_.CommandLine -like '*{name}*'" for name in RIG_SCRIPTS)
        cond = f"({likes})"
    script = ("Get-CimInstance Win32_Process | Where-Object { "
              f"({cond}) -and $_.Name -ne 'powershell.exe' "
              "} | Select-Object -ExpandProperty ProcessId")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW)
    except Exception as e:  # pragma: no cover - depends on host environment
        print(f"[experiment_control] could not list rig processes: {e}",
              file=sys.stderr, flush=True)
        return []
    pids = []
    for tok in proc.stdout.split():
        tok = tok.strip()
        if tok.isdigit():
            pids.append(int(tok))
    return pids


def taskkill(pid: int) -> None:
    """Force-kill a PID and its child tree (e.g. a cmd window + its python)."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except Exception:
        pass


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
            ttk.Button(toolbar, text="Quit rig (close all windows)",
                       command=quit_rig).pack(side=tk.RIGHT)
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

    # Reward dispensed: running total of reward volume for the current session,
    # accumulated from the firmware REWARD lines and the host calibration
    # (reward_volume.py). Lets the experimenter titrate the daily
    # water-restriction top-up. Reset on a new session (MODE line) or the Reset
    # button. see docs/decisions/0019
    reward_totals = {"pulses": 0, "ul": 0.0,
                     "auto_pulses": 0, "auto_ul": 0.0,
                     "man_pulses": 0, "man_ul": 0.0}
    reward_total_var = tk.StringVar()
    reward_detail_var = tk.StringVar()

    def render_rewards() -> None:
        reward_total_var.set(
            f"{format_volume(reward_totals['ul'])}  "
            f"({reward_totals['pulses']} pulses)")
        reward_detail_var.set(
            f"auto {format_volume(reward_totals['auto_ul'])} "
            f"({reward_totals['auto_pulses']})  /  "
            f"manual {format_volume(reward_totals['man_ul'])} "
            f"({reward_totals['man_pulses']})")

    def reset_rewards() -> None:
        for k in reward_totals:
            reward_totals[k] = 0 if k.endswith("pulses") else 0.0
        render_rewards()

    def add_reward(dur_ms: int, manual: bool) -> None:
        vol = reward_volume_ul(dur_ms)
        reward_totals["pulses"] += 1
        reward_totals["ul"] += vol
        prefix = "man" if manual else "auto"
        reward_totals[f"{prefix}_pulses"] += 1
        reward_totals[f"{prefix}_ul"] += vol
        render_rewards()

    reset_rewards()   # seed the display with zeros

    # Connect to the broker (must exist before wiring callbacks).
    client = RigClient(root, host=BROKER_HOST, port=BROKER_PORT)

    # Pretty labels for the MODE,<name> tokens the firmware emits.
    MODE_LABELS = {"FREEROAM": "Free Roam", "FULLTASK": "Full task"}

    def on_line(line: str) -> None:
        fields = line.split(",")
        if len(fields) >= 3 and fields[1] == "MODE":
            name = fields[2].strip()
            mode_var.set(f"Mode: {MODE_LABELS.get(name, name)}")
            reset_rewards()   # a MODE line (re)starts the session: zero the tally
        elif len(fields) >= 3 and fields[1] == "REWARD":
            # REWARD,CENTER,<dur_ms> (automatic) or REWARD,<port>,MANUAL,<dur_ms>
            # (manual). Tolerate a missing trailing <dur_ms> (pre-v4 firmware /
            # log) by falling back to the default pulse width. see docs/decisions/0019
            manual = "MANUAL" in fields
            last = fields[-1].strip()
            dur = int(last) if last.lstrip("+-").isdigit() else DEFAULT_REWARD_PULSE_MS
            add_reward(dur, manual)
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

    # Training-mode control: switch the firmware between Free Roam (no cues, no
    # trials; refill the central well manually with the R2 button below) and the
    # Full cued task. Selecting a mode (re)starts the session on the Due.
    mode_frame = ttk.LabelFrame(
        rig_controls_parent,
        text="Training mode  -  switch the firmware between Free Roam and the Full task")
    mode_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)

    def confirm_and_send_mode(label: str, cmd: str) -> None:
        if messagebox.askyesno(
                "Switch training mode",
                f"Switch to {label}?\n\nThis (re)starts the session on the Due "
                "(a new trial sequence is generated for the Full task)."):
            client.send(cmd)

    ttk.Button(mode_frame, text="Free Roam",
               command=lambda: confirm_and_send_mode("Free Roam", "MODE,FREE")).grid(
        row=0, column=0, padx=4, pady=4)
    ttk.Button(mode_frame, text="Full task",
               command=lambda: confirm_and_send_mode("Full task", "MODE,FULL")).grid(
        row=0, column=1, padx=4, pady=4)
    ttk.Label(mode_frame, textvariable=mode_var).grid(
        row=0, column=2, sticky="w", padx=(12, 4), pady=4)

    # Solenoid mapping helper: fire R<port><sol>,<ms> and watch which physical
    # valve clicks vs. which marker flashes in the Rig view tab. The SOL acks that
    # drive the flashes are emitted by both tmaze_test and cue-guided-task, so the
    # buttons map the valves under either sketch.
    sol_frame = ttk.LabelFrame(
        rig_controls_parent,
        text="Solenoid test  -  fire R<port><sol>, watch the valve and its marker")
    sol_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)

    # Configurable pulse width (ms). Default matches REWARD_PULSE_MS in
    # rig_config.h; tmaze_test clamps the value to <= 5000 ms.
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

    # Column headers: sol 0-2 reward, 3 drain, 4 rinse.
    headers = ("", "reward", "reward", "reward", "drain", "rinse")
    for col, text in enumerate(headers):
        ttk.Label(sol_frame, text=text, font=("TkDefaultFont", 8)).grid(
            row=0, column=col, padx=2, pady=(2, 0))
    port_rows = (("Left", 0), ("Right", 1), ("Center / start", 2))
    for row, (port_name, port) in enumerate(port_rows, start=1):
        ttk.Label(sol_frame, text=f"{port_name} (port {port}):").grid(
            row=row, column=0, sticky="w", padx=(6, 8), pady=2)
        for sol in range(5):
            ttk.Button(sol_frame, text=f"R{port}{sol}", width=5,
                       command=lambda p=port, s=sol: send_sol(p, s)).grid(
                row=row, column=sol + 1, padx=2, pady=2)

    ttk.Label(sol_frame, text="Pulse (ms):").grid(
        row=4, column=0, sticky="e", padx=(6, 8), pady=(6, 4))
    ttk.Spinbox(sol_frame, from_=1, to=5000, increment=10, width=6,
                textvariable=pulse_var).grid(
        row=4, column=1, columnspan=2, sticky="w", pady=(6, 4))
    ttk.Label(sol_frame, text="per click (<= 5000)", font=("TkDefaultFont", 8)).grid(
        row=4, column=3, columnspan=3, sticky="w", pady=(6, 4))

    # Reward dispensed: live running total of reward volume for the current
    # session, mapped from the firmware REWARD lines through the host calibration
    # (reward_volume.py). The split shows automatic (task) vs manual fires.
    # see docs/decisions/0019
    reward_frame = ttk.LabelFrame(
        rig_controls_parent,
        text="Reward dispensed  -  running total this session (resets on a mode switch)")
    reward_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)
    ttk.Label(reward_frame, text="Total:").grid(
        row=0, column=0, sticky="e", padx=(6, 4), pady=4)
    ttk.Label(reward_frame, textvariable=reward_total_var,
              font=("TkDefaultFont", 10, "bold")).grid(
        row=0, column=1, sticky="w", padx=(0, 16), pady=4)
    ttk.Label(reward_frame, textvariable=reward_detail_var,
              font=("TkDefaultFont", 8)).grid(
        row=0, column=2, sticky="w", padx=(0, 16), pady=4)
    ttk.Button(reward_frame, text="Reset", command=reset_rewards).grid(
        row=0, column=3, sticky="e", padx=4, pady=4)

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
