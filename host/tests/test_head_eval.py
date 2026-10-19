"""Headless tests for tmaze.core.head_eval (DeepLabCut label reading + scoring).

Run from host/:  python -m unittest tests.test_head_eval
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core import head_eval as HE

_CSV = """scorer,,,R,R,R,R,R,R,R,R,R,R,R,R
individuals,,,600,600,600,600,600,600,600,600,601,601,601,601
bodyparts,,,nose,nose,left_ear,left_ear,neck,neck,tail_base,tail_base,nose,nose,right_ear,right_ear
coords,,,x,y,x,y,x,y,x,y,x,y,x,y
labeled-data,600_x,img00012.png,10,20,14,24,30,20,90,20,,,18,16
labeled-data,600_x,img00040.png,,,,,,,,,50,60,,
"""


class TestHeadEval(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_CSV)

    def tearDown(self):
        os.remove(self.path)

    def test_reads_parts_taking_first_individual_with_a_value(self):
        data = HE.read_collected_data(self.path)
        self.assertEqual(set(data), {"img00012.png", "img00040.png"})
        a = data["img00012.png"]
        self.assertEqual(a["nose"], (10.0, 20.0))          # 600's, not 601's blank
        self.assertEqual(a["right_ear"], (18.0, 16.0))     # only labelled under 601
        self.assertEqual(data["img00040.png"], {"nose": (50.0, 60.0)})

    def test_frame_index(self):
        self.assertEqual(HE.frame_index_of("img01902.png"), 1902)
        self.assertIsNone(HE.frame_index_of("frame.png"))

    def test_head_centre_and_wrong_end(self):
        parts = HE.read_collected_data(self.path)["img00012.png"]
        hc = HE.head_centre(parts)
        self.assertAlmostEqual(hc[0], (10 + 14 + 18) / 3)
        self.assertAlmostEqual(hc[1], (20 + 24 + 16) / 3)
        self.assertFalse(HE.wrong_end((25.0, 20.0), parts))
        self.assertTrue(HE.wrong_end((80.0, 20.0), parts))
        self.assertIsNone(HE.wrong_end((0.0, 0.0), {"nose": (1.0, 1.0)}))

    def test_percentile(self):
        self.assertEqual(HE.percentile([3, 1, 2], 0.5), 2)
        self.assertAlmostEqual(HE.percentile([0, 10], 0.9), 9.0)
        self.assertIsNone(HE.percentile([], 0.5))

    def test_summarise(self):
        parts = {"nose": (0.0, 0.0), "neck": (10.0, 0.0), "tail_base": (50.0, 0.0)}
        scores = [HE.score_point("com", "s1", 1, (40.0, 0.0), True, parts),
                  HE.score_point("com", "s2", 2, (20.0, 0.0), False, parts),
                  HE.score_point("head", "s1", 1, (2.0, 0.0), True, parts),
                  HE.score_point("head", "s1", 3, None, False, parts)]
        com, head = HE.summarise(scores)
        self.assertEqual(com.candidate, "com")
        self.assertEqual(com.n, 2)
        self.assertAlmostEqual(com.valid_frac, 0.5)
        self.assertAlmostEqual(com.head_median, 30.0)
        self.assertAlmostEqual(com.wrong_end_frac, 0.5)
        self.assertEqual(com.per_group, {"s1": 40.0, "s2": 20.0})
        self.assertEqual(head.n_point, 1)
        self.assertIn("com", HE.format_table([com, head], px_to_cm=0.25))


_PRED = """scorer,R,R,R,R,R,R
individuals,600,600,600,600,600,600
bodyparts,nose,nose,nose,tail_base,tail_base,tail_base
coords,x,y,likelihood,x,y,likelihood
0,,,,,,
1,10.0,20.0,0.9,50.0,20.0,0.3
2,11.0,21.0,0.6,51.0,21.0,0.95
"""

_PRED_SINGLE = """scorer,R,R,R
bodyparts,nose,nose,nose
coords,x,y,likelihood
5,1.5,2.5,0.99
"""


class TestPredictions(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_reads_multi_animal_with_likelihood_cut(self):
        pred = HE.read_dlc_predictions(self._write(_PRED), min_likelihood=0.6)
        self.assertEqual(set(pred), {1, 2})                 # frame 0 is blank
        self.assertEqual(pred[1], {"nose": (10.0, 20.0)})   # tail_base 0.3 dropped
        self.assertEqual(pred[2]["tail_base"], (51.0, 21.0))

    def test_reads_single_animal(self):
        pred = HE.read_dlc_predictions(self._write(_PRED_SINGLE))
        self.assertEqual(pred, {5: {"nose": (1.5, 2.5)}})


class TestVideoScorer(unittest.TestCase):
    PARTS = {"nose": (0.0, 0.0), "neck": (10.0, 0.0), "tail_base": (50.0, 0.0)}

    def test_distance_jumps_and_jitter(self):
        sc = HE.VideoScorer("c", fps=10.0, jump_px=20.0, still_px=1.0)
        for f, pt in enumerate([(1.0, 0.0), (2.0, 0.0), (45.0, 0.0), (3.0, 0.0)]):
            sc.add(f, pt, True, self.PARTS)
        sc.add(5, (3.0, 0.0), False, {})      # gap at frame 4, no target
        s = sc.summary()
        self.assertEqual((s.frames, s.n, s.n_point), (5, 4, 4))
        self.assertEqual(s.valid_frac, 1.0)
        self.assertAlmostEqual(s.head_median, 2.5)
        self.assertEqual(sc._jumps, 2)        # 2->45 and 45->3; not across the gap
        self.assertAlmostEqual(s.jumps_per_min, 2 / (5 / 10.0 / 60.0))
        self.assertAlmostEqual(s.jitter_still, 42.0)   # median of 1, 43, 42
        self.assertAlmostEqual(s.wrong_end_frac, 0.25)

    def test_long_wrong_runs(self):
        sc = HE.VideoScorer("c", fps=4.0, long_run_s=1.0)   # long run = 4 frames
        seq = ["w"] * 5 + ["ok"] + ["w"] * 2 + [None] + ["w"]
        for f, kind in enumerate(seq):
            pt = None if kind is None else ((45.0, 0.0) if kind == "w" else (5.0, 0.0))
            sc.add(f, pt, True, self.PARTS)
        s = sc.summary()
        self.assertAlmostEqual(s.wrong_end_frac, 8 / 9)
        self.assertAlmostEqual(s.wrong_long_frac, 5 / 9)   # the 2+1 run spans a gap but is short
        self.assertIn("jumps/min", HE.format_video_table([s], px_to_cm=0.2))


class TestDirection(unittest.TestCase):
    def test_head_direction_is_neck_to_head_centre(self):
        parts = {"neck": (0.0, 0.0), "nose": (10.0, 0.0), "right_ear": (4.0, 3.0),
                 "left_ear": (4.0, -3.0)}
        self.assertAlmostEqual(HE.head_direction(parts), 0.0)
        self.assertAlmostEqual(HE.direction_deg((0, 0), (0, 4)), 90.0)     # image down
        self.assertIsNone(HE.head_direction({"nose": (1.0, 1.0)}))
        self.assertEqual(HE.angle_diff(170, -170), 20)

    def test_scorer_counts_error_turns_and_jitter(self):
        sc = HE.DirectionScorer("x", fps=60.0, turn_deg=90.0, still_deg=2.0)
        sc.add(0, 10.0, 0.0)
        sc.add(1, 12.0, 0.5)     # target steady: 2 deg jitter
        sc.add(2, -170.0, 1.0)   # 178 deg jump: a turn, error 171
        sc.add(3, None, 1.0)     # no prediction
        sc.add(5, 0.0, 1.0)      # not consecutive with frame 3's prediction anyway
        s = sc.summary()
        self.assertEqual(s.n, 4)
        self.assertAlmostEqual(s.err_median, (10.0 + 11.5) / 2)
        self.assertAlmostEqual(s.over_45, 0.25)
        self.assertAlmostEqual(s.turns_per_min, 1 / (5 / 60.0 / 60.0))
        self.assertAlmostEqual(s.jitter_still, (2.0 + 178.0) / 2)
        self.assertIn("x", HE.format_direction_table([s]))


if __name__ == "__main__":
    unittest.main()
