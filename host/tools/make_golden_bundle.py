"""make_golden_bundle.py - regenerate the committed GOLDEN session bundle.

The golden bundle is a tiny, complete Phase 3 session written by the REAL rig
write path, committed under ``host/tests/fixtures/`` and copied into the analysis
repo (``drieu_lab_analysis``) at the split (see FW-0048 / FW-0050). It is the
shared, versioned reference both repos test their half of the on-disk contract
against:

- the analysis repo loads it through ``bundle_reader`` and round-trips it through
  the Parquet / NWB exporters (the drift tripwire that replaced the same-repo
  ``TestCoreParity`` when the analysis package moved out);
- this rig repo keeps a writer-diff test (``test_golden_bundle.py``) that re-runs
  THIS generator and diffs its output against the committed copy, so a change to
  the writer's format shows up as a fixture diff - a reminder to bump
  ``bundle_format.FORMAT_VERSION`` and re-copy the fixture to the analysis repo.

Everything here is HARD-CODED (session id, seed, timestamps, sidecar rows) so a
re-run is byte-identical on any machine / timezone - otherwise the writer-diff
test would be noisy. The trial CSV comes from ``Phase3Log.render_csv()`` and the
format manifest from ``bundle_format.format_manifest()`` (the real writers); only
the zone map and video sidecar are small fixed synthetic stand-ins.

Run (rig conda env ``drieu_t_maze``, from the repo root):

    python -m tools.make_golden_bundle                 # writes host/tests/fixtures/<sid>/
    python -m tools.make_golden_bundle --out DIR       # writes DIR/<sid>/

See PROGRAMS.md.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict

from tmaze.core import maze_state
from tmaze.core.phase3 import bundle_format
from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.trial_sequence import LEFT, TrialConfig, TrialSequence
from tmaze.core.session.session_log import SessionManifest

# --- fixed identity + anchor (never derive from the wall clock: keep it stable) --
SID = "600_20260101_120000"
ANIMAL = "600"
SEED = 20260830
N_TRIALS = 6
# Fixed firmware serial proto for the fixture: a current (v8) session, so
# format.json carries sensor_polarity = "corrected" (see docs/decisions/0164).
FIRMWARE_PROTO = 8
# Offset-aware ISO stamps (format v4, ADR-0182). The fixture is dated January, so
# the rig's local offset (America/New_York) is EST = -05:00; a v4 bundle carries
# the offset so the analysis mirror parses it natively (no assume_tz needed).
TZ_OFFSET = "-05:00"
SESSION_START_ISO = f"2026-01-01T11:59:55.000{TZ_OFFSET}"   # gate-arm (secondary event)
T0: Dict[str, Any] = {                              # canonical session anchor (ADR-0124)
    "wall": 1000000.0,
    "mono": 2.0,
    "iso": f"2026-01-01T12:00:00.000{TZ_OFFSET}",
}
# Fixed cue playback calibration for the fixture's trial-CSV header (P3, ADR-0182):
# the HIGH/LOW tones as played (commanded Hz + gain %). Fixed, never read live.
CUE_CALIBRATION: Dict[str, Any] = {
    "high_cmd_hz": 8000, "high_gain_pct": 46,
    "low_cmd_hz": 4000, "low_gain_pct": 10,
}
# Fixed provenance stub for the fixture (manifest schema v3, docs/decisions/0180):
# NEVER collect live git/package/host values here - they would make the fixture
# non-reproducible. Mirrors the shape of tmaze.core.provenance.collect().
PROVENANCE: Dict[str, Any] = {
    "code_commit": "0000000",
    "python_version": "3.13.0",
    "host_name": "golden-fixture",
    "packages": {"tmaze": "0.0.0", "numpy": "0.0.0"},
    "firmware_proto_source": FIRMWARE_PROTO,
    "firmware_proto_seen": FIRMWARE_PROTO,
    # Resolved per-animal cue->side mapping + its source (ADR-0186); "config"
    # here matches the phase3 header's default source for this fixture.
    "cue_mapping": {"high_tone_is_left": True, "source": "config"},
}

# A small but realistic zone map (normalised polygons). Stands in for the live
# host/zones.json so the fixture is self-contained + deterministic; ``version``
# tracks the live schema so the reader's zones-version guard is exercised.
_ZONES: Dict[str, Any] = {
    "version": maze_state.ZONES_VERSION,
    "zones": [
        {"name": "CENTRAL_STEM",
         "polygon_norm": [[0.44, 0.34], [0.54, 0.33], [0.53, 0.59], [0.46, 0.59]]},
        {"name": "JUNCTION",
         "polygon_norm": [[0.45, 0.18], [0.44, 0.34], [0.54, 0.33], [0.54, 0.19]]},
        {"name": "LEFT_CHOICE",
         "polygon_norm": [[0.28, 0.18], [0.45, 0.18], [0.44, 0.34], [0.27, 0.34]]},
        {"name": "RIGHT_CHOICE",
         "polygon_norm": [[0.54, 0.19], [0.71, 0.19], [0.72, 0.35], [0.54, 0.33]]},
        {"name": "HUB",
         "polygon_norm": [[0.39, 0.64], [0.59, 0.63], [0.58, 0.88], [0.41, 0.89]]},
    ],
    "well_norm": [[0.46, 0.78], [0.54, 0.78], [0.54, 0.85], [0.46, 0.85]],
    "stem_axis": {"base": [0.5, 0.85], "tip": [0.5, 0.2]},
}

# A short fixed video sidecar (one invalid ACQUIRING row among valid TRACKING
# frames) so the tracking table / NWB position series have real content.
_SIDECAR = (
    "# animal: 600\n"
    "# session_id: 600_20260101_120000\n"
    "# width: 1280\n"
    "# height: 1024\n"
    "# start_wall_clock: 2026-01-01 12:00:00\n"
    "frame_idx,host_unix_s,track_state,track_smooth_cx,track_smooth_cy,"
    "track_confidence\n"
    "0,1000000.10,TRACKING,640.0,700.0,0.95\n"
    "1,1000000.20,TRACKING,642.0,560.0,0.94\n"
    "2,1000000.30,ACQUIRING,,,0.0\n"
    "3,1000000.40,TRACKING,648.0,300.0,0.90\n"
    "4,1000000.50,TRACKING,520.0,260.0,0.88\n"
)


def _iso(trial: int, frac: str) -> str:
    """A fixed OFFSET-AWARE ISO stamp for trial ``trial`` (0-based) within minute
    12:00 (format v4, ADR-0182)."""
    return f"2026-01-01T12:00:{trial:02d}.{frac}{TZ_OFFSET}"


def build_log() -> Phase3Log:
    """Drive the real Phase 3 writer through ``N_TRIALS`` completed, rewarded trials."""
    seq = TrialSequence(
        TrialConfig(max_trials=N_TRIALS, block_size=N_TRIALS, max_same_run=3),
        seed=SEED,
    )
    log = Phase3Log(seq, animal=ANIMAL, session_start_iso=SESSION_START_ISO,
                    session_id=SID, session_t0=T0, reward_pulse_ms=500,
                    cue_calibration=CUE_CALIBRATION)
    for i in range(N_TRIALS):
        side = "left" if seq.correct_arm(i) == LEFT else "right"
        log.trial_started(i, _iso(i, "000"))
        log.choice_cue_started(i, _iso(i, "100"))
        log.cue_acked(i, 12100 + i)                              # firmware CUE,ON echo ms
        log.choice_cue_stopped(i, _iso(i, "200"))
        log.choice_scored(i, side, "CORRECT", _iso(i, "500"))   # always correct
        log.reward_fired(i, _iso(i, "550"), 500)
        log.reward_acked(i, 12550 + i)                           # firmware SOL/REWARD echo ms
        log.lick(_iso(i, "600"), 12300 + i)
        log.trial_ended(i, _iso(i, "900"))
    return log


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _write_json(path: str, obj: Any) -> None:
    _write(path, json.dumps(obj, indent=2) + "\n")


def build_bundle(out_root: str) -> str:
    """Write the golden bundle under ``out_root/<sid>/`` and return the bundle dir."""
    bundle = os.path.join(out_root, SID)
    _write(os.path.join(bundle, "task", f"phase3_{SID}.csv"), build_log().render_csv())
    _write_json(os.path.join(bundle, "config", "format.json"),
                bundle_format.format_manifest(firmware_proto=FIRMWARE_PROTO))
    _write_json(os.path.join(bundle, "config", "zones.json"), _ZONES)
    _write_json(os.path.join(bundle, "config", "session_meta.json"),
                {"trainer": "RJ", "stage": "Phase 3", "schema_version": 1})
    manifest = SessionManifest(
        session_id=SID, animal=ANIMAL, t0=T0,
        streams={
            "phase3_task": f"task/phase3_{SID}.csv",
            "camera_video": f"video/{SID}.mp4",
            "camera_sidecar": f"video/{SID}.csv",
        },
        provenance=PROVENANCE,
    )
    _write(os.path.join(bundle, f"session_{SID}.json"), manifest.render())
    _write(os.path.join(bundle, "video", f"{SID}.csv"), _SIDECAR)
    return bundle


def _default_out() -> str:
    """Rig-repo default: host/tests/fixtures (this file is host/tools/…)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "tests", "fixtures")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", default=None,
                        help="output root (a <sid>/ dir is created inside); "
                             "default host/tests/fixtures/")
    args = parser.parse_args(argv)
    bundle = build_bundle(args.out or _default_out())
    print(f"Wrote golden bundle -> {bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
