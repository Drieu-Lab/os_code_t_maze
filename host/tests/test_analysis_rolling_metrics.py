"""
test_analysis_rolling_metrics.py - headless tests for the rolling-trace core.

Covers ``rolling_metrics.build_traces`` / ``block_epochs`` over hand-built signal
frames: the masked rolling rate (a pure alternator reads switch_rate ~ 1), rolling
accuracy + per-cue accuracy, the out-of-block baseline (post-block trials included,
intervention/probe trials excluded; ADR-0132), the engagement inter-trial-gap proxy,
session bounds, and block-epoch span reconstruction. Pure pandas; no hardware/Tk.

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
                "probe": r.get("probe", ""),
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


class TestRollingAccuracy(unittest.TestCase):
    def test_accuracy_trace(self):
        # 10 correct then 10 incorrect, all out-of-block; window 10.
        rows = [{"trial": t + 1, "cue": "HIGH", "choice": "LEFT",
                 "outcome": "CORRECT"} for t in range(10)]
        rows += [{"trial": t + 11, "cue": "HIGH", "choice": "RIGHT",
                  "outcome": "INCORRECT"} for t in range(10)]
        sig = cs.add_signals(_trials(rows))
        res = rm.build_traces(sig, window=10, min_den=1)
        self.assertAlmostEqual(res.traces["accuracy"].iloc[9], 1.0, places=6)
        self.assertAlmostEqual(res.traces["accuracy"].iloc[-1], 0.0, places=6)

    def test_per_cue_accuracy_split(self):
        # HIGH-cue trials all correct, LOW-cue trials all incorrect.
        rows = []
        for t in range(20):
            if t % 2 == 0:
                rows.append({"trial": t + 1, "cue": "HIGH", "choice": "LEFT",
                             "outcome": "CORRECT"})
            else:
                rows.append({"trial": t + 1, "cue": "LOW", "choice": "LEFT",
                             "correct_arm": "RIGHT", "outcome": "INCORRECT"})
        sig = cs.add_signals(_trials(rows))
        res = rm.build_traces(sig, window=20, min_den=1)
        self.assertAlmostEqual(res.traces["high_acc"].iloc[-1], 1.0, places=6)
        self.assertAlmostEqual(res.traces["low_acc"].iloc[-1], 0.0, places=6)


class TestOutOfBlockBaseline(unittest.TestCase):
    """ADR-0132: baseline accuracy/side-bias/cue-follow run over OUT-OF-BLOCK trials
    (not intervention, not probe) - and post-block recovery trials ARE included."""

    def test_post_block_trials_included_inblock_excluded(self):
        # 2 free LEFT, a 2-trial forced RIGHT block, then 4 post-block LEFT trials.
        rows = [{"trial": 1, "cue": "HIGH", "choice": "LEFT"},
                {"trial": 2, "cue": "HIGH", "choice": "LEFT"}]
        rows += [
            {"trial": 3, "cue": "HIGH", "choice": "RIGHT",
             "correct_arm": "RIGHT", "sched_state": "INTERVENTION",
             "block_id": "1", "block_trigger": "auto", "block_kind": "side_bias"},
            {"trial": 4, "cue": "HIGH", "choice": "RIGHT",
             "correct_arm": "RIGHT", "sched_state": "INTERVENTION",
             "block_id": "1", "block_trigger": "auto", "block_kind": "side_bias"},
        ]
        rows += [{"trial": t + 5, "cue": "HIGH", "choice": "LEFT"} for t in range(4)]
        sig = cs.add_signals(_trials(rows))
        res = rm.build_traces(sig, window=4, min_den=1)
        # Trailing window at the last trial is the 4 post-block LEFT trials: they
        # count (oob), so side-bias-left is defined and 1.0 - not NaN as it would be
        # if post-block trials were excluded.
        last = res.traces["side_bias_left"].iloc[-1]
        self.assertFalse(pd.isna(last))
        self.assertAlmostEqual(last, 1.0, places=6)

    def test_probe_trials_excluded_from_baseline(self):
        # All free LEFT except a probe trial forced RIGHT; probe must not bias it.
        rows = [{"trial": t + 1, "cue": "HIGH", "choice": "LEFT"} for t in range(6)]
        rows.append({"trial": 7, "cue": "HIGH", "choice": "RIGHT", "probe": "1"})
        rows += [{"trial": t + 8, "cue": "HIGH", "choice": "LEFT"} for t in range(3)]
        sig = cs.add_signals(_trials(rows))
        res = rm.build_traces(sig, window=10, min_den=1)
        # The probe RIGHT is excluded, so side-bias-left stays 1.0 throughout.
        self.assertAlmostEqual(res.traces["side_bias_left"].iloc[-1], 1.0, places=6)


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
