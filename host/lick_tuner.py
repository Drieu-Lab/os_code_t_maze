"""
lick_tuner.py - Real-time visualization of D12DAB6 lick-detector output.

Use this when tuning the Gain and Hold dials on the three Banner D12DAB6
amplifiers so each lick produces exactly one clean rising/falling edge pair.

The script connects to the rig broker (rig_broker.py) over TCP, listens for
protocol-v2 "<ms>,L,<i>,<state>" edge messages, and renders three stacked step
traces as a function of time. The visible time window is adjustable from 1 to 60
seconds; press Pause to freeze the display while inspecting an event.

Two diagnostics for tuning are shown below the plot:
  - Edges per channel since reset (a single lick = 2 edges; more = chatter).
  - Width of the most recent HIGH pulse in ms (this is what Hold sets).

NOTE: rig_broker.py must already be running. Use start_rig.bat to launch
the broker and both GUIs together, or run "python rig_broker.py" first.

Dependencies:
    pip install pyserial matplotlib

Run from os_code_t_maze/host/:
    python lick_tuner.py
"""

import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

import matplotlib

matplotlib.use("TkAgg")  # noqa: E402
from matplotlib.backends.backend_tkagg import (  # noqa: E402
    FigureCanvasTkAgg,
    NavigationToolbar2Tk,
)
from matplotlib.figure import Figure  # noqa: E402

from rig_io import RigClient  # noqa: E402

# ================= CONFIG =================
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 5555

N_CHANNELS = 3

# Per IMG_1134 in the rig manual: lickPins[0] = pin 35 = Lick 3,
# lickPins[1] = pin 36 = Lick 2, lickPins[2] = pin 37 = Lick 1.
# Edit these strings if the firmware lickPins[] order changes.
CHANNEL_LABELS = [
    "L0  (pin 35 / Lick 3)",
    "L1  (pin 36 / Lick 2)",
    "L2  (pin 37 / Lick 1)",
]
CHANNEL_COLORS = ["#1f77b4", "#d62728", "#2ca02c"]

Y_LOW_HIGH = 1.0   # vertical span of one channel (low to high)
Y_GAP = 1.0        # vertical gap between channels
REFRESH_MS = 40    # ~25 Hz screen refresh
HISTORY_S = 120    # keep at most this many seconds of edges in memory

# ================= STATE =================
data_lock = threading.Lock()
data = [deque() for _ in range(N_CHANNELS)]   # (t_seconds, state in {0, 1})
edge_count = [0] * N_CHANNELS
last_edge_t = [None] * N_CHANNELS
last_rise_t = [None] * N_CHANNELS
last_pulse_ms = [None] * N_CHANNELS

t_session_start = time.monotonic()


def now_s() -> float:
    return time.monotonic() - t_session_start


paused = [False]
pause_snapshot_t = [None]

# ================= GUI =================
root = tk.Tk()
root.withdraw()  # do not show until the broker connection is confirmed

# Connect to the rig broker on the main thread so failures are immediate
# and visible (rather than hiding behind a background retry loop).
client = RigClient(root, host=BROKER_HOST, port=BROKER_PORT)
try:
    print(f"[lick_tuner] connecting to broker {BROKER_HOST}:{BROKER_PORT} ...",
          flush=True)
    client.start()
    print(f"[lick_tuner] connected", flush=True)
except Exception as e:
    err = (f"Could not connect to rig broker on {BROKER_HOST}:{BROKER_PORT}: {e}\n\n"
           "Is rig_broker.py running? Try start_rig.bat.")
    print(f"[lick_tuner] {err}", file=sys.stderr, flush=True)
    messagebox.showerror("Lick tuner - broker not reachable", err)
    root.destroy()
    sys.exit(1)

root.title("D12DAB6 Lick Tuner")
root.geometry("1100x680")
root.deiconify()

top = ttk.Frame(root, padding=8)
top.pack(side=tk.TOP, fill=tk.X)

ttk.Label(top, text="Window (s):").pack(side=tk.LEFT)
window_var = tk.DoubleVar(value=10.0)
window_scale = ttk.Scale(
    top, from_=1.0, to=60.0, orient=tk.HORIZONTAL,
    length=260, variable=window_var,
)
window_scale.pack(side=tk.LEFT, padx=6)
window_value_label = ttk.Label(top, text="10.0 s", width=8)
window_value_label.pack(side=tk.LEFT, padx=(0, 16))


def toggle_pause():
    paused[0] = not paused[0]
    if paused[0]:
        pause_snapshot_t[0] = now_s()
        pause_btn.config(text="Resume")
    else:
        pause_snapshot_t[0] = None
        pause_btn.config(text="Pause")


pause_btn = ttk.Button(top, text="Pause", command=toggle_pause)
pause_btn.pack(side=tk.LEFT, padx=4)


def reset_counts():
    for i in range(N_CHANNELS):
        edge_count[i] = 0
        last_edge_t[i] = None
        last_rise_t[i] = None
        last_pulse_ms[i] = None


reset_btn = ttk.Button(top, text="Reset edge counts", command=reset_counts)
reset_btn.pack(side=tk.LEFT, padx=8)

status_var = tk.StringVar(value="Connecting...")
status_lbl = ttk.Label(top, textvariable=status_var)
status_lbl.pack(side=tk.RIGHT)

# ---------- Plot ----------
fig = Figure(figsize=(10, 5), dpi=100)
ax = fig.add_subplot(111)
canvas = FigureCanvasTkAgg(fig, master=root)
canvas_widget = canvas.get_tk_widget()
canvas_widget.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

toolbar = NavigationToolbar2Tk(canvas, root, pack_toolbar=False)
toolbar.update()
toolbar.pack(side=tk.BOTTOM, fill=tk.X)

# Status row at the bottom: per-channel diagnostics for tuning
status_row = ttk.Frame(root, padding=(8, 4))
status_row.pack(side=tk.BOTTOM, fill=tk.X)

count_labels = []
pulse_labels = []
for i in range(N_CHANNELS):
    f = ttk.Frame(status_row, padding=(8, 2), relief="groove", borderwidth=1)
    f.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
    title = tk.Label(f, text=CHANNEL_LABELS[i],
                     fg=CHANNEL_COLORS[i], font=("TkDefaultFont", 10, "bold"))
    title.pack(anchor=tk.W)
    cl = tk.Label(f, text="edges: 0   last: --")
    cl.pack(anchor=tk.W)
    pl = tk.Label(f, text="last pulse: --")
    pl.pack(anchor=tk.W)
    count_labels.append(cl)
    pulse_labels.append(pl)

# Pre-create one Line2D per channel and reuse it across refreshes.
lines = []
for i in range(N_CHANNELS):
    (line,) = ax.plot(
        [], [], drawstyle="steps-post",
        color=CHANNEL_COLORS[i], linewidth=1.5,
        label=CHANNEL_LABELS[i],
    )
    lines.append(line)

ymax = N_CHANNELS * (Y_LOW_HIGH + Y_GAP) - Y_GAP
ax.set_ylim(-0.3, ymax + 0.3)
ax.set_yticks(
    [i * (Y_LOW_HIGH + Y_GAP) + Y_LOW_HIGH / 2 for i in range(N_CHANNELS)]
)
ax.set_yticklabels([f"L{i}" for i in range(N_CHANNELS)])

# Faint dashed reference for each channel's "low" baseline.
for i in range(N_CHANNELS):
    ax.axhline(i * (Y_LOW_HIGH + Y_GAP), color="#cccccc",
               linewidth=0.5, linestyle="--")

ax.grid(True, axis="x", alpha=0.3)
ax.set_xlabel("time (s)")
fig.tight_layout()


# ================= RENDER =================
def channel_trace(i: int, t_start: float, t_end: float):
    """Build (xs, ys) suitable for a step-post trace inside [t_start, t_end]."""
    with data_lock:
        pts = list(data[i])
    state_at_start = 0
    xs, ys = [], []
    for t, s in pts:
        if t < t_start:
            state_at_start = s
        elif t <= t_end:
            xs.append(t)
            ys.append(s)
    xs.insert(0, t_start)
    ys.insert(0, state_at_start)
    xs.append(t_end)
    ys.append(ys[-1])
    return xs, ys


def render():
    if paused[0] and pause_snapshot_t[0] is not None:
        t_end = pause_snapshot_t[0]
    else:
        t_end = now_s()
    window_s = window_var.get()
    t_start = t_end - window_s
    window_value_label.config(text=f"{window_s:.1f} s")

    for i in range(N_CHANNELS):
        xs, ys = channel_trace(i, t_start, t_end)
        offset = i * (Y_LOW_HIGH + Y_GAP)
        ys_off = [y * Y_LOW_HIGH + offset for y in ys]
        lines[i].set_data(xs, ys_off)

    ax.set_xlim(t_start, t_end)
    canvas.draw_idle()
    update_status()


def update_status():
    now = now_s()
    for i in range(N_CHANNELS):
        last = "--"
        if last_edge_t[i] is not None:
            last = f"{(now - last_edge_t[i]):.2f}s ago"
        count_labels[i].config(text=f"edges: {edge_count[i]}   last: {last}")
        pw = "--"
        if last_pulse_ms[i] is not None:
            pw = f"{last_pulse_ms[i]:.1f} ms"
        pulse_labels[i].config(text=f"last pulse: {pw}")


def refresh_loop():
    try:
        render()
    finally:
        root.after(REFRESH_MS, refresh_loop)


# ================= LINE HANDLER =================
status_var.set(f"Connected to broker {BROKER_HOST}:{BROKER_PORT}")


def handle_line(line: str) -> None:
    """Parse a protocol-v2 "<ms>,L,<i>,<state>" edge line and update buffers.

    All other line types (S, SOL, TRIAL, ...) are ignored here. Runs on the
    Tk main thread (RigClient dispatches via root.after).
    """
    parts = line.split(",")
    if len(parts) != 4 or parts[1] != "L" or not parts[0].isdigit():
        return
    try:
        i = int(parts[2])
        s = int(parts[3])
    except ValueError:
        return
    if not (0 <= i < N_CHANNELS) or s not in (0, 1):
        return

    t = now_s()
    with data_lock:
        data[i].append((t, s))
        cutoff = t - HISTORY_S
        while data[i] and data[i][0][0] < cutoff:
            data[i].popleft()

    edge_count[i] += 1
    last_edge_t[i] = t
    if s == 1:
        last_rise_t[i] = t
    elif last_rise_t[i] is not None:
        last_pulse_ms[i] = (t - last_rise_t[i]) * 1000.0


def set_status(text: str) -> None:
    status_var.set(text)


client.on_line(handle_line)
client.on_status(set_status)


# ================= LIFECYCLE =================
def on_close():
    print("[lick_tuner] closing", flush=True)
    try:
        client.stop()
    except Exception:
        pass
    root.destroy()


root.protocol("WM_DELETE_WINDOW", on_close)
root.after(REFRESH_MS, refresh_loop)
try:
    root.mainloop()
except KeyboardInterrupt:
    on_close()
