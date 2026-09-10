"""
test_paths.py - headless tests for the on-disk location resolver + GUI prefs.

Covers the rig_prefs.json accessors and camera_output_dir() precedence
(GUI pref > TMAZE_RECORDINGS_DIR env > machine default) and the
operator-configurable low-disk threshold round-trip, per docs/decisions/0151
(extends 0088). Pure stdlib; the prefs file is redirected to a temp path so a
real host/rig_prefs.json on the dev machine can't leak into the test.

Run from host/:  python -m unittest test_paths
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tmaze import paths


class _PrefsFixture(unittest.TestCase):
    """Redirect paths.prefs_path() to a temp file and clear the recordings env
    var so each test starts from a known, isolated state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prefs = Path(self._tmp.name) / "rig_prefs.json"
        self._patch = mock.patch.object(paths, "prefs_path",
                                        return_value=self._prefs)
        self._patch.start()
        # Isolate the recordings env var (restored in tearDown).
        self._saved_env = os.environ.pop(paths._RECORDINGS_ENV, None)

    def tearDown(self):
        self._patch.stop()
        os.environ.pop(paths._RECORDINGS_ENV, None)
        if self._saved_env is not None:
            os.environ[paths._RECORDINGS_ENV] = self._saved_env
        self._tmp.cleanup()


class TestRecordingsBase(_PrefsFixture):
    def test_default_when_nothing_set(self):
        self.assertEqual(str(paths.camera_output_dir()),
                         paths._DEFAULT_CAMERA_OUTPUT_DIR)

    def test_env_overrides_default(self):
        os.environ[paths._RECORDINGS_ENV] = r"D:\env_recordings"
        self.assertEqual(str(paths.camera_output_dir()), r"D:\env_recordings")

    def test_gui_pref_wins_over_env(self):
        os.environ[paths._RECORDINGS_ENV] = r"D:\env_recordings"
        paths.set_recordings_base(r"E:\picked")
        self.assertEqual(str(paths.camera_output_dir()), r"E:\picked")
        # phase3 root derives from it.
        self.assertEqual(str(paths.phase3_recordings_root()),
                         os.path.join(r"E:\picked", "phase3_recordings"))

    def test_blank_pref_falls_through(self):
        paths.write_prefs(recordings_base="   ")
        os.environ[paths._RECORDINGS_ENV] = r"D:\env_recordings"
        self.assertEqual(str(paths.camera_output_dir()), r"D:\env_recordings")


class TestLowDiskThreshold(_PrefsFixture):
    def test_default_when_unset(self):
        self.assertEqual(paths.low_disk_warn_gb(),
                         paths.DEFAULT_LOW_DISK_WARN_GB)

    def test_round_trip(self):
        paths.set_low_disk_warn_gb(75)
        self.assertEqual(paths.low_disk_warn_gb(), 75.0)

    def test_invalid_or_nonpositive_falls_back(self):
        paths.write_prefs(low_disk_warn_gb="oops")
        self.assertEqual(paths.low_disk_warn_gb(), paths.DEFAULT_LOW_DISK_WARN_GB)
        paths.write_prefs(low_disk_warn_gb=0)
        self.assertEqual(paths.low_disk_warn_gb(), paths.DEFAULT_LOW_DISK_WARN_GB)

    def test_keys_coexist(self):
        # Writing one key must not clobber the other (single merge-writer).
        paths.set_recordings_base(r"E:\picked")
        paths.set_low_disk_warn_gb(55)
        self.assertEqual(str(paths.camera_output_dir()), r"E:\picked")
        self.assertEqual(paths.low_disk_warn_gb(), 55.0)


if __name__ == "__main__":
    unittest.main()
