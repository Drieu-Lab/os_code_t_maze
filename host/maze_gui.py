import sys
import tkinter as tk
from functools import partial
from tkinter import messagebox

import protocol
from rig_config import load_rig_config
from rig_io import RigClient

# ================= CONFIG =================
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5555

_RIG_CFG = load_rig_config()
# Port order matches firmware + experiment_control: 0=left, 1=right, 2=center.
_PORT_PREFIX = ("L", "R", "C")
_SOL_SUFFIX = ("R1", "R2", "R3", "DRAIN", "RINSE")


def _sol_label(port: int, sol: int) -> str:
    return f"{_PORT_PREFIX[port]}_{_SOL_SUFFIX[sol]}"


def sol_color(name: str) -> str:
    if "R" in name and "DRAIN" not in name and "RINSE" not in name:
        return "#90EE90"
    if "DRAIN" in name:
        return "#FF9999"
    if "RINSE" in name:
        return "#ADD8E6"
    return "lightgray"


# ================= GUI =================
root = tk.Tk()
root.title("T-Maze Monitor")

sensor_labels = []
lick_labels = []

# ---- Sensors ----
tk.Label(root, text="Body Sensors").grid(row=0, column=0, columnspan=6)

for i in range(6):
    lbl = tk.Label(root, text=f"S{i}", width=10, bg="gray")
    lbl.grid(row=1, column=i)
    sensor_labels.append(lbl)

# ---- Licks ----
tk.Label(root, text="Lick Sensors").grid(row=2, column=0, columnspan=3)

for i in range(3):
    if _RIG_CFG.ok:
        label = _RIG_CFG.lick_label(i)
    else:
        label = f"L{i}"
    lbl = tk.Label(root, text=label, width=14, bg="gray")
    lbl.grid(row=3, column=i)
    lick_labels.append(lbl)

# ---- Connect to broker (must exist before send/handle_line are wired up) ----
client = RigClient(root, host=BROKER_HOST, port=BROKER_PORT)


def send(cmd):
    client.send(cmd)


# ---- Solenoids (rows: left / right / center; cols: sol 0-4) ----
tk.Label(root, text="Solenoids").grid(row=4, column=0, columnspan=5)

_port_names = ("Left (port 0)", "Right (port 1)", "Center (port 2)")
for row, port in enumerate(range(3)):
    tk.Label(root, text=_port_names[row]).grid(row=5 + row, column=0, sticky="w", padx=4)
    for sol in range(5):
        name = _sol_label(port, sol)
        tk.Button(
            root,
            text=name,
            width=12,
            bg=sol_color(name),
            command=partial(send, f"R{port}{sol}")
        ).grid(row=5 + row, column=sol + 1)

# ---- Log ----
log = tk.Text(root, height=12, width=60)
log.grid(row=10, column=0, columnspan=6)

# ---- Status ----
status_var = tk.StringVar(value="Connecting...")
status_lbl = tk.Label(root, textvariable=status_var, anchor="w")
status_lbl.grid(row=11, column=0, columnspan=6, sticky="we")

# ================= HANDLER =================


def handle_line(line):
    # Every firmware line is "<ms>,<TYPE>,<payload...>" (see firmware/PROTOCOL.md);
    # parsing goes through the shared protocol module.
    if protocol.parse(line) is None:
        return

    try:
        sensor = protocol.sensor_edge(line)
        lick = protocol.lick_edge(line)
        if sensor is not None:
            idx, active = sensor
            sensor_labels[idx].config(bg="green" if active else "gray")
        elif lick is not None:
            idx, active = lick
            lick_labels[idx].config(bg="blue" if active else "gray")
        else:
            log.insert(tk.END, line + "\n")
            log.see(tk.END)
    except IndexError as e:
        print("Parse error:", e, line)


def set_status(text):
    status_var.set(text)


# ================= CLIENT START =================
client.on_line(handle_line)
client.on_status(set_status)

try:
    client.start()
except Exception as e:
    err = (f"Could not connect to rig broker on {BROKER_HOST}:{BROKER_PORT}: {e}\n\n"
           "Is rig_broker.py running? Try start_rig.bat.")
    print(f"[maze_gui] {err}", file=sys.stderr, flush=True)
    messagebox.showerror("T-Maze Monitor - broker not reachable", err)
    root.destroy()
    sys.exit(1)


def on_close():
    client.stop()
    root.destroy()


root.protocol("WM_DELETE_WINDOW", on_close)

# ================= RUN =================
try:
    root.mainloop()
except KeyboardInterrupt:
    on_close()
