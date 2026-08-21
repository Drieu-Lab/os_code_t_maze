"""
test_analysis_choice_signals.py - headless tests for the per-trial signal core.

Covers ``choice_signals.add_signals`` over small hand-built frames: the cue->side
mapping (both ``high_tone_is_left`` polarities), cue-following, the truly-adjacent
switch/stay pairing (a gap or an intervention trial breaks the pair), the
free/in-block/post-block epoch tag, and the lag-1 return-cue carryover features.
Pure pandas; no hardware/Tk.

Run from host/tests:  python -m unittest test_analysis_choice_signals
"""
from __future__ import annotations

import unittest

import pandas as pd

from tmaze.analysis import choice_signals as cs


def _trials(rows, high_tone_is_left=True):
    """Build a reader-shaped trial frame from compact row dicts (one session)."""
    out = []
    for i, r in enumerate(rows):
        cue = r["cue"]
        out.append(
            {
                "session_id": r.get("session_id", "600_20260101_120000"),
                "animal": "600",
                "date": "2026-01-01",
                "run_idx": r.get("run_idx", 0),
                "trial": r["trial"],
                "correct_arm": r.get("correct_arm", ""),
                "choice_cue": cue,
                "return_cue_left": r.get("return_cue_left", ""),
                "return_cue_right": r.get("return_cue_right", ""),
                "chosen_arm": r.get("choice", ""),
                "outcome": r.get("outcome", "CORRECT"),
                "rewarded": r.get("rewarded", 0),
                "return_arm_used": r.get("return_arm_used", ""),
                "return_cue_played": r.get("return_cue_played", ""),
                "sched_state": r.get("sched_state", "NORMAL"),
                "block_id": r.get("block_id", ""),
                "block_trigger": r.get("block_trigger", ""),
                "bias": 0.0,
                "block_kind": r.get("block_kind", ""),
                "alt_rate": 0.0,
                "high_tone_is_left": high_tone_is_left,
                "seq": i,
                "trial_start_iso_dt": pd.Timestamp("2026-01-01T12:00:00")
                + pd.Timedelta(seconds=10 * i),
            }
        )
    return pd.DataFrame(out)


class TestCueSideMapping(unittest.TestCase):
    def test_high_tone_is_left_true(self):
        df = _trials(
            [{"trial": 1, "cue": "HIGH", "choice": "LEFT"},
             {"trial": 2, "cue": "LOW", "choice": "LEFT"}],
            high_tone_is_left=True,
        )
        out = cs.add_signals(df)
        self.assertEqual(out.loc[0, "cue_side"], "LEFT")
        self.assertEqual(out.loc[1, "cue_side"], "RIGHT")
        self.assertTrue(bool(out.loc[0, "cue_followed"]))   # chose LEFT, cue LEFT
        self.assertFalse(bool(out.loc[1, "cue_followed"]))  # chose LEFT, cue RIGHT

    def test_high_tone_is_left_false_mirrors(self):
        df = _trials([{"trial": 1, "cue": "HIGH", "choice": "RIGHT"}],
                     high_tone_is_left=False)
        out = cs.add_signals(df)
        self.assertEqual(out.loc[0, "cue_side"], "RIGHT")


class TestSwitchPairing(unittest.TestCase):
    def test_switch_and_stay_over_adjacent_trials(self):
        df = _trials([
            {"trial": 1, "cue": "HIGH", "choice": "LEFT"},
            {"trial": 2, "cue": "LOW", "choice": "RIGHT"},   # switch
            {"trial": 3, "cue": "LOW", "choice": "RIGHT"},   # stay
        ])
        out = cs.add_signals(df)
        self.assertTrue(pd.isna(out.loc[0, "switch"]))       # no prior trial
        self.assertTrue(bool(out.loc[1, "switch"]))
        self.assertFalse(bool(out.loc[2, "switch"]))
        self.assertTrue(bool(out.loc[2, "stay"]))

    def test_gap_breaks_pair(self):
        df = _trials([
            {"trial": 1, "cue": "HIGH", "choice": "LEFT"},
            {"trial": 3, "cue": "LOW", "choice": "RIGHT"},   # trial 2 missing
        ])
        out = cs.add_signals(df)
        self.assertTrue(pd.isna(out.loc[1, "switch"]))       # not consecutive

    def test_intervention_breaks_own_pair(self):
        df = _trials([
            {"trial": 1, "cue": "HIGH", "choice": "LEFT"},
            {"trial": 2, "cue": "LOW", "choice": "RIGHT",
             "sched_state": "INTERVENTION", "block_id": "1",
             "block_trigger": "auto", "block_kind": "side_bias"},
        ])
        out = cs.add_signals(df)
        # pair is valid (consecutive completed) but not an OWN (free) pair.
        self.assertTrue(bool(out.loc[1, "pair_valid"]))
        self.assertFalse(bool(out.loc[1, "own_pair"]))


class TestEpochTag(unittest.TestCase):
    def test_free_in_block_post_block(self):
        rows = [{"trial": t, "cue": "HIGH", "choice": "LEFT"} for t in range(1, 4)]
        rows += [{"trial": 4, "cue": "HIGH", "choice": "LEFT",
                  "sched_state": "INTERVENTION", "block_id": "1",
                  "block_trigger": "auto", "block_kind": "side_bias"}]
        rows += [{"trial": t, "cue": "HIGH", "choice": "LEFT"} for t in range(5, 8)]
        out = cs.add_signals(_trials(rows), post_window=2)
        self.assertEqual(out.loc[0, "epoch"], cs.EPOCH_FREE)
        self.assertEqual(out.loc[3, "epoch"], cs.EPOCH_IN_BLOCK)
        self.assertEqual(out.loc[4, "epoch"], cs.EPOCH_POST_BLOCK)
        self.assertEqual(out.loc[5, "epoch"], cs.EPOCH_POST_BLOCK)
        self.assertEqual(out.loc[6, "epoch"], cs.EPOCH_FREE)  # past post_window=2


class TestReturnCueCarryover(unittest.TestCase):
    def test_prev_return_cue_and_congruency(self):
        df = _trials([
            # N-1: choice cue HIGH, played return cue LOW -> incongruent
            {"trial": 1, "cue": "HIGH", "choice": "LEFT", "return_cue_played": "LOW"},
            {"trial": 2, "cue": "LOW", "choice": "RIGHT", "return_cue_played": "LOW"},
        ], high_tone_is_left=True)
        out = cs.add_signals(df)
        self.assertEqual(out.loc[0, "return_cue_congruent"], False)
        # carried onto trial 2 as the lag-1 feature
        self.assertEqual(out.loc[1, "prev_return_cue"], "LOW")
        self.assertEqual(out.loc[1, "prev_return_cue_side"], "RIGHT")  # LOW -> RIGHT
        self.assertEqual(bool(out.loc[1, "prev_return_cue_congruent"]), False)


if __name__ == "__main__":
    unittest.main()
