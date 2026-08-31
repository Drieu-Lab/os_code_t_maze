"""export_parquet.py - PROOF-OF-CONCEPT offline Parquet exporter for a session bundle.

PROOF OF CONCEPT for docs/decisions/0143 (the two-tier data model). Reads ONE
finished session bundle (the plain-text files the rig already wrote) and derives
typed, tidy **Parquet** tables into a separate output folder:

    trials.parquet       one row per attempted trial (typed; keyed session_id/animal/trial)
    excursions.parquet   one row per outbound trip   (when the bundle has an excursion log)
    sessions.parquet     one row: this session's identity + config + husbandry context
    notes.parquet        one row per operator/auto note (when the session has notes)
    tracking.parquet     one row per valid tracked frame (from the video sidecar)
    data_dictionary.json meaning + unit + dtype for every exported column

Why Parquet: it stores each column's TYPE and a units sidecar INSIDE the dataset,
so pooling many sessions is a one-liner in Python (``pandas.read_parquet``) and it
is read natively in MATLAB (``parquetread``) - fixing the "re-parse the wide CSV
and re-type every column in every tool" cost the design review flags. This is a
DERIVED layer: the text bundle stays the source of truth, and re-running this
regenerates the Parquet at any time.

Read-only + offline: it never touches the rig, the live write path, or the source
bundle (it only ever WRITES under ``--out``). It lives in ``tmaze.analysis`` and
imports only the analysis-side contract (``bundle_reader`` / ``trajectories`` /
``phase3_format`` / ``bundle_data_dictionary``), never ``tmaze.core`` /
``tmaze.paths`` (docs/decisions/0142 boundary), so it travels with the analysis
repo at the eventual split (FW-0048).

Run (needs the analysis deps + ``pyarrow``; NOT needed by the live rig tools):

    python -m tmaze.analysis.export_parquet <bundle-dir-or-session-id> [--out DIR]

``pyarrow`` is an OPTIONAL dependency, guarded here with a clear message; the lean
rig environment does not need it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from typing import Dict, Optional

from tmaze.analysis import bundle_data_dictionary as ddict
from tmaze.analysis import bundle_reader, trajectories


def _require_pyarrow() -> None:
    """Exit with a helpful message if the optional ``pyarrow`` dep is absent."""
    if importlib.util.find_spec("pyarrow") is None:
        sys.exit(
            "export_parquet needs the optional 'pyarrow' package (pandas writes "
            "Parquet through it).\n  pip install pyarrow\n"
            "It is an analysis-only dependency and is intentionally NOT installed "
            "in the lean rig environment."
        )


def resolve_bundle(arg: str) -> str:
    """Turn a bundle directory OR a bare session id into an absolute bundle path."""
    if os.path.isdir(arg):
        return os.path.abspath(arg)
    found = trajectories.find_bundle(arg)
    if found is None:
        sys.exit(
            f"no bundle found for '{arg}': pass a bundle directory, or a session id "
            "resolvable under TMAZE_RECORDINGS_DIR / TMAZE_RECORDINGS_ARCHIVES."
        )
    return found


def _session_row(info: bundle_reader.SessionInfo) -> Dict[str, object]:
    """A flat one-row dict for sessions.parquet (drops the nested header dict)."""
    return {
        "session_id": info.session_id,
        "animal": info.animal,
        "date": info.date,
        "start_iso": info.start_iso,
        "gate_arm_iso": info.gate_arm_iso,
        "generator": info.generator,
        "high_tone_is_left": info.high_tone_is_left,
        "high_tone_hz": info.high_tone_hz,
        "low_tone_hz": info.low_tone_hz,
        "n_trials": info.n_trials,
        "stage": info.stage,
        "trainer": info.trainer,
        "pct_baseline": info.pct_baseline,
        "weight_g": info.weight_g,
        "hours_since_water": info.hours_since_water,
        "probe_planned": info.probe_planned,
        "probe_disabled": info.probe_disabled,
        "bundle": info.bundle,
    }


def export_bundle(bundle: str, out_dir: str, *,
                  min_confidence: float = 0.5,
                  include_tracking: bool = True) -> Dict[str, str]:
    """Write the Parquet tables for ``bundle`` into ``out_dir``. Returns a summary."""
    import pandas as pd  # local: analysis-only dep

    loaded = bundle_reader.load_bundle(bundle)
    if loaded is None:
        sys.exit(f"{bundle}: no attempted trials to export (empty/aborted session).")
    trials, excursions, info, notes = loaded

    os.makedirs(out_dir, exist_ok=True)
    written: Dict[str, str] = {}
    dictionaries: Dict[str, Dict[str, Dict[str, str]]] = {}

    # --- trials (typed from the merged data dictionary) ---
    trial_dict = ddict.trial_dictionary(bundle)
    trials_typed = ddict.coerce_dtypes(trials, trial_dict)
    # Drop the *_dt helper columns bundle_reader adds (duplicates of the *_iso ones,
    # now typed as datetime in the export).
    dt_helpers = [c for c in trials_typed.columns if c.endswith("_iso_dt")]
    trials_typed = trials_typed.drop(columns=dt_helpers, errors="ignore")
    _write_parquet(trials_typed, os.path.join(out_dir, "trials.parquet"))
    written["trials"] = f"{len(trials_typed)} rows"
    dictionaries["trials"] = {c: trial_dict[c] for c in trials_typed.columns
                              if c in trial_dict}

    # --- excursions (optional) ---
    exc_dict = ddict.excursion_dictionary()
    if not excursions.empty:
        exc_typed = ddict.coerce_dtypes(excursions, exc_dict)
        _write_parquet(exc_typed, os.path.join(out_dir, "excursions.parquet"))
        written["excursions"] = f"{len(exc_typed)} rows"
        dictionaries["excursions"] = {c: exc_dict[c] for c in exc_typed.columns
                                      if c in exc_dict}
    else:
        written["excursions"] = "skipped (no excursion log in bundle)"

    # --- sessions (one row) ---
    sessions_df = pd.DataFrame([_session_row(info)])
    _write_parquet(sessions_df, os.path.join(out_dir, "sessions.parquet"))
    written["sessions"] = "1 row"

    # --- notes (optional) ---
    if not notes.empty:
        _write_parquet(notes, os.path.join(out_dir, "notes.parquet"))
        written["notes"] = f"{len(notes)} rows"
    else:
        written["notes"] = "skipped (no notes in bundle)"

    # --- tracking (optional; from the video sidecar) ---
    track_dict = ddict.tracking_dictionary()
    if include_tracking:
        try:
            sidecar = trajectories.load_sidecar(bundle, min_confidence=min_confidence)
        except FileNotFoundError:
            written["tracking"] = "skipped (no video sidecar in bundle)"
        else:
            frames = sidecar.frames.copy()
            frames.insert(0, "session_id", info.session_id)
            frames.insert(1, "animal", info.animal)
            track_typed = ddict.coerce_dtypes(frames, track_dict)
            _write_parquet(track_typed, os.path.join(out_dir, "tracking.parquet"))
            written["tracking"] = f"{len(track_typed)} rows"
            dictionaries["tracking"] = {c: track_dict[c] for c in track_typed.columns
                                        if c in track_dict}
    else:
        written["tracking"] = "skipped (--no-tracking)"

    # --- data dictionary companion ---
    dict_path = os.path.join(out_dir, "data_dictionary.json")
    with open(dict_path, "w", encoding="utf-8") as fh:
        json.dump(dictionaries, fh, indent=2, sort_keys=True)
        fh.write("\n")
    written["data_dictionary.json"] = "written"
    return written


def _write_parquet(df, path: str) -> None:
    """Write one DataFrame to Parquet (pyarrow engine), no index column."""
    df.to_parquet(path, index=False)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="PROOF OF CONCEPT (docs/decisions/0143): export one session "
                    "bundle to typed Parquet tables. Read-only; never touches the "
                    "rig or the source bundle.")
    parser.add_argument("bundle",
                        help="bundle directory, or a bare session id resolvable "
                             "under TMAZE_RECORDINGS_DIR / _ARCHIVES")
    parser.add_argument("--out", default=None,
                        help="output directory (default: <session_id>_parquet in "
                             "the current directory)")
    parser.add_argument("--min-confidence", type=float, default=0.5,
                        help="tracker confidence gate for tracking.parquet (0..1)")
    parser.add_argument("--no-tracking", action="store_true",
                        help="skip the per-frame tracking table")
    args = parser.parse_args(argv)

    _require_pyarrow()
    bundle = resolve_bundle(args.bundle)
    session_id = os.path.basename(os.path.normpath(bundle))
    out_dir = args.out or os.path.join(os.getcwd(), f"{session_id}_parquet")

    written = export_bundle(bundle, out_dir,
                            min_confidence=args.min_confidence,
                            include_tracking=not args.no_tracking)

    print(f"Exported {session_id} -> {out_dir}")
    for name, status in written.items():
        print(f"  {name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
