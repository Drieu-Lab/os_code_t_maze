# Programs & run commands

A quick reference of every runnable program in this repo, what it does, and how
to launch it from Anaconda. The conda env is **`drieu_t_maze`**.

> Keep this file current: whenever a program is added, removed, or its launch
> command changes, update the relevant table below. (See `AGENTS.md`.)

## Setup (once per terminal)

```
conda activate drieu_t_maze
```

The Python host tools import sibling modules and use relative paths, so run them
from the `host/` directory:

```
cd host
```

## Host tools (Python)

Start `rig_broker.py` first; the GUIs are TCP clients of the broker and need it
running. Run each in its own terminal (or use the launcher below).

| Program | Description | Command (from `host/`) |
|---------|-------------|------------------------|
| `rig_broker.py` | Owns the Arduino Due's COM port and fans its serial stream out over TCP `localhost:5555`. Run this first. | `python rig_broker.py --port COM4` |
| `maze_gui.py` | Live monitor for body sensors, lick sensors, and solenoid flush buttons. | `python maze_gui.py` |
| `lick_tuner.py` | Real-time lick-edge traces for tuning the lick-detector amplifiers. | `python lick_tuner.py` |
| `experiment_control.py` | Extensible tabbed experiment-control GUI (first tab is a live maze illustration). | `python experiment_control.py` |
| `tools/test_sensors_licks.py` | Legacy bring-up tool that opens the serial port **directly** (bypasses the broker; don't run alongside it on the same port). | `python tools/test_sensors_licks.py` |

### Launcher (Windows)

Starts the broker plus the GUIs together (run from `host/`):

```
start_rig.bat            REM defaults to COM4
start_rig.bat COM5       REM override the serial port
```

## Libraries (not run directly)

| Module | Description |
|--------|-------------|
| `host/rig_io.py` | `RigClient`, the shared TCP client used by the GUIs. |
| `host/rig_view.py` | `RigView`, the maze-illustration module used by `experiment_control.py`. |

## Firmware (Arduino Due)

Flashed onto the Due with the Arduino IDE (open the `.ino`, select the Due +
correct port, upload) rather than run from the PC.

| Sketch | Description |
|--------|-------------|
| `firmware/cue-guided-task/cue-guided-task.ino` | Production behavioral task firmware (auditory-cue T-maze). |
| `firmware/tests/tmaze_test/tmaze_test.ino` | Hardware bring-up / diagnostic sketch (raw sensor edges + manual solenoid control). |

## Docs / tooling

| Program | Description | Command (from repo root) |
|---------|-------------|--------------------------|
| `docs/render_graph.py` | Renders the architecture graph (`docs/architecture.dot`) to an image. Needs Graphviz: `conda install -c conda-forge graphviz python-graphviz`. | `python docs/render_graph.py --open` |
