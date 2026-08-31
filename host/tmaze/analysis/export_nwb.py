"""export_nwb.py - PROOF-OF-CONCEPT offline NWB exporter for a session bundle.

PROOF OF CONCEPT for docs/decisions/0143 (the two-tier data model). Reads ONE
finished session bundle and derives a single **NWB** (Neurodata Without Borders)
file - the systems-neuroscience community's standard container for an experiment.
The POC maps the BEHAVIOUR into NWB:

    - session_start_time  <- the bundle manifest's canonical t0 (docs/decisions/0124)
    - a standard TRIALS table  <- the per-trial CSV (start/stop + choice/cue/outcome/reward)
    - a Position SpatialSeries <- the video tracking sidecar (rat centroid over time)
    - cue-onset + reward-lick events <- the trial CSV event stamps

Everything is placed on ONE clock: seconds relative to the session t0, so a future
step can drop the Neuropixels recording into the same file and have spikes align to
the trials table for free. That behaviour+ephys unification, plus the DANDI sharing
story, is why NWB is the recommended ARCHIVAL / interchange target (Python: pynwb;
MATLAB: matnwb). See the design review and docs/decisions/0143.

This is a DERIVED, offline, read-only step: it never touches the rig, the live
write path, or the source bundle (it only WRITES the ``--out`` .nwb file). It lives
in ``tmaze.analysis`` and imports only the analysis-side contract, never
``tmaze.core`` / ``tmaze.paths`` (docs/decisions/0142 boundary).

Run (needs the analysis deps + ``pynwb``; NOT needed by the live rig tools):

    python -m tmaze.analysis.export_nwb <bundle-dir-or-session-id> [--out FILE.nwb]

``pynwb`` (which pulls in HDF5) is an OPTIONAL dependency, guarded here with a clear
message; the lean rig environment does not need it. This exporter is a starting
point, not production - productionizing it + adding ephys/DANDI is FW-0051.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from tmaze.analysis import bundle_data_dictionary as ddict
from tmaze.analysis import bundle_reader, trajectories

# Trial-CSV columns carried into the NWB trials table, split by exported type so
# each NWB column stays type-consistent (a POC keeps most as strings). start/stop
# come from the ISO event stamps (converted to seconds-from-t0), not columns.
_TRIAL_STR_COLS = (
    "correct_arm", "choice_cue", "chosen_arm", "outcome", "sched_state",
    "block_kind", "block_trigger", "block_id", "return_cue_played",
    "return_arm_used", "probe", "probe_trigger", "edit",
)
_TRIAL_NUM_COLS = ("trial", "rewarded", "bias", "alt_rate", "prime_ms",
                   "topup_ms_total")


def _require_pynwb() -> None:
    """Exit with a helpful message if the optional ``pynwb`` dep is absent."""
    if importlib.util.find_spec("pynwb") is None:
        sys.exit(
            "export_nwb needs the optional 'pynwb' package (it pulls in HDF5).\n"
            "  pip install pynwb\n"
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


def _read_manifest_t0(bundle: str) -> Dict[str, object]:
    """The manifest ``t0`` anchor (wall + iso), or ``{}`` for a legacy bundle."""
    session_id = os.path.basename(os.path.normpath(bundle))
    path = os.path.join(bundle, f"session_{session_id}.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return {}
    t0 = obj.get("t0") if isinstance(obj, dict) else None
    return t0 if isinstance(t0, dict) else {}


def _session_start(t0: Dict[str, object], info: bundle_reader.SessionInfo) -> datetime:
    """A timezone-aware session_start_time from the manifest t0 (or the CSV anchor)."""
    iso = str(t0.get("iso") or info.start_iso or "")
    if iso:
        try:
            ts = datetime.fromisoformat(iso)
            return ts if ts.tzinfo else ts.astimezone()  # naive -> local tz
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _t0_wall(t0: Dict[str, object], info: bundle_reader.SessionInfo) -> float:
    """The epoch-seconds zero for relative timing (manifest t0.wall, else t0 ISO)."""
    wall = t0.get("wall")
    if isinstance(wall, (int, float)):
        return float(wall)
    # Legacy bundle: fall back to converting the ISO anchor the same way the
    # tracking sidecar's host_unix_s is read (local naive -> POSIX; ADR-0128).
    return trajectories.iso_to_unix(str(t0.get("iso") or info.start_iso or ""))


def _num(value) -> float:
    """Cell -> float, with pandas NA / blank -> nan (kept import-light)."""
    import pandas as pd
    if value is None or (isinstance(value, float) and value != value):
        return float("nan")
    if pd.isna(value):
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _str(value) -> str:
    """Cell -> string, with pandas NA / None -> empty string."""
    import pandas as pd
    if value is None or pd.isna(value):
        return ""
    return str(value)


def build_nwb(bundle: str):
    """Build (but do not write) an NWBFile from ``bundle``. Returns the NWBFile."""
    import numpy as np
    from pynwb import NWBFile
    from pynwb.behavior import Position, SpatialSeries

    loaded = bundle_reader.load_bundle(bundle)
    if loaded is None:
        sys.exit(f"{bundle}: no attempted trials to export (empty/aborted session).")
    trials, _excursions, info, _notes = loaded

    t0 = _read_manifest_t0(bundle)
    start_time = _session_start(t0, info)
    zero = _t0_wall(t0, info)

    nwb = NWBFile(
        session_description=f"T-maze cue-guided Phase 3 session {info.session_id}",
        identifier=info.session_id,
        session_start_time=start_time,
        session_id=info.session_id,
        experimenter=[info.trainer] if info.trainer else None,
        lab="Drieu lab",
        institution="Johns Hopkins University",
        experiment_description=(
            "Auditory-cue T-maze: cue -> left/right choice -> reward "
            "(see docs/design/data-format-review.md; ADR-0143 POC export)."
        ),
    )
    from pynwb.file import Subject
    nwb.subject = Subject(subject_id=info.animal, species="Rattus norvegicus")

    _add_trials(nwb, trials, bundle, zero)
    _add_behavior(nwb, bundle, info, zero, np, Position, SpatialSeries)
    _add_trial_events(nwb, trials, zero)
    return nwb


def _add_trials(nwb, trials, bundle: str, zero: float) -> None:
    """Populate the standard NWB trials table from the per-trial CSV."""
    import math

    trial_dict = ddict.trial_dictionary(bundle)

    def _desc(col: str) -> str:
        meaning = trial_dict.get(col, {}).get("meaning", "")
        return meaning or col

    for col in _TRIAL_STR_COLS + _TRIAL_NUM_COLS:
        nwb.add_trial_column(name=col, description=_desc(col))

    start_unix = trajectories.iso_series_to_unix(trials["trial_start_iso"])
    stop_iso = trials["trial_end_iso"] if "trial_end_iso" in trials.columns \
        else trials["trial_start_iso"]
    stop_unix = trajectories.iso_series_to_unix(stop_iso)

    skipped = 0
    for i in range(len(trials)):
        s = start_unix.iloc[i]
        if s != s:  # NaN start: cannot place on the timeline
            skipped += 1
            continue
        e = stop_unix.iloc[i]
        start_s = float(s) - zero
        stop_s = (float(e) - zero) if (e == e) else start_s
        if stop_s < start_s:
            stop_s = start_s
        row = {c: _str(trials.iloc[i][c]) for c in _TRIAL_STR_COLS
               if c in trials.columns}
        for c in _TRIAL_NUM_COLS:
            if c in trials.columns:
                v = _num(trials.iloc[i][c])
                row[c] = v if not math.isnan(v) else float("nan")
        # Any declared column missing from this (older) CSV: fill a neutral value.
        for c in _TRIAL_STR_COLS:
            row.setdefault(c, "")
        for c in _TRIAL_NUM_COLS:
            row.setdefault(c, float("nan"))
        nwb.add_trial(start_time=start_s, stop_time=stop_s, **row)
    if skipped:
        print(f"  note: {skipped} trial(s) had no start stamp and were skipped")


def _add_behavior(nwb, bundle, info, zero, np, Position, SpatialSeries) -> None:
    """Add the rat position SpatialSeries from the video tracking sidecar (if any)."""
    try:
        sidecar = trajectories.load_sidecar(bundle)
    except FileNotFoundError:
        return
    frames = sidecar.frames
    if frames.empty:
        return
    t = frames["host_unix_s"].to_numpy(dtype=float) - zero
    xy = np.column_stack([
        frames["track_smooth_cx"].to_numpy(dtype=float),
        frames["track_smooth_cy"].to_numpy(dtype=float),
    ])
    pos = SpatialSeries(
        name="position",
        description="Kalman-smoothed rat centroid (undistorted full-frame pixels).",
        data=xy,
        unit="pixels",
        reference_frame="top-left of the undistorted camera frame; +x right, +y down",
        timestamps=t,
    )
    behavior = nwb.create_processing_module(
        name="behavior", description="Behavioural tracking + task events (POC).")
    behavior.add(Position(spatial_series=pos))


def _add_trial_events(nwb, trials, zero: float) -> None:
    """Add cue-onset and reward-lick point events (with a text label per event).

    Prefers the modern NWB ``EventsTable`` (a table with a ``timestamp`` column +
    an ``annotation`` label column); falls back to the older ``AnnotationSeries``
    on a pynwb that lacks ``create_events_table``. AnnotationSeries is deprecated in
    recent pynwb, so the EventsTable path is the forward-compatible one.
    """
    use_events_table = hasattr(nwb, "create_events_table")
    _add_events(nwb, trials, "choice_cue_onset",
                "Choice-cue onset; annotation = tone (HIGH/LOW).",
                "choice_cue_on_iso", "choice_cue", zero, use_events_table)
    _add_events(nwb, trials, "reward_licks",
                "Reward-collection lick; annotation = trial outcome.",
                "lick_time_iso", "outcome", zero, use_events_table)


def _event_rows(trials, iso_col, label_col, zero):
    """(times, labels) for an event, time-sorted; empty when the column is absent."""
    if iso_col not in trials.columns:
        return [], []
    times: List[float] = []
    labels: List[str] = []
    unix = trajectories.iso_series_to_unix(trials[iso_col])
    for i in range(len(trials)):
        u = unix.iloc[i]
        if u != u:  # NaN: no such event this trial
            continue
        times.append(float(u) - zero)
        labels.append(_str(trials.iloc[i][label_col]) if label_col in trials.columns
                      else "")
    order = sorted(range(len(times)), key=lambda k: times[k])
    return [times[k] for k in order], [labels[k] for k in order]


def _add_events(nwb, trials, name, description, iso_col, label_col, zero,
                use_events_table) -> None:
    """One event series/table from an ISO event column + a label column."""
    times, labels = _event_rows(trials, iso_col, label_col, zero)
    if not times:
        return
    if use_events_table:
        table = nwb.create_events_table(name=name, description=description)
        table.add_column(name="annotation",
                         description="text label for each event")
        for t, label in zip(times, labels):
            table.add_row(timestamp=float(t), annotation=str(label))
        return
    # Fallback for older pynwb: an AnnotationSeries under a behavior module.
    from pynwb.misc import AnnotationSeries
    behavior = nwb.processing.get("behavior")
    if behavior is None:
        behavior = nwb.create_processing_module(
            name="behavior", description="Behavioural task events (POC).")
    behavior.add(AnnotationSeries(name=name, description=description,
                                  data=labels, timestamps=times))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="PROOF OF CONCEPT (docs/decisions/0143): export one session "
                    "bundle to an NWB file (behaviour: trials table + position + "
                    "events). Read-only; never touches the rig or the source bundle.")
    parser.add_argument("bundle",
                        help="bundle directory, or a bare session id resolvable "
                             "under TMAZE_RECORDINGS_DIR / _ARCHIVES")
    parser.add_argument("--out", default=None,
                        help="output .nwb path (default: <session_id>.nwb in the "
                             "current directory)")
    args = parser.parse_args(argv)

    _require_pynwb()
    from pynwb import NWBHDF5IO

    bundle = resolve_bundle(args.bundle)
    session_id = os.path.basename(os.path.normpath(bundle))
    out_path = args.out or os.path.join(os.getcwd(), f"{session_id}.nwb")

    nwb = build_nwb(bundle)
    with NWBHDF5IO(out_path, "w") as io:
        io.write(nwb)

    n_trials = len(nwb.trials) if nwb.trials is not None else 0
    n_events = len(getattr(nwb, "events", {}) or {})
    bits = []
    if "behavior" in nwb.processing:
        bits.append("position")
    if n_events:
        bits.append(f"{n_events} event table(s)")
    print(f"Exported {session_id} -> {out_path}")
    print(f"  trials: {n_trials} rows")
    print(f"  behaviour: {', '.join(bits) if bits else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
