"""Headless tests for live head tracking (tmaze.vision.head_live + RatTracker.set_head).

Covers: the crop the tracker builds live is identical to the one the training
dataset / evaluator build offline; the shipped model loads and records which
file it is; a missing model is refused; the tracker attaches head samples only
while head tracking is on, feeds the filter a miss when no blob is chosen,
resets the filter with the track, and switches head tracking off (keeping the
position track) if the network fails.

Run from host/:  python -m unittest tests.test_head_live
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.head_filter import NO_PREDICTION, OK, HeadSample
from tmaze.vision import head_learned as HL
from tmaze.vision import tracker
from tmaze.vision.tracker import RatTracker, TrackerParams

_HAVE = tracker.cv2 is not None and tracker.np is not None
if _HAVE:
    from tmaze.tools import head_point_eval as E
    from tmaze.vision import head_live as LH

W, H = 800, 600


def _bg():
    np = tracker.np
    f = np.full((H, W, 3), 140, np.uint8)
    f[:, :, 0] = 120                     # not pure grey, so colour paths run too
    return f


def _rat(cx=400, cy=300):
    cv2 = tracker.cv2
    f = _bg()
    cv2.ellipse(f, (cx, cy), (70, 22), 20, 0, 360, (25, 25, 25), -1)
    cv2.ellipse(f, (cx - 95, cy - 30), (25, 4), 20, 0, 360, (40, 40, 40), -1)   # tail
    return f


def _tracker():
    trk = RatTracker(TrackerParams(proc_max_w=400, thresh=20, min_area=50.0,
                                   confirm_frames=1))
    trk.set_roi_polygon([(0.05, 0.05), (0.95, 0.05), (0.95, 0.95), (0.05, 0.95)])
    trk.grab_background()
    trk.process(_bg())
    return trk


class _Recorder:
    """Stands in for LiveHead: keeps the crop it was asked to run on."""

    def __init__(self):
        self.inputs, self.misses, self.resets = [], 0, 0

    def step(self, grey, diff, members, com, scale, thresh, morph, roi_mask=None):
        self.inputs.append(HL.net_input(grey, diff, members, com, thresh, morph, roi_mask))
        return HeadSample(True, OK, {"head": (1.0, 2.0)}, 0.0)

    def miss(self):
        self.misses += 1
        return HeadSample(False, NO_PREDICTION)

    def reset(self):
        self.resets += 1


class _Broken(_Recorder):
    def step(self, *a, **k):
        raise RuntimeError("boom")


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestLiveInputMatchesTraining(unittest.TestCase):
    def test_tracker_crop_equals_dataset_crop(self):
        trk = _tracker()
        rec = _Recorder()
        trk.set_head(rec)
        frame = _rat()
        result = trk.process(frame)
        self.assertTrue(result.detected)
        self.assertEqual(len(rec.inputs), 1)
        prepared = E._spine_masks(result, frame, _bg(), trk)
        offline = HL.crop_input(prepared.grey, prepared.diff,
                                prepared.masks["light"], prepared.com)
        self.assertTrue(tracker.np.array_equal(rec.inputs[0], offline))
        self.assertGreater(int(offline[2].max()), 0)        # the mask channel has the rat


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestTrackerHook(unittest.TestCase):
    def test_off_by_default(self):
        trk = _tracker()
        self.assertIsNone(trk.get_head())
        self.assertIsNone(trk.process(_rat()).head)

    def test_miss_when_no_blob_and_reset_with_track(self):
        trk = _tracker()
        rec = _Recorder()
        trk.set_head(rec)
        self.assertTrue(trk.process(_rat()).head.valid)
        r = trk.process(_bg())
        self.assertEqual(r.head.status, NO_PREDICTION)
        self.assertEqual(rec.misses, 1)
        trk.reset()
        trk.process(_rat())
        self.assertEqual(rec.resets, 1)
        trk.set_head(None)
        self.assertIsNone(trk.process(_rat()).head)

    def test_network_error_turns_head_tracking_off_only(self):
        trk = _tracker()
        trk.set_head(_Broken())
        r = trk.process(_rat())
        self.assertTrue(r.detected)                         # position track unaffected
        self.assertIsNotNone(r.centroid)
        self.assertIsNone(r.head)
        self.assertIsNone(trk.get_head())
        self.assertIn("boom", trk.head_error())
        trk.set_head(_Recorder())
        self.assertEqual(trk.head_error(), "")


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestShippedModel(unittest.TestCase):
    def test_loads_and_records_provenance(self):
        live = LH.LiveHead.load()
        self.assertEqual(live.model_info["file"], LH.MODEL_FILE)
        self.assertEqual(len(live.model_info["sha256"]), 64)
        self.assertTrue(live.model_info["sessions"])

    def test_runs_in_the_tracker_in_full_frame_pixels(self):
        trk = _tracker()
        trk.set_head(LH.LiveHead.load())
        r = trk.process(_rat())
        self.assertTrue(r.head.valid)
        self.assertEqual(set(r.head.points), set(HL.TARGETS))
        cx, cy = r.centroid
        half = HL.CROP_HALF_PX * W / 400.0                  # crop half-width in full px
        for x, y in r.head.points.values():
            self.assertLessEqual(abs(x - cx), half + 1)
            self.assertLessEqual(abs(y - cy), half + 1)
        self.assertIsNotNone(r.head.direction_deg)

    def test_missing_model_is_refused(self):
        with self.assertRaises(ValueError):
            LH.LiveHead.load(os.path.join(tempfile.gettempdir(), "no_such_head_net.onnx"))


if __name__ == "__main__":
    unittest.main()
