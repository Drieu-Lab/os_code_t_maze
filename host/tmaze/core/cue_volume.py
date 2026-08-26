"""
cue_volume.py - per-cue playback volume calibration (equalize HIGH vs LOW).

A cheap speaker's frequency response is not flat, so two cue tones played at the
SAME amplitude can measure at different loudness (dB) - the HIGH cue and the LOW
cue don't match by ear or on a meter. The firmware can now play each cue at a
chosen fraction of full amplitude (PROTOCOL v7: ``CUE_ON,<kind>,<hz>,<gain>``,
gain a percent 0..100; see docs/decisions/0125). This module owns the small
rig-level calibration file that records the balance the operator measured, and
turns a tone frequency into the gain percent to send.

Because the firmware's amplitude already sits just under the DAC clip ceiling,
the calibration only ever ATTENUATES (100 = full, the default); the louder cue is
turned down to match the quieter one, and overall level is restored with the
speaker's own gain potentiometer.

The measured points live in ``host/cue_calibration.json`` (gain percent keyed by
frequency in Hz), beside the other host config files (``rig_scale.json`` /
``reward_calibration.csv``); this module reads/writes that file and does the
lookup. An unset or unknown frequency falls back to 100 % (no change), so a rig
that never calibrates behaves exactly as before. Pure stdlib + headless-testable
(mirrors the reward-volume calibration pattern, docs/decisions/0091).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from tmaze.paths import host_dir

__all__ = [
    "SCHEMA_VERSION", "DEFAULT_GAIN_PCT", "GAIN_PCT_MIN", "GAIN_PCT_MAX",
    "clamp_gain_pct", "default_calibration_path",
    "load_gains", "save_gains", "gain_pct_for", "reload_calibration",
]

# Bump if the on-disk layout changes so an older reader can branch.
SCHEMA_VERSION = 1

# Percent of full DAC amplitude. 100 == the firmware's full level (the default
# when no gain is sent); it can only be turned DOWN (see module docstring and
# CUE_GAIN_PCT_* in firmware/common/rig_config.h).
DEFAULT_GAIN_PCT = 100
GAIN_PCT_MIN = 0
GAIN_PCT_MAX = 100


def clamp_gain_pct(value) -> int:
    """Coerce ``value`` to an int percent within [GAIN_PCT_MIN, GAIN_PCT_MAX].

    Non-numeric / missing input falls back to the full-volume default, so a bad
    cell can never silence a cue or push the DAC past clipping.
    """
    try:
        pct = int(round(float(value)))
    except (TypeError, ValueError):
        return DEFAULT_GAIN_PCT
    return max(GAIN_PCT_MIN, min(GAIN_PCT_MAX, pct))


def _norm_hz(hz) -> Optional[int]:
    """Frequency as a positive int (the dict key), or None if not usable."""
    try:
        f = int(round(float(hz)))
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def default_calibration_path() -> Path:
    """Canonical location of the calibration file: ``host/cue_calibration.json``
    (beside the package, like ``rig_scale.json`` / ``reward_calibration.csv``)."""
    return host_dir() / "cue_calibration.json"


def load_gains(path=None) -> Dict[int, int]:
    """Read the ``{frequency_hz: gain_pct}`` map from ``path`` (default: the host
    JSON).

    A missing file returns ``{}`` (everything falls back to 100 %). A present but
    malformed file (not a JSON object, or a bad ``gain_pct`` block) raises
    ``ValueError`` so the caller can surface it rather than silently losing a
    saved balance. Each frequency key is normalized to an int Hz and each gain is
    clamped into range, so the returned map is always usable.
    """
    p = Path(path) if path is not None else default_calibration_path()
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("cue_calibration.json must be a JSON object")
    block = raw.get("gain_pct", {})
    if not isinstance(block, dict):
        raise ValueError("cue_calibration.json 'gain_pct' must be an object")
    out: Dict[int, int] = {}
    for k, v in block.items():
        hz = _norm_hz(k)
        if hz is not None:
            out[hz] = clamp_gain_pct(v)
    return out


def save_gains(path, gains: Dict[int, int]) -> None:
    """Write the ``{frequency_hz: gain_pct}`` map to ``path`` (pretty JSON).

    Frequencies are normalized/sorted and gains clamped, so the file is always a
    valid, round-trippable calibration. A schema-version header is written for
    forward compatibility.
    """
    p = Path(path) if path is not None else default_calibration_path()
    clean: Dict[str, int] = {}
    for hz, pct in gains.items():
        f = _norm_hz(hz)
        if f is not None:
            clean[str(f)] = clamp_gain_pct(pct)
    ordered = {k: clean[k] for k in sorted(clean, key=int)}
    obj = {"_schema": SCHEMA_VERSION, "gain_pct": ordered}
    with p.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


# Cached calibration map: {frequency_hz: gain_pct}. Loaded lazily on first use and
# re-loadable via reload_calibration() (e.g. after the Setup subtab rewrites the
# file, or to point a test at a temp file). Mirrors reward_volume's cache.
_GAINS: Optional[Dict[int, int]] = None
_GAINS_PATH = None  # None -> the default host/cue_calibration.json


def reload_calibration(path=None) -> None:
    """(Re)load the calibration map from ``path`` (default: the host JSON).

    Call after the calibration file is rewritten, or from a test to point at a
    temporary file. Passing a path makes it the source for subsequent lookups. A
    malformed file leaves the previous (or empty) map in place so a bad save can't
    crash the cue path mid-session.
    """
    global _GAINS, _GAINS_PATH
    if path is not None:
        _GAINS_PATH = path
    try:
        _GAINS = load_gains(_GAINS_PATH or default_calibration_path())
    except (OSError, ValueError):
        if _GAINS is None:
            _GAINS = {}


def _gains() -> Dict[int, int]:
    if _GAINS is None:
        reload_calibration()
    return _GAINS or {}


def gain_pct_for(hz) -> int:
    """Playback volume percent (0..100) to send for a cue of frequency ``hz``.

    Falls back to :data:`DEFAULT_GAIN_PCT` (100 %, full volume / no change) for an
    un-calibrated or unusable frequency, so an un-measured tone plays at the
    firmware default.
    """
    f = _norm_hz(hz)
    if f is None:
        return DEFAULT_GAIN_PCT
    return _gains().get(f, DEFAULT_GAIN_PCT)
