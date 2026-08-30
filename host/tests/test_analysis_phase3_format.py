"""Headless tests for tmaze.analysis.phase3_format - the analysis-side data contract.

This suite has two jobs:

1. **Parity / drift guard (TEMPORARY).** While the analysis package still lives in
   the same repo as the rig core, assert its vendored copy of the on-disk format
   (tokens, the zone classifier, the notes parser) still matches ``tmaze.core``
   exactly. When the analysis layer is split into its own repo, ``tmaze.core`` is
   no longer importable there, so delete ``TestCoreParity`` at that point (the
   bundle's ``config/format.json`` + version guards become the sole contract).
2. **Behaviour** of the version guards + recordings-root resolver, which have no
   core counterpart.

Run from host/:  python -m unittest test_analysis_phase3_format
"""

import json
import os
import tempfile
import unittest

from tmaze.analysis import phase3_format as pf
from tmaze.core import maze_state
from tmaze.core.phase3 import bundle_format, phase3_log
from tmaze.core.phase3 import phase3_task, trial_sequence
from tmaze.core.session import session_notes

# A representative zones.json dict (two zones + a well + a stem axis).
_ZONES_DICT = {
    "version": 1,
    "zones": [
        {"name": "CENTRAL_STEM",
         "polygon_norm": [[0.44, 0.34], [0.54, 0.33], [0.53, 0.59], [0.46, 0.59]]},
        {"name": "JUNCTION",
         "polygon_norm": [[0.45, 0.18], [0.44, 0.34], [0.54, 0.33], [0.54, 0.19]]},
        {"name": "HUB",
         "polygon_norm": [[0.39, 0.64], [0.59, 0.63], [0.58, 0.88], [0.41, 0.89]]},
    ],
    "well_norm": [[0.46, 0.78], [0.54, 0.78], [0.54, 0.85], [0.46, 0.85]],
    "stem_axis": {"base": [0.5, 0.85], "tip": [0.5, 0.2]},
}


class TestCoreParity(unittest.TestCase):
    """DELETE this class when the analysis package moves to its own repo."""

    def test_outcome_tokens_match_core(self):
        self.assertEqual(pf.CORRECT, phase3_task.CORRECT)
        self.assertEqual(pf.INCORRECT, phase3_task.INCORRECT)
        self.assertEqual(pf.ABANDONED, phase3_task.ABANDONED)

    def test_cue_tokens_match_core(self):
        self.assertEqual(pf.HIGH, trial_sequence.HIGH)
        self.assertEqual(pf.LOW, trial_sequence.LOW)

    def test_zone_vocabulary_matches_core(self):
        self.assertEqual(pf.EDITABLE_ZONES, maze_state.EDITABLE_ZONES)
        self.assertEqual(pf.ZONES_VERSION, maze_state.ZONES_VERSION)
        for name in ("HUB", "CENTRAL_STEM", "JUNCTION", "LEFT_CHOICE",
                     "RIGHT_CHOICE", "LEFT_RETURN", "RIGHT_RETURN"):
            self.assertEqual(getattr(pf, name), getattr(maze_state, name))

    def test_classify_point_matches_core_on_grid(self):
        mine = pf.ZoneMap.from_dict(_ZONES_DICT)
        theirs = maze_state.ZoneMap.from_dict(_ZONES_DICT)
        n = 40
        for i in range(n + 1):
            for j in range(n + 1):
                x, y = i / n, j / n
                self.assertEqual(mine.classify_point(x, y),
                                 theirs.classify_point(x, y),
                                 msg=f"mismatch at ({x:.3f}, {y:.3f})")

    def test_stem_progress_and_well_match_core(self):
        mine = pf.ZoneMap.from_dict(_ZONES_DICT)
        theirs = maze_state.ZoneMap.from_dict(_ZONES_DICT)
        for x, y in [(0.5, 0.3), (0.5, 0.6), (0.5, 0.85), (0.48, 0.81)]:
            self.assertEqual(mine.stem_progress(x, y), theirs.stem_progress(x, y))
            self.assertEqual(mine.in_well(x, y), theirs.in_well(x, y))

    def test_auto_tag_constants_match_core(self):
        self.assertEqual(pf.AUTO_PROBE_DISABLED, session_notes.AUTO_PROBE_DISABLED)
        self.assertEqual(pf.OPERATOR_SOURCE, session_notes.OPERATOR_SOURCE)
        self.assertEqual(pf.AUTO_SOURCE, session_notes.AUTO_SOURCE)

    def test_notes_parser_matches_core(self):
        log = session_notes.NoteLog()
        log.add("started", iso="2026-08-27T15:00:00", mono=1.0, trial_index=1)
        log.add_auto(session_notes.AUTO_PROBE_DISABLED, "probes off",
                     iso="2026-08-27T15:01:00", trial_index=3)
        text = log.render_jsonl()
        # Add a blank line, a corrupt line, and a no-id line: all skipped.
        text = text + "\n{ not json\n" + json.dumps({"iso": "x"}) + "\n"
        want = [e.as_dict() for e in session_notes.parse_jsonl(text).entries()]
        self.assertEqual(pf.parse_notes_jsonl(text), want)

    def test_manifest_enumerations_match_mirror(self):
        man = bundle_format.format_manifest()
        self.assertEqual(tuple(man["outcome_labels"]), pf.OUTCOME_LABELS)
        self.assertEqual(tuple(man["cue_labels"]), pf.CUE_LABELS)
        self.assertEqual(tuple(man["arm_labels"]), pf.ARM_LABELS)
        self.assertEqual(tuple(man["zone_names"]), pf.ZONE_NAMES)
        self.assertEqual(man["format_version"], pf.FORMAT_VERSION)
        self.assertEqual(bundle_format.FORMAT_VERSION, pf.FORMAT_VERSION)
        self.assertEqual(man["proto"], phase3_log.PROTO_VERSION)
        self.assertEqual(list(man["columns"].keys()), list(phase3_log.COLUMNS))


def _make_bundle(root, *, proto=10, zones_version=1, format_manifest=None):
    """Create a minimal bundle dir with a trial CSV + zones.json (+ optional format)."""
    sid = "600_20260101_000000"
    bundle = os.path.join(root, sid)
    os.makedirs(os.path.join(bundle, "task"))
    os.makedirs(os.path.join(bundle, "config"))
    with open(os.path.join(bundle, "task", f"phase3_{sid}.csv"), "w",
              encoding="utf-8") as fh:
        fh.write(f"# phase3_session animal=600 session_id={sid} seed=1 "
                 f"proto={proto}\n")
        fh.write("trial,outcome\n")
        fh.write("1,CORRECT\n")
    with open(os.path.join(bundle, "config", "zones.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"version": zones_version, "zones": []}, fh)
    if format_manifest is not None:
        with open(os.path.join(bundle, "config", "format.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(format_manifest, fh)
    return bundle


class TestVersionGuards(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_legacy_bundle_without_manifest_is_allowed(self):
        bundle = _make_bundle(self.root)  # no format.json
        info = pf.assert_supported(bundle)
        self.assertEqual(info.source, "inferred")
        self.assertIsNone(info.format_version)
        self.assertEqual(info.proto, 10)
        self.assertEqual(info.zones_version, 1)

    def test_manifest_bundle_is_read(self):
        man = bundle_format.format_manifest()
        bundle = _make_bundle(self.root, format_manifest=man)
        info = pf.assert_supported(bundle)
        self.assertEqual(info.source, "format.json")
        self.assertEqual(info.format_version, pf.FORMAT_VERSION)

    def test_newer_format_version_raises(self):
        bundle = _make_bundle(
            self.root, format_manifest={"format_version": pf.FORMAT_VERSION + 1})
        with self.assertRaises(pf.UnsupportedBundleFormat):
            pf.assert_supported(bundle)

    def test_newer_zones_version_raises(self):
        bundle = _make_bundle(self.root, zones_version=pf.ZONES_VERSION + 1)
        with self.assertRaises(pf.UnsupportedBundleFormat):
            pf.assert_supported(bundle)


class TestRecordingsRoots(unittest.TestCase):
    def test_env_override_and_archives(self):
        env = {
            "TMAZE_RECORDINGS_DIR": os.path.join("X:", os.sep, "rec"),
            "TMAZE_RECORDINGS_ARCHIVES":
                os.path.join("Y:", os.sep, "a", "phase3_recordings")
                + os.pathsep
                + os.path.join("Z:", os.sep, "b", "phase3_recordings"),
        }
        saved = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            roots = [str(p) for p in pf.phase3_recordings_roots()]
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(roots[0],
                         os.path.join("X:", os.sep, "rec", "phase3_recordings"))
        self.assertIn(os.path.join("Y:", os.sep, "a", "phase3_recordings"), roots)
        self.assertIn(os.path.join("Z:", os.sep, "b", "phase3_recordings"), roots)


if __name__ == "__main__":
    unittest.main()
