# os_code_t_maze

Code that runs the Drieu lab T-maze behavioral rig. This repository contains
**only experiment code** - the Arduino firmware that drives the rig and the
PC-side Python tools that control and monitor it.

Hardware references (circuit photos, the rig manual, audio-hardware notes, CAD),
planning material, lab notes, and GUI mockups live in the sibling repository
[`os_code_t_maze_resources`](../os_code_t_maze_resources). Offline data analysis
lives in its own separate repository.

## Layout

```
firmware/                  Arduino sketches that run on the Arduino Due
  cue-guided-task/         Behavioral task firmware (auditory-cue T-maze)
  tmaze_test/              Hardware bring-up / diagnostic sketch
host/                      PC-side Python control & monitoring tools
  rig_broker.py            Owns the Due's serial port; fans it out over TCP
  rig_io.py                Shared TCP client library (RigClient)
  maze_gui.py              Live sensor / lick / solenoid monitor
  lick_tuner.py            Lick-amplifier tuning GUI
  start_rig.bat            Launches the broker + both GUIs
  requirements.txt         Python dependencies
  tools/                   Standalone diagnostics
```

## Runtime architecture

```
Arduino Due  <--USB serial-->  rig_broker.py  <--TCP localhost:5555-->  N clients
                                                  (maze_gui.py, lick_tuner.py, ...)
```

The broker is the single owner of the serial port. GUIs and other tools connect
to it over TCP rather than opening the serial port directly, so several tools
can share the rig at once.

## Getting started

See [`PROGRAMS.md`](PROGRAMS.md) for the full list of programs and the exact
commands to run each one (Anaconda env `drieu_t_maze`).

See [`host/README.md`](host/README.md) for how to install dependencies and launch
the rig. Open a `firmware/` sketch in the Arduino IDE to flash the Due.

New to the camera-driven side of the rig? [`docs/how-tracking-and-rules-work.md`](docs/how-tracking-and-rules-work.md)
is a plain-language walkthrough of the blob tracker, the maze-state estimator,
the task-event layer, and the rules engine - no coding needed.
