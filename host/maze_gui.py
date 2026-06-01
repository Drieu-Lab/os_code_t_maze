import sys
import tkinter as tk
from functools import partial
from tkinter import messagebox

from rig_io import RigClient

# ================= CONFIG =================
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5555

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
    lbl = tk.Label(root, text=f"L{i}", width=10, bg="gray")
    lbl.grid(row=3, column=i)
    lick_labels.append(lbl)

# ---- Solenoids ----
solenoid_names = [
    ["C_R1", "C_R2", "C_R3", "C_DRAIN", "C_RINSE"],
    ["L_R1", "L_R2", "L_R3", "L_DRAIN", "L_RINSE"],
    ["R_R1", "R_R2", "R_R3", "R_DRAIN", "R_RINSE"]
]

def sol_color(name):
    if "R" in name and "DRAIN" not in name and "RINSE" not in name:
        return "#90EE90"
    if "DRAIN" in name:
        return "#FF9999"
    if "RINSE" in name:
        return "#ADD8E6"
    return "lightgray"

# ---- Connect to broker (must exist before send/handle_line are wired up) ----
client = RigClient(root, host=BROKER_HOST, port=BROKER_PORT)

def send(cmd):
    client.send(cmd)

tk.Label(root, text="Solenoids").grid(row=4, column=0, columnspan=5)

for port in range(3):
    for sol in range(5):
        name = solenoid_names[port][sol]

        tk.Button(
            root,
            text=name,
            width=12,
            bg=sol_color(name),
            command=partial(send, f"R{port}{sol}")
        ).grid(row=5 + port, column=sol)

# ---- Log ----
log = tk.Text(root, height=12, width=60)
log.grid(row=10, column=0, columnspan=6)

# ---- Status ----
status_var = tk.StringVar(value="Connecting...")
status_lbl = tk.Label(root, textvariable=status_var, anchor="w")
status_lbl.grid(row=11, column=0, columnspan=6, sticky="we")

# ================= HANDLER =================

def handle_line(line):
    parts = line.split(",")

    if len(parts) != 3:
        return

    try:
        t = parts[0]
        idx = int(parts[1])
        state = int(parts[2])

        log.insert(tk.END, line + "\n")
        log.see(tk.END)

        if t == "S":
            sensor_labels[idx].config(
                bg="green" if state == 1 else "gray"
            )

        elif t == "L":
            lick_labels[idx].config(
                bg="blue" if state == 1 else "gray"
            )

    except Exception as e:
        print("Parse error:", e)


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
