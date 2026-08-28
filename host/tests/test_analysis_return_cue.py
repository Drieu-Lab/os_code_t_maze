"""
test_analysis_return_cue.py - headless tests for the return-cue carryover summary.

Checks ``return_cue.wilson_interval`` on known values (5/10 -> the standard
[0.2366, 0.7634] score interval; empty -> NaN; boundary counts stay inside [0, 1])
and ``return_cue_switch_summary`` aggregation on a minimal own-pair frame. Pure
pandas/numpy; no Tk.

Run from host/tests:  python -m unittest test_analysis_return_cue
"""
from __future__ import annotations

import math
import unittest

import pandas as pd

from tmaze.analysis import return_cue as rc


class TestWilsonInterval(unittest.TestCase):
    def test_known_midpoint(self):
        p, lo, hi = rc.wilson_interval(5, 10)
        self.assertAlmostEqual(p, 0.5, places=6)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)

    def test_empty_is_nan(self):
        p, lo, hi = rc.wilson_interval(0, 0)
        self.assertTrue(math.isnan(p) and math.isnan(lo) and math.isnan(hi))

    def test_boundary_stays_in_unit_interval(self):
        # All-successes: point estimate 1.0; the Wilson upper bound is exactly 1.0
        # but the lower bound pulls in below 1 (not the degenerate [1, 1]).
        p, lo, hi = rc.wilson_interval(10, 10)
        self.assertAlmostEqual(p, 1.0, places=6)
        self.assertLessEqual(hi, 1.0)
        self.assertLess(lo, 1.0)
        self.assertGreater(lo, 0.0)
        # A near-boundary count keeps the whole interval strictly inside [0, 1].
        _, lo9, hi9 = rc.wilson_interval(9, 10)
        self.assertGreater(lo9, 0.0)
        self.assertLess(hi9, 1.0)


class TestReturnCueSummary(unittest.TestCase):
    def _frame(self):
        # 3 congruent pairs (switch T,T,F) and 3 incongruent (F,F,T); choice vs the
        # N-1 return-cue side matches on 4 of 6.
        return pd.DataFrame(
            {
                "own_pair": [True] * 6,
                "switch": [True, True, False, False, False, True],
                "prev_return_cue_congruent": [True, True, True, False, False, False],
                "prev_return_cue_side": ["LEFT", "LEFT", "LEFT",
                                         "RIGHT", "RIGHT", "RIGHT"],
                "choice": ["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT"],
            }
        )

    def test_switch_by_congruency(self):
        summ = rc.return_cue_switch_summary(self._frame())
        cong = summ.switch[summ.switch["congruent"]].iloc[0]
        incong = summ.switch[~summ.switch["congruent"]].iloc[0]
        self.assertEqual((cong["k"], cong["n"]), (2, 3))
        self.assertAlmostEqual(cong["p"], 2 / 3, places=6)
        self.assertEqual((incong["k"], incong["n"]), (1, 3))
        self.assertAlmostEqual(incong["p"], 1 / 3, places=6)

    def test_choice_matches_return_cue_side(self):
        summ = rc.return_cue_switch_summary(self._frame())
        self.assertEqual((summ.match_k, summ.match_n), (4, 6))
        self.assertAlmostEqual(summ.match_p, 4 / 6, places=6)
        self.assertFalse(math.isnan(summ.match_lo))

    def test_no_usable_pairs(self):
        empty = rc.return_cue_switch_summary(pd.DataFrame())
        self.assertTrue(empty.switch.empty)
        self.assertEqual(empty.match_n, 0)
        self.assertIn("congruent", empty.switch.columns)


if __name__ == "__main__":
    unittest.main()
