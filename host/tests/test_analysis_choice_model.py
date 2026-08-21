"""
test_analysis_choice_model.py - headless tests for the per-session choice model.

Covers ``choice_model.fit_by_session`` on synthetic sessions with a KNOWN generating
rule: a (noisy) cue-follower recovers a positive, significant cue weight; a (noisy)
return-cue-driven chooser recovers a positive, significant return-cue weight (the
machinery behind the return-cue-carryover hypothesis). Skips cleanly if statsmodels
is absent. Pure pandas; no hardware/Tk.

Run from host/tests:  python -m unittest test_analysis_choice_model
"""
from __future__ import annotations

import random
import unittest

import pandas as pd

from tmaze.analysis import choice_model as cm
from tmaze.analysis import choice_signals as cs

_HIGH_IS_LEFT = True


def _side(cue):
    # high_tone_is_left True: HIGH -> LEFT, LOW -> RIGHT
    return "LEFT" if cue == "HIGH" else "RIGHT"


def _mk(rows):
    out = []
    for i, r in enumerate(rows):
        choice = r["choice"]
        correct = choice == _side(r["cue"])
        out.append(
            {
                "session_id": "600_20260101_120000",
                "animal": "600",
                "date": "2026-01-01",
                "run_idx": 0,
                "trial": i + 1,
                "correct_arm": _side(r["cue"]),
                "choice_cue": r["cue"],
                "return_cue_left": "",
                "return_cue_right": "",
                "chosen_arm": choice,
                "outcome": "CORRECT" if correct else "INCORRECT",
                "rewarded": 1 if correct else 0,
                "return_arm_used": "",
                "return_cue_played": r["return_cue"],
                "sched_state": "NORMAL",
                "block_id": "",
                "block_trigger": "",
                "bias": 0.0,
                "block_kind": "",
                "alt_rate": 0.0,
                "high_tone_is_left": _HIGH_IS_LEFT,
                "seq": i,
                "trial_start_iso_dt": pd.Timestamp("2026-01-01T12:00:00")
                + pd.Timedelta(seconds=10 * i),
            }
        )
    return cs.add_signals(pd.DataFrame(out))


@unittest.skipUnless(cm.HAS_STATSMODELS, "statsmodels not installed")
class TestChoiceModel(unittest.TestCase):
    def test_cue_follower_recovers_cue_weight(self):
        rng = random.Random(7)
        rows = []
        for _ in range(200):
            cue = rng.choice(["HIGH", "LOW"])
            choice = _side(cue)
            if rng.random() < 0.15:  # noise so the fit isn't perfectly separated
                choice = "RIGHT" if choice == "LEFT" else "LEFT"
            rows.append({"cue": cue, "choice": choice,
                         "return_cue": rng.choice(["HIGH", "LOW"])})
        res = cm.fit_by_session(_mk(rows))
        row = res.iloc[0]
        self.assertTrue(bool(row.get("converged")))
        self.assertGreater(row["cue"], 0.0)      # cue implies LEFT -> P(LEFT) up
        self.assertLess(row["cue_p"], 0.05)

    def test_return_cue_driven_recovers_return_weight(self):
        rng = random.Random(11)
        prev_return = "HIGH"
        rows = []
        for _ in range(220):
            cue = rng.choice(["HIGH", "LOW"])  # independent of choice here
            # choice follows the PREVIOUS trial's played return cue (+ noise)
            choice = _side(prev_return)
            if rng.random() < 0.18:
                choice = "RIGHT" if choice == "LEFT" else "LEFT"
            this_return = rng.choice(["HIGH", "LOW"])
            rows.append({"cue": cue, "choice": choice, "return_cue": this_return})
            prev_return = this_return
        res = cm.fit_by_session(_mk(rows))
        row = res.iloc[0]
        self.assertTrue(bool(row.get("converged")))
        self.assertGreater(row["returncue"], 0.0)
        self.assertLess(row["returncue_p"], 0.05)

    def test_in_block_interaction_none_without_blocks(self):
        rng = random.Random(3)
        rows = [{"cue": rng.choice(["HIGH", "LOW"]),
                 "choice": rng.choice(["LEFT", "RIGHT"]),
                 "return_cue": rng.choice(["HIGH", "LOW"])} for _ in range(60)]
        self.assertIsNone(cm.fit_in_block_interaction(_mk(rows)))


if __name__ == "__main__":
    unittest.main()
