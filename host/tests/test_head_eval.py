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


if __name__ == "__main__":
    unittest.main()
