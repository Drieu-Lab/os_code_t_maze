"""
test_video_player.py - the offline workbench's video player and runs (ADR-0214).

On the synthetic moving-square session from ``test_offline_track``:
- a seeked frame is the same picture as that frame read in order;
- a jump re-tracks the warm-up before the target, a short step forward keeps
  tracking, a step back re-tracks, view mode never runs the tracker;
- playback stops at the end, and loops inside an A-B range;
- building a rat model from marked frames runs on the worker;
- a run started the window's way (``prepare_run`` + ``RunJob``) and the copied
  command line give the same rows;
- Cancel stops a run and leaves no output.

Needs numpy + OpenCV with an mp4 writer (skips otherwise).

Run from host/:  python -m unittest tests.test_video_player
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from tests.test_offline_track import _HAVE_CV, _N, SID, _make_session, _read_rows
from tmaze.core import offline_track as OT
from tmaze.core import offline_workbench as WB

if _HAVE_CV:
    from tmaze.tools import offline_track as CLI
    from tmaze.vision import offline_tracker as OTR
    from tmaze.vision import video_player as VP
    from tmaze.vision.tracker import cv2, np


def _wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@unittest.skipUnless(_HAVE_CV, "numpy + OpenCV required")
class TestVideoPlayer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="video_player_")
        self.bundle = os.path.join(self.tmp, SID)
        self.models = os.path.join(self.tmp, "ratmodels")
        os.makedirs(self.models)
        if not _make_session(self.bundle, with_png=True):
            self.skipTest("no mp4 video writer in this OpenCV build")
        self.defaults = OT.resolve_settings(self.bundle, OT.Overrides(),
                                            ratmodels_dir=self.models)
        self.tracker, _, _ = OTR.build_tracker(self.defaults)
        self.tracker.update_params(min_area=100)
        OTR.install_background(self.tracker, OTR.load_background(self.defaults, _N))
        self.calls = []
        real = self.tracker.process

        def counting(frame):
            self.calls.append(1)
            return real(frame)
        self.tracker.process = counting
        self.player = VP.VideoPlayer(self.defaults.video, self.tracker, n_frames=_N,
                                     fps=25.0, warmup=10)
        self.assertTrue(_wait(lambda: self.player.shown().idx == 0))

    def tearDown(self):
        self.player.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _show(self, idx):
        return _wait(lambda: self.player.shown().idx == idx and not self.calls_pending())

    def calls_pending(self):
        p = self.player
        return p._seek is not None or p._step or p._retrack or p._jobs

    def _sequential(self, idx):
        cap = cv2.VideoCapture(self.defaults.video)
        try:
            for _ in range(idx + 1):
                ok, frame = cap.read()
            return frame
        finally:
            cap.release()

    def test_seeked_frame_matches_sequential_read(self):
        for idx in (47, 5, 33, 59):
            self.player.seek(idx)
            self.assertTrue(self._show(idx), idx)
            self.assertTrue(np.array_equal(self.player.shown().frame, self._sequential(idx)),
                            idx)

    def test_jump_and_step_tracking(self):
        self.player.seek(40)
        self.assertTrue(self._show(40))
        time.sleep(0.05)
        self.calls.clear()
        self.player.step(3)                        # short step forward: keep tracking
        self.assertTrue(self._show(43))
        self.assertEqual(len(self.calls), 3)
        self.calls.clear()
        self.player.step(-1)                       # step back: re-track warm-up
        self.assertTrue(self._show(42))
        self.assertEqual(len(self.calls), 11)
        self.assertIsNotNone(self.player.shown().result)

    def test_view_mode_does_not_track(self):
        self.player.set_mode(VP.MODE_VIEW)
        self.assertFalse(self.player.tracking_enabled())
        time.sleep(0.1)
        self.calls.clear()
        self.player.seek(30)
        self.assertTrue(self._show(30))
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.player.shown().result)

    def test_play_to_end_and_ab_loop(self):
        self.player.set_speed(0)                   # as fast as possible
        self.player.seek(50)
        self.assertTrue(self._show(50))
        self.player.play()
        self.assertTrue(_wait(lambda: not self.player.playing()))
        self.assertEqual(self.player.shown().idx, _N - 1)
        self.player.set_loop(WB.Marks(a=10, b=14), True)
        self.player.seek(12)
        self.assertTrue(self._show(12))
        self.player.play()
        seen = set()
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            seen.add(self.player.shown().idx)
            time.sleep(0.002)
        self.player.pause()
        self.assertTrue(seen <= set(range(10, 15)), seen)
        self.assertIn(10, seen)

    def test_register_rat_from_marks(self):
        self.player.register(rat_range=(0, 39))
        self.assertTrue(_wait(lambda: self.player.take_job_result("register") is not None
                              or self.player.take_error() is not None, 20))
        self.assertTrue(self.tracker.has_model())

    def test_window_run_matches_copied_command(self):
        self.tracker.update_params(thresh=33)
        files = WB.WorkbenchFiles(model=self.defaults.model,
                                  blocker_model=self.defaults.blocker_model,
                                  roi=self.defaults.roi, landmarks=self.defaults.landmarks,
                                  background=self.defaults.background)
        out = os.path.join(self.tmp, "out")
        settings, sets = VP.prepare_run(self.defaults, files, self.tracker,
                                        out_dir=out, tag="gui")
        self.assertIn("thresh=33", sets)
        self.assertIn("min_area=100", sets)
        c, j = OT.output_paths(out, SID, "gui")
        job = VP.RunJob(settings, csv_path=c, json_path=j, tag="gui",
                        start_frame=20, end_frame=45, warmup_frames=5)
        job.join(30)
        self.assertIsNone(job.error)
        argv, warn = WB.cli_args(self.defaults, files, sets, tag="cli", start_frame=20,
                                 end_frame=45, warmup_frames=5)
        self.assertEqual(warn, [])
        with mock.patch.object(CLI, "ratmodels_dir", lambda: self.models):
            self.assertEqual(CLI.main(argv + ["--out-dir", out]), 0)
        gui_rows = _read_rows(c)
        cli_rows = _read_rows(OT.output_paths(out, SID, "cli")[0])
        self.assertEqual(len(gui_rows), 26)
        self.assertEqual(gui_rows, cli_rows)

    def test_cancel_leaves_no_output(self):
        out = os.path.join(self.tmp, "out")
        c, j = OT.output_paths(out, SID, "x")
        with mock.patch.object(OTR, "PROGRESS_EVERY", 1):
            job = VP.RunJob(self.defaults, csv_path=c, json_path=j, tag="x")
            job.cancel()
            job.join(30)
        self.assertTrue(job.cancelled)
        self.assertFalse(os.path.exists(c))
        self.assertFalse(os.path.exists(c + ".partial"))

    def test_prepare_run_saves_in_memory_model(self):
        self.player.register(rat_range=(0, 39))
        self.assertTrue(_wait(lambda: self.player.take_job_result("register") is not None, 20))
        files = WB.WorkbenchFiles(model=OT.FileChoice(None, OT.SRC_REGISTERED),
                                  blocker_model=None, roi=None, landmarks=None,
                                  background=self.defaults.background)
        out = os.path.join(self.tmp, "out")
        settings, _ = VP.prepare_run(self.defaults, files, self.tracker, out_dir=out, tag="r")
        self.assertTrue(os.path.isfile(settings.model.path))
        self.assertTrue(settings.model.path.endswith("_offline_track_r_model.npz"))
        self.assertEqual(settings.model.source, OT.SRC_REGISTERED)
        edited = WB.WorkbenchFiles(model=None, blocker_model=None,
                                   roi=OT.FileChoice(None, WB.SRC_EDITED), landmarks=None,
                                   background=self.defaults.background)
        with self.assertRaises(ValueError):
            VP.prepare_run(self.defaults, edited, self.tracker, out_dir=out, tag="r")


if __name__ == "__main__":
    unittest.main()
