"""
reward_volume.py - map a reward solenoid pulse to a calibrated dispensed volume.

The rig dispenses reward by opening a valve for a fixed time; the volume
delivered depends on that pulse width. This module holds the bench calibration
(pulse width -> measured volume) and converts a pulse duration into microliters
so the experiment GUI can show a running total of reward dispensed during a
session (used to titrate the daily water-restriction top-up).

Calibration lives here as a CONFIG CONSTANT: re-measure and edit
REWARD_UL_CALIBRATION whenever you change the reward pulse duration. See
docs/decisions/0019 for why the firmware now logs each pulse's duration on the
wire (REWARD,CENTER,<dur_ms> / REWARD,<port>,MANUAL,<dur_ms>) and why volume is
computed on the host.
"""

from __future__ import annotations

# Measured dispensed volume (uL) per reward pulse, keyed by pulse width (ms).
# The 300 ms -> 60 uL point is the current bench calibration and matches
# REWARD_PULSE_MS in firmware/common/rig_config.h. Add a row (e.g. 150: 30.0)
# after measuring at any other pulse width you intend to use; reward delivery is
# roughly - but not exactly - linear, so an explicit point beats extrapolation.
REWARD_UL_CALIBRATION: dict[int, float] = {300: 60.0}

# Pulse width (ms) assumed for a reward line that carries no trailing duration
# field (e.g. an older, pre-v4 firmware / log). Matches REWARD_PULSE_MS. The host
# parsing is intentionally tolerant so the GUI still runs against an un-reflashed
# Due; see docs/decisions/0019.
DEFAULT_REWARD_PULSE_MS = 300


def reward_volume_ul(dur_ms: float | int | None) -> float:
    """Dispensed volume (uL) for a single reward pulse of ``dur_ms`` milliseconds.

    Exact for a calibrated pulse width; otherwise linearly inter/extrapolates
    across the measured points in :data:`REWARD_UL_CALIBRATION`. With a single
    calibration point, volume is assumed proportional to duration
    (origin-anchored). Returns ``0.0`` for a missing or non-positive duration,
    and never returns a negative volume.
    """
    if dur_ms is None:
        return 0.0
    try:
        x = float(dur_ms)
    except (TypeError, ValueError):
        return 0.0
    if x <= 0.0:
        return 0.0

    points = sorted(REWARD_UL_CALIBRATION.items())
    if not points:
        return 0.0
    if x in REWARD_UL_CALIBRATION:        # exact calibrated width (300.0 matches key 300)
        return float(REWARD_UL_CALIBRATION[x])

    # Single point: assume proportional to duration (line through the origin).
    if len(points) == 1:
        ms0, ul0 = points[0]
        return max(0.0, ul0 * x / ms0) if ms0 else 0.0

    # Multiple points: pick the bracketing segment (or the nearest end segment
    # for extrapolation) and interpolate linearly.
    if x <= points[0][0]:
        (x0, y0), (x1, y1) = points[0], points[1]
    elif x >= points[-1][0]:
        (x0, y0), (x1, y1) = points[-2], points[-1]
    else:
        x0, y0 = points[0]
        x1, y1 = points[-1]
        for i in range(len(points) - 1):
            if points[i][0] <= x <= points[i + 1][0]:
                (x0, y0), (x1, y1) = points[i], points[i + 1]
                break

    if x1 == x0:
        return max(0.0, float(y0))
    vol = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return max(0.0, float(vol))


def format_volume(ul: float) -> str:
    """Human-friendly volume string: uL under 1 mL, else mL."""
    if ul < 1000.0:
        return f"{ul:.0f} \u00b5L"
    return f"{ul / 1000.0:.2f} mL"
