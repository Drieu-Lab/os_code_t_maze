"""Headless tests for session_config.py (the per-session config snapshot core).

Run from host/:  python -m unittest test_session_config
"""

import hashlib
import json
import os
import tempfile
import unittest

from tmaze.core.session import session_config


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        # A fake host dir holding a subset of the known config files.
        self.host = os.path.join(self.root, "host")
        os.makedirs(self.host)
        _write(os.path.join(self.host, "zones.json"), '{"zones": []}')
        _write(os.path.join(self.host, "rig_scale.json"), '{"cm_per_px": 0.1}')
        # roi_polygon.json / landmarks.json / phase3.json / rules.json absent.

    def tearDown(self):
        self._tmp.cleanup()

    def test_sha256_matches_hashlib(self):
        p = os.path.join(self.host, "zones.json")
        with open(p, "rb") as fh:
            want = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(session_config.sha256_file(p), want)

    def test_sha256_missing_returns_none(self):
        self.assertIsNone(
            session_config.sha256_file(os.path.join(self.host, "nope.json")))

    def test_present_and_missing_split(self):
        dest = os.path.join(self.root, "config")
        out = session_config.write_config_snapshot(
            dest, session_id="600_x", host_dir=self.host)
        self.assertIn("zones.json", out["present"])
        self.assertIn("rig_scale.json", out["present"])
        self.assertIn("roi_polygon.json", out["missing"])
        self.assertIn("rules.json", out["missing"])
        # Present files are copied verbatim into the dest dir.
        self.assertTrue(os.path.isfile(os.path.join(dest, "zones.json")))
        self.assertFalse(os.path.isfile(os.path.join(dest, "roi_polygon.json")))

    def test_index_records_hash_and_presence(self):
        dest = os.path.join(self.root, "config")
        session_config.write_config_snapshot(dest, host_dir=self.host)
        with open(os.path.join(dest, "config_snapshot.json"), encoding="utf-8") as fh:
            idx = json.load(fh)
        self.assertEqual(idx["schema_version"], session_config.SCHEMA_VERSION)
        by_name = {e["name"]: e for e in idx["files"]}
        self.assertTrue(by_name["zones.json"]["present"])
        self.assertEqual(
            by_name["zones.json"]["sha256"],
            session_config.sha256_file(os.path.join(dest, "zones.json")))
        self.assertFalse(by_name["landmarks.json"]["present"])
        self.assertIsNone(by_name["landmarks.json"]["sha256"])

    def test_extra_sources_and_runtime_params(self):
        # An "extra" file living outside the host dir (e.g. lens calibration).
        cal = os.path.join(self.root, "camera_calibration.json")
        _write(cal, '{"camera_matrix": [[1,0,0],[0,1,0],[0,0,1]]}')
        dest = os.path.join(self.root, "config")
        params = {"camera": {"orientation": "rot180"}, "tracker": {"thresh": 30}}
        out = session_config.write_config_snapshot(
            dest, extra_sources={"camera_calibration.json": cal},
            runtime_params=params, host_dir=self.host)
        self.assertIn("camera_calibration.json", out["present"])
        self.assertTrue(os.path.isfile(os.path.join(dest, "camera_calibration.json")))
        rp = os.path.join(dest, "runtime_params.json")
        self.assertTrue(os.path.isfile(rp))
        with open(rp, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), params)
        self.assertTrue(out["runtime_params_path"].endswith("runtime_params.json"))

    def test_no_runtime_params_omits_file(self):
        dest = os.path.join(self.root, "config")
        out = session_config.write_config_snapshot(dest, host_dir=self.host)
        self.assertIsNone(out["runtime_params_path"])
        self.assertFalse(os.path.isfile(os.path.join(dest, "runtime_params.json")))
        with open(os.path.join(dest, "config_snapshot.json"), encoding="utf-8") as fh:
            self.assertIsNone(json.load(fh)["runtime_params_file"])

    def test_format_manifest_written_verbatim(self):
        dest = os.path.join(self.root, "config")
        manifest = {"format_version": 1, "proto": 10,
                    "outcome_labels": ["CORRECT", "INCORRECT", "ABANDONED"]}
        out = session_config.write_config_snapshot(
            dest, format_manifest=manifest, host_dir=self.host)
        fp = os.path.join(dest, "format.json")
        self.assertTrue(os.path.isfile(fp))
        self.assertTrue(out["format_path"].endswith("format.json"))
        with open(fp, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), manifest)

    def test_no_format_manifest_omits_file(self):
        dest = os.path.join(self.root, "config")
        out = session_config.write_config_snapshot(dest, host_dir=self.host)
        self.assertIsNone(out["format_path"])
        self.assertFalse(os.path.isfile(os.path.join(dest, "format.json")))

    def test_update_format_manifest_rewrites_through_patch(self):
        # docs/decisions/0200: a late firmware version is patched in after Start.
        dest = os.path.join(self.root, "config")
        out = session_config.write_config_snapshot(
            dest, format_manifest={"firmware_proto": None}, host_dir=self.host)
        with open(out["snapshot_path"], "rb") as fh:
            index_before = fh.read()
        self.assertTrue(session_config.update_format_manifest(
            out["format_path"], lambda m: dict(m, firmware_proto=9)))
        with open(out["format_path"], encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"firmware_proto": 9})
        with open(out["snapshot_path"], "rb") as fh:
            self.assertEqual(fh.read(), index_before, "index must not change")

    def test_update_format_manifest_no_change_or_missing_file(self):
        dest = os.path.join(self.root, "config")
        out = session_config.write_config_snapshot(
            dest, format_manifest={"firmware_proto": 7}, host_dir=self.host)
        self.assertFalse(session_config.update_format_manifest(
            out["format_path"], lambda m: None))
        self.assertFalse(session_config.update_format_manifest(
            os.path.join(dest, "absent.json"), lambda m: dict(m)))

    def test_led_map_is_a_known_config_file(self):
        # sync_fit --map writes host/led_map.json; the snapshot must carry it into
        # the bundle like the other host config (ADR-0153/0154).
        self.assertIn("led_map.json", session_config.HOST_CONFIG_FILES)

    def test_led_map_snapshotted_when_present_missing_otherwise(self):
        dest = os.path.join(self.root, "config")
        # Absent by default (no mapping session yet) -> recorded missing.
        out = session_config.write_config_snapshot(dest, host_dir=self.host)
        self.assertIn("led_map.json", out["missing"])
        # Once a map exists it is copied verbatim like the rest.
        _write(os.path.join(self.host, "led_map.json"), '{"schema_version": 2}')
        dest2 = os.path.join(self.root, "config2")
        out2 = session_config.write_config_snapshot(dest2, host_dir=self.host)
        self.assertIn("led_map.json", out2["present"])
        self.assertTrue(os.path.isfile(os.path.join(dest2, "led_map.json")))


if __name__ == "__main__":
    unittest.main()
