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
| `experiment_control.py` | Extensible tabbed experiment-control GUI (live maze illustration with the Basler camera preview/recording beside it, plus a "Tracking" tab to register/tune the markerless rat tracker, a "Rules" tab for the position-based rules engine, and a "Maze state" tab (discrete zone/direction/loop state + zone editor; ADR-0025), when their deps are present). The Rig view tab's bottom strip has a "Reward dispensed" readout that totals the session's reward volume (from the firmware `REWARD` lines via the `reward_volume.py` calibration; auto/manual split, resets on a mode switch; see ADR-0019), plus a "Quit rig" button that closes every window `start_rig.bat` opened (broker, logger, tuner, this GUI; see ADR-0016). | `python experiment_control.py` |
| `blob_tracker_prototype.py` | Standalone OpenCV-window tuning tool over the shared tracker core (`tracker.py`): tune reference-frame thresholds, draw the rig ROI polygon, register a colour+size+shape appearance model (`t`) so wrong-colour/size intruders are rejected, optionally enrich it while tracking (`a`), and sanity-check centroid tracking + re-acquisition. Trackbars (thresh/min_area/blur/gate/accept/coast), Kalman smoothing, confidence/elongation/solidity readout, save/load model (`w`/`o`, to `host/ratmodels/`) and ROI (`e`, to `host/roi_polygon.json`), optional centroid CSV. Source = rig Basler camera, a video file, or a webcam; `--background <png>` installs a saved empty-rig background as the reference so a recorded video (which has no empty-rig frame) can be tuned (see ADR-0020). | `python blob_tracker_prototype.py [--source basler\|<file>\|<webcam#>] [--background <png>]` |
| `test_rules_engine.py` | Headless unit tests for the pure rules-engine core (`rules_engine.py`): enter/exit/re-arm, cm->px thresholds, top-up count cap, the within-zone lick conjunction, confidence gate, cooldown, global backstop, and the homography distance path. No hardware/Tk/OpenCV. | `python -m unittest test_rules_engine` |
| `test_protocol.py` | Headless unit tests for the shared serial-line parser (`protocol.py`): every line type, malformed input, REWARD manual/auto/duration-fallback, MODE tokens. No hardware/Tk. | `python -m unittest test_protocol` |
| `test_maze_state.py` | Headless unit tests for the pure maze-state core (`maze_state.py`): point-in-polygon, zone classification, first-localization commit, min-dwell hysteresis, UNKNOWN + no-false-transition on low-conf/COASTING, in_well substate, up/down-stem direction, L/R loop detection, `visited_since`, and zones.json round-trip. No hardware/Tk/OpenCV. | `python -m unittest test_maze_state` |
| `test_task_events.py` | Headless unit tests for the task-event layer (`task_events.py`) driven through the real maze-state estimator (see ADR-0026): the well-arrival and choice-entered edges, `outbound_used_stem` (stem vs return-arm approach), clean vs stem-reversal loops, and the same-side run aggregates (`same_side_run` over loops, `same_side_choice_run` over stem-gated choices; ADR-0030). No hardware/Tk/OpenCV. | `python -m unittest test_task_events` |
| `test_well_reward.py` | Headless unit tests for the credential-gated well reward template (`well_reward_with_excursion`; see ADR-0026): Phase 1 (choice credit, no reward without an excursion, credit consumed by the prime, lick top-up cap), Phase 2a (stem-gated choice; return-arm approach denied), prime-at-choice-entry timing with capped top-ups and choice-side anti-bias (Phase 2b; `prime_on: credential`, ADR-0030), the legacy clean-loop credit (stem-reversal denied; fixed-debias withhold), and lick-gated priming. No hardware/Tk/OpenCV. | `python -m unittest test_well_reward` |
| `test_rules_editor.py` | Headless unit tests for the Rules-tab field-editor core (see ADR-0031): field type coercion, the `debias` sub-config set/clear helper, whole-profile validation (unknown credential/prime_on rejected), and a save/load round-trip that preserves the `_schema` block, defaults, and any key the editor did not touch. No hardware/Tk/OpenCV. | `python -m unittest test_rules_editor` |
| `test_tracker_model.py` | Headless unit tests for `RatModel` settings persistence (see ADR-0027): the acquisition-time `TrackerParams` snapshot survives a `.npz` save/load round-trip, older models without stored settings still load (`acq_params is None`), and `RatTracker.load_model` restores every `MODEL_REAPPLY_FIELDS` value while leaving the provenance-only `reg_*`/`adapt_*` knobs untouched. Round-trip/back-compat need only numpy; the restore tests skip without OpenCV. | `python -m unittest test_tracker_model` |
| `maze_state_validate.py` | Beam-vs-vision agreement + latency harness (see ADR-0025): replays a tracker track CSV through `maze_state.py` and aligns the `entered`-zone transitions to the firmware `S,<i>` beam edges from a `rig_logger` CSV on the host wall-clock, reporting per-beam match rate + signed latency + candidate false transitions. `--selftest` runs a synthetic end-to-end check. | `python maze_state_validate.py --selftest` |
| `tools/test_sensors_licks.py` | Legacy bring-up tool that opens the serial port **directly** (bypasses the broker; don't run alongside it on the same port). | `python tools/test_sensors_licks.py` |
| `run_tests.bat` | Runs every host unit test at once (discovers all `test_*.py`). Use this instead of invoking each `test_*` module by hand; tracker/model tests skip gracefully without OpenCV. | `run_tests.bat` (or `python -m unittest discover -p "test_*.py"`) |

### Launcher (Windows)

Starts the broker plus the GUIs together (run from `host/`). It prompts for a
rat id and passes it to `rig_logger.py` via `--animal` so the session file is
`data/<rat>_<time>.csv` (leave blank to fall back to the firmware `ANIMAL_ID`):

```
start_rig.bat            REM defaults to COM4
start_rig.bat COM5       REM override the serial port
```

## Libraries (not run directly)

| Module | Description |
|--------|-------------|
| `host/rig_io.py` | `RigClient`, the shared TCP client used by the GUIs. |
| `host/protocol.py` | The single host-side parser for the firmware's `<ms>,<TYPE>,<payload...>` serial lines (see `firmware/PROTOCOL.md`): `parse()` plus per-type helpers (`lick_edge`, `sensor_edge`, `sol_pulse`, `mode_name`, `animal_id`, `reward_info`). All host tools parse through it instead of hand-rolling `line.split(",")` (see ADR-0024). Pure stdlib; tested by `test_protocol.py`. |
| `host/gui_deps.py` | Shared defensive imports for optional GUI dependencies (`numpy`, `opencv-python`, `pillow`, `pypylon`, `av`) plus `missing_dependency()` so camera/tracking/rules tabs degrade gracefully when a dep is absent. |
| `host/preview.py` | Shared Tk live-preview helpers: `render_bgr_to_label()` (resize, optional overlay draw, show on a label) and `safe_after()` (schedule without TclError on teardown). Used by `camera_view.py`, `tracker_view.py`, and `rules_view.py`. |
| `host/rig_process.py` | Windows "Quit rig" helpers: `rig_session_id()`, `find_rig_pids()`, `taskkill()` to close every process `start_rig.bat` launched (see ADR-0016). |
| `host/rig_config.py` | Parses `firmware/common/rig_config.h` into the pin map (lick/beam/solenoid index->pin plus port roles) so the host tools (`lick_tuner.py`, `rig_view.py`, the `experiment_control.py` lick counts) label ports from one source of truth instead of hardcoded guesses. Assumes flashed == source (reflash the Due after editing the header). Pure stdlib. |
| `host/rig_view.py` | `RigView`, the maze-illustration module used by `experiment_control.py`. |
| `host/camera_view.py` | `CameraView`, the Basler-camera module beside the schematic (live preview + recording, selectable lossless FFV1 or compressed H.264) used by `experiment_control.py`. Applies optional lens-distortion correction at the source from a calibration JSON (see ADR-0015), and optionally runs the injected `RatTracker` on the grab thread to overlay tracking on the preview and add per-frame track columns to the sidecar CSV (default off; see ADR-0014). Needs `pypylon`, `opencv-python`, `numpy`, `pillow`, `av`. |
| `host/tracker.py` | `RatTracker`, the UI-free markerless position-tracking core (reference-frame subtraction, polygon ROI, distance/area gating, Kalman) plus the Phase 3 robustness layer: a learned `RatModel` appearance prior (pooled H-S colour histogram + a Gaussian over [log area, log elongation, solidity] capturing the size/shape range) built during a registration window, distance+colour+soft-shape scoring with live tolerance knobs (size band `area_gate_k`, appearance-vs-position bias, shape softness, colour hard-gate; see ADR-0017), an ACQUIRING/TRACKING/COASTING/LOST state machine with confidence, optional online enrichment (accumulate more samples while tracking), and model/ROI persistence. Also exports `TRACK_COLUMNS` and `track_row()` for aligned per-frame CSV output (video sidecar + standalone track log). Shared by `blob_tracker_prototype.py` and the experiment GUI. Needs `opencv-python`, `numpy`. |
| `host/tracker_view.py` | `TrackerControlView`, the "Tracking" tab for `experiment_control.py`: a live camera preview (rendered only while the tab is visible) plus enable tracking, grab background (and "Save background..." to write the full-res empty-rig frame as a PNG for offline tuning; also auto-paired as `<name>_background.png` with each recording, see ADR-0020), register/enrich the appearance model, tuning sliders plus a collapsible "Model tolerances" section (size/appearance/shape/colour knobs), a colorized binary-mask view stacked under the preview (tracked blob filled green), live state/confidence readout, save/load model (`host/ratmodels/`) and ROI polygon (`host/roi_polygon.json`, via a still-frame editor), and an optional standalone track CSV (written to a `track_logs/` subfolder of the recordings folder; see ADR-0014). Needs `opencv-python`, `numpy`, `pillow`. |
| `host/rules_engine.py` | Pure, UI-free position-based task-rule engine (see ADR-0023, 0026): a `Scale` (cm<->px via a global `cm_per_px` and/or an optional image->world homography), condition primitives (`InZone`/`TrackConfident`/`LickAt`), action intents (`FireReward`/`LogEvent`), a declarative rule-template registry (`prime_and_lick_topup`; and `well_reward_with_excursion` - the credential-gated well reward for the reward-shaping phases, reading the task-event context via `Frame.task`, with a probabilistic Phase 1->2a non-stem reward fade [ADR-0028] and selectable prime timing incl. prime-at-choice-entry for Phases 2a/2b via `prime_on` [ADR-0030]), and a `RulesEngine` whose `tick()`/`event()` return action intents (never actuates) with a short within-visit top-up cooldown, a longer between-visit anti-camp re-arm refractory, a per-visit top-up count cap, per-rule `needs_scale`/`needs_task` gating, and a global rate backstop. Also hosts the Rules-tab field-editor support (editable-field spec, value coercion, whole-profile validation, and `save_rules_config` write-back; ADR-0031). No Tk/OpenCV/numpy; headlessly tested by `test_rules_engine.py`/`test_well_reward.py`/`test_rules_editor.py`. Pure stdlib. |
| `host/task_events.py` | Pure task-semantic layer over the maze-state `RatState` stream (see ADR-0026): the single source of truth turning geometry into task vocabulary - edges (`well_arrived`, `choice_entered`, `loop_closed`), predicates (`at_well`, `outbound_used_stem`), and aggregates (`same_side_run`, `same_side_choice_run`, per-side loop counts) - as a per-tick `TaskEventFrame`. Policy-free (reports what happened, never rewards) so the reward rule and a future stats module share one definition. No Tk/OpenCV/numpy; headlessly tested by `test_task_events.py`. Pure stdlib. |
| `host/rules_view.py` | `RulesControlView`, the "Rules" tab for `experiment_control.py`: the single actuation authority over `rules_engine.py`. Loads `rules.json` (flat rules or reward-shaping-phase `profiles` with a Phase dropdown; ADR-0026; plus a "Shaping" control for the per-session non-stem reward probability that fades Phase 1 into 2a, ADR-0028), the cm/px scale (`rig_scale.json`), and the maze zone map (`zones.json`); polls the shared tracker (running the `maze_state` estimator + `task_events` for zone/loop rules), feeds lick lines, and executes returned intents (sends `R<port>1,<ms>` reward via the broker; writes a `rule_events` CSV under the recordings' `track_logs/`). Off by default; only fires while armed, tracking, the firmware is in Free Roam, and the active profile's needs are met (scale and/or zone map). Includes the cm/px "Measure scale" UI and draws each rule's proximity circle + the maze zones on the preview. Also has a "Rules editor" that edits the active phase's rule fields (port/pulse/top-ups/cooldowns/credential/prime_on/nonstem prob/debias) and writes them back to `rules.json` - validated first, and disarms on save (ADR-0031). Needs `opencv-python`, `numpy`, `pillow`. |
| `host/maze_state.py` | Pure, UI-free discrete maze-state estimator (see ADR-0025): a `ZoneMap` of normalized (0..1) polygons (zones + optional well region + stem axis) with ray-cast point-in-polygon and dot-product stem projection, `MazeStateParams`, and a `MazeStateEstimator` whose `update(track_result, now)` reduces the tracker centroid to an immutable per-tick `RatState` (zone + `in_well` substate, entered/exited edges, up/down-stem direction + stem_progress, completed loop detection, a bounded zone history with `visited_since`, and a validity flag - UNKNOWN with no false transition when the track is COASTING/LOST or low-confidence). Min-dwell hysteresis suppresses border flicker. Also exports `state_timeline_header()`/`state_row()`. No Tk/OpenCV/numpy; headlessly tested by `test_maze_state.py`. Pure stdlib. |
| `host/maze_state_view.py` | `MazeStateControlView`, the "Maze state" tab for `experiment_control.py`: a read-only consumer of the shared tracker that runs `maze_state.py` on the Tk poll loop, showing a live zone/substate/direction/loop readout and an optional state-timeline CSV (written under the recordings' `track_logs/`). Hosts a still-frame, multi-region polygon zone editor (the six zones + well region + stem base/tip) that writes `host/zones.json`, plus min-confidence / min-dwell knobs. Deps via `gui_deps`; needs `opencv-python`, `numpy`, `pillow`. |
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
