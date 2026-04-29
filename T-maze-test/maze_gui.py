import serial
import tkinter as tk
import threading
import time
from functools import partial

# ================= CONFIG =================
PORT = "COM3"
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=1)

time.sleep(2)
ser.reset_input_buffer()

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

def send(cmd):
    ser.write((cmd + "\n").encode())

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

# ================= SERIAL THREAD =================

def serial_loop():
    while True:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()

            if line:
                print(line)
                root.after(0, handle_line, line)

        except Exception as e:
            print("Serial error:", e)

threading.Thread(target=serial_loop, daemon=True).start()

# ================= RUN =================
root.mainloop()