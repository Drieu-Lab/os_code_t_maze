# os_code_t_maze

Code that runs the Drieu lab T-maze behavioral rig. This repository contains
**only experiment code** - the Arduino firmware that drives the rig and the
PC-side Python tools that control and monitor it.

Hardware references (circuit photos, the rig manual, audio-hardware notes, CAD),
planning material, lab notes, animal records, and GUI mockups live in the sibling
repository [`os_code_t_maze_resources`](../os_code_t_maze_resources). Offline data
analysis lives in its own repository, `drieu_lab_analysis`, which reads the session
bundles this rig writes but never imports its code
([ADR-0142](docs/decisions/0142-analysis-core-decoupling-data-contract.md)).

## Layout

```
firmware/                  Arduino sketches that run on the Arduino Due
  cue-guided-task/         Behavioral task firmware (auditory-cue T-maze)
  common/rig_config.h      Pin map shared by the sketches (the host reads it too)
  tests/                   Bring-up / diagnostic sketches (tmaze_test, sound_test)
  PROTOCOL.md              Serial/TCP message protocol
host/                      PC-side Python tools + the rig's config files (*.json)
  tmaze/                   The installable `tmaze` package (ADR-0083)
    core/                  Pure logic: protocol, maze state, rules, Phase 3 task,
                           session manifest, records, reward, video<->Arduino sync
    io/                    Serial broker, TCP client, logger, process helpers
    vision/                Camera tracking, calibration, LED detection, video player
    ui/                    Tk GUIs (experiment_control, records_manager, ...)
    tools/                 Command-line tools (sync_fit, offline_track, ...)
  tests/                   Headless unit tests (+ the golden session bundle)
  tools/                   Legacy scripts + the golden-bundle generator
  start_rig.bat            Launches the broker + the GUIs
  run_tests.bat            Runs every host unit test
docs/                      ADRs (decisions/), backlog (future-work.md), glossary,
                           design docs, walkthroughs, notes inbox
pyproject.toml             Package definition for `tmaze`
environment.yml            Conda env `drieu_t_maze`
```

The `host/tmaze/` layers follow the "pure core + thin shell" pattern described in
[`CONTRIBUTING.md`](CONTRIBUTING.md): logic lives in `core/` with no GUI, camera,
or serial code, so it can be tested on its own.

## Runtime architecture

```
Arduino Due  <--USB serial-->  rig_broker  <--TCP localhost:5555-->  N clients
                                                (experiment_control, lick_tuner, ...)
```

The broker (`tmaze.io.rig_broker`) is the single owner of the serial port. GUIs
and other tools connect to it over TCP rather than opening the serial port
directly, so several tools can share the rig at once.

## Getting started

Once per machine, from the repo root:

```
conda activate drieu_t_maze
pip install -e .
```

Programs then run as `python -m tmaze.<layer>.<name>` from any directory, or use
`host/start_rig.bat` to launch the broker and GUIs together. See
[`PROGRAMS.md`](PROGRAMS.md) for every program and its exact command, and
[`host/README.md`](host/README.md) for installing dependencies and launching the
rig. Open a `firmware/` sketch in the Arduino IDE to flash the Due.

New to the code? Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before changing host
code, and [`docs/glossary.md`](docs/glossary.md) for plain-language definitions of
recurring terms. [`docs/how-tracking-and-rules-work.md`](docs/how-tracking-and-rules-work.md)
is a no-coding walkthrough of the blob tracker, the maze-state estimator, the
task-event layer, and the rules engine. The reasons behind key choices are in
[`docs/decisions/`](docs/decisions/README.md).
