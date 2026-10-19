"""Headless tests for the live head-point filter (tmaze.core.head_filter).

Covers: the first prediction is accepted as is; a still head is smoothed with
the configured weight, every point the same way, a moving one not at all, with
stillness judged on raw steps; a one-frame leap is held (invalid) and dropped,
judged against the last raw head so smoothing lag never trips it; a real move
is accepted once enough frames agree;
disagreeing leap frames restart the count; head direction comes from the
smoothed neck and head centre in image axes; after a long gap the filter
starts fresh. Stdlib only.

Run from host/:  python -m unittest tests.test_head_filter
"""

from __future__ import annotations

import unittest

from tmaze.core import head_filter as HF


def pose(x, y, dx=10.0):
    """Head centre at (x, y), neck ``dx`` px to its left (head points image-right)."""
    return {"head": (x, y), "neck": (x - dx, y), "tail_base": (x - 4 * dx, y)}


class TestHeadFilter(unittest.TestCase):
    def setUp(self):
        self.f = HF.HeadFilter(HF.HeadFilterParams(max_step_px=40, confirm_frames=3,
                                                   agree_px=15, still_px=3, still_alpha=0.5,
                                                   max_gap_frames=2))

    def test_first_prediction_is_taken_as_is(self):
        s = self.f.update(pose(100, 50))
        self.assertTrue(s.valid)
        self.assertEqual(s.status, HF.OK)
        self.assertEqual(s.points["head"], (100, 50))
        self.assertAlmostEqual(s.direction_deg, 0.0)

    def test_a_still_head_is_smoothed_for_every_point(self):
        self.f.update(pose(100, 50))
        s = self.f.update(pose(102, 50))                         # 2 px < still_px
        self.assertEqual(s.points["head"], (101.0, 50.0))
        self.assertEqual(s.points["neck"], (91.0, 50.0))
        self.assertEqual(s.points["tail_base"], (61.0, 50.0))

    def test_a_moving_head_is_not_smoothed(self):
        self.f.update(pose(100, 50))
        s = self.f.update(pose(110, 50))                         # 10 px: moving
        self.assertEqual(s.points["head"], (110, 50))
        self.assertEqual(s.points["neck"], (100, 50))

    def test_stillness_is_judged_on_raw_steps(self):
        self.f.update(pose(100, 50))
        self.f.update(pose(101, 50))                              # smoothed head 100.5
        s = self.f.update(pose(103, 50))                          # raw step 2 (smoothed would be 2.5)
        self.assertEqual(s.points["head"], (101.75, 50.0))

    def test_a_one_frame_glitch_is_held_and_dropped(self):
        self.f.update(pose(100, 50))
        glitch = self.f.update(pose(300, 50))
        self.assertFalse(glitch.valid)
        self.assertEqual(glitch.status, HF.LEAP)
        self.assertEqual(glitch.points["head"], (100, 50))       # old points held
        back = self.f.update(pose(102, 50))
        self.assertTrue(back.valid)
        self.assertEqual(back.points["head"], (101.0, 50.0))     # judged vs the last raw (100)

    def test_smoothing_lag_does_not_trigger_the_guard(self):
        f = HF.HeadFilter(HF.HeadFilterParams(max_step_px=40, still_px=3, still_alpha=0.5))
        f.update(pose(100, 50))
        f.update(pose(102, 50))                                   # smoothed head lags at 101
        s = f.update(pose(141.5, 50))                             # 39.5 from the raw 102, 40.5 from 101
        self.assertTrue(s.valid)

    def test_a_real_move_is_accepted_once_frames_agree(self):
        self.f.update(pose(100, 50))
        self.assertFalse(self.f.update(pose(300, 50)).valid)
        self.assertFalse(self.f.update(pose(305, 50)).valid)
        s = self.f.update(pose(308, 52))
        self.assertTrue(s.valid)
        self.assertEqual(s.points["head"], (308, 52))           # jumps, no blend

    def test_disagreeing_leaps_restart_the_count(self):
        self.f.update(pose(100, 50))
        self.f.update(pose(300, 50))
        self.f.update(pose(200, 200))                            # far from the last leap
        self.assertFalse(self.f.update(pose(202, 200)).valid)    # only 2 agreeing so far
        self.assertTrue(self.f.update(pose(204, 200)).valid)

    def test_direction_uses_image_axes(self):
        s = self.f.update({"head": (0.0, 10.0), "neck": (0.0, 0.0)})
        self.assertAlmostEqual(s.direction_deg, 90.0)            # head below the neck
        self.assertIsNone(HF.direction_deg(None, (1.0, 1.0)))
        self.assertIsNone(HF.direction_deg((1.0, 1.0), (1.0, 1.0)))

    def test_short_gap_keeps_state_long_gap_starts_fresh(self):
        self.f.update(pose(100, 50))
        self.assertEqual(self.f.update(None).status, HF.NO_PREDICTION)
        self.assertEqual(self.f.update(pose(102, 50)).points["head"], (101.0, 50.0))
        for _ in range(3):
            self.f.update(None)
        s = self.f.update(pose(400, 50))                         # far, but after a long gap
        self.assertTrue(s.valid)
        self.assertEqual(s.points["head"], (400, 50))

    def test_a_prediction_without_a_head_counts_as_none(self):
        self.f.update(pose(100, 50))
        s = self.f.update({"neck": (1.0, 1.0)})
        self.assertFalse(s.valid)
        self.assertEqual(s.status, HF.NO_PREDICTION)


if __name__ == "__main__":
    unittest.main()
