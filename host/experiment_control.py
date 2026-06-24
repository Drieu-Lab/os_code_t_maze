"""
experiment_control.py - extensible experiment-control GUI for the T-maze rig.

Connects to the rig broker (rig_broker.py) via RigClient and hosts a set of
modules in a tabbed notebook. The first module is a live maze illustration
(rig_view.RigView). A "Solenoid test" bar at the bottom fires R<port><sol>,<ms>
commands (the pulse width is configurable, default DEFAULT_PULSE_MS) so you can
map each solenoid by watching which physical valve clicks and which marker
flashes in Rig view. Both the tmaze_test and cue-guided-task sketches ack with
SOL lines and honor the per-pulse duration, so the buttons work under either
(cue-guided-task fires non-blocking and in any mode). Future modules (reward control, trial
monitor, ...) drop in by adding a (title, factory) entry to MODULES; each module
only needs:

    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None  to handle each serial line

NOTE: rig_broker.py must already be running. Use start_rig.bat to launch the
broker and GUIs together, or run "python rig_broker.py" first.

Run from os_code_t_maze/host/:
    python experiment_control.py
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox, ttk

from rig_io import RigClient
from rig_view import RigView

# ================= CONFIG =================
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5555
DEFAULT_PULSE_MS = 150   # matches REWARD_PULSE_MS in firmware/common/rig_config.h

# Module registry: (tab title, factory(parent) -> module). Add new modules here.
MODULES = [
    ("Rig view", RigView),
]


def main() -> int:
    root = tk.Tk()
    root.title("T-Maze Experiment Control")

    notebook = ttk.Notebook(root)
    notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # Build modules into tabs.
    modules = []
    for title, factory in MODULES:
        module = factory(notebook)
        notebook.add(module.widget(), text=title)
        modules.append(module)

    # Status bar.
    status_var = tk.StringVar(value="Connecting...")
    ttk.Label(root, textvariable=status_var, anchor="w").pack(
        side=tk.BOTTOM, fill=tk.X)

    # Current training mode, reflected from the firmware's 0,MODE,<name> lines.
    mode_var = tk.StringVar(value="Mode: (unknown)")

    # Connect to the broker (must exist before wiring callbacks).
    client = RigClient(root, host=BROKER_HOST, port=BROKER_PORT)

    # Pretty labels for the MODE,<name> tokens the firmware emits.
    MODE_LABELS = {"FREEROAM": "Free Roam", "FULLTASK": "Full task"}

    def on_line(line: str) -> None:
        fields = line.split(",")
        if len(fields) >= 3 and fields[1] == "MODE":
            name = fields[2].strip()
            mode_var.set(f"Mode: {MODE_LABELS.get(name, name)}")
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
        root, text="Training mode  -  switch the firmware between Free Roam and the Full task")
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
        root, text="Solenoid test  -  fire R<port><sol>, watch the valve and its marker")
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

    def on_close() -> None:
        client.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
