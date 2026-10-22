"""Headless tests for the derived-data folder (tmaze.core.derivatives +
tmaze.io.derivatives_io + tmaze.paths.derivatives_root; docs/decisions/0226).

Covers: pipeline / session names, newest-first ordering, the input match
(size + modified time, path ignored, 2 s tolerance, upstream re-runs), the four
states, the manifest checks, atomic manifest writes, the session lock (incl.
taking over a dead run's lock), and the root setting.

Run from host/:  python -m unittest tests.test_derivatives
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest
from unittest import mock

from tmaze import paths
from tmaze.core import derivatives as D
from tmaze.io import derivatives_io as DIO

P, SID = "dlc_full_iter1", "602_20261021_142829"


def _video(size=100, mtime=1000.0, path=r"C:\a\602.mp4"):
    return D.file_input("video", path, size, mtime)


def _manifest(inputs, **kw):
    return D.new_manifest(P, SID, inputs=inputs, outputs=[{"file": "x.csv", "size": 1}],
                          started_at="t0", finished_at="t1", **kw)


class TestNames(unittest.TestCase):
    def test_pipeline_names(self):
        for ok in ("dlc_full_iter1", "movement_derivatives_v1", "a"):
            self.assertTrue(D.valid_pipeline_name(ok), ok)
        for bad in ("", "_logs", "DLC", "1dlc", "dlc-full", "dlc full"):
            self.assertFalse(D.valid_pipeline_name(bad), bad)
        with self.assertRaises(ValueError):
            D.session_dir("root", "Bad", SID)
        with self.assertRaises(ValueError):
            D.session_dir("root", P, "a/b")
        self.assertEqual(D.session_dir("root", P, SID), os.path.join("root", P, SID))

    def test_newest_first_whatever_the_animal(self):
        states = {"600_20261021_163521": "pending", "602_20261021_142829": "stale",
                  "601_20261020_152109": "partial", "600_20261020_160755": "done"}
        self.assertEqual(D.to_run(states), ["600_20261021_163521", "602_20261021_142829",
                                            "601_20261020_152109"])
        self.assertEqual(D.to_run(states, include=("done",)), ["600_20261020_160755"])
        self.assertEqual(D.counts(states), {"pending": 1, "partial": 1, "stale": 1, "done": 1})


class TestInputsAndStates(unittest.TestCase):
    def test_path_ignored_mtime_tolerance(self):
        rec = [_video()]
        self.assertTrue(D.inputs_match(rec, [_video(path=r"Z:\arch\602.mp4")]))
        self.assertTrue(D.inputs_match(rec, [_video(mtime=1001.9)]))
        self.assertFalse(D.inputs_match(rec, [_video(mtime=1003.0)]))
        self.assertFalse(D.inputs_match(rec, [_video(size=101)]))

    def test_roles_must_match(self):
        extra = [_video(), D.file_input("sidecar", "s.csv", 5, 1.0)]
        self.assertFalse(D.inputs_match([_video()], extra))
        self.assertFalse(D.inputs_match(extra, [_video()]))

    def test_upstream_rerun_makes_stale(self):
        up = [D.upstream_input(P, "2026-10-21T22:00:00-04:00", SID)]
        self.assertTrue(D.inputs_match(up, [D.upstream_input(P, "2026-10-21T22:00:00-04:00", SID)]))
        self.assertFalse(D.inputs_match(up, [D.upstream_input(P, "2026-10-22T09:00:00-04:00", SID)]))

    def test_four_states(self):
        cur = [_video()]
        self.assertEqual(D.classify(False, None, P, SID, cur), D.STATE_PENDING)
        self.assertEqual(D.classify(True, None, P, SID, cur), D.STATE_PARTIAL)
        self.assertEqual(D.classify(True, _manifest(cur), P, SID, cur), D.STATE_DONE)
        self.assertEqual(D.classify(True, _manifest(cur), P, SID, [_video(size=7)]),
                         D.STATE_STALE)

    def test_untrustworthy_manifests_are_partial(self):
        cur = [_video()]
        m = _manifest(cur)
        for change in ({"status": "running"}, {"session_id": "other"},
                       {"pipeline": "other_pipe"}, {"derivatives_format_version": 99}):
            bad = dict(m, **change)
            self.assertTrue(D.manifest_problems(bad, P, SID), change)
            self.assertEqual(D.classify(True, bad, P, SID, cur), D.STATE_PARTIAL)
        missing = dict(m)
        del missing["finished_at"]
        self.assertEqual(D.manifest_problems(missing, P, SID), ["missing finished_at"])
        self.assertEqual(D.manifest_problems([], P, SID), ["not a JSON object"])

    def test_manifest_fields(self):
        m = _manifest([_video()], params={"shuffle": 1}, model={"sha256": "ab"}, adopted=True)
        self.assertEqual((m["derivatives_format_version"], m["status"], m["adopted"]),
                         (D.FORMAT_VERSION, D.STATUS_COMPLETE, True))
        self.assertEqual(m["params"], {"shuffle": 1})


class TestFolderIO(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.folder = D.session_dir(self.root, P, SID)
        os.makedirs(self.folder)

    def test_manifest_round_trip_and_states(self):
        vid = os.path.join(self.root, "v.mp4")
        with open(vid, "wb") as fh:
            fh.write(b"12345")
        cur = [DIO.stat_file_input("video", vid)]
        other = "600_20261021_163521"
        self.assertEqual(DIO.session_states(self.root, P, {SID: cur, other: cur}),
                         {SID: D.STATE_PARTIAL, other: D.STATE_PENDING})
        DIO.write_manifest(self.folder, _manifest(cur))
        self.assertFalse(os.path.exists(os.path.join(self.folder, D.MANIFEST_NAME + ".tmp")))
        self.assertEqual(DIO.session_states(self.root, P, {SID: cur})[SID], D.STATE_DONE)
        DIO.remove_manifest(self.folder)
        DIO.remove_manifest(self.folder)
        self.assertIsNone(DIO.read_manifest(self.folder))

    def test_current_inputs_like_follows_the_bundle(self):
        """Any pipeline's recorded inputs can be re-checked: a bundle file via
        its path inside the (possibly moved) bundle, an upstream via its manifest."""
        old_bundle = os.path.join(self.root, "rig", SID)
        os.makedirs(os.path.join(old_bundle, "video"))
        vid = os.path.join(old_bundle, "video", SID + ".mp4")
        with open(vid, "wb") as fh:
            fh.write(b"abc")
        rec = [DIO.stat_file_input("video", vid, old_bundle)]
        self.assertEqual(rec[0]["bundle_rel"], f"video/{SID}.mp4")
        new_bundle = os.path.join(self.root, "archive", SID)
        os.renames(old_bundle, new_bundle)
        now = DIO.current_inputs_like(rec, new_bundle, self.root)
        self.assertTrue(D.inputs_match(rec, now))
        self.assertEqual(DIO.current_inputs_like(rec, os.path.join(self.root, "x"), self.root), [])

        DIO.write_manifest(self.folder, _manifest([_video()]))
        up = [D.upstream_input(P, "t1", SID)]
        self.assertTrue(D.inputs_match(up, DIO.current_inputs_like(up, None, self.root)))
        DIO.write_manifest(self.folder, dict(_manifest([_video()]), finished_at="t2"))
        self.assertFalse(D.inputs_match(up, DIO.current_inputs_like(up, None, self.root)))

    def test_unreadable_manifest_is_none(self):
        with open(os.path.join(self.folder, D.MANIFEST_NAME), "w") as fh:
            fh.write("{half")
        self.assertIsNone(DIO.read_manifest(self.folder))

    def test_list_pipelines_skips_housekeeping(self):
        os.makedirs(os.path.join(self.root, "_logs"))
        os.makedirs(os.path.join(self.root, "movement_derivatives_v1"))
        open(os.path.join(self.root, "notes.txt"), "w").close()
        self.assertEqual(DIO.list_pipelines(self.root), [P, "movement_derivatives_v1"])
        self.assertEqual(DIO.list_pipelines(os.path.join(self.root, "nope")), [])

    def test_lock_is_exclusive_and_released(self):
        with DIO.SessionLock(self.folder):
            with self.assertRaises(DIO.LockedError):
                DIO.SessionLock(self.folder, alive=lambda pid: True).acquire()
        self.assertFalse(os.path.exists(os.path.join(self.folder, D.LOCK_NAME)))

    def _stale_lock(self, host):
        with open(os.path.join(self.folder, D.LOCK_NAME), "w") as fh:
            json.dump({"pid": 999999, "host": host, "since": "x"}, fh)

    def test_dead_runs_lock_is_taken_over(self):
        self._stale_lock(socket.gethostname())
        with DIO.SessionLock(self.folder, alive=lambda pid: False):
            pass

    def test_other_pcs_lock_is_respected(self):
        self._stale_lock("some-other-pc")
        with self.assertRaises(DIO.LockedError):
            DIO.SessionLock(self.folder, alive=lambda pid: False).acquire()

    def test_unknown_liveness_is_respected(self):
        self._stale_lock(socket.gethostname())
        with self.assertRaises(DIO.LockedError):
            DIO.SessionLock(self.folder, alive=lambda pid: None).acquire()


class TestRoot(unittest.TestCase):
    def test_env_overrides_config(self):
        with mock.patch.dict(os.environ, {"TMAZE_DERIVATIVES_DIR": r"D:\deriv"}):
            self.assertEqual(str(paths.derivatives_root()), r"D:\deriv")

    def test_committed_config(self):
        with mock.patch.dict(os.environ, {"TMAZE_DERIVATIVES_DIR": ""}):
            self.assertEqual(str(paths.derivatives_root()), r"Z:\Ravi\derivatives")

    def test_missing_config_raises(self):
        with mock.patch.dict(os.environ, {"TMAZE_DERIVATIVES_DIR": ""}), \
                mock.patch.object(paths, "host_dir", return_value=paths.Path(tempfile.mkdtemp())):
            with self.assertRaises(RuntimeError):
                paths.derivatives_root()


if __name__ == "__main__":
    unittest.main()
