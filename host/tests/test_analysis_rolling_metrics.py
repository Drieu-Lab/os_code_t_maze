"""
test_analysis_rolling_metrics.py - headless tests for the rolling-trace core.

Covers ``rolling_metrics.build_traces`` / ``block_epochs`` over hand-built signal
frames: the masked rolling rate (a pure alternator reads switch_rate ~ 1), the
engagement inter-trial-gap proxy, session bounds, and block-epoch span
reconstruction. Pure pandas; no hardware/Tk.

Run from host/tests:  python -m unittest test_analysis_rolling_metrics
"""
from __future__ import annotations

import unittest

import pandas as pd

from tmaze.analysis import choice_signals as cs
from tmaze.analysis import rolling_metrics as rm


def _trials(rows, high_tone_is_left=True):
    out = []
    for i, r in enumerate(rows):
        out.append(
            {
                "session_id": r.get("session_id", "600_20260101_120000"),
                "animal": "600",
                "date": "2026-01-01",
                "run_idx": 0,
                "trial": r["trial"],
                "correct_arm": r.get("correct_arm", "LEFT"),
                "choice_cue": r["cue"],
                "return_cue_left": "",
                "return_cue_right": "",
                "chosen_arm": r["choice"],
                "outcome": r.get("outcome", "CORRECT"),
                "rewarded": r.get("rewarded", 0),
                "return_arm_used": "",
                "return_cue_played": r.get("return_cue_played", "HIGH"),
                "sched_state": r.get("sched_state", "NORMAL"),
                "block_id": r.get("block_id", ""),
                "block_trigger": r.get("block_trigger", ""),
                "bias": 0.0,
                "block_kind": r.get("block_kind", ""),
                "alt_rate": 0.0,
                "high_tone_is_left": high_tone_is_left,
                "seq": i,
                "trial_start_iso_dt": pd.Timestamp("2026-01-01T12:00:00")
                + pd.Timedelta(seconds=r.get("gap", 10) * i),
            }
        )
    return pd.DataFrame(out)


class TestMaskedRate(unittest.TestCase):
    def test_pure_alternator_switch_rate_one(self):
        arms = ["LEFT", "RIGHT"] * 15
        rows = [{"trial": t + 1, "cue": "HIGH", "choice": a}
                for t, a in enumerate(arms)]
        sig = cs.add_signals(_trials(rows))
        res = rm.build_traces(sig, window=10, min_den=3)
        # After enough trials the trailing switch rate is 1.0.
        self.assertAlmostEqual(res.traces["switch_rate"].iloc[-1], 1.0, places=6)

    def test_engagement_gap_proxy(self):
        rows = [{"trial": t + 1, "cue": "HIGH", "choice": "LEFT", "gap": 30}
                for t in range(10)]
        sig = cs.add_signals(_trials(rows))
        res = rm.build_traces(sig, window=5, min_den=1)
        # 30 s between successive trial starts -> rolling median gap ~ 30.
        self.assertAlmostEqual(res.traces["gap_s_med"].iloc[-1], 30.0, places=6)


class TestBlockEpochs(unittest.TestCase):
    def test_reconstructs_block_span(self):
        rows = [{"trial": t + 1, "cue": "HIGH", "choice": "LEFT"} for t in range(3)]
        rows += [
            {"trial": 4, "cue": "HIGH", "choice": "LEFT", "sched_state": "INTERVENTION",
             "block_id": "1", "block_trigger": "auto", "block_kind": "side_bias"},
            {"trial": 5, "cue": "HIGH", "choice": "LEFT", "sched_state": "INTERVENTION",
             "block_id": "1", "block_trigger": "auto", "block_kind": "side_bias"},
        ]
        rows += [{"trial": t + 6, "cue": "HIGH", "choice": "LEFT"} for t in range(2)]
        sig = cs.add_signals(_trials(rows))
        res = rm.build_traces(sig, window=5, min_den=1)
        self.assertEqual(len(res.blocks), 1)
        b = res.blocks[0]
        self.assertEqual(b["kind"], "side_bias")
        self.assertEqual(b["trigger"], "auto")
        self.assertEqual(b["length"], 2)
        self.assertEqual(b["start_seq"], 3)
        self.assertEqual(b["end_seq"], 4)


if __name__ == "__main__":
    unittest.main()
