"""
maze_state_validate.py - beam-vs-vision agreement + latency harness.

Quantifies how well the vision-derived maze-state transitions (from the pure
maze_state core over a recorded track CSV) agree with the firmware's body-beam
edges (the ground-truth crossing events), and the latency between them, over a
recorded session. This is the acceptance check for docs/decisions/0025: the beam
sensors are non-directional, but they ARE unambiguous crossing events, so they
are the reference the vision transitions are scored against.

Inputs (two CSVs sharing a host wall-clock timeline):
  - --beams : a rig_logger.py session CSV. Each row is
      "<wall_clock_iso>,<ms>,<TYPE>,<payload...>"; body-beam edges are
      "...,S,<i>,<state>" (state 1 = broken). Parsed via protocol.sensor_edge.
  - --track : a tracker track CSV (tracker_view standalone log, columns
      host_unix_s + track_* per tracker.TRACK_COLUMNS; or a camera sidecar).
      Read by header name, tolerating track_cx/cy vs cx/cy naming.

Beam role -> expected vision zone entry (firmware SENSOR_PINS / Beam order, see
firmware/cue-guided-task/rig_config.h): 0 ENTRY->CENTRAL_STEM, 1 EXIT->JUNCTION,
2 LCHOICE->LEFT_CHOICE, 3 RCHOICE->RIGHT_CHOICE, 4 LRETURN->LEFT_RETURN,
5 RRETURN->RIGHT_RETURN. For each beam rising edge we find the nearest vision
"entered == expected zone" transition within +/- window and record the signed
latency; unmatched beams and unmatched vision transitions are counted too. Note
the central reward HUB has no body beam (arrival there is sensed by the lick
port, not a crossing beam), so HUB entries are vision-only and are excluded from
the candidate-false-transition count.

Run from host/:
    python maze_state_validate.py --selftest
    python maze_state_validate.py --beams data/RAT_...csv --track recordings/track_logs/RAT_..._track.csv --frame-size 1280 1024
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import types
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import protocol
from maze_state import (
    HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN,
    RIGHT_RETURN, ZONES_PATH, MazeStateEstimator, MazeStateParams, ZoneMap,
    load_zone_map,
)

# Beam index (firmware Beam order) -> the zone whose ENTRY that beam marks.
BEAM_TO_ZONE = {
    0: CENTRAL_STEM,   # ENTRY   - central stem, lower
    1: JUNCTION,       # EXIT    - central stem upper / T-junction
    2: LEFT_CHOICE,    # LCHOICE
    3: RIGHT_CHOICE,   # RCHOICE
    4: LEFT_RETURN,    # LRETURN
    5: RIGHT_RETURN,   # RRETURN
}
BEAM_LABEL = {0: "ENTRY", 1: "EXIT", 2: "LCHOICE", 3: "RCHOICE",
              4: "LRETURN", 5: "RRETURN"}

DEFAULT_WINDOW_S = 0.75


# ============================== input parsing ================================
def load_beam_events(path: str) -> List[Tuple[float, int]]:
    """Rising body-beam edges as (unix_seconds, beam_index) from a rig_logger CSV."""
    events: List[Tuple[float, int]] = []
    with open(path, "r", encoding="utf-8") as f:
        for row in f:
            row = row.rstrip("\n")
            if not row or row.startswith("#"):
                continue
            # "<wall_clock_iso>,<ms>,<TYPE>,<payload...>": split off the iso stamp,
            # feed the rest (the verbatim firmware line) to the shared parser.
            iso, _, firmware_line = row.partition(",")
            edge = protocol.sensor_edge(firmware_line)
            if edge is None:
                continue
            index, active = edge
            if not active:
                continue  # rising edge (beam broken) is the crossing event
            t = _iso_to_unix(iso)
            if t is not None and index in BEAM_TO_ZONE:
                events.append((t, index))
    events.sort()
    return events


def _iso_to_unix(iso: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(iso.strip()).timestamp()
    except (ValueError, OSError):
        return None


def _get(row: dict, *names: str) -> Optional[str]:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def load_track_rows(path: str, frame_size: Tuple[int, int]
                    ) -> List[Tuple[float, object]]:
    """(unix_seconds, TrackResult-like) rows from a track CSV, sorted by time.

    Reads by header name, preferring smoothed coordinates and tolerating both the
    sidecar ``track_cx``/``track_cy`` and a standalone-log ``cx``/``cy`` naming.
    """
    rows: List[Tuple[float, object]] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            t_s = _get(r, "host_unix_s", "unix_s", "t")
            if t_s is None:
                continue
            try:
                t = float(t_s)
            except ValueError:
                continue
            cx = _get(r, "track_cx", "cx")
            cy = _get(r, "track_cy", "cy")
            sx = _get(r, "track_smooth_cx", "smooth_cx")
            sy = _get(r, "track_smooth_cy", "smooth_cy")
            centroid = _pair(cx, cy)
            smoothed = _pair(sx, sy)
            state = _get(r, "track_state", "state") or "NONE"
            conf_s = _get(r, "track_confidence", "confidence") or "0"
            try:
                conf = float(conf_s)
            except ValueError:
                conf = 0.0
            result = types.SimpleNamespace(
                state=state, confidence=conf, frame_size=frame_size,
                centroid=centroid, smoothed=smoothed)
            rows.append((t, result))
    rows.sort(key=lambda x: x[0])
    return rows


def _pair(a, b) -> Optional[Tuple[float, float]]:
    try:
        return (float(a), float(b))
    except (TypeError, ValueError):
        return None


# ============================== estimator pass ===============================
def vision_transitions(rows: List[Tuple[float, object]], zone_map: ZoneMap,
                       params: Optional[MazeStateParams] = None
                       ) -> List[Tuple[float, str]]:
    """(unix_seconds, entered_zone) for every committed transition over the track."""
    est = MazeStateEstimator(zone_map, params or MazeStateParams())
    out: List[Tuple[float, str]] = []
    for t, result in rows:
        s = est.update(result, t)
        if s.entered is not None:
            out.append((t, s.entered))
    return out


# ================================ alignment ==================================
def align(beam_events: List[Tuple[float, int]],
          transitions: List[Tuple[float, str]],
          window_s: float = DEFAULT_WINDOW_S) -> dict:
    """Match each beam edge to the nearest same-zone vision entry within +/- window.

    Returns a report dict: per-beam counts + signed latencies (vision - beam),
    the overall match rate, and the count of vision entries into a BEAMED zone
    not explained by any beam (candidate false transitions). Entries into zones
    with no beam (the HUB) are vision-only and excluded from that count.
    """
    beamed_zones = set(BEAM_TO_ZONE.values())
    used = [False] * len(transitions)
    per_beam: Dict[int, dict] = {
        i: {"n": 0, "matched": 0, "latencies": []} for i in BEAM_TO_ZONE}

    for bt, bi in beam_events:
        zone = BEAM_TO_ZONE[bi]
        per_beam[bi]["n"] += 1
        best_j = -1
        best_dt = None
        for j, (tt, tz) in enumerate(transitions):
            if used[j] or tz != zone:
                continue
            dt = tt - bt
            if abs(dt) <= window_s and (best_dt is None or abs(dt) < abs(best_dt)):
                best_dt = dt
                best_j = j
        if best_j >= 0:
            used[best_j] = True
            per_beam[bi]["matched"] += 1
            per_beam[bi]["latencies"].append(best_dt)

    unmatched_vision = sum(1 for j, u in enumerate(used)
                           if not u and transitions[j][1] in beamed_zones)
    total_beams = sum(v["n"] for v in per_beam.values())
    total_matched = sum(v["matched"] for v in per_beam.values())
    return {
        "per_beam": per_beam,
        "total_beams": total_beams,
        "total_matched": total_matched,
        "match_rate": (total_matched / total_beams) if total_beams else 0.0,
        "unmatched_vision": unmatched_vision,
        "n_transitions": len(transitions),
        "window_s": window_s,
    }


def format_report(report: dict) -> str:
    lines = []
    lines.append(f"Match window: +/- {report['window_s'] * 1000:.0f} ms")
    lines.append("")
    lines.append(f"{'beam':<9}{'zone':<14}{'n':>4}{'matched':>9}"
                 f"{'rate':>7}{'lat_ms_med':>12}{'lat_ms_mean':>13}{'lat_ms_sd':>11}")
    for bi in sorted(report["per_beam"]):
        v = report["per_beam"][bi]
        n, m, lat = v["n"], v["matched"], v["latencies"]
        rate = (m / n) if n else 0.0
        if lat:
            med = statistics.median(lat) * 1000.0
            mean = statistics.fmean(lat) * 1000.0
            sd = (statistics.pstdev(lat) * 1000.0) if len(lat) > 1 else 0.0
            lat_s = f"{med:>12.0f}{mean:>13.0f}{sd:>11.0f}"
        else:
            lat_s = f"{'-':>12}{'-':>13}{'-':>11}"
        lines.append(f"{BEAM_LABEL[bi]:<9}{BEAM_TO_ZONE[bi]:<14}{n:>4}{m:>9}"
                     f"{rate:>7.2f}{lat_s}")
    lines.append("")
    lines.append(f"overall: {report['total_matched']}/{report['total_beams']} beams "
                 f"matched (rate {report['match_rate']:.2f}); "
                 f"{report['n_transitions']} vision transitions, "
                 f"{report['unmatched_vision']} unmatched by a beam "
                 f"(candidate false transitions)")
    return "\n".join(lines)


# ================================ self-test ==================================
def _selftest_zone_map() -> ZoneMap:
    from test_maze_state import make_zone_map  # reuse the test fixture
    return make_zone_map()


def selftest() -> int:
    """Synthetic end-to-end check: a scripted figure-8 lap + beams at the
    boundary crossings should reconcile with high match rate and small latency.
    The HUB has no beam, so its entries are vision-only (not false transitions)."""
    zm = _selftest_zone_map()
    w, h = 1000, 1000

    # A scripted left lap: dwell in each zone a few ticks so the min-dwell
    # hysteresis commits, sampled at 20 Hz (0.05 s). (zone-center px, n_ticks,
    # beam index at entry or None for the beamless HUB).
    segments = [
        ((500, 900), 6, None),  # HUB (start, in well) - no beam
        ((500, 650), 6, 0),     # CENTRAL_STEM  <- ENTRY
        ((500, 420), 6, 1),     # JUNCTION      <- EXIT
        ((200, 350), 6, 2),     # LEFT_CHOICE   <- LCHOICE
        ((100, 650), 6, 4),     # LEFT_RETURN   <- LRETURN
        ((500, 900), 8, None),  # HUB again (closes the lap) - no beam
    ]
    rows: List[Tuple[float, object]] = []
    beam_events: List[Tuple[float, int]] = []
    t = 1000.0
    for (cx, cy), n, beam in segments:
        if beam is not None:
            # A real beam trips slightly before the centroid is deep in the
            # zone; nudge it 60 ms before the segment start.
            beam_events.append((t - 0.06, beam))
        for _ in range(n):
            rows.append((t, types.SimpleNamespace(
                state="TRACKING", confidence=0.9, frame_size=(w, h),
                centroid=(cx, cy), smoothed=(cx, cy))))
            t += 0.05
    beam_events.sort()

    params = MazeStateParams(min_dwell_ticks=3)
    trans = vision_transitions(rows, zm, params)
    report = align(beam_events, trans, window_s=DEFAULT_WINDOW_S)
    print("[selftest] synthetic left-lap figure-8 traversal")
    print(format_report(report))

    ok = report["match_rate"] >= 0.8 and report["unmatched_vision"] == 0
    print(f"\n[selftest] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


# =================================== main ====================================
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--beams", help="rig_logger session CSV (S,i beam edges)")
    ap.add_argument("--track", help="tracker track CSV (host_unix_s + coords)")
    ap.add_argument("--zones", default=ZONES_PATH,
                    help="zone map JSON (default: host/zones.json)")
    ap.add_argument("--frame-size", type=int, nargs=2, metavar=("W", "H"),
                    help="full-frame pixel size for normalizing track coords")
    ap.add_argument("--window-ms", type=float, default=DEFAULT_WINDOW_S * 1000.0,
                    help=f"match window in ms (default {DEFAULT_WINDOW_S * 1000:.0f})")
    ap.add_argument("--min-dwell", type=int, default=3,
                    help="min-dwell ticks for the estimator (default 3)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in synthetic check and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if not (args.beams and args.track and args.frame_size):
        ap.error("need --beams, --track, and --frame-size (or --selftest)")

    zone_map = load_zone_map(args.zones)
    if not zone_map.valid():
        print(f"[error] no usable zones in {args.zones}; define them in the "
              "Maze state tab first.", file=sys.stderr)
        return 2
    frame_size = (args.frame_size[0], args.frame_size[1])
    beam_events = load_beam_events(args.beams)
    rows = load_track_rows(args.track, frame_size)
    if not rows:
        print(f"[error] no usable track rows in {args.track} (need a host_unix_s "
              "column + coords).", file=sys.stderr)
        return 2
    params = MazeStateParams(min_dwell_ticks=max(1, args.min_dwell))
    trans = vision_transitions(rows, zone_map, params)
    report = align(beam_events, trans, window_s=args.window_ms / 1000.0)
    print(f"beams: {len(beam_events)} rising edges from {args.beams}")
    print(f"track: {len(rows)} rows from {args.track} (frame {frame_size[0]}x{frame_size[1]})")
    print("")
    print(format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
