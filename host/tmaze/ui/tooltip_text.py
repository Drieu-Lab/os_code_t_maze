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

from tmaze.core.rig_config import load_rig_config

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

    # ---- cue_calibration_view.py: per-cue speaker calibration (ADR-0125/0148) ----
    "cue.volume": "Playback volume for this cue, as a percent of full amplitude "
                  "(0-100). Turn the LOUDER cue down to match the quieter one; "
                  "100 % is the firmware's full level (volume only attenuates).",
    "cue.volume.play": "Play this cue at the volume set above (auto-stops after "
                       "the burst) so you can measure it with a dB meter.",
    "cue.volume.save": "Save the calibration (volume + frequency) to "
                       "host/cue_calibration.json. It is auto-applied to Phase 3 "
                       "cues and the speaker test.",
    "cue.freq": "Measured actual frequency (Hz) you read on a spectrum app while "
                "this cue plays. Enter it, then Set to correct the commanded tone "
                "so the speaker emits the target frequency.",
    "cue.freq.set": "Compute the corrected commanded frequency from your measured "
                    "value (commanded = commanded x target / measured) and update "
                    "the display. Re-play and re-measure to refine.",
    "cue.freq.play": "Play this cue at the CURRENT commanded frequency (auto-stops "
                     "after the burst) so you can measure the emitted tone.",

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
    "camera.status": "Live acquisition health. fps = grab rate; saved = frames "
                     "written; src-drop = camera frames NEVER received (BlockID "
                     "gaps); incomplete = partial/corrupt grabs; q-drop = "
                     "encode-queue drops; queue = encode-queue fill. The inline "
                     "'net: Nr/Mu' token is resend-requests / buffer-underruns.",
    "camera.show_zones": "Overlay the maze zone outlines on the live preview "
                         "(the current zone is drawn bold while tracking runs). "
                         "Display only - does not affect recording or tracking.",
    "camera.show_landmarks": "Overlay the reward-well / body-sensor landmark "
                             "markers on the live preview. Display only; shown "
                             "whether or not tracking is enabled.",

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
    "tracker.define_led_board_region": "Draw a polygon around the WHOLE indicator "
                                       "board (all S/L/SOL + the green/red pose "
                                       "fiducials, with margin) on a still frame. "
                                       "Saved to host/led_board_search.json and used "
                                       "by 'sync_fit --map' to confine LED detection "
                                       "to the board, excluding the maze the rat "
                                       "crosses (ADR-0195).",
    "tracker.define_landmarks": "Mark reward wells / body-sensor points on a "
                                "still frame.",
    "tracker.clear_landmarks": "Remove all placed landmarks.",
    "tracker.save_landmarks": "Save the placed landmarks to a JSON file you choose.",
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
                  "autonomy (CUE,BEAM). Re-arming with no recording running "
                  "starts a fresh run from the current seed and settings; "
                  "during a recording it resumes the same run (use Restart to "
                  "apply edits). See docs/decisions/0046, 0210.",
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
                     "cue re-arms on the next up-the-stem run from the hub. Disabled "
                     "while an intervention or probe block is running (a re-scored "
                     "trial would change the block's length; see docs/decisions/0187).",
    "phase3.back": "Step back one trial (undo an accidental advance) and clear that "
                   "trial's recorded fields so it can be re-run cleanly. Disabled "
                   "while an intervention or probe block is running (a re-scored "
                   "trial would change the block's length; see docs/decisions/0187).",
    "phase3.extend": "Append one more balanced block of trials to the plan, for "
                     "when the rat runs past the planned count. Append-only: the "
                     "trials already run are untouched (docs/decisions/0059).",
    "phase3.restart": "Restart the session at trial 1 from the same seed. The "
                      "current run's progress is discarded (its data file is kept); "
                      "asks for confirmation first.",
    "phase3.pause": "Mute the cues and freeze trial progress WITHOUT disarming; "
                    "press again (Resume) to continue. The next cue re-arms from a "
                    "fresh hub->stem run.",

    # ---- phase3_controller.py: closed-loop debias generator + block knobs ----
    "phase3.generator": "Trial-sequence generator. 'balanced' is the default "
                        "counterbalanced schedule (per-block L/R balance + a "
                        "max-same-run cap; open-loop). 'debias_blocks' keeps that "
                        "as the default but, when the animal develops a side bias, "
                        "runs a corrective block that forces the non-preferred side "
                        "as correct until it complies, then reverts (closed-loop; "
                        "see the ADR). The knobs below apply only to 'debias_blocks' "
                        "and take effect on the next arm.",
    "phase3.debias_window": "Sliding window (most-recent scored trials) the side "
                            "bias is measured over. Larger = smoother/slower to "
                            "react; smaller = twitchier.",
    "phase3.debias_min_errors": "How many directional ERRORS (wrong-arm choices) "
                                "must accrue in the window before a corrective block "
                                "can auto-trigger (must be <= window). Stops a "
                                "single early error reading as full bias.",
    "phase3.debias_trigger_bias": "The |directional-error bias| threshold (0-1) "
                                  "that starts a corrective block. Bias is measured "
                                  "over the window's ERRORS only (correct trials "
                                  "don't dilute it): b = (left-pulls - right-pulls) "
                                  "/ total errors. b>0 means left-biased; a block "
                                  "forces the over-pulled side's opposite as correct.",
    "phase3.debias_exit_consecutive": "How many consecutive correct choices to the "
                                      "forced (non-preferred) side end a corrective "
                                      "block and revert to counterbalanced.",
    "phase3.debias_max_block_len": "Welfare failsafe: the maximum trials a "
                                   "corrective block may run before it auto-reverts "
                                   "to counterbalanced (flagged as capped in the "
                                   "log) even if the animal never complies.",
    "phase3.debias_alt_veto_rate": "Don't AUTO-start a debias block while the "
                                   "animal is switch-dominant: the de-alternation "
                                   "alt_rate (how often it switches when told to "
                                   "repeat; ~0 camper, ~1 alternator) is at or "
                                   "above this (0-1). A debias block forces one "
                                   "fixed side, which an alternator lands on every "
                                   "other trial - a reliable 50% that trains a "
                                   "figure-8 loop (ADR-0149). The stack just stays "
                                   "counterbalanced instead. 0 = off; manual force "
                                   "always bypasses it.",
    "phase3.debias_toggle": "Manually start or end a corrective debias block, "
                            "overriding the closed-loop scheduler (only active "
                            "under the 'debias_blocks' generator). A manual block "
                            "forces the non-preferred side and is flagged "
                            "'manual' in the log; it still ends on the normal exit "
                            "criteria. Applies on the next trial.",
    "phase3.debias_enabled": "Run the side-bias debias block under the "
                             "'intervention_blocks' generator. Untick to disable "
                             "just this intervention while keeping others.",
    "phase3.alt_enabled": "Run the de-alternation block under the "
                          "'intervention_blocks' generator. It forces the animal's "
                          "LAST choice as correct (must repeat to earn reward) when "
                          "it alternates too much, using a cue-aware rate so a "
                          "cue-follower is never flagged. Untick to disable it.",
    "phase3.alt_window": "Sliding window (most-recent scored trials) the cue-aware "
                         "alternation rate is measured over. Larger = "
                         "smoother/slower to react; smaller = twitchier.",
    "phase3.alt_min_samples": "How many 'repeat-cued' trials (the cue told the rat "
                              "to repeat its last choice) must accrue in the window "
                              "before a de-alternation block can auto-trigger (must "
                              "be <= window). Stops a couple of early switches "
                              "reading as certainty.",
    "phase3.alt_trigger_rate": "The cue-aware alternation rate (0-1) that starts a "
                               "de-alternation block: P(switch | cue said to repeat "
                               "the last choice). 1.0 = always switches when told to "
                               "repeat (a pure alternator).",
    "phase3.alt_exit_repeats_per_side": "How many rewarded repeats the rat must "
                                        "earn on EACH arm before a de-alternation "
                                        "block ends on success (ADR-0129). A "
                                        "one-sided repeat run does not end the block: "
                                        "completing a run on one arm offers a one-off "
                                        "'bridge' to the other (ADR-0139), and the "
                                        "block exits only once the rat has repeated "
                                        "on both arms (else it rides to the cap).",
    "phase3.alt_max_block_len": "Welfare failsafe: the maximum trials a "
                                "de-alternation block may run before it auto-reverts "
                                "(flagged as capped) even if the animal never "
                                "complies.",
    "phase3.alt_toggle": "Manually start or end a de-alternation block, overriding "
                         "the closed-loop scheduler (only active under the "
                         "'intervention_blocks' generator with the de-alternation "
                         "block enabled). A manual block forces the last choice and "
                         "is flagged 'manual' in the log. Applies on the next trial.",
    "phase3.probe_toggle": "Turn unrewarded PROBE blocks OFF or ON for the rest of "
                           "this session. In a probe the cue plays and the choice is "
                           "scored normally, but no water is given - a read-out of "
                           "what the animal KNOWS vs what it works for. Probes fire "
                           "AUTOMATICALLY: the first correct choice inside a configured "
                           "trial 'window' withholds reward (the trigger trial) and "
                           "starts a counterbalanced unrewarded block on the following "
                           "trials. There is no manual start; this toggle DISABLES the "
                           "trigger (no window will fire, and any running block is "
                           "aborted) or re-ENABLES it. Restart re-arms. Works under any "
                           "generator (see docs/decisions/0115-probe, 0134, 0137).",

    # ---- phase3_traces_view.py: Metrics tab (live rolling traces) ----
    "phase3.traces": "Live rolling traces of the current Phase 3 session, plotted "
                     "against session wall-clock time: engagement (ITI), accuracy, "
                     "strategy, and directional/alternation bias. Read-only view "
                     "computed from the published session snapshot.",
    "phase3.traces.gap": "Disengaged line drawn on the ITI trace: the dead-time gap "
                         "(seconds, between one trial ending and the next starting) "
                         "above which the rat is treated as disengaged. View-only "
                         "reference line; doesn't change the task.",
    "phase3.traces.window": "Rolling-window size (most-recent completed trials) for "
                            "the rate traces (accuracy, strategy, bias). Larger = "
                            "smoother but slower to react.",
    "phase3.traces.iti_window": "Separate rolling-window size for the ITI engagement "
                                "trace, so it can be smoothed over a SHORTER span "
                                "than the rate traces and react to a rat going quiet "
                                "within a few trials. Blank = follow the main window.",
    "phase3.traces.width": "Width of the visible time window in minutes (the zoom). "
                           "Blank or 0 = show the whole session (fully zoomed out). "
                           "A smaller value zooms in; scroll with the bar below, or "
                           "hit Latest to jump back to the newest trials. View-only.",
    "phase3.traces.scroll": "Scroll the visible time window across the session (pan). "
                            "Dragging it stops the plot from auto-following the "
                            "newest trials; press Latest to resume following.",
    "phase3.traces.latest": "Jump back to the newest trials and resume auto-following "
                            "the live edge of the session (undoes a manual scroll).",
    "phase3.traces.reference": "Overlay a PAST session of the same rat as a faded, "
                               "dashed reference line behind the live traces, to "
                               "compare this run against an earlier one. Lists the "
                               "rat's recorded sessions (newest first); 'None' turns "
                               "the ghost off. Read from disk only when you pick one.",
    "phase3.traces.ref_refresh": "Re-scan the rat's recorded sessions into the "
                                 "Reference list (e.g. after a new session was saved "
                                 "or the animal id changed).",
    "phase3.traces.mismatch": "Flags run-config differences between the reference "
                              "session and the current one (cue mapping, generator, "
                              "tone frequencies, return-cue use, probe trials), so a "
                              "comparison across different conditions is obvious. "
                              "Quiet when they match.",
    "phase3.traces.toggle": "Show or hide the {group} subplot.",
    "phase3.traces.series": "Show or hide the '{series}' trace within the {group} "
                            "subplot (and its faded reference line). The subplot "
                            "itself stays; its legend drops the hidden trace.",
    "phase3.traces.inblock": "Overlay the animal's response DURING each intervention "
                             "block: cumulative compliance with the forced arm (on "
                             "Accuracy) and in-block P(left) (on Strategy), one "
                             "segment per block, coloured by block type. The block "
                             "shading + colour key stay either way. The free-choice "
                             "lines still gap over the block on purpose.",

    # ---- phase3_choice_raster_view.py: Metrics tab (trial-by-trial raster) ----
    "phase3.raster": "Trial-by-trial choice raster of the current Phase 3 session: "
                     "one bar per completed trial along the trial axis. A bar points "
                     "UP for a RIGHT choice (purple) and DOWN for a LEFT choice "
                     "(red); it is full-height + solid when the trial was correct "
                     "and half-height + faded when incorrect. Probe trials get a "
                     "teal outline, forced-arm (intervention) trials an orange "
                     "outline. Read-only view of the published session snapshot.",
    "phase3.raster.window": "Rolling-window size (most-recent completed trials) for "
                            "the green rolling-accuracy line overlaid on the raster. "
                            "Larger = smoother but slower to react. Only affects the "
                            "accuracy line, not the bars.",
    "phase3.raster.width": "Width of the visible window in TRIALS (the zoom). Blank "
                           "or 0 = show the whole session. A smaller value zooms in; "
                           "scroll with the bar below, or hit Latest to jump back to "
                           "the newest trials. View-only.",
    "phase3.raster.accuracy": "Overlay the rolling accuracy (0-1, right axis, green) "
                              "over the choice bars, with a dashed 50% reference. "
                              "Same rolling window as the Metrics traces panel.",
    "phase3.raster.scroll": "Scroll the visible trial window across the session "
                            "(pan). Dragging it stops the plot from auto-following "
                            "the newest trials; press Latest to resume following.",
    "phase3.raster.latest": "Jump back to the newest trials and resume auto-following "
                            "the live edge of the session (undoes a manual scroll).",

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
                        "other lap counts one. The [nL/nR] split is the side the "
                        "rat committed to on those denied trips - its directional "
                        "pull on the shortcuts (ADR-0095). An integer >= 0.",
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
                                "completed trial. Under the debias generator this "
                                "is over OUT-OF-BLOCK trials only (the [n/N] "
                                "bracket counts the same), so forced corrective "
                                "trials never distort it.",
    "behavior.phase3.bycue": "Accuracy split by the trial's CHOICE cue: HIGH tone "
                             "vs LOW tone (each with its completed-trial count). "
                             "A rat following the cue is accurate on both; a large "
                             "gap flags a cue it hasn't learned. -- until that cue "
                             "has been seen.",
    "behavior.phase3.bias": "Two side-bias views. 'Side bias (chose)' = the "
                            "fraction of trials the rat chose LEFT vs RIGHT (its "
                            "own choice, regardless of correctness), with the L/R "
                            "counts; 50/50 is unbiased. 'dir b' = the directional-"
                            "ERROR bias in [-1,+1] (b>0 = left-biased): of the "
                            "trials the rat got WRONG, how lopsided the pulls are "
                            "(ADR-0090). It's the session-level twin of the signal "
                            "the debias controller triggers on, shown for every "
                            "session so you can watch it track performance. -- "
                            "before the first choice / with no errors yet.",
    "behavior.phase3.reward": "Reward rate = rewarded / completed trials (over "
                              "OUT-OF-BLOCK trials under the debias generator, "
                              "matching the [n/N] bracket). Hidden entirely when "
                              "the 'reward incorrect choices' shaping aid is on, "
                              "because it then pins at ~100% by construction and no "
                              "longer tracks accuracy.",
    "behavior.phase3.strategy": "Naive-strategy readout over adjacent completed "
                                "trials, on the trial OUTCOME (not reward, so it "
                                "stays meaningful under the shaping aid): Win-stay "
                                "= chose the same arm again after a CORRECT trial; "
                                "Lose-shift = switched arms after an INCORRECT "
                                "one. High values flag a rat repeating/alternating "
                                "on its own result instead of following the cue. "
                                "-- until the conditioning outcome has occurred.",
    "behavior.phase3.dwell": "Cue-zone dwell = median/mean of (choice time - "
                             "choice-cue-on time) over completed trials (n = how "
                             "many). A RELATIVE within-animal signal, NOT a "
                             "reaction time: both stamps are host wall-clock "
                             "actuation times carrying uncharacterized serial/"
                             "actuation latency (why the old 'Choice RT' was "
                             "removed, ADR-0094), so only its CHANGE is meaningful "
                             "- e.g. whether the animal lingers longer at the cue "
                             "after a stem speedbump. The cue fires as the rat "
                             "enters the stem, so this is its cue-zone / stem-"
                             "traversal dwell. See ADR-0108. -- until a stamped "
                             "trial exists.",
    # ---- behavior_view.py: Phase 3 'Interventions' column - the live closed-loop
    #      scheduler group (side-bias debias + de-alternation; ADR-0086/0107). Only
    #      populated under the 'debias_blocks' / 'intervention_blocks' generators;
    #      blank/off for the balanced (open-loop) schedule. The learning measures in
    #      the left column are over out-of-block trials, so a block never distorts
    #      them. ----
    "behavior.phase3.interventions": "Live closed-loop intervention scheduler "
                                     "(side-bias debias + de-alternation). Off for "
                                     "the balanced generator; otherwise shows each "
                                     "enabled detector's live score vs its trigger, "
                                     "its auto-trigger sample gate, and the "
                                     "corrective-block tallies. The learning "
                                     "measures at left are over out-of-block trials, "
                                     "so a block never distorts them.",
    "behavior.phase3.iv_state": "Current scheduler state: NORMAL (handing out the "
                                "counterbalanced schedule) or INTERVENTION (a "
                                "corrective block is running), with which "
                                "intervention owns the active block in parentheses "
                                "(side_bias / alternation).",
    "behavior.phase3.iv_sidebias": "Side-bias debias detector. 'b=' is the live "
                                   "sliding-window directional bias vs its trigger "
                                   "threshold (b>0 = left-biased). 'err N/min' is "
                                   "the auto-trigger GATE: a block needs at least "
                                   "'min' directional errors in the window before "
                                   "the bias can fire one - so if |b| is over "
                                   "threshold but err hasn't reached min, no block "
                                   "starts yet (that's why). See debias.py / "
                                   "ADR-0090.",
    "behavior.phase3.iv_side_blocks": "Side-bias corrective blocks so far: total, "
                                      "then ok = ended by the consecutive-correct "
                                      "exit, cap = hit the max-length welfare "
                                      "failsafe.",
    "behavior.phase3.iv_alt": "De-alternation detector. 'r=' is the live cue-aware "
                              "alternation rate vs its trigger threshold "
                              "(P(switch | cue said to repeat the last choice); "
                              "1.0 = pure alternator, ~0 = cue-follower). 'samp "
                              "N/min' is the auto-trigger GATE: a block needs at "
                              "least 'min' repeat-cued samples in the window before "
                              "the rate can fire one - so if r is over threshold but "
                              "samp hasn't reached min, no block starts yet (that's "
                              "why). See alternation.py / ADR-0107.",
    "behavior.phase3.iv_alt_blocks": "De-alternation corrective blocks so far "
                                     "(count). Unlike side-bias blocks there is no "
                                     "ok/cap split: a block exits once the rat has "
                                     "repeated on BOTH arms (the two-sided exit, "
                                     "ADR-0129) or hits the welfare cap - best read "
                                     "from the per-trial block_kind/alt_rate in the "
                                     "CSV.",
    "behavior.phase3.iv_inblock": "Accuracy on the forced in-block (corrective) "
                                  "trials, with the completed in-block trial count "
                                  "in brackets - across both intervention kinds. "
                                  "Kept separate from the out-of-block learning "
                                  "measures at left.",

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
                               "don't appear in the cued-trial counts. The [nL/nR] "
                               "split is the side the rat committed to on those "
                               "denied trips - its directional pull on the "
                               "shortcuts (ADR-0095). Integer >= 0.",
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
