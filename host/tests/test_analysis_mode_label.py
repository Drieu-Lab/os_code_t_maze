"""
test_analysis_mode_label.py - headless tests for the rule-based mode labeler.

Covers ``mode_label.label_modes`` priority order (disengaged beats a co-occurring
side-bias; each free mode fires on its rate), the in-block RESPONSE labels
(complying/resisting), the non-stem "no data" gate when there is no excursion log,
and ``mode_occupancy``. Pure pandas; no hardware/Tk.

Run from host/tests:  python -m unittest test_analysis_mode_label
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from tmaze.analysis import mode_label as ml


def _traces(rows):
    """A trace frame with the columns label_modes reads (missing rates -> NaN)."""
    base = {
        "seq": 0, "session_id": "s", "date": "d", "epoch": "free",
        "in_block": False, "block_kind": pd.NA, "complying": False,
        "gap_s_med": np.nan, "side_bias_left": np.nan, "switch_rate": np.nan,
        "win_stay": np.nan, "lose_shift": np.nan, "cue_follow": np.nan,
        "cue_switch_tracks": np.nan, "reward_rate": np.nan,
    }
    out = []
    for i, r in enumerate(rows):
        row = dict(base)
        row.update(r)
        row["seq"] = i
        out.append(row)
    return pd.DataFrame(out)


class TestPriority(unittest.TestCase):
    def test_disengaged_beats_side_bias(self):
        t = _traces([{"gap_s_med": 100.0, "side_bias_left": 0.95}])
        lab = ml.label_modes(t)
        self.assertEqual(lab.loc[0, "mode"], ml.MODE_DISENGAGED)

    def test_each_free_mode_fires(self):
        t = _traces([
            {"side_bias_left": 0.9},
            {"side_bias_left": 0.5, "switch_rate": 0.9},
            {"side_bias_left": 0.5, "switch_rate": 0.5, "lose_shift": 0.8},
            {"side_bias_left": 0.5, "switch_rate": 0.5, "win_stay": 0.8},
            {"side_bias_left": 0.5, "switch_rate": 0.5, "cue_follow": 0.9},
            {"side_bias_left": 0.5, "switch_rate": 0.5},
        ])
        lab = ml.label_modes(t)
        self.assertEqual(
            list(lab["mode"]),
            [ml.MODE_SIDE_BIAS, ml.MODE_ALTERNATING, ml.MODE_LOSE_SHIFT,
             ml.MODE_WIN_STAY, ml.MODE_CUE_FOLLOWING, ml.MODE_UNCLASSIFIED],
        )

    def test_nonstem_gated_by_excursion_data(self):
        t = _traces([{"nonstem_frac": 0.9, "side_bias_left": 0.5}])
        # Without excursion data the non-stem mode is never assigned.
        self.assertNotEqual(
            ml.label_modes(t, has_excursions=False).loc[0, "mode"], ml.MODE_NONSTEM
        )
        self.assertEqual(
            ml.label_modes(t, has_excursions=True).loc[0, "mode"], ml.MODE_NONSTEM
        )


class TestInBlockResponse(unittest.TestCase):
    def test_complying_and_resisting(self):
        t = _traces([
            {"in_block": True, "complying": True, "block_kind": "side_bias"},
            {"in_block": True, "complying": False, "block_kind": "alternation"},
        ])
        lab = ml.label_modes(t)
        self.assertEqual(lab.loc[0, "mode"], ml.RESP_COMPLYING)
        self.assertEqual(lab.loc[1, "mode"], ml.RESP_RESISTING)
        self.assertEqual(lab.loc[0, "mode_overlay"], "side_bias")


class TestOccupancy(unittest.TestCase):
    def test_occupancy_excludes_in_block(self):
        t = _traces([
            {"side_bias_left": 0.9},
            {"side_bias_left": 0.9},
            {"switch_rate": 0.9, "side_bias_left": 0.5},
            {"in_block": True, "complying": True},
        ])
        lab = ml.label_modes(t)
        occ = ml.mode_occupancy(lab)
        # 3 free trials: 2 side-bias, 1 alternating -> 2/3, 1/3.
        self.assertAlmostEqual(occ.loc["s", ml.MODE_SIDE_BIAS], 2 / 3, places=6)
        self.assertAlmostEqual(occ.loc["s", ml.MODE_ALTERNATING], 1 / 3, places=6)


if __name__ == "__main__":
    unittest.main()
