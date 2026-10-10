"""
test_offline_workbench_ui.py - the offline workbench window (FW-0074, ADR-0216).

On the synthetic moving-square session from ``test_offline_track``, with the
window built but hidden:
- it shows frames and follows seeks;
- the Files panel follows the hosted tab (a drawn ROI is "changed here, not
  saved", and a run refuses to start until it is saved);
- a setting changed in the window shows as layer "you" and appears as --set in
  the copied command;
- a rat model built from the A-B marks becomes --register-frames;
- a range run started from the window and the copied command give the same rows.

Needs Tk + numpy + OpenCV with an mp4 writer + Pillow (skips otherwise).

Run from host/:  python -m unittest tests.test_offline_workbench_ui
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import tkinter as tk
import unittest
from unittest import mock

from tests.test_offline_track import _HAVE_CV, SID, _make_session, _read_rows
from tmaze.core import offline_track as OT
from tmaze.core import offline_workbench as WB

try:
    from tmaze.tools import offline_track as CLI
    from tmaze.ui import offline_workbench as UI
    from tmaze.ui.gui_deps import missing_dependency
    _DEPS = _HAVE_CV and missing_dependency("pillow") is None
except Exception:  # pragma: no cover
    _DEPS = False


@unittest.skipUnless(_DEPS, "needs numpy, OpenCV, Pillow")
class TestWorkbenchWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="workbench_ui_")
        self.bundle = os.path.join(self.tmp, SID)
        self.models = os.path.join(self.tmp, "ratmodels")
        os.makedirs(self.models)
        if not _make_session(self.bundle, with_png=True):
            self.skipTest("no mp4 video writer in this OpenCV build")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:            # no display (headless CI)
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        session = UI.load_session(self.bundle, OT.Overrides(sets=("min_area=100",)),
                                  ratmodels_dir=self.models)
        self.wb = UI.OfflineWorkbench(self.root, session, ratmodels_dir=self.models, warmup=10)
        self.assertTrue(self._pump(lambda: self.wb._last_idx == 0))

    def tearDown(self):
        if hasattr(self, "wb"):
            self.wb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pump(self, cond, timeout=20.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.root.update()
            if cond():
                return True
            time.sleep(0.01)
        return False

    def _idle(self):
        p = self.wb.player
        return not (p._seek is not None or p._step or p._retrack or p._jobs or p.busy())

    def test_follows_seek(self):
        self.wb.player.seek(30)
        self.assertTrue(self._pump(lambda: self.wb.frame_var.get() == "30"))
        self.assertIsNotNone(self.wb.player.shown().result)

    def test_files_follow_tab_and_block_unsaved_run(self):
        self.wb._on_tab_event("roi", "edit", None)
        self.assertEqual(self.wb.files["roi"].source, WB.SRC_EDITED)
        self.assertIn("not saved", self.wb.file_vars["roi"].get())
        self.wb.tracker.set_roi_polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
        err = self.wb.start_run()
        self.assertIn("ROI", err or "")
        path = os.path.join(self.tmp, "roi.json")
        self.assertTrue(self.wb.save_file("roi", path))
        self.assertEqual(self.wb.files["roi"].path, os.path.abspath(path))
        argv, warnings = self.wb.command()
        self.assertIn(os.path.abspath(path), argv)
        self.assertEqual(warnings, [])

    def test_setting_change_shows_as_you_and_in_command(self):
        self.wb.apply_settings({"thresh": "33"})
        self.assertTrue(self._pump(
            lambda: self.wb.table.item("thresh", "values")[1] == WB.LAYER_YOU))
        argv, _ = self.wb.command()
        self.assertIn("thresh=33", argv)
        self.assertIn("min_area=100.0", argv)
        with self.assertRaises(ValueError):
            self.wb.apply_settings({"no_such_setting": "1"})

    def test_rat_model_from_marks_becomes_register_frames(self):
        self.wb.player.seek(0)
        self.assertTrue(self._pump(self._idle))
        self.wb.set_mark("a")
        self.wb.player.seek(39)
        self.assertTrue(self._pump(lambda: self.wb.player.shown().idx == 39))
        self.wb.set_mark("b")
        self.wb.build_rat()
        self.assertTrue(self._pump(lambda: self.wb.reg_rat is not None, 30))
        self.assertEqual(self.wb.files["model"].source, OT.SRC_REGISTERED)
        argv, _ = self.wb.command()
        i = argv.index("--register-frames")
        self.assertEqual(argv[i + 1:i + 3], ["0", "39"])

    def test_window_run_matches_copied_command(self):
        self.wb.apply_settings({"thresh": "31"})
        self.wb.marks = WB.Marks(a=20, b=45)
        self.wb.range_var.set("ab")
        self.wb.run_warmup_var.set(5)
        self.wb.tag_var.set("gui")
        self.assertIsNone(self.wb.start_run())
        self.assertTrue(self._pump(lambda: self.wb.run_job is None, 60))
        self.assertIn("Done", self.wb.run_status.get())
        self.wb.tag_var.set("cli")
        argv, warnings = self.wb.command()
        self.assertEqual(warnings, [])
        with mock.patch.object(CLI, "ratmodels_dir", lambda: self.models):
            self.assertEqual(CLI.main(argv), 0)
        video = os.path.join(self.bundle, "video")
        gui = _read_rows(OT.output_paths(video, SID, "gui")[0])
        cli = _read_rows(OT.output_paths(video, SID, "cli")[0])
        self.assertEqual(len(gui), 26)
        self.assertEqual(gui, cli)
        self.assertIn("gui", WB.offline_runs(self.bundle))


if __name__ == "__main__":
    unittest.main()
