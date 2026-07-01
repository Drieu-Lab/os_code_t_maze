# host - PC-side rig control & monitoring

Python tools that talk to the Arduino Due over a shared serial broker.

## Install

From an environment with Python 3 (the lab uses a conda env, e.g. `drieu_t_maze`):

```
pip install -r requirements.txt
```

## Architecture

```
Arduino Due  <--USB serial-->  rig_broker.py  <--TCP localhost:5555-->  N clients
```

- `rig_broker.py` - owns the Due's COM port and exposes its byte stream on
  TCP `localhost:5555`. Run this first.
- `rig_io.py` - `RigClient`, the shared TCP client used by the GUIs.
- `maze_gui.py` - live monitor for body sensors, lick sensors, and solenoids.
- `lick_tuner.py` - real-time traces for tuning the lick-detector amplifiers.
- `experiment_control.py` - extensible experiment-control GUI; modules fill
  tabs (the first tab shows the maze schematic beside the camera). Add modules
  via the TABS registry.
- `rig_view.py` - the maze-illustration module used by `experiment_control.py`.
- `camera_view.py` - the camera module (beside the schematic): live Basler
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
start_rig.bat            REM defaults to COM4
start_rig.bat COM5       REM override the serial port
```

Or start the pieces manually:

```
python rig_broker.py --port COM4      # terminal 1
python maze_gui.py                    # terminal 2
python lick_tuner.py                  # terminal 3
python experiment_control.py          # terminal 4 (maze illustration)
```

`experiment_control.py` is a broker client like the GUIs above, so the broker
must be running first. It is built to grow: additional modules (reward control,
trial monitor, ...) are added as notebook tabs.
