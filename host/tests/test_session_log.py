"""
test_session_log.py - headless tests for the session manifest pure core.

Covers the clock anchor (three host clocks captured together), the manifest
structure (valid JSON, required keys, the default fiducial), the live mutators
(stream paths added/updated, the end anchor set), and the "does this stream file
belong to this session" path test (docs/decisions/0066). Pure stdlib; no
hardware/Tk. See docs/decisions/0048.

Run from host/:  python -m unittest test_session_log
"""

from __future__ import annotations

import json
import unittest

from tmaze.core.session.session_log import (
    FIDUCIAL_METHOD, SCHEMA_VERSION, SessionManifest, clock_snapshot,
    path_within_session,
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
        # schema v3: provenance + append-only post_hoc default empty (ADR-0180)
        self.assertEqual(obj["provenance"], {})
        self.assertEqual(obj["post_hoc"], [])
        # schema v4: firmware_resets default empty (ADR-0184)
        self.assertEqual(obj["firmware_resets"], [])

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

    def test_provenance_and_post_hoc_round_trip(self):
        # schema v3 (ADR-0180): the rig records provenance at Start; an offline
        # tool appends a post_hoc edit record; both survive a render/from_dict cycle.
        m = self._make()
        m.set_provenance({"code_commit": "abc1234",
                          "packages": {"tmaze": "0.1"}})
        m.add_post_hoc({"tool": "sync_fit", "tool_version": 1,
                        "at": "2026-01-01T00:00:00",
                        "fields_changed": ["streams.sync_clock_fit", "fiducial.method"]})
        obj = json.loads(m.render())
        self.assertEqual(obj["provenance"]["code_commit"], "abc1234")
        self.assertEqual(len(obj["post_hoc"]), 1)
        self.assertEqual(obj["post_hoc"][0]["tool"], "sync_fit")
        self.assertEqual(json.loads(SessionManifest.from_dict(obj).render()), obj)

    def test_firmware_resets_round_trip(self):
        # schema v4 (ADR-0184): a mid-session Due reboot the host survived is logged
        # as {host_iso, last_fw_ms}; the list survives a render/from_dict cycle.
        m = self._make()
        m.add_firmware_reset("2026-01-01T12:03:00.000-05:00", 183456)
        m.add_firmware_reset("2026-01-01T12:07:30.000-05:00", None)
        obj = json.loads(m.render())
        self.assertEqual(len(obj["firmware_resets"]), 2)
        self.assertEqual(obj["firmware_resets"][0],
                         {"host_iso": "2026-01-01T12:03:00.000-05:00",
                          "last_fw_ms": 183456})
        self.assertIsNone(obj["firmware_resets"][1]["last_fw_ms"])
        self.assertEqual(json.loads(SessionManifest.from_dict(obj).render()), obj)

    def test_set_end_stamps_the_end_anchor(self):
        m = self._make()
        self.assertIsNone(m.t_end)
        m.set_end(clock_snapshot())
        obj = json.loads(m.render())
        self.assertEqual(set(obj["t_end"]), {"wall", "mono", "iso"})

    def test_from_dict_round_trips_render(self):
        # An offline tool must be able to read a rendered manifest back, tweak it,
        # and re-render it (sync_fit registers the sync/ streams + flips
        # fiducial.method this way).
        m = self._make()
        m.set_stream("camera_video", "video/x.mp4")
        m.set_end(clock_snapshot())
        obj = json.loads(m.render())
        back = SessionManifest.from_dict(obj)
        self.assertEqual(json.loads(back.render()), obj)

    def test_from_dict_then_mutate_preserves_untouched_fields(self):
        m = self._make()
        m.set_stream("camera_video", "video/x.mp4")
        loaded = SessionManifest.from_dict(json.loads(m.render()))
        loaded.set_stream("sync_clock_fit", "sync/clock_fit_x.json")
        loaded.fiducial["method"] = "led-fit"
        obj = json.loads(loaded.render())
        # the new registration landed...
        self.assertEqual(obj["streams"]["sync_clock_fit"], "sync/clock_fit_x.json")
        self.assertEqual(obj["fiducial"]["method"], "led-fit")
        # ...without disturbing what the rig wrote (streams + the pending note).
        self.assertEqual(obj["streams"]["camera_video"], "video/x.mp4")
        self.assertIn("FW-0018", obj["fiducial"]["pending"])

    def test_from_dict_tolerates_missing_optional_keys(self):
        back = SessionManifest.from_dict(
            {"session_id": "s", "animal": "600",
             "t0": {"wall": 1.0, "mono": 2.0, "iso": "x"}})
        obj = json.loads(back.render())
        self.assertEqual(obj["streams"], {})
        self.assertEqual(obj["fiducial"]["method"], FIDUCIAL_METHOD)
        self.assertEqual(obj["schema_version"], SCHEMA_VERSION)


class TestPathWithinSession(unittest.TestCase):
    """The guard that keeps another session's file out of the manifest (ADR-0066)."""

    SESSION = r"C:\rec\phase3_recordings\600_20260727_134317"

    def test_file_in_a_subfolder_belongs(self):
        self.assertTrue(path_within_session(
            self.SESSION + r"\task\phase3_600_20260727_134317.csv", self.SESSION))

    def test_previous_sessions_file_does_not_belong(self):
        # The exact 2026-07-27 corruption: a sibling session's task file.
        self.assertFalse(path_within_session(
            r"C:\rec\phase3_recordings\000_20260727_132752\task"
            r"\phase3_000_20260727_132752.csv", self.SESSION))

    def test_host_data_fallback_on_another_drive_does_not_belong(self):
        self.assertFalse(path_within_session(
            r"D:\git_repos\os_code_t_maze\host\data\phase3_600_x.csv",
            self.SESSION))

    def test_sibling_with_a_shared_name_prefix_does_not_belong(self):
        # A plain string startswith() would wrongly accept this one.
        self.assertFalse(path_within_session(
            r"C:\rec\phase3_recordings\600_20260727_134317_old\task\x.csv",
            self.SESSION))

    def test_separator_and_case_differences_still_belong(self):
        self.assertTrue(path_within_session(
            "c:/REC/phase3_recordings/600_20260727_134317/task/x.csv",
            self.SESSION))

    def test_the_session_folder_itself_is_not_a_stream_file(self):
        self.assertFalse(path_within_session(self.SESSION, self.SESSION))

    def test_empty_inputs_are_not_within(self):
        self.assertFalse(path_within_session("", self.SESSION))
        self.assertFalse(path_within_session(self.SESSION + r"\task\x.csv", ""))


if __name__ == "__main__":
    unittest.main()
