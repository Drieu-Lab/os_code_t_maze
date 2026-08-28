"""
test_analysis_progression.py - headless tests for the per-session summary.

Checks ``progression.per_session_summary`` over a synthetic signal frame: accuracy +
left-choice bias computed over OUT-OF-BLOCK trials only (intervention/probe excluded,
post-block included; ADR-0132), one row per session in order. Pure pandas; no Tk.

Run from host/tests:  python -m unittest test_analysis_progression
"""
from __future__ import annotations

import unittest

import pandas as pd

from tmaze.analysis import choice_signals as cs
from tmaze.analysis import progression


def _trials(rows):
    out = []
    for i, r in enumerate(rows):
        out.append(
            {
                "session_id": r.get("session_id", "600_20260101_120000"),
                "animal": r.get("animal", "600"),
                "date": r.get("date", "2026-01-01"),
                "run_idx": 0,
                "trial": r["trial"],
                "correct_arm": r.get("correct_arm", "LEFT"),
                "choice_cue": r.get("cue", "HIGH"),
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
                "high_tone_is_left": True,
                "seq": i,
                "trial_start_iso_dt": pd.Timestamp("2026-01-01T12:00:00")
                + pd.Timedelta(seconds=10 * i),
            }
        )
    return cs.add_signals(pd.DataFrame(out))


class TestPerSessionSummary(unittest.TestCase):
    def test_accuracy_and_left_bias(self):
        # 3 correct LEFT + 1 incorrect RIGHT -> accuracy 0.75, P(left) 0.75, n 4.
        rows = [{"trial": t + 1, "choice": "LEFT", "outcome": "CORRECT"}
                for t in range(3)]
        rows.append({"trial": 4, "choice": "RIGHT", "correct_arm": "LEFT",
                     "outcome": "INCORRECT"})
        summ = progression.per_session_summary(_trials(rows))
        self.assertEqual(len(summ), 1)
        row = summ.iloc[0]
        self.assertEqual(row["n"], 4)
        self.assertAlmostEqual(row["accuracy"], 0.75, places=6)
        self.assertAlmostEqual(row["side_bias_left"], 0.75, places=6)

    def test_excludes_in_block_and_probe_keeps_post_block(self):
        # 2 free LEFT-correct, a forced-RIGHT-incorrect block, a probe RIGHT-incorrect,
        # then 2 post-block LEFT-correct. Only the 4 out-of-block LEFT trials count.
        rows = [{"trial": 1, "choice": "LEFT", "outcome": "CORRECT"},
                {"trial": 2, "choice": "LEFT", "outcome": "CORRECT"}]
        rows.append({"trial": 3, "choice": "RIGHT", "correct_arm": "RIGHT",
                     "outcome": "CORRECT", "sched_state": "INTERVENTION",
                     "block_id": "1", "block_trigger": "auto",
                     "block_kind": "side_bias"})
        rows.append({"trial": 4, "choice": "RIGHT", "correct_arm": "LEFT",
                     "outcome": "INCORRECT", "probe": "1"})
        rows += [{"trial": 5, "choice": "LEFT", "outcome": "CORRECT"},
                 {"trial": 6, "choice": "LEFT", "outcome": "CORRECT"}]
        summ = progression.per_session_summary(_trials(rows))
        row = summ.iloc[0]
        self.assertEqual(row["n"], 4)                      # 2 free + 2 post-block
        self.assertAlmostEqual(row["accuracy"], 1.0, places=6)
        self.assertAlmostEqual(row["side_bias_left"], 1.0, places=6)

    def test_two_sessions_in_order(self):
        rows = [{"trial": 1, "choice": "LEFT", "session_id": "600_20260101_120000"},
                {"trial": 1, "choice": "RIGHT", "correct_arm": "RIGHT",
                 "session_id": "600_20260102_120000"}]
        summ = progression.per_session_summary(_trials(rows))
        self.assertEqual(list(summ["session_id"]),
                         ["600_20260101_120000", "600_20260102_120000"])

    def test_empty_frame(self):
        summ = progression.per_session_summary(pd.DataFrame())
        self.assertTrue(summ.empty)
        self.assertIn("accuracy", summ.columns)


if __name__ == "__main__":
    unittest.main()
