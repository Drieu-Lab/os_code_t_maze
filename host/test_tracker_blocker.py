"""Headless tests for parallel blocker tracking (see docs/decisions/0034).

Builds synthetic BGR frames (no camera) with a black "rat" square and an
orange-bordered "blocker" square on a flat empty-rig background, and checks:
  - registration learns an orange range that brackets the tape's real hue and
    survives a save/load round-trip,
  - during tracking the blocker's footprint is subtracted so the rat is not
    stolen, including when the rat is touching the blocker (merged contour),
  - with no blocker model the tracker behaves exactly as before.

Run from host/:  python -m unittest test_tracker_blocker
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.vision import tracker
from tmaze.vision.tracker import RatTracker, TrackerParams

_HAVE = tracker.np is not None and tracker.cv2 is not None

# BGR (0, 128, 255) is orange -> OpenCV HSV hue == 15, S == V == 255, so it sits
# well above the saturation/value floors that isolate tape from black surfaces.
_ORANGE_BGR = (0, 128, 255)
_TAPE_HUE = 15
# Light background so both the black rat/interior AND the orange tape (luminance
# ~151) sit clearly above the foreground diff threshold, as they do on the real
# empty-rig floor. (A mid-gray 127 would make the orange nearly invisible.)
_BG_GRAY = 200
_W = _H = 300


def _bg():
    np = tracker.np
    return np.full((_H, _W, 3), _BG_GRAY, dtype=np.uint8)


def _draw_rat(img, x, y, s=40):
    img[y:y + s, x:x + s] = (0, 0, 0)


def _draw_blocker(img, x, y, s=70, border=8):
    """Black square with an orange (taped) border."""
    img[y:y + s, x:x + s] = (0, 0, 0)
    o = _ORANGE_BGR
    img[y:y + border, x:x + s] = o
    img[y + s - border:y + s, x:x + s] = o
    img[y:y + s, x:x + border] = o
    img[y:y + s, x + s - border:x + s] = o


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestBlockerTracking(unittest.TestCase):
    def _tracker(self):
        trk = RatTracker(TrackerParams())
        trk.grab_background()
        trk.process(_bg())  # empty-rig reference
        return trk

    def _register_blocker(self, trk, placements=((40, 40, 70), (170, 150, 90))):
        for (x, y, s) in placements:
            f = _bg()
            _draw_blocker(f, x, y, s)
            trk.capture_blocker_sample()
            trk.process(f)
        trk.finalize_blocker_model()
        trk.process(_bg())  # finalize is consumed on the next processed frame
        return trk.get_blocker_model()

    def test_registration_learns_orange_range(self):
        trk = self._tracker()
        model = self._register_blocker(trk)
        self.assertIsNotNone(model)
        self.assertGreater(model.n, 0)
        self.assertIsNotNone(model.orange_lo)
        lo, hi = model.orange_lo, model.orange_hi
        # Learned hue band brackets the real tape hue...
        self.assertLessEqual(lo[0], _TAPE_HUE)
        self.assertGreaterEqual(hi[0], _TAPE_HUE)
        # ...and is tight (not the whole hue circle).
        self.assertLess(hi[0] - lo[0], 30)
        # Saturation/value floors are meaningful (tape is bright + saturated).
        self.assertGreater(lo[1], 0)
        self.assertGreater(lo[2], 0)

    def test_orange_range_survives_save_load(self):
        trk = self._tracker()
        model = self._register_blocker(trk)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "blocker.npz")
            self.assertTrue(trk.save_blocker_model(path))
            trk.clear_blocker_model()
            self.assertFalse(trk.has_blocker_model())
            self.assertTrue(trk.load_blocker_model(path))
        loaded = trk.get_blocker_model()
        self.assertEqual(loaded.orange_lo, model.orange_lo)
        self.assertEqual(loaded.orange_hi, model.orange_hi)

    def test_blocker_excluded_from_rat(self):
        """A lone blocker is claimed + dropped; nothing is tracked as the rat."""
        trk = self._tracker()
        self._register_blocker(trk)
        f = _bg()
        _draw_blocker(f, 40, 40, 70)
        res = trk.process(f)
        self.assertTrue(res.blocker_detected)
        self.assertIsNotNone(res.blocker_centroid)
        bx, by = res.blocker_centroid
        self.assertAlmostEqual(bx, 40 + 35, delta=18)
        self.assertAlmostEqual(by, 40 + 35, delta=18)
        # The blocker is not mistaken for the rat.
        self.assertFalse(res.detected)

    def test_rat_tracked_near_but_separate_blocker(self):
        """Rat close to (but not fused with) the blocker: the blocker's own blob
        is dropped, the rat's separate blob is kept, and the tracked point is on
        the rat - not pulled onto the blocker (ADR-0039 containment-drop)."""
        trk = self._tracker()
        self._register_blocker(trk)
        bx, by, bs = 60, 60, 70
        # Seed the footprint from a clean lone-blocker frame first.
        f0 = _bg()
        _draw_blocker(f0, bx, by, bs)
        trk.process(f0)
        # Rat with a clear gap from the blocker's right edge (contours stay
        # separate; the rat blob does not overlap the dilated footprint halo).
        f1 = _bg()
        _draw_blocker(f1, bx, by, bs)
        rat_x = bx + bs + 15         # 15 px gap -> distinct contour
        rat_y = by + 15
        _draw_rat(f1, rat_x, rat_y, 40)
        res = trk.process(f1)
        self.assertTrue(res.detected, "separate rat next to blocker must track")
        self.assertTrue(res.blocker_detected)
        cx, _cy = res.centroid
        rat_center_x = rat_x + 20
        blocker_center_x = bx + bs // 2
        self.assertGreater(cx, blocker_center_x + 10)
        self.assertAlmostEqual(cx, rat_center_x, delta=25)

    def test_fused_rat_blocker_not_stolen(self):
        """Documented ADR-0039 trade-off: when the rat is FUSED with the blocker
        into one contour, whole-detection dropping cannot split it - the fused
        blob is dropped (its area is mostly inside the footprint), so the tracker
        does NOT lock onto the blocker. The rat coasts through direct contact
        instead of the track being stolen onto the blocker."""
        trk = self._tracker()
        self._register_blocker(trk)
        bx, by, bs = 60, 60, 70
        f0 = _bg()
        _draw_blocker(f0, bx, by, bs)
        trk.process(f0)
        # Rat pressed against the blocker's right edge so the contours merge.
        f1 = _bg()
        _draw_blocker(f1, bx, by, bs)
        _draw_rat(f1, bx + bs - 4, by + 15, 40)
        res = trk.process(f1)
        self.assertTrue(res.blocker_detected)
        # The fused blob is dropped rather than tracked as (or stealing) the rat.
        self.assertFalse(res.detected,
                         "fused rat+blocker blob must not be locked as the rat")

    def test_footprint_clears_when_blocker_leaves(self):
        """Once the blocker (and its tape) leaves view, the footprint stops
        masking, so a rat at the former blocker spot is tracked again."""
        trk = self._tracker()
        self._register_blocker(trk)
        bx, by, bs = 40, 40, 70
        # Blocker present -> footprint active, nothing tracked as the rat.
        f0 = _bg()
        _draw_blocker(f0, bx, by, bs)
        res0 = trk.process(f0)
        self.assertTrue(res0.blocker_detected)
        self.assertFalse(res0.detected)
        # Blocker gone; a rat now sits at the blocker's former location.
        f1 = _bg()
        rat_x, rat_y = bx + 15, by + 15
        _draw_rat(f1, rat_x, rat_y, 40)
        res1 = trk.process(f1)
        self.assertFalse(res1.blocker_detected, "stale footprint should be cleared")
        self.assertTrue(res1.detected, "rat at the former blocker spot must track")
        cx, cy = res1.centroid
        self.assertAlmostEqual(cx, rat_x + 20, delta=15)
        self.assertAlmostEqual(cy, rat_y + 20, delta=15)

    def test_no_blocker_model_unchanged(self):
        """Without a blocker model the largest blob is tracked as the rat."""
        trk = self._tracker()
        f = _bg()
        _draw_rat(f, 120, 120, 40)
        res = trk.process(f)
        self.assertTrue(res.detected)
        self.assertFalse(res.blocker_detected)
        cx, cy = res.centroid
        self.assertAlmostEqual(cx, 140, delta=12)
        self.assertAlmostEqual(cy, 140, delta=12)


if __name__ == "__main__":
    unittest.main()
