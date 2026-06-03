"""
experiment_control.py - extensible experiment-control GUI for the T-maze rig.

Connects to the rig broker (rig_broker.py) via RigClient and hosts a set of
modules in a tabbed notebook. The first module is a live maze illustration
(rig_view.RigView). A "Solenoid test" bar at the bottom fires R<port><sol>
commands so you can map each solenoid by watching which physical valve clicks
and which marker flashes in Rig view (mapping needs the tmaze_test sketch, which
acks with SOL lines). Future modules (reward control, trial monitor, ...) drop in
by adding a (title, factory) entry to MODULES; each module only needs:

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

    # Connect to the broker (must exist before wiring callbacks).
    client = RigClient(root, host=BROKER_HOST, port=BROKER_PORT)

    def on_line(line: str) -> None:
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

    # Solenoid mapping helper: fire R<port><sol> and watch which physical valve
    # clicks vs. which marker flashes in the Rig view tab. The SOL acks that drive
    # the flashes are emitted by tmaze_test (not the task sketch), so load
    # tmaze_test to map the valves.
    sol_frame = ttk.LabelFrame(
        root, text="Solenoid test  -  fire R<port><sol>, watch the valve and its marker")
    sol_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=4)
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
            cmd = f"R{port}{sol}"
            ttk.Button(sol_frame, text=cmd, width=5,
                       command=lambda c=cmd: client.send(c)).grid(
                row=row, column=sol + 1, padx=2, pady=2)

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
