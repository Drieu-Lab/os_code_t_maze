"""
test_analysis_exporters.py - smoke tests for the offline interchange exporters.

Covers the PROOF-OF-CONCEPT exporters (docs/decisions/0143):
``tmaze.analysis.export_parquet`` and ``tmaze.analysis.export_nwb``, plus the
shared ``bundle_data_dictionary`` (docs/decisions/0144 POC). Builds a tiny
synthetic bundle (a ``task/`` CSV + a ``config/format.json`` + a ``session_<id>``
manifest with a ``t0`` anchor + a small video sidecar) in a temp dir and round-
trips it through each exporter, asserting the derived files exist, are TYPED, and
have the expected row counts.

The exporters lean on OPTIONAL deps (``pyarrow`` / ``pynwb``) that the lean rig env
does not carry, so those tests SKIP cleanly when the dep is absent. The data-
dictionary + dtype-coercion tests need only ``pandas`` (already an analysis dep).

Run from host/tests:  python -m unittest test_analysis_exporters
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

_HAS_PYARROW = importlib.util.find_spec("pyarrow") is not None
_HAS_PYNWB = importlib.util.find_spec("pynwb") is not None

_HEADER = (
    "# phase3_session animal=600 session_id={sid} seed=42 proto=10\n"
    "# high_tone_is_left=True high_tone_hz=8000 low_tone_hz=4000\n"
    "# generator=balanced\n"
    "# session_t0_iso=2026-01-01T12:00:00.000 session_t0_wall=1000000.0"
    " session_t0_mono=2.0\n"
    "# session_start_iso=2026-01-01T11:59:55.000\n"
)
_COLS = (
    "trial,correct_arm,choice_cue,return_cue_left,return_cue_right,chosen_arm,"
    "outcome,rewarded,topups,return_arm_used,return_cue_played,trial_start_iso,"
    "choice_cue_on_iso,choice_time_iso,reward_cmd_iso,return_cue_on_iso,"
    "return_cue_off_iso,trial_end_iso,lick_time_iso,lick_fw_ms,sched_state,"
    "block_id,block_trigger,bias,block_kind,alt_rate\n"
)


def _row(trial):
    """One completed trial with start/choice/end + a reward lick stamp."""
    hh, mm = 12, 0
    start = f"2026-01-01T{hh:02d}:{mm:02d}:{trial:02d}.000"
    choice = f"2026-01-01T{hh:02d}:{mm:02d}:{trial:02d}.500"
    end = f"2026-01-01T{hh:02d}:{mm:02d}:{trial + 1:02d}.000"
    lick = f"2026-01-01T{hh:02d}:{mm:02d}:{trial:02d}.600"
    return (
        f"{trial},LEFT,HIGH,HIGH,LOW,LEFT,CORRECT,1,,LEFT,HIGH,"
        f"{start},{start},{choice},{choice},,,{end},{lick},5000{trial},NORMAL,"
        f",,0.0,,0.0\n"
    )


_SIDECAR = (
    "# animal: 600\n"
    "# session_id: {sid}\n"
    "# width: 1280\n"
    "# height: 1024\n"
    "# start_wall_clock: 2026-01-01 12:00:00\n"
    "frame_idx,host_unix_s,track_state,track_smooth_cx,track_smooth_cy,"
    "track_confidence\n"
    "0,1000000.10,TRACKING,640.0,512.0,0.9\n"
    "1,1000000.20,TRACKING,650.0,520.0,0.9\n"
    "2,1000000.30,ACQUIRING,,,0.0\n"
    "3,1000000.40,TRACKING,660.0,530.0,0.8\n"
)


def _make_bundle(root, sid="600_20260101_120000", n=4):
    bundle = os.path.join(root, sid)
    os.makedirs(os.path.join(bundle, "task"))
    os.makedirs(os.path.join(bundle, "config"))
    os.makedirs(os.path.join(bundle, "video"))
    with open(os.path.join(bundle, f"session_{sid}.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "session_id": sid, "animal": "600",
            "t0": {"wall": 1000000.0, "mono": 2.0, "iso": "2026-01-01T12:00:00.000"},
            "schema_version": 2,
        }, fh)
    with open(os.path.join(bundle, "config", "format.json"), "w",
              encoding="utf-8") as fh:
        json.dump({
            "format_version": 1, "proto": 10,
            "columns": {"trial": "1-based trial index within the session",
                        "bias": "signed directional-error bias at this trial"},
        }, fh)
    with open(os.path.join(bundle, "config", "session_meta.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"trainer": "RJ", "stage": "Phase 3", "schema_version": 1}, fh)
    with open(os.path.join(bundle, "task", f"phase3_{sid}.csv"), "w",
              encoding="utf-8") as fh:
        fh.write(_HEADER.format(sid=sid))
        fh.write(_COLS)
        for t in range(1, n + 1):
            fh.write(_row(t))
    with open(os.path.join(bundle, "video", f"{sid}.csv"), "w",
              encoding="utf-8") as fh:
        fh.write(_SIDECAR.format(sid=sid))
    return bundle


class TestDataDictionary(unittest.TestCase):
    """The POC data dictionary (docs/decisions/0144): meaning from format.json,
    units/dtype from the local table, and dtype coercion."""

    def test_trial_dictionary_merges_meaning_and_units(self):
        from tmaze.analysis import bundle_data_dictionary as ddict
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            d = ddict.trial_dictionary(bundle)
            # meaning came from config/format.json; unit/dtype from the local table
            self.assertEqual(d["trial"]["meaning"],
                             "1-based trial index within the session")
            self.assertEqual(d["trial"]["dtype"], "int")
            self.assertEqual(d["trial_start_iso"]["unit"], "iso8601_local")
            self.assertEqual(d["trial_start_iso"]["dtype"], "datetime")
            self.assertEqual(d["lick_fw_ms"]["unit"], "ms_firmware_clock")

    def test_coerce_dtypes_types_columns(self):
        import pandas as pd
        from tmaze.analysis import bundle_data_dictionary as ddict
        df = pd.DataFrame({
            "trial": ["1", "2"],
            "rewarded": ["1", "0"],
            "trial_start_iso": ["2026-01-01T12:00:01", ""],
            "bias": ["0.5", "x"],
        })
        typed = ddict.coerce_dtypes(df, ddict.trial_dictionary_static())
        self.assertEqual(str(typed["trial"].dtype), "Int64")
        self.assertEqual(typed["rewarded"].dtype, bool)
        self.assertTrue(str(typed["trial_start_iso"].dtype).startswith("datetime"))
        self.assertTrue(pd.isna(typed["bias"].iloc[1]))   # unparsable -> NA


@unittest.skipUnless(_HAS_PYARROW, "pyarrow not installed (optional export dep)")
class TestExportParquet(unittest.TestCase):
    def test_exports_typed_tables(self):
        import pandas as pd
        from tmaze.analysis import export_parquet
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root, n=4)
            out = os.path.join(root, "out")
            written = export_parquet.export_bundle(bundle, out)
            self.assertIn("trials", written)

            trials = pd.read_parquet(os.path.join(out, "trials.parquet"))
            self.assertEqual(len(trials), 4)
            self.assertEqual(str(trials["trial"].dtype), "Int64")
            self.assertEqual(trials["rewarded"].dtype, bool)
            self.assertTrue(
                str(trials["trial_start_iso"].dtype).startswith("datetime"))

            self.assertTrue(os.path.isfile(os.path.join(out, "sessions.parquet")))
            self.assertTrue(os.path.isfile(os.path.join(out, "tracking.parquet")))
            self.assertTrue(os.path.isfile(os.path.join(out, "data_dictionary.json")))

            tracking = pd.read_parquet(os.path.join(out, "tracking.parquet"))
            self.assertEqual(len(tracking), 3)   # 3 valid TRACKING frames

    def test_never_writes_into_source_bundle(self):
        from tmaze.analysis import export_parquet
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            before = _tree(bundle)
            out = os.path.join(root, "out")
            export_parquet.export_bundle(bundle, out)
            self.assertEqual(before, _tree(bundle))   # bundle untouched


@unittest.skipUnless(_HAS_PYNWB, "pynwb not installed (optional export dep)")
class TestExportNwb(unittest.TestCase):
    def test_builds_and_writes_nwb(self):
        from pynwb import NWBHDF5IO
        from tmaze.analysis import export_nwb
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root, n=4)
            nwb = export_nwb.build_nwb(bundle)
            self.assertEqual(nwb.session_id, "600_20260101_120000")
            self.assertEqual(len(nwb.trials), 4)
            # position added from the sidecar; trial start relative to t0 (>= 0)
            self.assertIn("behavior", nwb.processing)
            self.assertGreaterEqual(float(nwb.trials["start_time"][0]), 0.0)
            # cue/lick events: modern pynwb uses EventsTable (nwb.events), older
            # pynwb falls back to AnnotationSeries under the behavior module.
            if hasattr(nwb, "create_events_table"):
                self.assertIn("choice_cue_onset", nwb.events)
                self.assertIn("reward_licks", nwb.events)

            out = os.path.join(root, "s.nwb")
            with NWBHDF5IO(out, "w") as io:
                io.write(nwb)
            self.assertTrue(os.path.isfile(out))
            with NWBHDF5IO(out, "r") as io:
                read = io.read()
                self.assertEqual(len(read.trials), 4)


def _tree(path):
    """A set of relative file paths under ``path`` (to detect any bundle mutation)."""
    out = set()
    for base, _dirs, files in os.walk(path):
        for f in files:
            out.add(os.path.relpath(os.path.join(base, f), path))
    return out


if __name__ == "__main__":
    unittest.main()
