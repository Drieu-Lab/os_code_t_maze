# host - PC-side rig control & monitoring

Python tools that talk to the Arduino Due over a shared serial broker.

## Install

From an environment with Python 3 (the lab uses a conda env, e.g. `drieu_t_maze`):

```
pip install -r requirements.txt   # runtime deps
pip install -e .                  # the tmaze package, editable, from the repo root
```

The PC tools are an installable package **`tmaze`** (source under `host/tmaze/`,
layered `core / io / vision / ui / tools`; see
[ADR-0083](../docs/decisions/0083-host-tools-become-tmaze-package.md)). The
editable install makes `import tmaze` and `python -m tmaze...` work from any
directory; run it once per machine (including each rig PC after a pull). The
`test_*` suite lives in `host/tests/`.

## Architecture

```
Arduino Due  <--USB serial-->  tmaze.io.rig_broker  <--TCP localhost:5555-->  N clients
```

- `tmaze.io.rig_broker` - owns the Due's COM port and exposes its byte stream on
  TCP `localhost:5555`. Run this first.
- `tmaze.io.rig_io` - `RigClient`, the shared TCP client used by the GUIs.
- `tmaze.ui.maze_gui` - live monitor for body sensors, lick sensors, and solenoids.
- `tmaze.ui.lick_tuner` - real-time traces for tuning the lick-detector amplifiers.
- `tmaze.ui.experiment_control` - extensible experiment-control GUI; modules fill
  tabs (the first tab shows the maze schematic beside the camera). Add modules
  via the TABS registry.
- `tmaze.ui.rig_view` - the maze-illustration module used by `experiment_control`.
- `tmaze.ui.camera_view` - the camera module (beside the schematic): live Basler
  preview + recording (free-run), rotated to match the schematic. A **Format**
  dropdown picks per recording between lossless FFV1 (`.mkv`, bit-exact, large)
  and compressed H.264 (`.mp4`, lossy, ~25-100x smaller, with a CRF quality
  spinbox); see docs/decisions/0013. Needs `pypylon` (pip only), `numpy`,
  `pillow`, OpenCV (pip `opencv-python` or conda-forge `py-opencv` - install only
  one), and `av` (PyAV) for recording (preview works without `av`; recording is
  disabled until it's installed). Degrades to a message if a preview dep or the
  camera is unavailable. Close Pylon Viewer first - only one app can grab the
  camera at a time.
- `tools/test_sensors_licks.py` - legacy standalone test that opens the serial
  port directly (does **not** use the broker); handy for quick bring-up.

## Run

The simplest path on Windows is the launcher, which starts the broker and both
GUIs (run it from an Anaconda Prompt with the rig's conda env activated):

```
start_rig.bat            REM defaults to COM3, the Due's Programming port
start_rig.bat COM5       REM override the serial port
```

The console lives on the Programming port because the Native USB port wedges
~1030 s after the host opens it (see [ADR-0065](../docs/decisions/0065-console-on-the-programming-port.md)).
Two consequences worth knowing at the bench: opening the port **resets the board**,
so starting the broker begins a fresh firmware session with a new trial sequence,
and firmware uploads need the "Arduino Due (Programming Port)" board entry.

Or start the pieces manually (from any directory once `pip install -e .` has run):

```
python -m tmaze.io.rig_broker --port COM3    # terminal 1
python -m tmaze.ui.maze_gui                  # terminal 2
python -m tmaze.ui.lick_tuner                # terminal 3
python -m tmaze.ui.experiment_control        # terminal 4 (maze illustration)
python -m tmaze.io.rig_logger                # optional: standalone full-stream backup log
```

`rig_logger` is no longer launched by `start_rig.bat` (the session bundle now
captures the full serial stream itself; see
[ADR-0178](../docs/decisions/0178-full-verbatim-per-session-serial-capture.md) /
[ADR-0179](../docs/decisions/0179-retire-rig-logger-from-the-session-launch.md)).
Run it by hand only if you want the separate backup file.

`experiment_control` is a broker client like the GUIs above, so the broker must
be running first. It is built to grow: additional modules (reward control, trial
monitor, ...) are added as notebook tabs.
