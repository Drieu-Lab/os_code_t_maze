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
| `rig_logger.py` | Headless broker client that records every firmware line to `host/data/<animal>_<time>.csv` (wall-clock-stamped). | `python rig_logger.py` |
| `maze_gui.py` | Live monitor for body sensors, lick sensors, and solenoid flush buttons. | `python maze_gui.py` |
| `lick_tuner.py` | Real-time lick-edge traces for tuning the lick-detector amplifiers. | `python lick_tuner.py` |
| `experiment_control.py` | Extensible tabbed experiment-control GUI (live maze illustration with the Basler camera preview/recording beside it, plus a "Tracking" tab to register/tune the markerless rat tracker when its deps are present). The Rig view tab's bottom strip has a "Reward dispensed" readout that totals the session's reward volume (from the firmware `REWARD` lines via the `reward_volume.py` calibration; auto/manual split, resets on a mode switch; see ADR-0019), plus a "Quit rig" button that closes every window `start_rig.bat` opened (broker, logger, tuner, this GUI; see ADR-0016). | `python experiment_control.py` |
| `blob_tracker_prototype.py` | Standalone OpenCV-window tuning tool over the shared tracker core (`tracker.py`): tune reference-frame thresholds, draw the rig ROI polygon, register a colour+size+shape appearance model (`t`) so wrong-colour/size intruders are rejected, optionally enrich it while tracking (`a`), and sanity-check centroid tracking + re-acquisition. Trackbars (thresh/min_area/blur/gate/accept/coast), Kalman smoothing, confidence/elongation/solidity readout, save/load model (`w`/`o`, to `host/ratmodels/`) and ROI (`e`, to `host/roi_polygon.json`), optional centroid CSV. Source = rig Basler camera, a video file, or a webcam; `--background <png>` installs a saved empty-rig background as the reference so a recorded video (which has no empty-rig frame) can be tuned (see ADR-0020). | `python blob_tracker_prototype.py [--source basler\|<file>\|<webcam#>] [--background <png>]` |
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
| `host/camera_view.py` | `CameraView`, the Basler-camera module beside the schematic (live preview + recording, selectable lossless FFV1 or compressed H.264) used by `experiment_control.py`. Applies optional lens-distortion correction at the source from a calibration JSON (see ADR-0015), and optionally runs the injected `RatTracker` on the grab thread to overlay tracking on the preview and add per-frame track columns to the sidecar CSV (default off; see ADR-0014). Needs `pypylon`, `opencv-python`, `numpy`, `pillow`, `av`. |
| `host/tracker.py` | `RatTracker`, the UI-free markerless position-tracking core (reference-frame subtraction, polygon ROI, distance/area gating, Kalman) plus the Phase 3 robustness layer: a learned `RatModel` appearance prior (pooled H-S colour histogram + a Gaussian over [log area, log elongation, solidity] capturing the size/shape range) built during a registration window, distance+colour+soft-shape scoring with live tolerance knobs (size band `area_gate_k`, appearance-vs-position bias, shape softness, colour hard-gate; see ADR-0017), an ACQUIRING/TRACKING/COASTING/LOST state machine with confidence, optional online enrichment (accumulate more samples while tracking), and model/ROI persistence. Shared by `blob_tracker_prototype.py` and the experiment GUI. Needs `opencv-python`, `numpy`. |
| `host/tracker_view.py` | `TrackerControlView`, the "Tracking" tab for `experiment_control.py`: a live camera preview (rendered only while the tab is visible) plus enable tracking, grab background (and "Save background..." to write the full-res empty-rig frame as a PNG for offline tuning; also auto-paired as `<name>_background.png` with each recording, see ADR-0020), register/enrich the appearance model, tuning sliders plus a collapsible "Model tolerances" section (size/appearance/shape/colour knobs), a colorized binary-mask view stacked under the preview (tracked blob filled green), live state/confidence readout, save/load model (`host/ratmodels/`) and ROI polygon (`host/roi_polygon.json`, via a still-frame editor), and an optional standalone track CSV (written to a `track_logs/` subfolder of the recordings folder; see ADR-0014). Needs `opencv-python`, `numpy`, `pillow`. |
| `host/calibration.py` | `load_calibration()` + `Undistorter`: shared lens-distortion calibration loading and undistortion (OpenCV-convention JSON), used by both `camera_view.py` and `blob_tracker_prototype.py` (see ADR-0015). Needs `opencv-python`, `numpy`. |
| `host/reward_volume.py` | `reward_volume_ul()` + the `REWARD_UL_CALIBRATION` constant: maps a reward solenoid pulse width (ms) to a calibrated dispensed volume (uL) so `experiment_control.py` can show a per-session reward total (see ADR-0019). Edit the calibration table when you re-measure at a new pulse width. Pure stdlib. |

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
