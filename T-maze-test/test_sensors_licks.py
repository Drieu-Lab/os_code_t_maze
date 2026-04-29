import serial
import tkinter as tk
import threading

# ================= CONFIG =================
PORT = "COM3"
BAUD = 115200

# ================= SERIAL =================
ser = serial.Serial(PORT, BAUD, timeout=1)

# wait for Arduino reboot
import time
time.sleep(2)
ser.reset_input_buffer()

# ================= GUI =================
root = tk.Tk()
root.title("T-Maze Monitor")

sensor_labels = []
lick_labels = []

# ---- Body sensors ----
tk.Label(root, text="Body Sensors").grid(row=0, column=0, columnspan=6)

for i in range(6):
    lbl = tk.Label(root, text=f"S{i}", width=10, bg="gray")
    lbl.grid(row=1, column=i)
    sensor_labels.append(lbl)

# ---- Lick sensors ----
tk.Label(root, text="Lick Sensors").grid(row=2, column=0, columnspan=3)

for i in range(3):
    lbl = tk.Label(root, text=f"L{i}", width=10, bg="gray")
    lbl.grid(row=3, column=i)
    lick_labels.append(lbl)

# ================= DATA HANDLER =================

def handle_line(line):
    parts = line.split(",")

    if len(parts) < 3:
        return

    try:
        if parts[0] == "S":
            idx = int(parts[1])
            state = int(parts[2])

            # INPUT_PULLUP logic: 0 = active
            color = "green" if state == 0 else "gray"
            sensor_labels[idx].config(bg=color)

        elif parts[0] == "L":
            idx = int(parts[1])
            state = int(parts[2])

            color = "blue" if state == 0 else "gray"
            lick_labels[idx].config(bg=color)

    except Exception as e:
        print("Parse error:", e, line)

# ================= SERIAL THREAD =================

def serial_loop():
    while True:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()

            if line:
                print(line)  # debug console
                root.after(0, handle_line, line)

        except Exception as e:
            print("Serial error:", e)

# run serial in background
threading.Thread(target=serial_loop, daemon=True).start()

# ================= START GUI =================
root.mainloop()