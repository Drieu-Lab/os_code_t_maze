"""Headless tests for clustering a fragmented rat (see docs/decisions/0037).

The rat's foreground mask is frequently several disconnected blobs (pose +
lighting). These tests check that:
  - nearby blobs merge into one candidate when within cluster_gap, and stay
    separate beyond it (and cluster_gap = 0 is a no-op / backward compatible),
  - the merge is colour-gated: with a model + cluster_hist_min, a differently-
    coloured speck is kept out while genuine same-colour fragments still merge,
  - registration is DECOUPLED: the colour support learns detached-but-nearby
    parts (proximity union) while the size band stays tied to the dominant
    gap-cluster only.

Run from host/:  python -m unittest test_tracker_cluster
"""

from __future__ import annotations

import unittest

from tmaze.vision import tracker
from tmaze.vision.tracker import RatTracker, TrackerParams

_HAVE = tracker.np is not None and tracker.cv2 is not None

_BG_GRAY = 200
_W = _H = 300


def _bg():
    np = tracker.np
    return np.full((_H, _W, 3), _BG_GRAY, dtype=np.uint8)


def _rect_contour(x0, y0, x1, y1):
    """A closed axis-aligned rectangle contour (proc-px, inclusive corners)."""
    np = tracker.np
    return np.array([[[x0, y0]], [[x1 - 1, y0]],
                     [[x1 - 1, y1 - 1]], [[x0, y1 - 1]]], dtype=np.int32)


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestClusterGeometry(unittest.TestCase):
    """Pure spatial merging via _detections + _cluster_detections (no model)."""

    def _trk(self, **kw):
        return RatTracker(TrackerParams(min_area=50.0, **kw))

    def _mask_two(self, gap):
        """Two 40x40 blobs `gap` px apart (horizontally) on a blank mask."""
        np = tracker.np
        m = np.zeros((_H, _W), np.uint8)
        m[80:120, 60:100] = 255
        x0 = 100 + gap
        m[80:120, x0:x0 + 40] = 255
        return m

    def test_bbox_gap(self):
        trk = self._trk()
        self.assertEqual(trk._bbox_gap((0, 0, 10, 10), (5, 5, 10, 10)), 0.0)   # overlap
        self.assertEqual(trk._bbox_gap((0, 0, 10, 10), (20, 0, 10, 10)), 10.0)  # x-gap
        self.assertAlmostEqual(
            trk._bbox_gap((0, 0, 10, 10), (13, 14, 5, 5)), 5.0, places=6)       # diag

    def test_merge_nearby_fragments(self):
        params = self._trk().get_params()
        params = TrackerParams(min_area=50.0, cluster_gap=30.0)
        trk = RatTracker(params)
        dets = trk._detections(self._mask_two(gap=10), params)
        self.assertEqual(len(dets), 2)  # raw: two blobs
        merged = trk._cluster_detections(dets, None, None, params, _W, _H)
        self.assertEqual(len(merged), 1)                    # merged into one
        self.assertIsNotNone(merged[0].members)
        self.assertEqual(len(merged[0].members), 2)         # keeps both fragments
        # Aggregated area ~ sum of the two 40x40 blobs; centroid between them.
        self.assertGreater(merged[0].area, 2 * 1400)
        cx, _cy = merged[0].centroid
        self.assertTrue(90 < cx < 110)

    def test_far_fragments_not_merged(self):
        params = TrackerParams(min_area=50.0, cluster_gap=30.0)
        trk = RatTracker(params)
        dets = trk._detections(self._mask_two(gap=60), params)  # 60 > cluster_gap
        merged = trk._cluster_detections(dets, None, None, params, _W, _H)
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(d.members is None for d in merged))

    def test_cluster_gap_zero_is_noop(self):
        params = TrackerParams(min_area=50.0, cluster_gap=0.0)
        trk = RatTracker(params)
        dets = trk._detections(self._mask_two(gap=5), params)  # very close
        merged = trk._cluster_detections(dets, None, None, params, _W, _H)
        self.assertEqual(len(merged), len(dets))
        self.assertTrue(all(d.members is None for d in merged))


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestClusterColourGate(unittest.TestCase):
    """The colour-membership gate keeps a wrong-coloured speck out of the rat."""

    # Two rat-coloured fragments (A, A2) and one differently-coloured blob (B),
    # all within cluster_gap of their neighbour.
    _A_BGR = (60, 60, 200)    # reddish
    _B_BGR = (200, 60, 60)    # bluish
    _ROWS = (90, 130)
    _A = (40, 80)
    _A2 = (90, 130)
    _B = (140, 180)

    def _frame_and_dets(self):
        np, cv2 = tracker.np, tracker.cv2
        f = _bg()
        r0, r1 = self._ROWS
        f[r0:r1, self._A[0]:self._A[1]] = self._A_BGR
        f[r0:r1, self._A2[0]:self._A2[1]] = self._A_BGR
        f[r0:r1, self._B[0]:self._B[1]] = self._B_BGR
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        mask = np.zeros((_H, _W), np.uint8)
        mask[r0:r1, self._A[0]:self._A[1]] = 255
        mask[r0:r1, self._A2[0]:self._A2[1]] = 255
        mask[r0:r1, self._B[0]:self._B[1]] = 255
        params = TrackerParams(min_area=50.0, cluster_gap=30.0)
        trk = RatTracker(params)
        dets = trk._detections(mask, params)
        self.assertEqual(len(dets), 3)
        return trk, dets, hsv, params

    def _model_supporting_A(self, trk, hsv):
        """A model whose colour support is exactly blob A's palette."""
        r0, r1 = self._ROWS
        cnt = _rect_contour(self._A[0], r0, self._A[1], r1)
        model = tracker.RatModel()
        model.hist_counts = trk._blob_histogram(hsv, [cnt], normalize=False)
        return model

    def test_gate_off_merges_all(self):
        trk, dets, hsv, params = self._frame_and_dets()
        params = TrackerParams(min_area=50.0, cluster_gap=30.0, cluster_hist_min=0.0)
        merged = trk._cluster_detections(dets, None, hsv, params, _W, _H)
        self.assertEqual(len(merged), 1)  # purely spatial -> everything joins

    def test_gate_rejects_wrong_colour(self):
        trk, dets, hsv, _ = self._frame_and_dets()
        model = self._model_supporting_A(trk, hsv)
        # Sanity: the membership metric separates the two colours.
        r0, r1 = self._ROWS
        a_hist = trk._blob_histogram(hsv, [_rect_contour(self._A[0], r0, self._A[1], r1)],
                                     normalize=False)
        b_hist = trk._blob_histogram(hsv, [_rect_contour(self._B[0], r0, self._B[1], r1)],
                                     normalize=False)
        self.assertGreater(model.in_support_fraction(a_hist), 0.9)
        self.assertLess(model.in_support_fraction(b_hist), 0.1)
        # Gate on: A + A2 merge (same colour), B stays out (wrong colour).
        params = TrackerParams(min_area=50.0, cluster_gap=30.0, cluster_hist_min=0.5)
        merged = trk._cluster_detections(dets, model, hsv, params, _W, _H)
        self.assertEqual(len(merged), 2)
        with_members = [d for d in merged if d.members is not None]
        self.assertEqual(len(with_members), 1)
        self.assertEqual(len(with_members[0].members), 2)  # A + A2 only


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestDecoupledRegistration(unittest.TestCase):
    """Registration learns colour broadly (proximity union) but size narrowly
    (dominant gap-cluster only) - see docs/decisions/0037."""

    _BODY = (120, 170)          # 50x50 black body (dominant)
    _TAIL = (196, 244)          # 48x48 coloured tail, detached beyond cluster_gap
    _ROWS_BODY = (120, 170)
    _ROWS_TAIL = (120, 168)
    _TAIL_BGR = (60, 150, 220)  # a distinct (non-black) colour

    def _draw(self, f):
        rb0, rb1 = self._ROWS_BODY
        f[rb0:rb1, self._BODY[0]:self._BODY[1]] = (0, 0, 0)
        rt0, rt1 = self._ROWS_TAIL
        f[rt0:rt1, self._TAIL[0]:self._TAIL[1]] = self._TAIL_BGR

    def _register(self):
        params = TrackerParams(thresh=20, min_area=50.0, cluster_gap=5.0,
                               reg_proximity_mult=30.0, reg_min_frames=3,
                               reg_logarea_spread_min=0.0)
        trk = RatTracker(params)
        trk.grab_background()
        trk.process(_bg())
        trk.start_registration()
        for _ in range(5):
            f = _bg()
            self._draw(f)
            trk.process(f)
        trk.finalize_registration()
        trk.process(_bg())  # finalize consumed on the next frame
        return trk

    def test_colour_support_includes_tail(self):
        cv2 = tracker.cv2
        trk = self._register()
        model = trk.get_model()
        self.assertIsNotNone(model)
        self.assertGreater(model.n, 0)
        f = _bg()
        self._draw(f)
        hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
        rt0, rt1 = self._ROWS_TAIL
        tail_cnt = _rect_contour(self._TAIL[0], rt0, self._TAIL[1], rt1)
        tail_hist = trk._blob_histogram(hsv, [tail_cnt], normalize=False)
        # The detached tail's colour was learned even though it never merged into
        # the dominant (form) cluster.
        self.assertGreater(model.in_support_fraction(tail_hist), 0.8)

    def test_size_band_is_dominant_only(self):
        trk = self._register()
        model = trk.get_model()
        # Measure the two parts' areas independently.
        params = trk.get_params()
        np = tracker.np
        body_mask = np.zeros((_H, _W), np.uint8)
        rb0, rb1 = self._ROWS_BODY
        body_mask[rb0:rb1, self._BODY[0]:self._BODY[1]] = 255
        body_area = trk._detections(body_mask, params)[0].area
        tail_mask = np.zeros((_H, _W), np.uint8)
        rt0, rt1 = self._ROWS_TAIL
        tail_mask[rt0:rt1, self._TAIL[0]:self._TAIL[1]] = 255
        tail_area = trk._detections(tail_mask, params)[0].area
        # The learned size centre matches the BODY (dominant), not the union.
        self.assertAlmostEqual(model.area_p50, body_area, delta=0.25 * body_area)
        # ...and the union would fall OUTSIDE the size band (proving the band was
        # NOT learned from a proximity union, which would over-state the area).
        self.assertGreater(body_area + tail_area, model.area_max)


if __name__ == "__main__":
    unittest.main()
