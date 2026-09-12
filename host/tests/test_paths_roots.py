"""
test_paths_roots.py - headless tests for the tracked recordings-roots config.

Covers ``host/recordings_roots.json`` handling in ``tmaze.paths``
(docs/decisions/0156): best-effort read, atomic write that keeps the file's
``_schema`` note, ``primary`` null = the rig's write folder, the
``TMAZE_RECORDINGS_ARCHIVES`` env override, de-duplication in
``phase3_recordings_roots``, and that the committed config parses.

Both the config path and the rig-prefs path are redirected to a temp dir so the
dev machine's real files can't leak in.

Run from host/tests:  python -m unittest test_paths_roots
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tmaze import paths


class _RootsFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._cfg = d / "recordings_roots.json"
        self._patches = [
            mock.patch.object(paths, "recordings_roots_config_path",
                              return_value=self._cfg),
            mock.patch.object(paths, "prefs_path",
                              return_value=d / "rig_prefs.json"),
        ]
        for p in self._patches:
            p.start()
        self._saved = {k: os.environ.pop(k, None)
                       for k in (paths._RECORDINGS_ENV,
                                 paths._RECORDINGS_ARCHIVES_ENV)}
        os.environ[paths._RECORDINGS_ENV] = r"D:\rig"   # write folder = D:\rig\phase3_recordings

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        self._tmp.cleanup()

    def _write(self, obj):
        self._cfg.write_text(json.dumps(obj), encoding="utf-8")


class TestReadConfig(_RootsFixture):
    def test_absent_or_malformed_is_empty(self):
        self.assertEqual(paths.read_recordings_roots_config(),
                         {"primary": None, "archives": []})
        self._cfg.write_text("{nope", encoding="utf-8")
        self.assertEqual(paths.read_recordings_roots_config(),
                         {"primary": None, "archives": []})
        self._write(["list"])
        self.assertEqual(paths.read_recordings_roots_config()["archives"], [])

    def test_reads_and_trims(self):
        self._write({"schema_version": 1, "primary": "  E:\\scan ",
                     "archives": ["Z:\\a", "", 3, " Z:\\b"]})
        cfg = paths.read_recordings_roots_config()
        self.assertEqual(cfg, {"primary": "E:\\scan",
                               "archives": ["Z:\\a", "Z:\\b"]})


class TestWriteConfig(_RootsFixture):
    def test_write_keeps_schema_note_and_is_readable(self):
        self._write({"_schema": {"note": "keep me"}, "schema_version": 1,
                     "primary": None, "archives": []})
        paths.write_recordings_roots_config("E:\\scan", ["Z:\\a", " ", "Z:\\b"])
        raw = json.loads(self._cfg.read_text(encoding="utf-8"))
        self.assertEqual(raw["_schema"], {"note": "keep me"})
        self.assertEqual(raw["schema_version"], paths.ROOTS_CONFIG_SCHEMA_VERSION)
        self.assertEqual(raw["primary"], "E:\\scan")
        self.assertEqual(raw["archives"], ["Z:\\a", "Z:\\b"])
        self.assertFalse(os.path.exists(str(self._cfg) + ".tmp"))

    def test_blank_primary_stored_as_null(self):
        paths.write_recordings_roots_config("  ", [])
        raw = json.loads(self._cfg.read_text(encoding="utf-8"))
        self.assertIsNone(raw["primary"])
        self.assertEqual(raw["archives"], [])


class TestResolution(_RootsFixture):
    def test_primary_null_means_rig_write_folder(self):
        self._write({"primary": None, "archives": []})
        self.assertEqual(str(paths.scan_primary_root()),
                         os.path.join(r"D:\rig", "phase3_recordings"))
        self.assertEqual(str(paths.phase3_recordings_root()),
                         os.path.join(r"D:\rig", "phase3_recordings"))

    def test_primary_set_redirects_scan_not_write(self):
        self._write({"primary": "E:\\scan", "archives": []})
        self.assertEqual(str(paths.scan_primary_root()), "E:\\scan")
        # The rig's WRITE location is untouched by the scan config.
        self.assertEqual(str(paths.phase3_recordings_root()),
                         os.path.join(r"D:\rig", "phase3_recordings"))

    def test_archives_from_config_and_dedup(self):
        self._write({"primary": None,
                     "archives": ["Z:\\arch", r"D:\rig\phase3_recordings"]})
        roots = [str(p) for p in paths.phase3_recordings_roots()]
        self.assertEqual(roots, [os.path.join(r"D:\rig", "phase3_recordings"),
                                 "Z:\\arch"])

    def test_env_overrides_config_archives(self):
        self._write({"primary": None, "archives": ["Z:\\from_config"]})
        os.environ[paths._RECORDINGS_ARCHIVES_ENV] = os.pathsep.join(
            ["Y:\\env1", "Y:\\env2"])
        self.assertEqual([str(p) for p in paths.phase3_recordings_archives()],
                         ["Y:\\env1", "Y:\\env2"])
        self.assertEqual([str(p) for p in paths.archives_env_override()],
                         ["Y:\\env1", "Y:\\env2"])
        # Set-but-blank env = "no archives" (still an override).
        os.environ[paths._RECORDINGS_ARCHIVES_ENV] = ""
        self.assertEqual(paths.phase3_recordings_archives(), [])
        self.assertEqual(paths.archives_env_override(), [])

    def test_no_env_means_no_override(self):
        self.assertIsNone(paths.archives_env_override())


class TestCommittedConfig(unittest.TestCase):
    def test_committed_file_parses(self):
        p = paths.recordings_roots_config_path()
        self.assertTrue(p.exists(), p)
        raw = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], paths.ROOTS_CONFIG_SCHEMA_VERSION)
        self.assertIn("archives", raw)
        self.assertIn("_schema", raw)


if __name__ == "__main__":
    unittest.main()
