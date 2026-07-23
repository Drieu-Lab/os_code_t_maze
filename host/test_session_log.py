"""
test_session_log.py - headless tests for the session manifest pure core.

Covers the clock anchor (three host clocks captured together), the manifest
structure (valid JSON, required keys, the default fiducial), and the live
mutators (stream paths added/updated, the end anchor set). Pure stdlib; no
hardware/Tk. See docs/decisions/0048.

Run from host/:  python -m unittest test_session_log
"""

from __future__ import annotations

import json
import unittest

from session_log import (
    FIDUCIAL_METHOD, SCHEMA_VERSION, SessionManifest, clock_snapshot,
)


class TestClockSnapshot(unittest.TestCase):
    def test_has_three_clocks_of_expected_types(self):
        snap = clock_snapshot()
        self.assertEqual(set(snap), {"wall", "mono", "iso"})
        self.assertIsInstance(snap["wall"], float)
        self.assertIsInstance(snap["mono"], float)
        self.assertIsInstance(snap["iso"], str)
        # ISO string is date+time (has the 'T' separator).
        self.assertIn("T", snap["iso"])

    def test_monotonic_never_goes_backwards(self):
        a = clock_snapshot()
        b = clock_snapshot()
        self.assertGreaterEqual(b["mono"], a["mono"])


class TestManifest(unittest.TestCase):
    def _make(self):
        return SessionManifest(session_id="RAT600_20260723_120000",
                               animal="RAT600", t0=clock_snapshot())

    def test_renders_valid_json_with_required_keys(self):
        m = self._make()
        obj = json.loads(m.render())
        self.assertEqual(obj["session_id"], "RAT600_20260723_120000")
        self.assertEqual(obj["animal"], "RAT600")
        self.assertEqual(obj["schema_version"], SCHEMA_VERSION)
        self.assertIsNone(obj["t_end"])
        self.assertEqual(obj["streams"], {})
        self.assertEqual(set(obj["t0"]), {"wall", "mono", "iso"})

    def test_default_fiducial_is_host_t0_with_pending_upgrade(self):
        obj = json.loads(self._make().render())
        self.assertEqual(obj["fiducial"]["method"], FIDUCIAL_METHOD)
        self.assertIn("FW-0018", obj["fiducial"]["pending"])

    def test_set_stream_adds_and_updates(self):
        m = self._make()
        m.set_stream("camera_video", "C:/rec/RAT600_x.mkv")
        m.set_stream("phase3_task", "host/data/phase3_RAT600_x.csv")
        m.set_stream("serial_log", {"dir": "host/data", "animal": "RAT600"})
        # updating an existing stream overwrites in place
        m.set_stream("camera_video", "C:/rec/RAT600_y.mkv")
        obj = json.loads(m.render())
        self.assertEqual(obj["streams"]["camera_video"], "C:/rec/RAT600_y.mkv")
        self.assertEqual(obj["streams"]["phase3_task"],
                         "host/data/phase3_RAT600_x.csv")
        self.assertEqual(obj["streams"]["serial_log"]["animal"], "RAT600")

    def test_set_end_stamps_the_end_anchor(self):
        m = self._make()
        self.assertIsNone(m.t_end)
        m.set_end(clock_snapshot())
        obj = json.loads(m.render())
        self.assertEqual(set(obj["t_end"]), {"wall", "mono", "iso"})


if __name__ == "__main__":
    unittest.main()
