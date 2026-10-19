"""Headless tests for the skeleton head-point estimator (tmaze.vision.head_point).

Synthetic blobs stand in for a rat: an elliptical body plus a thin tail drawn at
several angles. Checks that both head/tail rules put the head point at the body's
front tip (not the tail), that a tailless ellipse is flagged not-valid (nothing
says which end is the head), that ``head_far`` reaches a detached head fragment,
and that ``spine_mask`` recovers tail pixels the tracker's mask lost without
picking up a far speck.

Run from host/:  python -m unittest tests.test_head_point
"""

from __future__ import annotations

import math
import unittest

from tmaze.vision import head_point as HP

_HAVE_CV = HP.cv2 is not None and HP.np is not None and hasattr(HP.cv2, "ximgproc")

SIZE = 320
CENTRE = (160, 160)
BODY_HALF_LEN = 35
BODY_HALF_WIDTH = 13
TAIL_LEN = 50
TAIL_THICK = 4


def _unit(deg):
    return (math.cos(math.radians(deg)), math.sin(math.radians(deg)))


def _rat(deg, tail=True, head_fragment=False):
    """Mask of a body ellipse facing ``deg`` (+ optional tail behind, + optional
    detached head disc in front). Returns (mask, front_tip, tail_tip, head_disc_tip)."""
    cv2, np = HP.cv2, HP.np
    m = np.zeros((SIZE, SIZE), np.uint8)
    u = _unit(deg)
    cv2.ellipse(m, CENTRE, (BODY_HALF_LEN, BODY_HALF_WIDTH), deg, 0, 360, 255, -1)
    front = (CENTRE[0] + u[0] * BODY_HALF_LEN, CENTRE[1] + u[1] * BODY_HALF_LEN)
    rear = (CENTRE[0] - u[0] * (BODY_HALF_LEN - 2), CENTRE[1] - u[1] * (BODY_HALF_LEN - 2))
    tail_tip = (rear[0] - u[0] * TAIL_LEN, rear[1] - u[1] * TAIL_LEN)
    if tail:
        cv2.line(m, (int(rear[0]), int(rear[1])), (int(tail_tip[0]), int(tail_tip[1])),
                 255, TAIL_THICK)
    disc_tip = None
    if head_fragment:
        r = 7
        c = (CENTRE[0] + u[0] * (BODY_HALF_LEN + 4 + r), CENTRE[1] + u[1] * (BODY_HALF_LEN + 4 + r))
        cv2.circle(m, (int(round(c[0])), int(round(c[1]))), r, 255, -1)
        disc_tip = (c[0] + u[0] * r, c[1] + u[1] * r)
    return m, front, tail_tip, disc_tip


def _com(mask):
    mo = HP.cv2.moments(mask, binaryImage=True)
    return (mo["m10"] / mo["m00"], mo["m01"] / mo["m00"])


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


@unittest.skipUnless(_HAVE_CV, "OpenCV with ximgproc + numpy required")
class TestHeadPoint(unittest.TestCase):
    ANGLES = (0, 37, 90, 135, 200, 291)

    def test_both_rules_find_the_front_tip(self):
        for rule in HP.RULES:
            for deg in self.ANGLES:
                with self.subTest(rule=rule, deg=deg):
                    mask, front, tail_tip, _ = _rat(deg)
                    est = HP.estimate_head(mask, _com(mask), rule=rule)
                    self.assertTrue(est.valid, f"margin={est.margin:.2f}")
                    self.assertLess(_dist(est.head, front), 6.0)
                    self.assertLess(_dist(est.tail, tail_tip), 8.0)

    def test_head_point_is_ahead_of_the_centre_of_mass(self):
        mask, front, _, _ = _rat(0)
        com = _com(mask)
        est = HP.estimate_head(mask, com)
        self.assertLess(_dist(est.head, front), _dist(com, front) - 20)

    def test_tailless_ellipse_is_not_valid(self):
        for rule in HP.RULES:
            with self.subTest(rule=rule):
                mask, _, _, _ = _rat(30, tail=False)
                est = HP.estimate_head(mask, _com(mask), rule=rule)
                self.assertFalse(est.valid)

    def test_head_far_reaches_a_detached_head_fragment(self):
        mask, front, _, disc_tip = _rat(60, head_fragment=True)
        est = HP.estimate_head(mask, _com(mask))
        self.assertTrue(est.valid)
        self.assertLess(_dist(est.head, front), 6.0)      # spine ends at the body
        self.assertLess(_dist(est.head_far, disc_tip), 4.0)

    def test_empty_and_tiny_masks(self):
        np = HP.np
        self.assertFalse(HP.estimate_head(np.zeros((50, 50), np.uint8), (25, 25)).valid)
        tiny = np.zeros((50, 50), np.uint8)
        HP.cv2.circle(tiny, (25, 25), 3, 255, -1)
        self.assertFalse(HP.estimate_head(tiny, (25, 25)).valid)

    def test_unknown_rule_raises(self):
        mask, _, _, _ = _rat(0)
        with self.assertRaises(ValueError):
            HP.estimate_head(mask, _com(mask), rule="nope")

    def test_tracker_motion_overrules_a_misleading_shape(self):
        # A thin stub at the FRONT and no tail: shape alone says the front is the tail.
        cv2 = HP.cv2
        shape_only = None
        tracker = HP.HeadTracker(rule=HP.RULE_THIN_END,
                                 weights=HP.OrientationWeights(shape=1.0, motion=3.0))
        for step in range(8):
            dx = 3 * step
            mask, front, _, _ = _rat(0, tail=False)
            mask = HP.np.roll(mask, dx, axis=1)
            front = (front[0] + dx, front[1])
            cv2.line(mask, (int(front[0]) - 2, int(front[1])), (int(front[0]) + 30, int(front[1])),
                     255, TAIL_THICK)
            com = _com(mask)
            if shape_only is None:
                shape_only = HP.estimate_head(mask, com)
            est = tracker.update(mask, com)
        self.assertLess(shape_only.head[0], CENTRE[0])        # shape alone: wrong end
        self.assertGreater(est.votes["motion"], 0.5)
        self.assertGreater(est.head[0], front[0])              # motion: the stub is the front
        self.assertTrue(est.valid)

    def test_tracker_keeps_orientation_when_still_and_ambiguous(self):
        tracker = HP.HeadTracker()
        mask, front, _, _ = _rat(0)
        first = tracker.update(mask, _com(mask))
        self.assertLess(_dist(first.head, front), 6.0)
        round_mask, _, _, _ = _rat(0, tail=False)    # tail hidden, rat not moving
        for _ in range(5):
            est = tracker.update(round_mask, _com(round_mask))
        self.assertGreater(est.votes["continuity"], 0.5)
        self.assertLess(_dist(est.head, front), 6.0)

    def test_tracker_resets_when_the_rat_is_lost(self):
        tracker = HP.HeadTracker()
        mask, _, _, _ = _rat(0)
        tracker.update(mask, _com(mask))
        self.assertFalse(tracker.update(mask, None).valid)
        est = tracker.update(mask, _com(mask))
        self.assertEqual(est.votes["continuity"], 0.0)
        self.assertEqual(est.votes["motion"], 0.0)

    def test_spine_mask_recovers_nearby_tail_but_not_far_specks(self):
        cv2, np = HP.cv2, HP.np
        body_only, _, _, _ = _rat(0, tail=False)
        light, _, tail_tip, _ = _rat(0, tail=True)
        cv2.circle(light, (20, 20), 4, 255, -1)          # unrelated far speck
        contours, _ = cv2.findContours(body_only, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        sm = HP.spine_mask(light, contours, grow_px=6)
        # The tail grows from the body edge, so only its first ~grow_px join.
        self.assertGreater(int(np.count_nonzero(sm)), int(np.count_nonzero(body_only)))
        self.assertEqual(int(sm[20, 20]), 0)


if __name__ == "__main__":
    unittest.main()
