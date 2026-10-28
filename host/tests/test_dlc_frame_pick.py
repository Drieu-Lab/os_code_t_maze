"""Tests for tmaze.core.dlc_frame_pick and tmaze.tools.dlc_pick_frames (no video)."""

import unittest

from tmaze.core import dlc_frame_pick as P
from tmaze.core.maze_state import ZoneMap
from tmaze.tools import dlc_pick_frames as T


class TestUnwrap(unittest.TestCase):
    def test_restart_adds_a_period_and_small_drops_do_not(self):
        self.assertEqual(P.unwrap_block_ids([65534, 65535, 0, 1]), [65534, 65535, 65536, 65537])
        self.assertEqual(P.unwrap_block_ids([10, 12, 11]), [10, 12, 11])
        self.assertEqual(P.unwrap_block_ids([65535, 0, 65535, 0]), [65535, 65536, 131071, 131072])


class TestPick(unittest.TestCase):
    def test_lowest_first_spaced_and_capped_per_region(self):
        c = [P.Candidate(100, "well_side", 0.1), P.Candidate(101, "well_side", 0.05),
             P.Candidate(300, "well_side", 0.3), P.Candidate(500, "well_side", 0.2),
             P.Candidate(102, "returns", 0.0), P.Candidate(900, "returns", 0.5),
             P.Candidate(950, "returns", 0.9)]
        got = P.pick(c, per_region=2, min_gap=50)
        self.assertEqual([(g.frame_idx, g.region) for g in got],
                         [(102, "returns"), (300, "well_side"), (500, "well_side"),
                          (900, "returns")])

    def test_only_frames_where_the_body_is_seen_and_the_head_is_not(self):
        self.assertTrue(P.head_unsure_body_seen(0.2, [0.1, 0.9, None, 0.3]))
        self.assertFalse(P.head_unsure_body_seen(-1.0, [-1.0, -1.0, -1.0, -1.0]))
        self.assertFalse(P.head_unsure_body_seen(0.7, [0.9]))
        self.assertFalse(P.head_unsure_body_seen(None, [0.9]))

    def test_region_and_image_name(self):
        self.assertEqual(P.region_of("LEFT_RETURN"), "returns")
        self.assertIsNone(P.region_of(None))
        self.assertEqual(P.image_name(42, 93276), "img00042.png")


class TestCandidates(unittest.TestCase):
    def test_join_survives_the_block_id_restart(self):
        zones = ZoneMap.from_dict({"zones": [
            {"name": "HUB", "polygon_norm": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]},
            {"name": "CENTRAL_STEM", "polygon_norm": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]}]})
        ids = [65534, 65535, 0, 1]
        side = [{"frame_idx": str(i), "cam_block_id": str(b), "track_state": "TRACKING",
                 "track_cx": "10", "track_cy": "10"} for i, b in enumerate(ids)]
        side[1]["track_state"] = "LOST"
        lk = ["0.9", "0.1", "0.2", "0.3"]
        neck = ["0.9", "0.9", "0.9", "0.1"]
        dlc = [{"cam_block_id": str(b), "dlc_status": "low", "head_center_likelihood": l,
                "neck_likelihood": n} for b, l, n in zip(ids, lk, neck)]
        cands, rows = T.candidates(side, dlc, zones, 100, 100, ["well_side"])
        self.assertEqual([(c.frame_idx, c.likelihood) for c in cands], [(2, 0.2)])
        self.assertIs(rows[2], dlc[2])


if __name__ == "__main__":
    unittest.main()
