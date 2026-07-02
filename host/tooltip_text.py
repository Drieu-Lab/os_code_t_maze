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
    "camera.status": "Live acquisition health: frames grabbed / saved, source "
                     "drops, incomplete grabs, encode-queue drops, and queue "
                     "fill.",

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
    "tracker.save_model": "Save the appearance model (.npz) for this animal.",
    "tracker.load_model": "Load a saved appearance model (.npz).",
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
    "rules.log": "Rule events (prime/top-up/re-arm/suppressed) are written to a "
                 "rule_events CSV in the recordings' track_logs folder.",
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

    # ---- tracker_view.py: tuning sliders (keyed by param name) ----
    "tracker.thresh": "Foreground threshold on the background difference (0-255).",
    "tracker.min_area": "Minimum blob area (px) to be considered the rat.",
    "tracker.blur": "Blur kernel applied before thresholding (noise smoothing).",
    "tracker.gate_radius": "Max jump (px) allowed between frames before the track "
                           "is rejected.",
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
