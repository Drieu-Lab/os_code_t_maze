"""
test_trace_viewport.py - headless tests for the Metrics tab viewport helper.

Covers ``trace_viewport.compute_viewport`` (docs/decisions/0116): the whole-session
(zoomed-out) case, a narrower width panned by a scroll offset, start clamping, the
follow-unless-zoomed pin to the right edge, and the empty-data fallback. Pure
stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_trace_viewport
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3.trace_viewport import compute_viewport


class TestEmpty(unittest.TestCase):
    def test_no_data_gives_unit_window(self):
        for width in (None, 0, 5.0):
            xlo, xhi, lo, hi = compute_viewport(0.0, width, 0.0, follow=True)
            self.assertEqual((xlo, xhi), (0.0, 1.0))
            self.assertEqual((lo, hi), (0.0, 1.0))

    def test_negative_total_treated_as_empty(self):
        self.assertEqual(compute_viewport(-3.0, 5.0, 0.0, follow=False),
                         (0.0, 1.0, 0.0, 1.0))


class TestWholeSession(unittest.TestCase):
    def test_blank_width_shows_whole_session(self):
        xlo, xhi, lo, hi = compute_viewport(40.0, None, 0.0, follow=True)
        self.assertEqual((xlo, xhi), (0.0, 40.0))
        self.assertEqual((lo, hi), (0.0, 1.0))          # thumb fills the bar

    def test_width_wider_than_data_shows_whole_session(self):
        xlo, xhi, lo, hi = compute_viewport(10.0, 25.0, 3.0, follow=False)
        self.assertEqual((xlo, xhi), (0.0, 10.0))
        self.assertEqual((lo, hi), (0.0, 1.0))


class TestZoomedPan(unittest.TestCase):
    def test_narrow_window_panned_from_left(self):
        # 10 min window over a 40 min session, panned to start at 12 min.
        xlo, xhi, lo, hi = compute_viewport(40.0, 10.0, 12.0, follow=False)
        self.assertEqual((xlo, xhi), (12.0, 22.0))
        self.assertAlmostEqual(lo, 12.0 / 40.0)
        self.assertAlmostEqual(hi, 22.0 / 40.0)

    def test_start_clamped_to_right_edge(self):
        # Asking to start past the end clamps so the window ends at total.
        xlo, xhi, lo, hi = compute_viewport(40.0, 10.0, 999.0, follow=False)
        self.assertEqual((xlo, xhi), (30.0, 40.0))
        self.assertAlmostEqual(hi, 1.0)

    def test_negative_start_clamped_to_zero(self):
        xlo, xhi, _lo, _hi = compute_viewport(40.0, 10.0, -5.0, follow=False)
        self.assertEqual((xlo, xhi), (0.0, 10.0))


class TestFollow(unittest.TestCase):
    def test_follow_pins_to_right_edge_ignoring_start(self):
        xlo, xhi, lo, hi = compute_viewport(40.0, 10.0, 0.0, follow=True)
        self.assertEqual((xlo, xhi), (30.0, 40.0))
        self.assertAlmostEqual(hi, 1.0)

    def test_follow_tracks_growing_session(self):
        # As total grows, a follow window keeps the same width at the right edge.
        xlo, xhi, _lo, _hi = compute_viewport(55.0, 10.0, 0.0, follow=True)
        self.assertEqual((xlo, xhi), (45.0, 55.0))


if __name__ == "__main__":
    unittest.main()
