"""
test_tracker_view_options.py - the Tracking tab's host options (ADR-0213).

The default options must keep the live rig's behaviour: load host/roi_polygon.json
and host/landmarks.json at start, save to and delete those files. The offline
workbench's options must never read or write them. The host files are pointed at
a temp folder for every test, so the real host/ files are never touched.

Needs Tk + numpy + OpenCV + Pillow (skips otherwise).

Run from host/:  python -m unittest tests.test_tracker_view_options
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import tkinter as tk
import unittest
from unittest import mock

try:
    import numpy as np
    from tmaze.ui import tracker_view as TV
    from tmaze.vision.tracker import RatTracker, draw_overlay
    _DEPS = TV.TrackerControlView._missing_dependency() is None
except Exception:  # pragma: no cover
    _DEPS = False

_ROI = [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]]
_LM = [{"kind": "custom", "label": "p", "x": 0.5, "y": 0.5}]

WORKBENCH = None if not _DEPS else TV.TrackerTabOptions(
    load_host_files=False, ask_before_saving=True, show_preview=False,
    hide=frozenset({TV.HIDE_ENABLE, TV.HIDE_GRAB_BG, TV.HIDE_LED_BOARD,
                    TV.HIDE_TRACK_LOG}))


def _texts(widget):
    """{button/checkbox text: widget} for every descendant."""
    out = {}
    for child in widget.winfo_children():
        try:
            t = child.cget("text")
            if t:
                out[str(t)] = child
        except tk.TclError:
            pass
        out.update(_texts(child))
    return out


def _shown(widget, top) -> bool:
    """True when ``widget`` and every container up to ``top`` is laid out."""
    w = widget
    while w is not None and w is not top:
        if w.winfo_manager() == "":
            return False
        w = w.master
    return True


def _read(path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


@unittest.skipUnless(_DEPS, "needs numpy, OpenCV, Pillow")
class TestTrackerTabOptions(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:            # no display (headless CI)
            raise unittest.SkipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.tmp = tempfile.mkdtemp(prefix="tracker_tab_")
        self.roi_path = os.path.join(self.tmp, "roi_polygon.json")
        self.lm_path = os.path.join(self.tmp, "landmarks.json")
        with open(self.roi_path, "w", encoding="utf-8") as f:
            json.dump({"polygon_norm": _ROI}, f)
        with open(self.lm_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "landmarks": _LM}, f)
        self.patches = [mock.patch.object(TV, "ROI_PATH", self.roi_path),
                        mock.patch.object(TV, "LANDMARKS_PATH", self.lm_path)]
        for p in self.patches:
            p.start()
        self.views = []

    def tearDown(self):
        for v in self.views:
            v.close()
        for p in self.patches:
            p.stop()
        self.root.destroy()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _view(self, options=None, tracker=None):
        tracker = tracker or RatTracker()
        v = TV.TrackerControlView(self.root, tracker=tracker, camera=None, options=options)
        v.widget().pack()
        self.root.update_idletasks()
        self.views.append(v)
        return v, tracker

    def test_default_loads_host_files_and_shows_everything(self):
        v, tr = self._view()
        self.assertIsNotNone(tr.get_roi_polygon())
        self.assertEqual(len(tr.get_landmarks()), 1)
        w = _texts(v.widget())
        for text in ("Enable tracking", "Grab background", "Define LED board region...",
                     "Start track log", "Save ROI", "Show mask (tracked blob in green)"):
            self.assertIn(text, w)
            self.assertTrue(_shown(w[text], v.widget()), text)
        self.assertNotIn("Save landmarks...", w)

    def test_default_saves_and_deletes_host_files(self):
        v, tr = self._view()
        os.remove(self.roi_path)
        v._save_roi()
        self.assertTrue(os.path.isfile(self.roi_path))
        os.remove(self.lm_path)
        v._on_landmarks_committed(_LM)
        self.assertTrue(os.path.isfile(self.lm_path))
        v._clear_landmarks()
        self.assertFalse(os.path.exists(self.lm_path))

    def test_default_keeps_enrich_off(self):
        _, tr = self._view()
        self.assertFalse(tr.get_params().adapt_enrich)

    def test_workbench_never_reads_host_files(self):
        _, tr = self._view(WORKBENCH)
        self.assertIsNone(tr.get_roi_polygon())
        self.assertEqual(tr.get_landmarks(), [])

    def test_workbench_hides_live_only_controls(self):
        v, _ = self._view(WORKBENCH)
        w = _texts(v.widget())
        for text in ("Enable tracking", "Grab background", "Define LED board region...",
                     "Start track log", "Show mask (tracked blob in green)"):
            self.assertFalse(_shown(w[text], v.widget()), text)
        self.assertIn("Save ROI...", w)
        self.assertIn("Save landmarks...", w)

    def test_workbench_saves_only_where_asked(self):
        v, tr = self._view(WORKBENCH)
        before = {p: _read(p) for p in (self.roi_path, self.lm_path)}
        tr.set_roi_polygon([tuple(p) for p in _ROI])
        chosen = os.path.join(self.tmp, "chosen", "my_roi.json")
        with mock.patch.object(TV.filedialog, "asksaveasfilename", return_value=chosen):
            v._save_roi()
        self.assertTrue(os.path.isfile(chosen))
        with mock.patch.object(TV.filedialog, "asksaveasfilename", return_value=""):
            v._save_roi()                     # cancelled dialog: nothing written
        v._on_landmarks_committed(_LM)        # in memory only
        self.assertEqual(len(tr.get_landmarks()), 1)
        lm_chosen = os.path.join(self.tmp, "chosen", "my_lm.json")
        with mock.patch.object(TV.filedialog, "asksaveasfilename", return_value=lm_chosen):
            v._save_landmarks_as()
        self.assertTrue(os.path.isfile(lm_chosen))
        v._clear_landmarks()
        after = {p: _read(p) for p in (self.roi_path, self.lm_path)}
        self.assertEqual(before, after)

    def test_enrich_follows_tracker_and_refresh(self):
        tr = RatTracker()
        tr.update_params(adapt_enrich=True, thresh=33)
        v, _ = self._view(WORKBENCH, tracker=tr)
        self.assertTrue(tr.get_params().adapt_enrich)   # not reset by the tab
        self.assertEqual(tr.get_params().thresh, 33)
        tr.update_params(thresh=55, adapt_enrich=False)
        v.refresh_from_tracker()
        self.assertEqual(int(v._sliders["thresh"][0].get()), 55)
        self.assertFalse(v._enrich_var.get())

    def test_active_paths(self):
        v, _ = self._view(WORKBENCH)
        self.assertIsNone(v.active_blocker_model_path())
        v.set_active_paths("m.npz", "b.npz")
        self.assertEqual(v.active_model_path(), "m.npz")
        self.assertEqual(v.active_blocker_model_path(), "b.npz")
        v._clear_blocker()
        self.assertIsNone(v.active_blocker_model_path())


@unittest.skipUnless(_DEPS, "needs numpy, OpenCV")
class TestDrawOverlayToggles(unittest.TestCase):
    def test_roi_toggle(self):
        tr = RatTracker()
        tr.set_roi_polygon([tuple(p) for p in _ROI])
        on = np.zeros((100, 100, 3), np.uint8)
        off = on.copy()
        draw_overlay(on, 1.0, tr, None)
        draw_overlay(off, 1.0, tr, None, draw_roi=False)
        self.assertGreater(int(on.sum()), 0)
        self.assertEqual(int(off.sum()), 0)


if __name__ == "__main__":
    unittest.main()
