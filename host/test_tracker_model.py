"""Headless tests for RatModel settings persistence (see docs/decisions/0027).

Covers the acquisition-time TrackerParams that are stored in the saved model
.npz and restored on load:
  - full param snapshot survives a save/load round-trip (numpy only),
  - older models without stored settings still load (acq_params is None),
  - RatTracker.load_model restores every MODEL_REAPPLY_FIELDS value while
    leaving the provenance-only knobs (reg_*/adapt_*) untouched (needs OpenCV).

Run from host/:  python -m unittest test_tracker_model
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import asdict, replace

import tracker
from tracker import (MODEL_PROVENANCE_ONLY_FIELDS, MODEL_REAPPLY_FIELDS,
                     RatModel, TrackerParams)

_HAVE_NUMPY = tracker.np is not None
_HAVE_CV2 = tracker.cv2 is not None

# A params snapshot with every field pushed off its default, so a round-trip /
# restore that misses any field is caught.
_CUSTOM = replace(
    TrackerParams(),
    thresh=42, min_area=222.0, max_area=9000.0, blur=7, morph=7, proc_max_w=640,
    gate_radius=99.0, use_kalman=False,
    w_dist=0.3, w_hist=0.4, w_form=0.25, accept_score=0.62, confirm_frames=5,
    coast_max_frames=44, appearance_bias=0.7, form_tol=2.0, hist_min=0.1,
    reg_min_frames=50, reg_dominance=0.4, reg_logarea_spread_min=0.2,
    area_gate_k=4.2, adapt_enrich=True, adapt_min_conf=0.9, adapt_max_samples=100,
)


@unittest.skipUnless(_HAVE_NUMPY, "numpy required")
class TestRatModelPersistence(unittest.TestCase):
    def _sampled_model(self, acq_params) -> RatModel:
        np = tracker.np
        m = RatModel()
        m.animal = "600"
        m.date = "2026-07-07"
        m.acq_params = acq_params
        hist = np.ones((tracker.HIST_H_BINS, tracker.HIST_S_BINS), np.float64)
        for area in (400.0, 500.0, 600.0):
            m.add_sample(hist, tracker.compute_form_vector(area, 2.0, 0.9))
        m.recompute(_CUSTOM)
        return m

    def test_round_trip_preserves_acq_params(self):
        m = self._sampled_model(_CUSTOM)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.npz")
            m.save(path)
            loaded = RatModel.load(path)
        self.assertEqual(loaded.n, m.n)
        self.assertEqual(loaded.animal, "600")
        self.assertIsNotNone(loaded.acq_params)
        # Every field survives the JSON round-trip (proc_max_w explicitly checked).
        self.assertEqual(loaded.acq_params.proc_max_w, 640)
        self.assertEqual(asdict(loaded.acq_params), asdict(_CUSTOM))

    def test_backward_compat_without_acq_params(self):
        m = self._sampled_model(None)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "legacy.npz")
            m.save(path)
            loaded = RatModel.load(path)
        self.assertIsNone(loaded.acq_params)
        self.assertEqual(loaded.n, m.n)
        self.assertEqual(loaded.animal, m.animal)


@unittest.skipUnless(_HAVE_NUMPY and _HAVE_CV2, "numpy + OpenCV required")
class TestLoadModelRestore(unittest.TestCase):
    def test_load_restores_reapply_fields_only(self):
        from tracker import RatTracker
        np = tracker.np
        model = RatModel()
        model.acq_params = _CUSTOM
        model.add_sample(
            np.ones((tracker.HIST_H_BINS, tracker.HIST_S_BINS), np.float64),
            tracker.compute_form_vector(500.0, 2.0, 0.9))
        model.recompute(_CUSTOM)

        trk = RatTracker(TrackerParams())  # session starts at defaults
        defaults = TrackerParams()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.npz")
            model.save(path)
            trk.load_model(path)
        after = trk.get_params()

        # Reapply fields snapped to the model's saved values...
        for name in MODEL_REAPPLY_FIELDS:
            self.assertEqual(getattr(after, name), getattr(_CUSTOM, name),
                             f"{name} not restored")
        # ...provenance-only knobs left at the session defaults (not applied).
        for name in MODEL_PROVENANCE_ONLY_FIELDS:
            self.assertEqual(getattr(after, name), getattr(defaults, name),
                             f"{name} should not be reapplied")
            self.assertNotEqual(getattr(_CUSTOM, name), getattr(defaults, name),
                                f"test bug: {name} must differ to be meaningful")

    def test_load_without_settings_keeps_session(self):
        from tracker import RatTracker
        np = tracker.np
        model = RatModel()  # no acq_params
        model.add_sample(
            np.ones((tracker.HIST_H_BINS, tracker.HIST_S_BINS), np.float64),
            tracker.compute_form_vector(500.0, 2.0, 0.9))
        model.recompute(TrackerParams())

        session = replace(TrackerParams(), thresh=77, proc_max_w=512)
        trk = RatTracker(session)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "legacy.npz")
            model.save(path)
            trk.load_model(path)
        after = trk.get_params()
        self.assertEqual(after.thresh, 77)
        self.assertEqual(after.proc_max_w, 512)


if __name__ == "__main__":
    unittest.main()
