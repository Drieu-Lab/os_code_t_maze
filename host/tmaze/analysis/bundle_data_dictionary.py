"""bundle_data_dictionary.py - a PROOF-OF-CONCEPT column data dictionary (units + dtype).

PROOF OF CONCEPT for docs/decisions/0144. The rig's ``config/format.json`` already
carries a one-line MEANING per trial-CSV column (docs/decisions/0142,
``bundle_format._COLUMN_DOC``), but not the UNITS or DATA TYPE a typed exporter
needs. ADR-0144 proposes folding units + dtype into that manifest and generating a
human-readable dictionary from it - but that touches the rig write path, so it is
deferred.

Meanwhile this module carries a small, clearly-labelled LOCAL units/dtype table so
the offline exporters (``export_parquet`` / ``export_nwb``) can emit typed,
annotated tables without the rig change. It MERGES the meanings straight from the
bundle's ``config/format.json`` when present (so the meaning stays single-sourced
from the rig) and augments them with the local unit/dtype. When ADR-0144 lands,
this table moves into ``bundle_format`` and this module becomes a thin reader.

Read-only, analysis-side: stdlib + pandas only; imports nothing from ``tmaze.core``
/ ``tmaze.paths`` (respects the docs/decisions/0142 boundary).
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional

import pandas as pd

# dtype tokens understood by :func:`coerce_dtypes`. Kept small and explicit so the
# mapping to pandas/Parquet/NWB types is obvious.
#   int      -> nullable Int64        float -> float64
#   bool     -> boolean (from "1"/"true")   datetime -> datetime64 (naive local)
#   id/date/category/str -> string (left as text)
_NUMERIC = "float"
_INT = "int"
_BOOL = "bool"
_DATETIME = "datetime"
_STR = "str"

# {column: (unit, dtype)} for the per-trial table. UNITS are plain words a
# non-specialist can read; the exact clock conventions live in docs/decisions/0047
# and docs/decisions/0124 (see the design review). "iso8601_local" = a host
# wall-clock ISO string read as LOCAL time (matches the video sidecar host_unix_s;
# docs/decisions/0128).
TRIAL_COLUMN_META: Dict[str, tuple] = {
    "trial": ("index", _INT),
    "correct_arm": ("arm", _STR),
    "choice_cue": ("tone", _STR),
    "return_cue_left": ("tone", _STR),
    "return_cue_right": ("tone", _STR),
    "chosen_arm": ("arm", _STR),
    "outcome": ("category", _STR),
    "rewarded": ("bool", _BOOL),
    "topups": ("count", _INT),
    "return_arm_used": ("arm", _STR),
    "return_cue_played": ("tone", _STR),
    "trial_start_iso": ("iso8601_local", _DATETIME),
    "choice_cue_on_iso": ("iso8601_local", _DATETIME),
    "choice_cue_off_iso": ("iso8601_local", _DATETIME),
    "choice_time_iso": ("iso8601_local", _DATETIME),
    "reward_cmd_iso": ("iso8601_local", _DATETIME),
    "return_cue_on_iso": ("iso8601_local", _DATETIME),
    "return_cue_off_iso": ("iso8601_local", _DATETIME),
    "trial_end_iso": ("iso8601_local", _DATETIME),
    "lick_time_iso": ("iso8601_local", _DATETIME),
    "lick_fw_ms": ("ms_firmware_clock", _INT),
    "sched_state": ("category", _STR),
    "block_id": ("index", _INT),
    "block_trigger": ("category", _STR),
    "bias": ("rate", _NUMERIC),
    "block_kind": ("category", _STR),
    "alt_rate": ("rate", _NUMERIC),
    "prime_ms": ("ms", _NUMERIC),
    "topup_ms_total": ("ms", _NUMERIC),
    "probe": ("bool", _BOOL),
    "probe_trigger": ("bool", _BOOL),
    "attempt": ("count", _INT),
    "superseded": ("bool", _BOOL),
    "edit": ("category", _STR),
    # Columns the loader attaches (not in the raw CSV) - documented so the exported
    # tables are fully self-describing too.
    "session_id": ("id", _STR),
    "animal": ("id", _STR),
    "date": ("date", _STR),
    "run_idx": ("index", _INT),
    "run_file": ("filename", _STR),
    "high_tone_is_left": ("bool", _BOOL),
    "seq": ("index", _INT),
}

# {column: (unit, meaning, dtype)} for the per-excursion table (docs/decisions/0109).
# Meanings are local here (format.json only documents the trial CSV).
EXCURSION_COLUMN_META: Dict[str, tuple] = {
    "event_index": ("index", "1-based outbound-trip index in append order", _INT),
    "iso": ("iso8601_local", "host ISO time of the choice-commit edge", _DATETIME),
    "mono": ("s", "host monotonic clock (seconds)", _NUMERIC),
    "type": ("category", "up-stem (scored) or non-stem (denied) trip", _STR),
    "side": ("arm", "LEFT/RIGHT arm of the trip", _STR),
    "outcome": ("category", "scored or denied", _STR),
    "linked_trial": ("index", "trial number this trip scored (blank if denied)", _INT),
    "sched_state": ("category", "NORMAL or INTERVENTION at the trip", _STR),
    "bias": ("rate", "signed directional-error bias at the trip", _NUMERIC),
    "cue_dwell_s": ("s", "relative host-clock cue-zone dwell (not a reaction time)",
                    _NUMERIC),
    "session_id": ("id", "session id", _STR),
    "animal": ("id", "animal id", _STR),
    "date": ("date", "session date", _STR),
}

# {column: (unit, meaning, dtype)} for the per-frame tracking table (the video
# sidecar; docs/decisions/0014). Only the analysis-relevant columns are typed here;
# other raw columns pass through as text.
TRACKING_COLUMN_META: Dict[str, tuple] = {
    "frame_idx": ("index", "0-based camera frame index", _INT),
    "host_unix_s": ("s_posix", "host wall-clock at frame grab (epoch seconds)",
                    _NUMERIC),
    "rig_ms": ("ms_firmware_clock", "latest firmware ms at grab; resets on MODE",
               _INT),
    "track_state": ("category", "tracker state (TRACKING/ACQUIRING/COASTING/LOST)",
                    _STR),
    "track_smooth_cx": ("px", "Kalman-smoothed centroid x (pixels)", _NUMERIC),
    "track_smooth_cy": ("px", "Kalman-smoothed centroid y (pixels)", _NUMERIC),
    "track_confidence": ("fraction", "tracker confidence 0..1", _NUMERIC),
    "cx_n": ("fraction", "centroid x normalized to frame width (0..1)", _NUMERIC),
    "cy_n": ("fraction", "centroid y normalized to frame height (0..1)", _NUMERIC),
}


def load_format_columns(bundle: str) -> Dict[str, str]:
    """Read the ``{column: meaning}`` map from a bundle's ``config/format.json``.

    Returns ``{}`` when the file is absent (legacy bundle) or unreadable - the
    exporters then fall back to a blank meaning. This is the single-sourced meaning
    from the rig (docs/decisions/0142); this module only adds units + dtype.
    """
    path = os.path.join(bundle, "config", "format.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return {}
    cols = obj.get("columns") if isinstance(obj, dict) else None
    return {str(k): str(v) for k, v in cols.items()} if isinstance(cols, dict) else {}


def trial_dictionary(bundle: str) -> Dict[str, Dict[str, str]]:
    """The merged ``{column: {meaning, unit, dtype}}`` dictionary for the trial table.

    Meaning comes from the bundle's ``config/format.json`` (single-sourced from the
    rig); unit + dtype come from :data:`TRIAL_COLUMN_META` (this POC's local
    augmentation, pending docs/decisions/0144).
    """
    meanings = load_format_columns(bundle)
    out: Dict[str, Dict[str, str]] = {}
    for col, (unit, dtype) in TRIAL_COLUMN_META.items():
        out[col] = {
            "meaning": meanings.get(col, ""),
            "unit": unit,
            "dtype": dtype,
        }
    # Any column present in format.json but not in the local table still gets a row
    # (so the dictionary never silently drops a column the rig documented).
    for col, meaning in meanings.items():
        if col not in out:
            out[col] = {"meaning": meaning, "unit": "", "dtype": _STR}
    return out


def trial_dictionary_static() -> Dict[str, Dict[str, str]]:
    """The trial ``{column: {meaning, unit, dtype}}`` from the LOCAL table only.

    Like :func:`trial_dictionary` but without a bundle - meanings are blank (they
    live in the bundle's ``format.json``). Handy for typing a frame when no bundle
    path is at hand.
    """
    return {
        col: {"meaning": "", "unit": unit, "dtype": dtype}
        for col, (unit, dtype) in TRIAL_COLUMN_META.items()
    }


def _simple_dictionary(meta: Dict[str, tuple]) -> Dict[str, Dict[str, str]]:
    """Turn a ``(unit, meaning, dtype)`` table into ``{col: {meaning, unit, dtype}}``."""
    return {
        col: {"meaning": meaning, "unit": unit, "dtype": dtype}
        for col, (unit, meaning, dtype) in meta.items()
    }


def excursion_dictionary() -> Dict[str, Dict[str, str]]:
    """The ``{column: {meaning, unit, dtype}}`` dictionary for the excursion table."""
    return _simple_dictionary(EXCURSION_COLUMN_META)


def tracking_dictionary() -> Dict[str, Dict[str, str]]:
    """The ``{column: {meaning, unit, dtype}}`` dictionary for the tracking table."""
    return _simple_dictionary(TRACKING_COLUMN_META)


def coerce_dtypes(df: pd.DataFrame,
                  dictionary: Dict[str, Dict[str, str]]) -> pd.DataFrame:
    """Return a copy of ``df`` with columns coerced to the dictionary's dtypes.

    Best-effort and non-destructive: only columns present in both ``df`` and the
    dictionary are touched; unparsable cells become NA (numeric/datetime) rather
    than raising. This is what makes the exported Parquet/NWB tables TYPED instead
    of all-string (the gap the design review flags).
    """
    out = df.copy()
    for col, spec in dictionary.items():
        if col not in out.columns:
            continue
        dtype = spec.get("dtype", _STR)
        series = out[col]
        if dtype == _INT:
            out[col] = pd.to_numeric(series, errors="coerce").astype("Int64")
        elif dtype == _NUMERIC:
            out[col] = pd.to_numeric(series, errors="coerce")
        elif dtype == _DATETIME:
            out[col] = pd.to_datetime(series, errors="coerce")
        elif dtype == _BOOL:
            out[col] = (
                series.astype(str).str.strip().str.lower().isin(("1", "true", "yes"))
            )
        else:  # str / id / date / category / filename
            out[col] = series.astype("string")
    return out


def units_map(dictionary: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """A flat ``{column: unit}`` map (handy for NWB ``TimeSeries.unit`` etc.)."""
    return {col: spec.get("unit", "") for col, spec in dictionary.items()}
