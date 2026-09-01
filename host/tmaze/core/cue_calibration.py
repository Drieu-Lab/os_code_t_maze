"""
cue_calibration.py - per-cue playback calibration (volume + frequency).

A cheap speaker is imperfect in two ways this module corrects, per cue:

  1. VOLUME - its frequency response is not flat, so two cue tones played at the
     SAME amplitude measure at different loudness (dB). The firmware can play each
     cue at a chosen fraction of full amplitude (PROTOCOL v7:
     ``CUE_ON,<kind>,<hz>,<gain>``, gain a percent 0..100; see docs/decisions/0125).
  2. FREQUENCY - the emitted tone sits slightly BELOW the commanded frequency
     (measured with a spectrum app). Modelled as a fixed proportional clock
     offset: if commanding ``C`` Hz is measured at ``E`` Hz and we want target
     ``T`` Hz, the corrected command is ``C_new = round(C * T / E)``. This is
     host-only - the firmware already accepts any ``<hz>`` in [FREQ_HZ_MIN,
     FREQ_HZ_MAX], so we just send a corrected frequency (no reflash, no protocol
     change; see docs/decisions/0148, extends 0125).

Both corrections are keyed by the TARGET cue frequency (4000 / 8000). Volume can
only ATTENUATE (100 = full, the default); the louder cue is turned down to match
the quieter one and overall level is restored with the speaker's own gain
potentiometer. Frequency correction defaults to identity (command == target).

The measured points live in ``host/cue_calibration.json`` beside the other host
config files (``rig_scale.json`` / ``reward_calibration.csv``): a ``gain_pct``
block (percent keyed by frequency) and a ``commanded_hz`` block (commanded Hz
keyed by target frequency). This module reads/writes that file and does the
lookups. An unset or unknown frequency falls back to 100 % gain / identity
frequency, so a rig that never calibrates behaves exactly as before. Pure stdlib
+ headless-testable (mirrors the reward-volume calibration pattern,
docs/decisions/0091).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

from tmaze.paths import host_dir

__all__ = [
    "SCHEMA_VERSION", "DEFAULT_GAIN_PCT", "GAIN_PCT_MIN", "GAIN_PCT_MAX",
    "FREQ_HZ_MIN", "FREQ_HZ_MAX",
    "clamp_gain_pct", "clamp_freq_hz", "default_calibration_path",
    "load_gains", "load_commanded", "save_gains", "save_commanded",
    "save_calibration", "gain_pct_for", "commanded_hz_for", "reload_calibration",
]

# Bump if the on-disk layout changes so an older reader can branch. v2 adds the
# ``commanded_hz`` block alongside v1's ``gain_pct`` (docs/decisions/0148).
SCHEMA_VERSION = 2

# Percent of full DAC amplitude. 100 == the firmware's full level (the default
# when no gain is sent); it can only be turned DOWN (see module docstring and
# CUE_GAIN_PCT_* in firmware/common/rig_config.h).
DEFAULT_GAIN_PCT = 100
GAIN_PCT_MIN = 0
GAIN_PCT_MAX = 100

# Allowed commanded-frequency range, mirroring CUE_HZ_MIN / CUE_HZ_MAX in
# firmware/common/rig_config.h; the firmware clamps to the same window.
FREQ_HZ_MIN = 500
FREQ_HZ_MAX = 20000


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


def clamp_freq_hz(value, default=None) -> Optional[int]:
    """Coerce ``value`` to an int Hz within [FREQ_HZ_MIN, FREQ_HZ_MAX].

    Non-numeric / missing input falls back to ``default`` (itself normalized when
    given, else ``None``), so a bad measured-frequency cell can never command an
    out-of-range or nonsensical tone.
    """
    try:
        hz = int(round(float(value)))
    except (TypeError, ValueError):
        return _norm_hz(default) if default is not None else None
    return max(FREQ_HZ_MIN, min(FREQ_HZ_MAX, hz))


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


def _read_raw(path) -> dict:
    """Load the calibration JSON object from ``path`` (``{}`` if missing).

    A present-but-non-object file raises ``ValueError`` so callers surface it
    rather than silently losing a saved calibration.
    """
    p = Path(path) if path is not None else default_calibration_path()
    try:
        with p.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("cue_calibration.json must be a JSON object")
    return raw


def load_gains(path=None) -> Dict[int, int]:
    """Read the ``{frequency_hz: gain_pct}`` map from ``path`` (default: the host
    JSON).

    A missing file returns ``{}`` (everything falls back to 100 %). A present but
    malformed file (not a JSON object, or a bad ``gain_pct`` block) raises
    ``ValueError`` so the caller can surface it rather than silently losing a
    saved balance. Each frequency key is normalized to an int Hz and each gain is
    clamped into range, so the returned map is always usable.
    """
    raw = _read_raw(path)
    block = raw.get("gain_pct", {})
    if not isinstance(block, dict):
        raise ValueError("cue_calibration.json 'gain_pct' must be an object")
    out: Dict[int, int] = {}
    for k, v in block.items():
        hz = _norm_hz(k)
        if hz is not None:
            out[hz] = clamp_gain_pct(v)
    return out


def load_commanded(path=None) -> Dict[int, int]:
    """Read the ``{target_hz: commanded_hz}`` map from ``path`` (default: host JSON).

    A missing file (or absent ``commanded_hz`` block) returns ``{}`` (every cue
    falls back to identity: command == target). A present-but-malformed
    ``commanded_hz`` block raises ``ValueError``. Keys are normalized to int Hz
    and commanded values clamped into [FREQ_HZ_MIN, FREQ_HZ_MAX]; an unusable
    commanded value is dropped so the target-identity fallback applies.
    """
    raw = _read_raw(path)
    block = raw.get("commanded_hz", {})
    if not isinstance(block, dict):
        raise ValueError("cue_calibration.json 'commanded_hz' must be an object")
    out: Dict[int, int] = {}
    for k, v in block.items():
        target = _norm_hz(k)
        cmd = clamp_freq_hz(v)
        if target is not None and cmd is not None:
            out[target] = cmd
    return out


def save_calibration(path, gains: Dict[int, int],
                     commanded: Dict[int, int]) -> None:
    """Write BOTH the ``gain_pct`` and ``commanded_hz`` maps to ``path`` (pretty
    JSON, schema-versioned).

    Frequencies are normalized/sorted, gains clamped, and commanded values
    clamped into range, so the file is always a valid, round-trippable
    calibration.
    """
    p = Path(path) if path is not None else default_calibration_path()

    clean_gain: Dict[str, int] = {}
    for hz, pct in gains.items():
        f = _norm_hz(hz)
        if f is not None:
            clean_gain[str(f)] = clamp_gain_pct(pct)
    ordered_gain = {k: clean_gain[k] for k in sorted(clean_gain, key=int)}

    clean_cmd: Dict[str, int] = {}
    for target, cmd in commanded.items():
        f = _norm_hz(target)
        c = clamp_freq_hz(cmd)
        if f is not None and c is not None:
            clean_cmd[str(f)] = c
    ordered_cmd = {k: clean_cmd[k] for k in sorted(clean_cmd, key=int)}

    obj = {
        "_schema": SCHEMA_VERSION,
        "gain_pct": ordered_gain,
        "commanded_hz": ordered_cmd,
    }
    with p.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def save_gains(path, gains: Dict[int, int]) -> None:
    """Write the ``{frequency_hz: gain_pct}`` map, PRESERVING any existing
    ``commanded_hz`` block on disk (so tuning volume never clobbers frequency)."""
    try:
        commanded = load_commanded(path)
    except (OSError, ValueError):
        commanded = {}
    save_calibration(path, gains, commanded)


def save_commanded(path, commanded: Dict[int, int]) -> None:
    """Write the ``{target_hz: commanded_hz}`` map, PRESERVING any existing
    ``gain_pct`` block on disk (so tuning frequency never clobbers volume)."""
    try:
        gains = load_gains(path)
    except (OSError, ValueError):
        gains = {}
    save_calibration(path, gains, commanded)


# Cached calibration maps, loaded lazily on first use and re-loadable via
# reload_calibration() (e.g. after the Setup subtab rewrites the file, or to
# point a test at a temp file). Mirrors reward_volume's cache.
_GAINS: Optional[Dict[int, int]] = None
_COMMANDED: Optional[Dict[int, int]] = None
_GAINS_PATH = None  # None -> the default host/cue_calibration.json


def reload_calibration(path=None) -> None:
    """(Re)load BOTH calibration maps from ``path`` (default: the host JSON).

    Call after the calibration file is rewritten, or from a test to point at a
    temporary file. Passing a path makes it the source for subsequent lookups. A
    malformed file leaves the previous (or empty) maps in place so a bad save
    can't crash the cue path mid-session.
    """
    global _GAINS, _COMMANDED, _GAINS_PATH
    if path is not None:
        _GAINS_PATH = path
    src = _GAINS_PATH or default_calibration_path()
    try:
        _GAINS = load_gains(src)
    except (OSError, ValueError):
        if _GAINS is None:
            _GAINS = {}
    try:
        _COMMANDED = load_commanded(src)
    except (OSError, ValueError):
        if _COMMANDED is None:
            _COMMANDED = {}


def _gains() -> Dict[int, int]:
    if _GAINS is None:
        reload_calibration()
    return _GAINS or {}


def _commanded() -> Dict[int, int]:
    if _COMMANDED is None:
        reload_calibration()
    return _COMMANDED or {}


def gain_pct_for(hz) -> int:
    """Playback volume percent (0..100) to send for a cue of TARGET frequency ``hz``.

    Falls back to :data:`DEFAULT_GAIN_PCT` (100 %, full volume / no change) for an
    un-calibrated or unusable frequency, so an un-measured tone plays at the
    firmware default.
    """
    f = _norm_hz(hz)
    if f is None:
        return DEFAULT_GAIN_PCT
    return _gains().get(f, DEFAULT_GAIN_PCT)


def commanded_hz_for(target_hz) -> Optional[int]:
    """Commanded frequency (Hz) to send so the speaker EMITS ``target_hz``.

    Returns the calibrated commanded value for the target, or the target itself
    (identity) when un-calibrated - so an un-measured tone is commanded exactly as
    before. An unusable input returns ``None`` (the caller then omits the field).
    """
    f = _norm_hz(target_hz)
    if f is None:
        return None
    return _commanded().get(f, f)
