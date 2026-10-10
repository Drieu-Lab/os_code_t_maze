"""
test_offline_workbench.py - the pure decisions behind the offline workbench.

Covers ``tmaze.core.offline_workbench``: warm-up start and A-B loop stepping,
trial-log times mapped to video frames, the settings table and the ``--set``
values that reproduce the window's settings (round-tripped through the same
``merge_params`` the command line uses), the equivalent command line for each
file choice, ghost-track reading and distances. Stdlib only, no video.

Run from host/:  python -m unittest tests.test_offline_workbench
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tmaze.core import offline_track as OT
from tmaze.core import offline_workbench as WB


def _choice(path, source=OT.SRC_BUNDLE):
    return OT.FileChoice(path, source, None)


def _defaults(bundle="C:/rec/602_x", **kw):
    base = dict(bundle=bundle, session_id="602_x", video=bundle + "/video/602_x.mp4",
                sidecar=None, model=_choice("C:/rec/602_x/config/model_a.npz"),
                blocker_model=_choice("C:/host/ratmodels/blk.npz", OT.SRC_FALLBACK),
                roi=_choice("C:/rec/602_x/config/roi_polygon.json"),
                landmarks=None,
                background=_choice("C:/rec/602_x/video/602_x_background.png"))
    base.update(kw)
    return OT.OfflineSettings(**base)


def _files(d, **kw):
    base = dict(model=d.model, blocker_model=d.blocker_model, roi=d.roi,
                landmarks=d.landmarks, background=d.background)
    base.update(kw)
    return WB.WorkbenchFiles(**base)


class TestFrames(unittest.TestCase):
    def test_warmup_start(self):
        self.assertEqual(WB.warmup_start(1000, 150), 850)
        self.assertEqual(WB.warmup_start(100, 150), 0)
        self.assertEqual(WB.warmup_start(100, -5), 100)

    def test_next_frame_clamps(self):
        self.assertEqual(WB.next_frame(0, -1, 100), 0)
        self.assertEqual(WB.next_frame(99, 5, 100), 99)
        self.assertEqual(WB.next_frame(10, 5, 100), 15)

    def test_ab_loop_wraps(self):
        m = WB.Marks(a=50, b=20)
        self.assertEqual(m.ab(), (20, 50))
        self.assertEqual(WB.next_frame(50, 1, 100, m, loop=True), 20)
        self.assertEqual(WB.next_frame(20, -1, 100, m, loop=True), 50)
        self.assertEqual(WB.next_frame(50, 1, 100, m, loop=False), 51)
        self.assertEqual(WB.next_frame(50, 1, 100, WB.Marks(a=5), loop=True), 51)

    def test_toggle_blocker(self):
        m = WB.Marks().toggle_blocker(30).toggle_blocker(10)
        self.assertEqual(m.blocker_frames, (10, 30))
        self.assertEqual(m.toggle_blocker(30).blocker_frames, (10,))


class TestTrials(unittest.TestCase):
    T0 = datetime(2026, 10, 9, 13, 21, 46, tzinfo=timezone(timedelta(hours=-4)))

    def _iso(self, s):
        return (self.T0 + timedelta(seconds=s)).isoformat()

    def test_iso_to_unix(self):
        self.assertAlmostEqual(WB.iso_to_unix(self.T0.isoformat()), self.T0.timestamp())
        self.assertIsNone(WB.iso_to_unix(""))
        self.assertIsNone(WB.iso_to_unix("nope"))

    def test_frame_at_time(self):
        times = [10.0, 10.1, 10.2, 10.3]
        self.assertEqual(WB.frame_at_time(times, 10.14), 1)
        self.assertEqual(WB.frame_at_time(times, 10.16), 2)
        self.assertEqual(WB.frame_at_time(times, 9.5), 0)     # within the slack
        self.assertIsNone(WB.frame_at_time(times, 5.0))
        self.assertIsNone(WB.frame_at_time(times, None))
        self.assertIsNone(WB.frame_at_time([], 10.0))

    def test_trial_markers(self):
        t0 = self.T0.timestamp()
        times = [t0 + i * 0.1 for i in range(1000)]          # 100 s of video at 10 fps
        rows = [
            SimpleNamespace(trial=2, trial_start_iso=self._iso(40), choice_time_iso=self._iso(45),
                            reward_cmd_iso=None, trial_end_iso=self._iso(50),
                            outcome="INCORRECT", choice_cue="LOW", chosen_arm="LEFT"),
            SimpleNamespace(trial=1, trial_start_iso=self._iso(10), choice_time_iso=self._iso(14),
                            reward_cmd_iso=self._iso(14.5), trial_end_iso=self._iso(20),
                            outcome="CORRECT", choice_cue="HIGH", chosen_arm="LEFT"),
            SimpleNamespace(trial=3, trial_start_iso=self._iso(500), outcome=None),  # after video
        ]
        m = WB.trial_markers(rows, times)
        self.assertEqual([x.trial for x in m], [1, 2])
        self.assertEqual((m[0].start, m[0].choice, m[0].reward, m[0].end), (100, 140, 145, 200))
        self.assertIsNone(m[1].reward)
        self.assertEqual(m[1].outcome, "INCORRECT")
        self.assertIsNone(WB.trial_at(m, 50))
        self.assertEqual(WB.trial_at(m, 399).trial, 1)
        self.assertEqual(WB.trial_at(m, 400).trial, 2)
        self.assertEqual(WB.trial_frame(m, 2), 400)
        self.assertIsNone(WB.trial_frame(m, 9))


class TestSettings(unittest.TestCase):
    BASE = {"thresh": 20, "accept_score": 0.35, "adapt_enrich": False, "blur": 5}

    def test_slider_noise_is_not_a_change(self):
        cur = dict(self.BASE, accept_score=35 * 0.01)       # 0.35000000000000003
        self.assertEqual(WB.changed_settings(self.BASE, cur), {})
        self.assertEqual(WB.set_args(self.BASE, cur), ())

    def test_set_args_round_trip_through_merge_params(self):
        cur = dict(self.BASE, thresh=31, accept_score=0.42 + 1e-15, adapt_enrich=True)
        sets = WB.set_args(self.BASE, cur)
        self.assertEqual(sets, ("accept_score=0.42", "adapt_enrich=true", "thresh=31"))
        settings = _defaults()
        settings.set_params = sets
        merged, _ = OT.merge_params(self.BASE, settings)
        self.assertEqual(WB.changed_settings(merged, cur), {})

    def test_settings_table_layers(self):
        cur = dict(self.BASE, thresh=31)
        rows = WB.settings_table(cur, self.BASE, {"blur": OT.PSRC_SNAPSHOT})
        layers = {k: layer for k, _, layer in rows}
        self.assertEqual(layers["thresh"], WB.LAYER_YOU)
        self.assertEqual(layers["blur"], OT.PSRC_SNAPSHOT)
        self.assertEqual(layers["adapt_enrich"], WB.LAYER_MODEL)
        self.assertEqual([k for k, _, _ in rows], sorted(cur))


class TestCommandLine(unittest.TestCase):
    def test_defaults_give_a_bare_command(self):
        d = _defaults()
        argv, warn = WB.cli_args(d, _files(d), ())
        self.assertEqual(argv, [d.bundle])
        self.assertEqual(warn, [])

    def test_file_changes(self):
        d = _defaults()
        files = _files(d, model=_choice("C:/m/other.npz", OT.SRC_OVERRIDE),
                       blocker_model=None,
                       roi=_choice(None, WB.SRC_EDITED),
                       background=OT.FileChoice(None, OT.SRC_COMPUTED))
        argv, warn = WB.cli_args(d, files, ("thresh=31",), tag="t1",
                                 start_frame=100, end_frame=200, warmup_frames=50)
        self.assertEqual(argv, [d.bundle, "--model", "C:/m/other.npz", "--no-blocker",
                                "--background-from-video", "--set", "thresh=31",
                                "--tag", "t1", "--start-frame", "100",
                                "--end-frame", "200", "--warmup-frames", "50"])
        self.assertEqual(len(warn), 1)
        self.assertIn("ROI", warn[0])

    def test_registered_models(self):
        d = _defaults()
        files = _files(d, model=OT.FileChoice(None, OT.SRC_REGISTERED),
                       blocker_model=OT.FileChoice(None, OT.SRC_REGISTERED))
        reg = OT.RegistrationRequest(rat_frames=(10, 40), blocker_frames=(3, 7))
        argv, warn = WB.cli_args(d, files, (), registration=reg)
        self.assertEqual(argv, [d.bundle, "--register-frames", "10", "40",
                                "--blocker-frames", "3", "7"])
        self.assertEqual(warn, [])
        _, warn = WB.cli_args(d, files, ())
        self.assertEqual(len(warn), 2)
        saved = _files(d, model=OT.FileChoice("C:/m/new.npz", OT.SRC_REGISTERED))
        argv, _ = WB.cli_args(d, saved, ())
        self.assertEqual(argv, [d.bundle, "--model", "C:/m/new.npz"])

    def test_no_blocker_by_default_needs_no_flag(self):
        d = _defaults(blocker_model=None)
        argv, _ = WB.cli_args(d, _files(d), ())
        self.assertEqual(argv, [d.bundle])

    def test_bad_range_or_tag_raise(self):
        d = _defaults()
        with self.assertRaises(ValueError):
            WB.cli_args(d, _files(d), (), tag="bad tag")
        with self.assertRaises(ValueError):
            WB.cli_args(d, _files(d), (), start_frame=10, end_frame=5)

    def test_format_command_quotes_spaces(self):
        cmd = WB.format_command(["C:/My Rec/602_x", "--set", "thresh=31"])
        self.assertEqual(cmd, 'python -m tmaze.tools.offline_track "C:/My Rec/602_x" '
                              '--set thresh=31')

    def test_run_model_paths(self):
        m, b = WB.run_model_paths("out", "602_x", "t1")
        self.assertEqual(os.path.basename(m), "602_x_offline_track_t1_model.npz")
        self.assertEqual(os.path.basename(b), "602_x_offline_track_t1_blockermodel.npz")


class TestGhosts(unittest.TestCase):
    def setUp(self):
        self.bundle = os.path.join(tempfile.mkdtemp(), "602_x")
        os.makedirs(os.path.join(self.bundle, "video"))

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.bundle), ignore_errors=True)

    def _write(self, name, text):
        p = os.path.join(self.bundle, "video", name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_read_track_points_and_distance(self):
        p = self._write("602_x.csv", "# width: 1280\n"
                        "frame_idx,host_unix_s,track_state,track_smooth_cx,track_smooth_cy\n"
                        "0,1.0,TRACKING,10.0,20.0\n"
                        "1,1.1,LOST,,\n"
                        "2,1.2,TRACKING,13.0,24.0\n")
        pts = WB.read_track_points(p)
        self.assertEqual(sorted(pts), [0, 1, 2])
        self.assertEqual(pts[1].state, "LOST")
        self.assertIsNone(pts[1].x)
        self.assertAlmostEqual(WB.distance(pts[0], pts[2]), 5.0)
        self.assertIsNone(WB.distance(pts[0], pts[1]))
        self.assertIsNone(WB.distance(pts[0], None))

    def test_offline_runs(self):
        for name in ("602_x.csv", "602_x_offline_track.csv", "602_x_offline_track_b.csv",
                     "602_x_offline_track_a.csv", "602_x_offline_track_c.csv.partial"):
            self._write(name, "frame_idx\n")
        self.assertEqual(WB.offline_runs(self.bundle), [None, "a", "b"])


if __name__ == "__main__":
    unittest.main()
