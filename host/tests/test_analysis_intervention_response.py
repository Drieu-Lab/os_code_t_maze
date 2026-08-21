"""
test_analysis_intervention_response.py - headless tests for the block-response core.

Covers ``intervention_response``: per-block metrics (in-block accuracy, compliance,
escape-via-cue vs a pre-onset baseline, trials-to-first-correct) and the peri-
intervention pre->post transition restricted to AUTO blocks (a manual block is
excluded from the transition summary but still appears in the block table). Pure
pandas; no hardware/Tk.

Run from host/tests:  python -m unittest test_analysis_intervention_response
"""
from __future__ import annotations

import unittest

import pandas as pd

from tmaze.analysis import choice_signals as cs
from tmaze.analysis import intervention_response as ir
from tmaze.analysis import mode_label as ml
from tmaze.analysis import rolling_metrics as rm


def _mk(rows):
    out = []
    for i, r in enumerate(rows):
        out.append(
            {
                "session_id": "600_20260101_120000",
                "animal": "600",
                "date": "2026-01-01",
                "run_idx": 0,
                "trial": i + 1,
                "correct_arm": r["correct_arm"],
                "choice_cue": r["cue"],
                "return_cue_left": "",
                "return_cue_right": "",
                "chosen_arm": r["choice"],
                "outcome": r["outcome"],
                "rewarded": 1 if r["outcome"] == "CORRECT" else 0,
                "return_arm_used": "",
                "return_cue_played": "HIGH",
                "sched_state": r.get("sched_state", "NORMAL"),
                "block_id": r.get("block_id", ""),
                "block_trigger": r.get("block_trigger", ""),
                "bias": 0.0,
                "block_kind": r.get("block_kind", ""),
                "alt_rate": 0.0,
                "high_tone_is_left": True,
                "seq": i,
                "trial_start_iso_dt": pd.Timestamp("2026-01-01T12:00:00")
                + pd.Timedelta(seconds=10 * i),
            }
        )
    return cs.add_signals(pd.DataFrame(out))


def _scenario():
    rows = []
    # 12 pre free trials: choose LEFT, cue HIGH (=> LEFT), all correct -> side-bias
    # + cue_follow == 1.0 baseline.
    for _ in range(12):
        rows.append({"correct_arm": "LEFT", "cue": "HIGH", "choice": "LEFT",
                     "outcome": "CORRECT"})
    # AUTO side-bias block of 4, pushing RIGHT (cue LOW => RIGHT); choices
    # [RIGHT, LEFT, RIGHT, RIGHT] -> accuracy 3/4, compliance 3/4.
    blk = [("RIGHT", "CORRECT"), ("LEFT", "INCORRECT"),
           ("RIGHT", "CORRECT"), ("RIGHT", "CORRECT")]
    for choice, outcome in blk:
        rows.append({"correct_arm": "RIGHT", "cue": "LOW", "choice": choice,
                     "outcome": outcome, "sched_state": "INTERVENTION",
                     "block_id": "1", "block_trigger": "auto",
                     "block_kind": "side_bias"})
    # 12 post free trials alternating -> alternating mode.
    for k in range(12):
        arm = "LEFT" if k % 2 == 0 else "RIGHT"
        cue = "HIGH" if arm == "LEFT" else "LOW"
        rows.append({"correct_arm": arm, "cue": cue, "choice": arm,
                     "outcome": "CORRECT"})
    # A MANUAL block of 2 at the end (should appear in the table, not the peri set).
    for _ in range(2):
        rows.append({"correct_arm": "RIGHT", "cue": "LOW", "choice": "RIGHT",
                     "outcome": "CORRECT", "sched_state": "INTERVENTION",
                     "block_id": "2", "block_trigger": "manual",
                     "block_kind": "side_bias"})
    return _mk(rows)


class TestBlockTable(unittest.TestCase):
    def test_metrics(self):
        sig = _scenario()
        bt = ir.block_table(sig, pre_window=12)
        auto = bt[bt["block_id"] == "1"].iloc[0]
        self.assertAlmostEqual(auto["in_block_accuracy"], 0.75, places=6)
        self.assertAlmostEqual(auto["compliance"], 0.75, places=6)
        self.assertAlmostEqual(auto["pre_cue_follow"], 1.0, places=6)
        self.assertAlmostEqual(auto["in_cue_follow"], 0.75, places=6)
        self.assertAlmostEqual(auto["escape_via_cue"], -0.25, places=6)
        self.assertEqual(int(auto["trials_to_first_correct"]), 1)
        # Both blocks (auto + manual) are tabulated.
        self.assertEqual(set(bt["block_id"]), {"1", "2"})


class TestPeriTransitions(unittest.TestCase):
    def test_auto_only_pre_post(self):
        sig = _scenario()
        res = rm.build_traces(sig, window=8, min_den=1)
        lab = ml.label_modes(res.traces)
        pt = ir.peri_transitions(lab, res.blocks, pre_window=12, post_window=12)
        # Only the AUTO block yields a transition row; the manual one is excluded.
        self.assertEqual(len(pt), 1)
        row = pt.iloc[0]
        self.assertEqual(row["pre_mode"], ml.MODE_SIDE_BIAS)
        self.assertEqual(row["post_mode"], ml.MODE_ALTERNATING)
        self.assertTrue(bool(row["changed"]))


if __name__ == "__main__":
    unittest.main()
