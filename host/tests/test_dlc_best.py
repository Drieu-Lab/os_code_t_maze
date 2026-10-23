"""Tests for tmaze.core.dlc_best (best candidate skeleton per frame)."""

import csv
import io
import unittest

from tmaze.core import dlc_best as B

MISSING = [-1.0, -1.0, -1.0, -1.0]


class TestBest(unittest.TestCase):
    def test_highest_total_score_wins_and_missing_parts_count_zero(self):
        weak = [[10, 10, 0.9, -1], MISSING, MISSING]
        strong = [[50, 50, 0.6, -1], [51, 52, 0.7, -1], MISSING]
        self.assertEqual(B.best_candidate([weak, strong]), 1)
        self.assertEqual(B.best_candidate([strong, strong]), 0)
        self.assertIsNone(B.best_candidate([[MISSING, MISSING]]))
        self.assertIsNone(B.best_candidate([]))
        self.assertEqual(B.candidate_score([[0, 0, float("nan")], [1, 1, 0.5]]), 0.5)

    def test_row_blanks_missing_parts_and_empty_frames(self):
        cand = [[50, 60, 0.8, -1], MISSING]
        self.assertEqual(B.frame_row([cand], 2), [50.0, 60.0, 0.8, None, None, None])
        self.assertEqual(B.frame_row([], 2), [None] * 6)

    def test_track_has_one_row_per_frame(self):
        cand = [[1, 2, 0.9, -1]]
        rows = B.track({0: [cand], 2: [cand]}, 4, 1)
        self.assertEqual(len(rows), 4)
        self.assertEqual(B.found_frames(rows), 2)
        self.assertEqual(rows[1], [None] * 3)

    def test_csv_layout_matches_deeplabcut(self):
        rows = [[None] * 6, [1.5, 2.25, 0.987654, None, None, None]]
        text = B.dlc_csv("DLC_x", "600", ["nose", "neck"], rows)
        got = list(csv.reader(io.StringIO(text)))
        self.assertEqual(got[0], ["scorer"] + ["DLC_x"] * 6)
        self.assertEqual(got[1], ["individuals"] + ["600"] * 6)
        self.assertEqual(got[2], ["bodyparts", "nose", "nose", "nose", "neck", "neck", "neck"])
        self.assertEqual(got[3], ["coords"] + ["x", "y", "likelihood"] * 2)
        self.assertEqual(got[4], ["0"] + [""] * 6)
        self.assertEqual(got[5], ["1", "1.5", "2.25", "0.9877", "", "", ""])


if __name__ == "__main__":
    unittest.main()
