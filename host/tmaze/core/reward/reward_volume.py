"""
reward_volume.py - map a reward solenoid pulse to a calibrated dispensed volume.

The rig dispenses reward by opening a valve for a fixed time; the volume
delivered depends on that pulse width AND on the liquid (viscosity). This module
turns a pulse duration into microliters so the experiment GUI can show a running
total of reward dispensed during a session (used to titrate the daily
water-restriction top-up).

The measured points live in a data file (``host/reward_calibration.csv``, keyed
by ``(port, liquid, pulse_ms)``) read via :mod:`reward_calibration`; this module
does the lookup + interpolation over that table. With a single point for a curve,
volume is assumed proportional to duration (origin-anchored line); with several
points it interpolates linearly. If the file is missing or a curve isn't measured
yet, it falls back gracefully (requested liquid -> water -> the in-code central
bench point) so the GUI never crashes. See docs/decisions/0091 (supersedes 0019)
for why volume is measured directly per liquid and stored in a data file rather
than a single in-code constant, and why the firmware logs each pulse's duration
on the wire (REWARD,CENTER,<dur_ms> / REWARD,<port>,MANUAL,<dur_ms>).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from tmaze.core.reward.reward_calibration import (
    DEFAULT_REWARD_LIQUID,
    default_calibration_path,
    load_calibration,
    normalize_liquid,
    normalize_port,
)

# Re-exported so existing callers can keep importing it from here.
__all__ = [
    "DEFAULT_REWARD_LIQUID", "DEFAULT_REWARD_PULSE_MS",
    "reward_volume_ul", "reload_calibration", "format_volume",
]

# In-code fallback used only when no measured curve applies (missing file / an
# un-characterized port+liquid). The 300 ms -> 60 uL point is the original bench
# calibration for the central spout (water) and matches REWARD_PULSE_MS in
# firmware/common/rig_config.h; with one point the lookup assumes volume is
# proportional to duration. Real curves come from host/reward_calibration.csv.
_FALLBACK_CURVE: Dict[int, float] = {300: 60.0}

# Pulse width (ms) assumed for a reward line that carries no trailing duration
# field (e.g. an older, pre-v4 firmware / log). Matches REWARD_PULSE_MS. The host
# parsing is intentionally tolerant so the GUI still runs against an un-reflashed
# Due; see docs/decisions/0019.
DEFAULT_REWARD_PULSE_MS = 300

# Cached calibration table: {(port, liquid): {pulse_ms: volume_ul}}. Loaded lazily
# on first use and re-loadable via reload_calibration() (e.g. after the
# calibration tool rewrites the file, or to point tests at a temp file).
_CALIBRATION: Optional[Dict[Tuple[str, str], Dict[int, float]]] = None
_CALIBRATION_PATH = None  # None -> the default host/reward_calibration.csv


def reload_calibration(path=None) -> None:
    """(Re)load the calibration table from ``path`` (default: the host CSV).

    Call after the measurement tool rewrites the file, or from a test to point at
    a temporary file. Passing a path makes it the source for subsequent lookups.
    """
    global _CALIBRATION, _CALIBRATION_PATH
    if path is not None:
        _CALIBRATION_PATH = path
    _CALIBRATION = load_calibration(_CALIBRATION_PATH or default_calibration_path())


def _calibration() -> Dict[Tuple[str, str], Dict[int, float]]:
    if _CALIBRATION is None:
        reload_calibration()
    return _CALIBRATION or {}


def _curve_for(port: str, liquid: str) -> Dict[int, float]:
    """The best matching measured curve for ``(port, liquid)``.

    Fallback chain: requested ``(port, liquid)`` -> ``(port, water)`` -> the
    in-code central bench point, so an un-measured liquid or port degrades
    gracefully instead of erroring.
    """
    cal = _calibration()
    for key in ((port, liquid), (port, "water")):
        curve = cal.get(key)
        if curve:
            return curve
    return dict(_FALLBACK_CURVE)


def _interp(curve: Dict[int, float], x: float) -> float:
    """Volume (uL) at pulse width ``x`` (ms) over a ``{ms: uL}`` curve.

    Exact for a measured width; a single point is treated as proportional (line
    through the origin); multiple points inter/extrapolate linearly. Never
    negative.
    """
    if x in curve:
        return max(0.0, float(curve[x]))
    points = sorted(curve.items())
    if not points:
        return 0.0
    if len(points) == 1:
        ms0, ul0 = points[0]
        return max(0.0, ul0 * x / ms0) if ms0 else 0.0

    if x <= points[0][0]:
        (x0, y0), (x1, y1) = points[0], points[1]
    elif x >= points[-1][0]:
        (x0, y0), (x1, y1) = points[-2], points[-1]
    else:
        (x0, y0), (x1, y1) = points[0], points[-1]
        for i in range(len(points) - 1):
            if points[i][0] <= x <= points[i + 1][0]:
                (x0, y0), (x1, y1) = points[i], points[i + 1]
                break

    if x1 == x0:
        return max(0.0, float(y0))
    vol = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return max(0.0, float(vol))


def reward_volume_ul(dur_ms: float | int | None, port: str = "center",
                     liquid: str = DEFAULT_REWARD_LIQUID) -> float:
    """Dispensed volume (uL) for one reward pulse of ``dur_ms`` ms at ``port``.

    Uses the measured curve for ``(port, liquid)`` (see :func:`_curve_for` for the
    fallback chain). Returns ``0.0`` for a missing or non-positive duration, and
    never a negative volume. ``port``/``liquid`` are normalized, so an index
    (0/1/2) or a free-typed liquid name is accepted.
    """
    if dur_ms is None:
        return 0.0
    try:
        x = float(dur_ms)
    except (TypeError, ValueError):
        return 0.0
    if x <= 0.0:
        return 0.0
    curve = _curve_for(normalize_port(port), normalize_liquid(liquid))
    return _interp(curve, x)


def format_volume(ul: float) -> str:
    """Human-friendly volume string: uL under 1 mL, else mL."""
    if ul < 1000.0:
        return f"{ul:.0f} \u00b5L"
    return f"{ul / 1000.0:.2f} mL"
