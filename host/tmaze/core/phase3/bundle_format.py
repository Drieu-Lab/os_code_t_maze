"""bundle_format.py - assemble the session bundle's self-describing data contract.

The rig writes one ``config/format.json`` per session so an offline reader (the
analysis stack in the separate ``drieu_lab_analysis`` repo, and MATLAB / other
tools) can learn the on-disk *format* - the token vocabularies, the protocol
version, the zone names, and the trial-CSV column dictionary - from the bundle
itself, without importing the rig-control code. This is what let the analysis
layer be carved into its own repo (ADR-0142 / FW-0048): it versions against this
stable, machine-readable contract instead of a shared Python import.

Single source of truth: every value here is derived from the live cores that
actually shape the data (``phase3_task`` outcomes, ``trial_sequence`` cue tones,
``phase3_log`` arm labels + columns + proto, ``maze_state`` zone names), so the
manifest can't drift from what the rig writes. The analysis repo keeps its own
mirror of these tokens; the shared golden fixture (``tools/make_golden_bundle.py``
-> ``tests/fixtures/``) is what now keeps the two in agreement across the repos:
this repo's ``test_golden_bundle`` regenerates it and the analysis repo's reader/
exporter tests round-trip it, so a format change surfaces on both sides (bump
``FORMAT_VERSION`` and re-issue the fixture when that happens).

Pure core: stdlib only, no Tk/OpenCV. Headless tests: python -m unittest
test_session_config (which exercises the write path) and test_golden_bundle (which
regenerates the committed fixture from this manifest + the trial writer).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from tmaze.core import maze_state
from tmaze.core.phase3 import phase3_log
from tmaze.core.phase3.phase3_task import ABANDONED, CORRECT, INCORRECT
from tmaze.core.phase3.trial_sequence import HIGH, LEFT, LOW, RIGHT

# Bump when the format.json layout / semantics change. Kept in lockstep with the
# analysis mirror (drieu_lab_analysis: tmaze_analysis.phase3_format.FORMAT_VERSION),
# which the reader checks to fail loudly on a newer format.
# v2 (ADR-0154): adds the ``sync`` block below (the offline LED clock-fit stream
# written by tmaze.tools.sync_fit into sync/).
# v3 (ADR-0164): adds ``firmware_proto`` + ``sensor_polarity`` - the firmware
# serial protocol version seen on the run's ``0,PROTO`` handshake and the S/L
# state convention it implies (proto >= 8 corrected, else the legacy inverted
# meaning), so the analysis reads each bundle's beam/lick polarity per session.
FORMAT_VERSION = 3

# The firmware serial protocol version at/after which the body-beam (S) and lick
# (L) state bit is CORRECTED (1 = broken/contact); below it the recorded meaning
# is inverted (1 = resting). See firmware/PROTOCOL.md and docs/decisions/0164.
CORRECTED_POLARITY_PROTO = 8

POLARITY_CORRECTED = "corrected"
POLARITY_LEGACY = "legacy_inverted"
POLARITY_UNKNOWN = "unknown"


def sensor_polarity_for(firmware_proto: Optional[int]) -> str:
    """The S/L state convention implied by a firmware serial proto version.

    ``corrected`` (proto >= 8): ``S,<i>,1`` = beam broken, ``L,<i>,1`` = tongue
    contact. ``legacy_inverted`` (proto < 8): those ``1`` states are the RESTING
    states. ``unknown``: the ``0,PROTO`` handshake was not seen this run (the
    reader then infers from date / the trial-CSV proto). See docs/decisions/0164.
    """
    if firmware_proto is None:
        return POLARITY_UNKNOWN
    return POLARITY_CORRECTED if firmware_proto >= CORRECTED_POLARITY_PROTO \
        else POLARITY_LEGACY

# One-line meaning per trial-CSV column, keyed by the phase3_log.COLUMNS names.
# Columns without an entry still appear in the manifest (with an empty string), so
# adding a column to phase3_log.COLUMNS never silently drops it from the contract.
_COLUMN_DOC: Dict[str, str] = {
    "trial": "1-based trial index within the session",
    "correct_arm": "planned rewarded arm (LEFT/RIGHT)",
    "choice_cue": "choice-point cue tone (HIGH/LOW)",
    "return_cue_left": "planned left return-arm cue tone (HIGH/LOW)",
    "return_cue_right": "planned right return-arm cue tone (HIGH/LOW)",
    "chosen_arm": "arm the rat actually chose (LEFT/RIGHT)",
    "outcome": "trial result (CORRECT/INCORRECT/ABANDONED)",
    "rewarded": "1 if a reward was delivered, else 0",
    "topups": "count of lick top-up reward pulses delivered",
    "return_arm_used": "return arm the rat took back to the hub (LEFT/RIGHT)",
    "return_cue_played": "return cue tone actually played (HIGH/LOW), if any",
    "trial_start_iso": "host wall-clock ISO time the trial started",
    "choice_cue_on_iso": "host ISO time the choice cue turned on",
    "choice_cue_off_iso": "host ISO time the choice cue turned off",
    "choice_time_iso": "host ISO time the choice was scored (entered a choice arm)",
    "reward_cmd_iso": "host ISO time the reward command was issued",
    "return_cue_on_iso": "host ISO time the return cue turned on",
    "return_cue_off_iso": "host ISO time the return cue turned off",
    "trial_end_iso": "host ISO time the trial ended",
    "lick_time_iso": "host ISO time of the rewarded lick (blank if none)",
    "lick_fw_ms": "firmware hardware-clock ms of the lick (see docs/decisions/0047)",
    "sched_state": "NORMAL or INTERVENTION (in a corrective/adversarial block)",
    "block_id": "1-based index within an intervention block (blank in NORMAL)",
    "block_trigger": "how the block started (auto/manual)",
    "bias": "signed directional-error bias at this trial's decision",
    "block_kind": "which intervention owns the block (side_bias/alternation)",
    "alt_rate": "cue-aware alternation rate at this trial's decision",
    "prime_ms": "raw prime reward pulse width (ms) on a rewarded trial",
    "topup_ms_total": "sum of this trial's lick top-up pulse widths (ms)",
    "probe": "1 on an unrewarded probe trial, else blank",
    "probe_trigger": "1 on the correct trial that started a probe block",
    "choice_cue_on_fw_ms": "firmware hardware-clock ms of the choice cue onset, "
                           "from the CUE,ON echo (see docs/decisions/0165)",
    "reward_cmd_fw_ms": "firmware hardware-clock ms of the prime reward pulse, from "
                        "the first SOL/REWARD echo (see docs/decisions/0165)",
    "attempt": "1-based attempt number for this trial (append-only edits)",
    "superseded": "1 on a preserved attempt a later Repeat/Back replaced",
    "edit": "operator action that created a >1 attempt (repeat/back)",
}


def _columns() -> Dict[str, str]:
    """Ordered {column name: one-line meaning} for the trial CSV."""
    return {name: _COLUMN_DOC.get(name, "") for name in phase3_log.COLUMNS}


# The offline cross-stream sync stream (ADR-0153/0154; docs/design/cross-stream-
# sync-design.md sec 3.6). tmaze.tools.sync_fit WRITES these files into sync/; this
# manifest only DOCUMENTS their shape so an offline reader learns the columns from
# the bundle. The tool is the source of truth for the actual columns/keys - keep
# these two in step (the golden-bundle round trip + the design doc are the guard).
_SYNC_EVENT_COLUMN_DOC: Dict[str, str] = {
    "led_id": "detected board LED id (blob_N, or a mapped channel name)",
    "edge": "'on' or 'off' - which LED transition this row marks",
    "frame_idx": "0-based video frame index of the edge",
    "cam_timestamp": "camera hardware-clock tick at that frame (ns), blank if unknown",
    "host_unix_s": "host wall-clock epoch seconds at that frame (from the sidecar)",
    "matched_event": "serial channel the LED is bound to (e.g. SOL2.1, S2=0)",
    "firmware_ms": "Arduino millis() of the matched serial edge, blank if unmatched",
    "residual_ms": "edge misalignment vs the fit in firmware-ms, blank if unmatched",
}

_SYNC_CLOCK_FIT_FIELDS: Dict[str, str] = {
    "method": "alignment method that produced the fit (led-fit)",
    "fit": "per-segment offset + rate (cam_ticks = offset[seg] + rate*fw_ms) + residual stats",
    "bindings": "per-LED serial channel + polarity + precision/recall/lag",
    "board_rect": "board search rectangle [x0,y0,x1,y1] in full-frame px",
    "board_poly": "board ROI outline (fiducial hull + margin) in full-frame px",
    "fiducials": "persistent red/green fiducial count + colour split + registration pose",
    "registration": "v1 blinking-constellation registration pose (when no fiducials)",
    "verdict": "overall fit verdict",
}


def _sync() -> Dict[str, Any]:
    """The ``sync`` block: the LED clock-fit stream's on-disk shape (see above)."""
    return {
        "stream": "sync",
        "method": "led-fit",
        "led_events": "sync/led_events_<session_id>.csv",
        "led_events_columns": dict(_SYNC_EVENT_COLUMN_DOC),
        "clock_fit": "sync/clock_fit_<session_id>.json",
        "clock_fit_fields": dict(_SYNC_CLOCK_FIT_FIELDS),
    }


def format_manifest(firmware_proto: Optional[int] = None) -> Dict[str, Any]:
    """The ``config/format.json`` payload: the bundle's machine-readable contract.

    Enumerates the token vocabularies, the protocol version, the maze zone names,
    and the trial-CSV column dictionary - all derived from the live cores so the
    manifest is always consistent with what the rig writes.

    ``firmware_proto`` is the version seen on the run's ``0,PROTO`` handshake (the
    one runtime-observed value here); it is recorded verbatim and used to derive
    ``sensor_polarity`` - the S/L state convention (see docs/decisions/0164). It
    is ``None`` (-> ``sensor_polarity`` "unknown") when the handshake was not seen.
    """
    return {
        "format_version": FORMAT_VERSION,
        "proto": phase3_log.PROTO_VERSION,
        "firmware_proto": firmware_proto,
        "sensor_polarity": sensor_polarity_for(firmware_proto),
        "zones_version": maze_state.ZONES_VERSION,
        "outcome_labels": [CORRECT, INCORRECT, ABANDONED],
        "cue_labels": [HIGH, LOW],
        "arm_labels": [phase3_log._ARM_LABEL[LEFT], phase3_log._ARM_LABEL[RIGHT]],
        "zone_names": list(maze_state.EDITABLE_ZONES),
        "columns": _columns(),
        "sync": _sync(),
    }
