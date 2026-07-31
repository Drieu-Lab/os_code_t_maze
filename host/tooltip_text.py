"""tooltip_text.py - the single source of hover-tooltip text for the host GUIs.

Every tooltip string lives here, referenced by a dotted key (e.g. "reward.total",
"tracker.thresh"). tooltips.tip()/canvas_tip() resolve a key through resolve()
and bind the popup, so call sites in the GUI carry no prose - just the key.

A registry entry is either:
  - a plain string (optionally with ``{placeholders}`` filled at wire time), or
  - a callable(**fmt) -> str, for the few tooltips whose text is derived from the
    firmware pin map (firmware/common/rig_config.h via rig_config.py) so lick /
    beam / solenoid roles can't drift from the firmware.

resolve() returns "" for an unknown key; the caller (tooltips.py) warns on that,
so a mistyped/removed key is caught rather than silently wrong.
"""

from __future__ import annotations

from typing import Callable, Dict, Union

from rig_config import load_rig_config

# Parsed once, like the other host tools. Degrades gracefully (ok == False) if
# the header can't be read; the builders below fall back to bare labels.
_CFG = load_rig_config()

# Solenoid layout mirrors rig_view / experiment_control: five valves per port,
# sol 0-2 = reward R1/R2/R3, sol 3 = drain, sol 4 = rinse. Ports 0/1/2 = L/R/C.
_PORT_NAMES = {0: "Left", 1: "Right", 2: "Center/start"}
_SOL_ROLES = {0: "reward R1", 1: "reward R2", 2: "reward R3",
              3: "drain", 4: "rinse"}


# ------------------------------ builders -----------------------------------
def _lick_label(index: int) -> str:
    return _CFG.lick_label(index) if _CFG.ok else f"L{index}"


def _beam_label(index: int) -> str:
    return _CFG.beam_label(index) if _CFG.ok else f"S{index}"


def lick_marker(index: int) -> str:
    return f"Lick detector {_lick_label(index)}; lights blue while a lick is reported."


def lick_count(index: int) -> str:
    return (f"{_lick_label(index)}: licks (rising edges) counted this session; "
            "resets on a mode switch.")


def sensor_marker(index: int) -> str:
    return f"Body beam {_beam_label(index)}; lights green while the beam is broken."


def solenoid(port: int, sol: int) -> str:
    name = _PORT_NAMES.get(port, f"port {port}")
    role = _SOL_ROLES.get(sol, f"sol {sol}")
    return f"{name} {role} valve - fire command R{port}{sol}."


# ------------------------------ the registry -------------------------------
Entry = Union[str, Callable[..., str]]

TIPS: Dict[str, Entry] = {
    # ---- experiment_control.py: solenoid test ----
    "sol.button": solenoid,           # fmt: port=, sol=
    "sol.pulse": "Pulse width per valve click, in ms (1-5000).",
    "sol.header.reward": "Reward valves R1/R2/R3 for this port.",
    "sol.header.drain": "Drain valve for this port.",
    "sol.header.rinse": "Rinse valve for this port.",

    # ---- experiment_control.py: audio cue test ----
    "cue.high": "Play the HIGH choice tone through the speakers to test them "
                "(sends CUE_ON, auto-stops after the burst).",
    "cue.low": "Play the LOW choice tone through the speakers to test them "
               "(sends CUE_ON, auto-stops after the burst).",
    "cue.custom": "Frequency (Hz) for the Play button (500-20000).",
    "cue.play": "Play the custom-frequency tone (auto-stops after the burst).",
    "cue.stop": "Stop any playing test tone now (sends CUE_OFF).",
    "cue.kind": "Which cue this test tone counts as - fires the matching Open "
                "Ephys tone TTL. The audible tone is set by the frequency, not "
                "the kind.",
    "cue.burst": "How long each test tone plays before the host auto-stops it, "
                 "in ms (1-5000).",

    # ---- experiment_control.py: training mode ----
    "mode.free": "Switch the firmware to Free Roam (no cues/trials); refill the "
                 "central well manually. Restarts the session.",
    "mode.full": "Switch the firmware to the Full cued task (generates a new "
                 "trial sequence). Restarts the session.",
    "mode.label": "Current training mode, reflected from the firmware.",

    # ---- experiment_control.py: reward dispensed ----
    "reward.total": "Total reward volume dispensed this session (and pulse count), "
                    "from the host reward-volume calibration.",
    "reward.detail": "Split of the total into automatic (task) vs manual (button) "
                     "reward.",
    "reward.reset": "Reset the reward tally to zero.",

    # ---- experiment_control.py: lick counts ----
    "lick.count": lick_count,         # fmt: index=
    "lick.reset": "Reset the per-port lick counts to zero.",

    # ---- experiment_control.py: toolbar ----
    "quit.rig": "Close every window started by start_rig.bat (broker, logger, "
                "lick tuner, this GUI); stops recording and frees the serial "
                "port and camera.",

    # ---- rig_view.py: canvas markers ----
    "marker.sensor": sensor_marker,   # fmt: index=
    "marker.lick": lick_marker,       # fmt: index=
    "marker.solenoid": solenoid,      # fmt: port=, sol=
    "legend.sensor": "Body-beam markers light green while the beam is broken.",
    "legend.lick": "Lick markers light blue while a lick is reported.",
    "legend.solenoid": "Solenoid markers flash orange on each valve pulse.",
    "legend.role.reward": "Reward valves (R1/R2/R3) - marker outline colour.",
    "legend.role.drain": "Drain valves - marker outline colour.",
    "legend.role.rinse": "Rinse valves - marker outline colour.",

    # ---- lick_tuner.py ----
    "licktuner.window": "Visible time window of the traces, in seconds (1-60).",
    "licktuner.window_value": "Current time window width.",
    "licktuner.pause": "Freeze the display to inspect an event (data keeps "
                       "accumulating).",
    "licktuner.reset": "Zero the per-channel edge counts and pulse-width readouts.",
    "licktuner.status": "Broker connection status.",
    "licktuner.edges": "Edges since reset (one lick = one rising + one falling = "
                       "2 edges; more = chatter to tune out).",
    "licktuner.pulse": "Width of the most recent HIGH pulse - this is what the "
                       "Hold dial sets.",

    # ---- camera_view.py ----
    "camera.record": "Start/stop recording video (+ a per-frame sidecar CSV) to "
                     "the output folder.",
    "camera.format": "Recording codec: lossless FFV1 (large, bit-exact) or "
                     "compressed H.264 (much smaller, lossy).",
    "camera.crf": "H.264 quality: lower = better/larger (~18 is visually "
                  "lossless). Only used for the compressed format.",
    "camera.pfs": "Load a Pylon feature file (.pfs) to apply saved camera "
                  "settings (exposure, gain, ...).",
    "camera.output": "Folder where recordings and sidecar CSVs are written.",
    "camera.browse": "Choose the output folder.",
    "camera.undistort": "Apply the lens-distortion correction at the source "
                        "(preview, tracking, and recording).",
    "camera.calibration": "Load a camera calibration JSON to enable "
                          "undistortion.",
    "camera.calib_status": "Active lens calibration (file, resolution, RMS "
                           "reprojection error).",
    "camera.status": "Live acquisition health: grab rate (fps), frames saved, "
                     "source drops, incomplete grabs, encode-queue drops, and "
                     "queue fill.",

    # ---- tracker_view.py: controls ----
    "tracker.enable": "Run the markerless tracker on each camera frame (off by "
                      "default).",
    "tracker.grab_bg": "Capture the current empty-rig frame as the background "
                       "reference.",
    "tracker.save_bg": "Save the empty-rig background image for offline "
                       "re-tuning.",
    "tracker.register": "Build the rat's appearance model over a window of "
                        "frames (colour + size + shape).",
    "tracker.cancel": "Abort the in-progress registration.",
    "tracker.enrich": "Keep refining the appearance model online while "
                      "tracking.",
    "tracker.save_model": "Save the appearance model (.npz) for this animal, "
                          "including the tracker settings it was tuned with.",
    "tracker.load_model": "Load a saved appearance model (.npz). Its stored "
                          "settings are restored (except registration/enrichment "
                          "knobs); re-grab the background if proc_max_w changed.",
    "tracker.clear_model": "Discard the current appearance model.",
    "tracker.define_region": "Define the maze ROI by clicking a captured still "
                             "frame.",
    "tracker.clear_region": "Clear the maze ROI polygon.",
    "tracker.save_roi": "Save the maze ROI polygon to disk.",
    "tracker.load_roi": "Load a maze ROI polygon from disk.",
    "tracker.define_landmarks": "Mark reward wells / body-sensor points on a "
                                "still frame.",
    "tracker.clear_landmarks": "Remove all placed landmarks.",
    "tracker.show_mask": "Show the binary detection mask with the tracked blob "
                         "filled green.",
    "tracker.readout": "Live tracker state, confidence, and blob metrics.",
    "tracker.log": "Start/stop a standalone track CSV (independent of video "
                   "recording).",

    # ---- rules_view.py: Rules tab ----
    "rules.enable": "Arm the position-based rules engine (OFF by default). It "
                    "only actuates while the firmware is in Free Roam, the track "
                    "is confident, and a scale calibration exists.",
    "rules.status": "Live rule state (armed/primed, fires) and why the engine is "
                    "or isn't gated to fire.",
    "rules.reload": "Reload rules.json from disk (pick up edits without "
                    "restarting the GUI).",
    "rules.profile": "Reward-shaping phase (from rules.json profiles): Phase 1 "
                     "(well + explore), Phase 2a (stem-gated choice, primed at "
                     "the choice), Phase 2b (stem-gated choice + anti-bias, no "
                     "top-ups). Switching disarms the engine.",
    "rules.nonstem": "Phase 1 -> 2a fade: probability that a NON-stem excursion "
                     "earns the next well reward (stem excursions always do). "
                     "1.0 = pure Phase 1; lower it across days toward 0.0 = "
                     "Phase 2a. Applied live and logged.",
    "rules.log": "Rule events (prime/top-up/re-arm/suppressed) are written to a "
                 "rule_events CSV in the recordings' track_logs folder.",
    "rules.fast_min_dwell": "Camera frames a zone must be held before the "
                            "ACTUATION (fast) view commits it and rules may fire. "
                            "Lower = snappier reward, more jitter-sensitive; "
                            "independent of the debounced display view used by the "
                            "Maze state / Behavior tabs. Applied live.",
    "rules.editor.save": "Write the edited fields of the active phase's rule(s) "
                         "back to rules.json (validated first; nothing is saved "
                         "if a value is invalid). Saving disarms the engine - "
                         "re-arm when ready.",
    "rules.editor.debias": "Anti-bias cap on consecutive same-side runs "
                           "(choice_via_stem / clean_loop only). none = no cap; "
                           "fixed uses n; uniform redraws a cap in [min, max] on "
                           "each side switch. Runs past the cap are withheld.",
    "rules.editor.field.enabled": "Enable this rule. Unchecking disables it "
                                  "without deleting it from rules.json.",
    "rules.editor.field.port": "Reward port this rule fires (0 = left, 1 = "
                               "right, 2 = center well).",
    "rules.editor.field.pulse_ms": "Reward valve open time in milliseconds (sets "
                                   "the dispensed volume per fire).",
    "rules.editor.field.max_topups": "Maximum lick-triggered top-ups per visit, "
                                     "after the initial prime.",
    "rules.editor.field.lick_debounce_ms": "Ignore lick edges closer together "
                                           "than this (ms). Anti-chatter only.",
    "rules.editor.field.topup_cooldown_ms": "Minimum ms between fires WITHIN a "
                                            "visit (prime->top-up, top-up->"
                                            "top-up). Keep short.",
    "rules.editor.field.rearm_refractory_ms": "Between-visit anti-camp refractory "
                                              "(ms): time since the last fire "
                                              "before the rule may prime again on "
                                              "a fresh visit.",
    "rules.editor.field.rearm_on_exit": "Re-arm the rule once the animal leaves "
                                        "the zone (prime_and_lick_topup).",
    "rules.editor.field.radius_cm": "Proximity radius in cm around the landmark "
                                    "that counts as 'in the zone' "
                                    "(prime_and_lick_topup).",
    "rules.editor.field.min_confidence": "Only act while the track is confident "
                                         "at or above this (0..1).",
    "rules.editor.field.credential": "Which excursion earns the next well payout: "
                                     "choice (Phase 1, either arm), "
                                     "choice_via_stem (Phase 2a/2b, arm reached "
                                     "via the stem), or clean_loop (a completed "
                                     "clean lap).",
    "rules.editor.field.prime_on": "When/where the prime fires: well_arrival (on "
                                   "reaching the well), well_lick (first lick at "
                                   "the well), or credential (the instant the "
                                   "credential is earned, at the choice zone - "
                                   "Phase 2a/2b).",
    "rules.editor.field.nonstem_reward_prob": "choice credential only: "
                                              "probability 0..1 that a NON-stem "
                                              "excursion earns credit (stem "
                                              "excursions always do). 1.0 = "
                                              "Phase 1, 0.0 = Phase 2a.",
    "rules.measure.a": "First landmark of the measurement segment.",
    "rules.measure.b": "Second landmark of the measurement segment.",
    "rules.measure.cm": "The real-world distance between the two landmarks, in "
                        "centimeters.",
    "rules.measure.add": "Add this segment; the global cm/px scale is the mean "
                         "over all segments (measure across several pairs for an "
                         "average + a sense of variance).",
    "rules.measure.clear": "Remove all measurement segments.",
    "rules.measure.save": "Save the scale (mean cm/px + segments) to "
                          "rig_scale.json.",
    "rules.measure.stats": "Mean cm/px and the coefficient of variation across "
                           "segments (high CV suggests the single scale is "
                           "inaccurate across the frame - consider a homography).",

    # ---- task_view.py: Task tab (controller selector + shared animal id) ----
    "task.animal": "Animal ID for this session - the SINGLE host-side source "
                   "(docs/decisions/0053). It names the session manifest + folder "
                   "(<animal>_<timestamp>) and the Phase 3 data file, and is "
                   "auto-filled from the firmware ANIMAL handshake if you leave it "
                   "blank; blank falls back to 'unknown'.",
    "task.controller": "Which task controller is active. Only the selected one is "
                       "shown and can actuate reward - switching disarms the other "
                       "(Phase 3 = the host-owned cued task; Rules = the "
                       "position-based reward-shaping engine).",

    # ---- phase3_controller.py: Phase 3 tab (host-owned cued task) ----
    "phase3.arm": "Arm the HOST-owned Phase 3 cued task (OFF by default). The "
                  "firmware must be in Free Roam (its trial FSM off): the host "
                  "owns the trial sequence, cue identity, scoring, and reward, "
                  "and the Due only plays tones, pulses reward, and fires the "
                  "Open Ephys TTLs. Disarming cuts the tone and restores firmware "
                  "autonomy (CUE,BEAM). See docs/decisions/0046.",
    "phase3.choice": "Play the CHOICE tone while the rat is in the central-stem "
                     "zone, on a trip that set OFF up the stem from the hub "
                     "(ADR-0067) - a lap run backwards, out along a return arm, "
                     "stays silent crossing the stem on its way home. The host "
                     "supplies the tone identity for the current trial "
                     "(CUE_ON,CHOICE,<hz>). Fails OPEN: a confidence dropout "
                     "never cuts a playing tone.",
    "phase3.up_stem": "Only start the choice tone while the rat is heading UP the "
                      "stem (toward the junction), not on the way back down. With "
                      "this OFF the tone plays in either direction for the whole "
                      "trip, including a reversal back into the stem after the "
                      "choice; the hub-departure requirement above still applies.",
    "phase3.return": "Play the return-arm tone when the rat comes back via a "
                     "return arm AFTER running out via the stem (CUE_ON,RETURN,"
                     "<hz>); a run that starts up a return arm from the hub gets "
                     "no cue (ADR-0061). Identity is a balanced hi/lo schedule "
                     "pre-generated per physical arm from the seed and looked up "
                     "by the arm the rat enters (ADR-0047). Fails SAFE on dropout.",
    "phase3.reward_incorrect": "Shaping aid: dispense reward at the central well "
                               "even on an INCORRECT choice (otherwise only "
                               "correct choices are rewarded).",
    "phase3.reward_pulse_ms": "Reward valve open time (ms) for the correct-choice "
                              "prime pulse at the central well; the calibrated "
                              "dispensed volume is shown below (ADR-0019). Applied "
                              "when you arm.",
    "phase3.max_topups": "How many extra reward pulses the rat can earn by licking "
                         "at the central well after the prime (0 = none). Uses the "
                         "same shared dispenser as the reward-shaping phases "
                         "(ADR-0068); applied when you arm.",
    "phase3.sequence_config": "The scientific schedule + tone parameters (trial "
                              "count, block balancing, tone frequencies, and the "
                              "optional pinned seed) read from phase3.json. These "
                              "are edited in the file - not here - so they aren't "
                              "changed by accident mid-session (FW-0024/ADR-0070); "
                              "use Reload after editing.",
    "phase3.save_settings": "Write the current reward + cue settings back to "
                            "phase3.json (validated first; nothing is saved if a "
                            "value is invalid). They then reload automatically on "
                            "the next launch. Sequence/tones are edited in the file.",
    "phase3.reload_config": "Re-read phase3.json from disk into the tab (e.g. after "
                            "hand-editing the sequence/tones or pinning a seed). "
                            "Applies on the next arm.",
    "phase3.min_confidence": "Extra track-confidence floor for a cue onset, on top "
                             "of the estimator's own validity threshold (0..1; 0 "
                             "defers entirely to the estimator).",
    "phase3.seed": "PRNG seed for the balanced L/R trial sequence (32-bit). The "
                   "same seed reproduces the same schedule; applied when you arm.",
    "phase3.new_seed": "Draw a fresh random seed (takes effect on the next arm).",
    "phase3.ribbon": "The current trial flanked by the last few resolved trials "
                     "and the next few upcoming ones. Each cell shows the choice "
                     "cue and the arm it means (e.g. HIGH->L), the two planned "
                     "return-arm cues (ret L:_ R:_), and, once scored, the arm the "
                     "rat chose with OK/X. The current trial is highlighted; blank "
                     "until you arm.",
    "phase3.sequence": "The whole planned trial sequence for this session (from "
                       "the seed): choice cue -> correct arm and the two return-arm "
                       "cues per trial, with the rat's choice + OK/X filled in as "
                       "trials resolve. The current trial is highlighted and kept "
                       "scrolled into view.",

    # ---- trial_control_bar.py: Rig-view operator buttons (ADR-0058) ----
    "phase3.controls": "Operator controls for the live Phase 3 run - steer the "
                       "trial cursor without leaving the Rig view. Buttons enable "
                       "only while a session is armed. See docs/decisions/0058.",
    "phase3.skip": "Abandon the current trial (marked SKIP in the data), cut the "
                   "cues, and advance to the next trial. The next choice cue only "
                   "sounds once the rat is back at the hub and heads up the stem.",
    "phase3.repeat": "Re-run the CURRENT trial: cut the cues and clear this trial's "
                     "recorded fields so it starts clean, WITHOUT advancing. The "
                     "cue re-arms on the next up-the-stem run from the hub.",
    "phase3.back": "Step back one trial (undo an accidental advance) and clear that "
                   "trial's recorded fields so it can be re-run cleanly.",
    "phase3.extend": "Append one more balanced block of trials to the plan, for "
                     "when the rat runs past the planned count. Append-only: the "
                     "trials already run are untouched (docs/decisions/0059).",
    "phase3.restart": "Restart the session at trial 1 from the same seed. The "
                      "current run's progress is discarded (its data file is kept); "
                      "asks for confirmation first.",
    "phase3.pause": "Mute the cues and freeze trial progress WITHOUT disarming; "
                    "press again (Resume) to continue. The next cue re-arms from a "
                    "fresh hub->stem run.",

    # ---- maze_state_view.py: Maze state tab ----
    "maze.define_zones": "Draw the maze zones (central reward HUB, stem, "
                         "junction, choice/return arms), the well region (inside "
                         "the HUB), and the stem axis on a captured still frame.",
    "maze.save_zones": "Save the zone map to zones.json.",
    "maze.load_zones": "Load a zone map from disk.",
    "maze.min_confidence": "Track confidence below which the maze state is "
                           "UNKNOWN (no transitions emitted).",
    "maze.min_dwell": "Camera frames a new zone must persist before it commits "
                      "(hysteresis against border jitter). The shared estimator "
                      "now ticks at the camera frame rate, so this counts frames, "
                      "not GUI polls (ADR-0041).",
    "maze.readout": "Live discrete maze state: current zone/substate, direction "
                    "along the stem, entered/exited edges, and completed loops.",
    "maze.log": "Start/stop a state-timeline CSV in the recordings' track_logs "
                "folder (for offline analysis / the stats module).",

    # ---- behavior_view.py: Phase 2a behavior readout ----
    "behavior.start": "Start/Stop computing the live Phase 2a measures. Stop "
                      "freezes the values and pauses the active-time clock (so a "
                      "break doesn't dilute the per-minute rates); Start resumes.",
    "behavior.reset": "Reset all behavior measures and the active-time clock to "
                      "zero (also happens automatically on a training-mode "
                      "switch).",
    "behavior.window": "Size of the rolling window (most recent choice entries) "
                       "used for the 'last N' figures shown beside the "
                       "cumulative ones. Range 1-100 entries.",
    "behavior.reload": "Reload the zone map (zones.json) and the px->cm scale "
                       "(rig_scale.json) from disk - e.g. after redrawing zones "
                       "or measuring the scale.",
    "behavior.status": "Readout state: running, stopped, or waiting for tracking "
                       "/ zones.",
    "behavior.session": "Session clock: start time, stop time, and duration, driven "
                        "by this panel's Start/Stop button. Duration is wall-clock "
                        "span from the first Start (it INCLUDES paused stretches), "
                        "so it differs from the active-time that denominates the "
                        "per-minute rates. Resets with the Reset button and on a "
                        "training-mode switch.",
    "behavior.compliance": "Stem-compliance: fraction of choices that went out via "
                           "the central stem (a valid Phase 2a trial), cumulative "
                           "and over the last N. One choice is counted per outbound "
                           "trip (the first choice-arm entry after leaving the hub), "
                           "so an out-one-arm/back-the-other lap is one choice, not "
                           "two. The primary 'is 2a working' number. Range 0-100% "
                           "(-- until the first choice); rises toward 100% as stem "
                           "use takes hold.",
    "behavior.lr": "Left/right split of stem-gated (valid) choices, cumulative "
                   "and over the last N. Phase 2a has no anti-bias cap, so this "
                   "is informational. Each side 0-100% and the two sum to 100 "
                   "(50/50 unbiased, 100/0 all-left, 0/100 all-right); -- until "
                   "the first stem-gated trial.",
    "behavior.nonstem": "Count of non-stem choices this session (an outbound trip "
                        "that reached a choice arm without using the stem; credit "
                        "denied). Counted once per trip, so an out-one-arm/back-the-"
                        "other lap counts one. An integer >= 0.",
    "behavior.sameside": "Consecutive same-side stem-gated choices: current run "
                         "and session max, each with the side (L/R) it is on "
                         "(e.g. 3L / max 4R). Each is an integer (0 before any "
                         "stem-gated trial, otherwise >= 1). A large max flags a "
                         "rat that will fight the Phase 2b anti-bias cap.",
    "behavior.throughput": "Stem-gated trials this session (integer >= 0) and "
                           "the rate per minute of active (running) time (>= 0, "
                           "shown as -- until ~1 s of active time has elapsed).",
    "behavior.distance": "Total distance the rat has run (from the smoothed "
                         "centroid via the rig px->cm scale) and the rate per "
                         "minute. Both >= 0 (rate -- until ~1 s of active time). "
                         "Untracked stretches are not counted; shows -- (needs "
                         "scale) without the Rules-tab scale.",

    # ---- behavior_view.py: Phase 3 cued-task stats (shown while a Phase 3
    #      session is armed; computed by phase3_stats over the published table) ----
    "behavior.phase3.session": "Session clock: start time, stop time, and duration, "
                               "driven by the Session panel's Start/Stop button "
                               "(docs/decisions/0060) - NOT the Phase 3 Arm "
                               "checkbox. Duration is the wall-clock span from "
                               "Start; because Stop leaves Phase 3 running, the "
                               "stop time and final duration stay shown here.",
    "behavior.phase3.trials": "Session progress: trials the rat has completed "
                              "(a choice has been scored) out of the planned "
                              "total, and the correct / incorrect split. "
                              "Read live from the Phase 3 tab's published trial "
                              "table (docs/decisions/0050).",
    "behavior.phase3.accuracy": "Choice accuracy = correct / completed trials, "
                                "cumulative and over the last N completed trials "
                                "(the Window control). The primary 'is the rat "
                                "learning the cue' number; -- until the first "
                                "completed trial.",
    "behavior.phase3.bycue": "Accuracy split by the trial's CHOICE cue: HIGH tone "
                             "vs LOW tone (each with its completed-trial count). "
                             "A rat following the cue is accurate on both; a large "
                             "gap flags a cue it hasn't learned. -- until that cue "
                             "has been seen.",
    "behavior.phase3.bias": "Side bias: the fraction of completed trials on which "
                            "the rat chose LEFT vs RIGHT (its own choice, "
                            "regardless of correctness), with the L/R counts. "
                            "50/50 is unbiased; a lopsided split flags a side "
                            "preference. -- before the first choice.",
    "behavior.phase3.reward": "Reward rate = rewarded / completed trials. With the "
                              "'reward incorrect choices' shaping aid on this "
                              "approaches 100% and no longer tracks accuracy.",
    "behavior.phase3.strategy": "Naive-strategy readout over adjacent completed "
                                "trials, on the trial OUTCOME (not reward, so it "
                                "stays meaningful under the shaping aid): Win-stay "
                                "= chose the same arm again after a CORRECT trial; "
                                "Lose-shift = switched arms after an INCORRECT "
                                "one. High values flag a rat repeating/alternating "
                                "on its own result instead of following the cue. "
                                "-- until the conditioning outcome has occurred.",
    "behavior.phase3.rt": "Mean choice reaction time: choice-cue onset -> scored "
                          "choice, averaged over completed trials that carry both "
                          "stamps (n). These are host wall-clock times (they carry "
                          "serial/actuation latency), so it is an approximate "
                          "latency, not a hardware-precise reaction time.",

    # ---- behavior_view.py: Phase 3 'Excursions' column - the same live
    #      locomotor/compliance group as Phase 2a, but over ALL excursions during
    #      the Phase 3 session (denied non-stem trips included), from the
    #      session-scoped accumulator (docs/decisions/0071). Distinct from the
    #      cued-trial counts in the left column above. ----
    "behavior.phase3.compliance": "Stem-compliance during the Phase 3 session: "
                                  "fraction of outbound choices that went out via "
                                  "the central stem, cumulative and over the last "
                                  "N. Counted per outbound trip, over ALL "
                                  "excursions (not just scored cued trials), so it "
                                  "surfaces the non-stem trips the cued-task rows "
                                  "above never see. Range 0-100% (-- until the "
                                  "first choice).",
    "behavior.phase3.nonstem": "Count of non-stem choices this session (an outbound "
                               "trip that reached a choice arm without using the "
                               "stem; credit denied). Counted once per trip. These "
                               "are the compliant-but-invisible excursions that "
                               "don't appear in the cued-trial counts. Integer >= 0.",
    "behavior.phase3.run": "Consecutive same-side stem-gated choices during the "
                           "session: current run and session max, each with the "
                           "side (L/R) it is on (e.g. 3L / max 4R). Integers "
                           "(0 before any stem-gated trip). A large max flags a "
                           "side-perseverating rat.",
    "behavior.phase3.throughput": "Stem-gated trips this session (integer >= 0) and "
                                  "the rate per minute of active (running) time "
                                  "(>= 0, shown as -- until ~1 s of active time). "
                                  "Counts all stem excursions, so it can exceed the "
                                  "scored-trial count in the left column.",
    "behavior.phase3.distance": "Total distance the rat has run this session (from "
                                "the smoothed centroid via the rig px->cm scale) and "
                                "the rate per minute. Both >= 0 (rate -- until ~1 s "
                                "of active time). Untracked stretches are not "
                                "counted; shows -- (needs scale) without the "
                                "Rules-tab scale.",

    # ---- tracker_view.py: tuning sliders (keyed by param name) ----
    "tracker.thresh": "Foreground threshold on the background difference (0-255).",
    "tracker.min_area": "Minimum blob area (px) to be considered the rat.",
    "tracker.blur": "Blur kernel applied before thresholding (noise smoothing).",
    "tracker.gate_radius": "Max jump (px) allowed between frames before the track "
                           "is rejected.",
    "tracker.cluster_gap": "Merge separate blobs (px between their bounding boxes) "
                           "into one rat candidate - reassembles a fragmented rat. "
                           "0 = off (one blob = one candidate).",
    "tracker.accept_score": "Minimum match score to accept a detection as the "
                            "rat.",
    "tracker.coast_max_frames": "How many frames to coast on the prediction after "
                                "a lost detection.",
    "tracker.area_gate_k": "Size tolerance: accept areas within k standard "
                           "deviations of the model.",
    "tracker.appearance_bias": "Weighting of appearance vs distance when scoring "
                               "candidates.",
    "tracker.form_tol": "Shape-match softness (Gaussian tolerance).",
    "tracker.hist_min": "Minimum hue/saturation correlation to pass the colour "
                        "gate (0 = off).",
    "tracker.cluster_hist_min": "When merging fragments, the fraction of a blob's "
                                "pixels that must be colours the model has seen for "
                                "it to join the cluster (0 = spatial-only merge).",
}


def resolve(key: str, **fmt) -> str:
    """Return the tooltip text for ``key`` (formatted with ``fmt``), or "".

    A string entry is ``.format(**fmt)``-ed when kwargs are given; a callable
    entry is invoked as ``entry(**fmt)``. Unknown keys return "" so the caller
    can warn about drift.
    """
    entry = TIPS.get(key)
    if entry is None:
        return ""
    try:
        if callable(entry):
            return entry(**fmt)
        return entry.format(**fmt) if fmt else entry
    except Exception:  # noqa: BLE001 - a bad placeholder must not crash the GUI
        return entry if isinstance(entry, str) else ""
