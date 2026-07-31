"""
test_session_summary.py - headless tests for the session-summary core.

Covers the pure ``session_summary.build_summary`` + its JSON/text renderers
(docs/decisions/0071): it folds a ``phase3_stats.Phase3Stats`` and a
``behavior_stats.BehaviorSnapshot`` (built here via the real cores over
deterministic inputs) plus session metadata into one summary, and serialises both
groups without recomputing anything. Also checks the empty-input placeholders and
the duration/scope metadata. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_session_summary
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import phase3_stats
import session_summary
from behavior_stats import BehaviorStats
from phase3_log import Phase3Log
from phase3_task import CORRECT, INCORRECT
from trial_sequence import TrialConfig, TrialSequence


# ---- deterministic Phase 3 cued stats (mirrors test_phase3_stats helpers) ----
def _seq(n=6, seed=123, high_tone_is_left=True):
    return TrialSequence(
        TrialConfig(max_trials=n, block_size=n, max_same_run=3,
                    high_tone_is_left=high_tone_is_left),
        seed=seed)


def _side_for(log, index, correct):
    correct_left = log._rows[index].correct_arm == "LEFT"
    want_left = correct_left if correct else not correct_left
    return "left" if want_left else "right"


def _cued_stats():
    """A small scored table -> a Phase3Stats with a mix of correct/incorrect."""
    log = Phase3Log(_seq(n=6), animal="RAT600",
                    session_start_iso="2026-07-31T12:00:00")
    outcomes = [CORRECT, CORRECT, INCORRECT, CORRECT]
    for i, oc in enumerate(outcomes):
        log.choice_scored(i, _side_for(log, i, oc == CORRECT), oc,
                          f"t_choice{i}")
        if oc == CORRECT:
            log.reward_fired(i, f"t_reward{i}")
    return phase3_stats.compute(log.snapshot(len(outcomes), armed=True)), log


# ---- deterministic locomotor snapshot (via the real BehaviorStats core) ----
def _frame(now, *, committed=None, stem=False):
    """A minimal duck-typed frame carrying only the fields BehaviorStats reads
    (``now`` + ``valid`` + the choice-commit edge + the stem-gate flag)."""
    return SimpleNamespace(now=now, valid=True, choice_committed=committed,
                           outbound_used_stem=stem)


def _locomotor_snapshot():
    """Fold a few stem/non-stem choice commits into a BehaviorSnapshot."""
    st = BehaviorStats()
    # Two left stem choices, one right stem choice, one denied non-stem left.
    st.update(_frame(1.0, committed="left", stem=True))
    st.update(_frame(1.2, committed="left", stem=True))
    st.update(_frame(1.4, committed="right", stem=True))
    st.update(_frame(1.6, committed="left", stem=False))
    return st.snapshot()


_T0 = {"wall": 1000.0, "mono": 5.0, "iso": "2026-07-31T12:00:00.000"}
_T_END = {"wall": 1123.0, "mono": 128.0, "iso": "2026-07-31T12:02:03.000"}


class TestBuildSummary(unittest.TestCase):
    def test_metadata_and_duration(self):
        cued, _ = _cued_stats()
        summ = session_summary.build_summary(
            session_id="RAT600_20260731_120000", animal="RAT600",
            t0=_T0, t_end=_T_END, cued=cued, locomotor=_locomotor_snapshot(),
            seed=123, tones={"high_tone_is_left": True,
                             "high_tone_hz": 8000, "low_tone_hz": 2000})
        self.assertEqual(summ.session_id, "RAT600_20260731_120000")
        self.assertEqual(summ.animal, "RAT600")
        self.assertEqual(summ.seed, 123)
        self.assertEqual(summ.duration_s, 123.0)
        self.assertEqual(summ.scope, session_summary.SCOPE)

    def test_duration_none_without_end(self):
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=None)
        self.assertIsNone(summ.duration_s)


class TestRenderJson(unittest.TestCase):
    def test_json_carries_both_groups_verbatim(self):
        cued, _ = _cued_stats()
        loco = _locomotor_snapshot()
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, locomotor=loco, seed=7)
        data = json.loads(summ.render_json())
        self.assertEqual(data["schema_version"],
                         session_summary.SCHEMA_VERSION)
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["duration_s"], 123.0)
        # Cued fields emitted verbatim (not recomputed): completed=4, correct=3.
        self.assertEqual(data["cued_task"]["completed"], 4)
        self.assertEqual(data["cued_task"]["correct"], 3)
        self.assertEqual(data["cued_task"]["incorrect"], 1)
        # Locomotor fields: 4 total choices, 3 stem-gated, 1 non-stem.
        self.assertEqual(data["locomotion"]["total_choices"], 4)
        self.assertEqual(data["locomotion"]["stem_choices"], 3)
        self.assertEqual(data["locomotion"]["nonstem_choices"], 1)

    def test_json_none_groups(self):
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END)
        data = json.loads(summ.render_json())
        self.assertIsNone(data["cued_task"])
        self.assertIsNone(data["locomotion"])


class TestRenderText(unittest.TestCase):
    def test_text_has_both_sections_and_values(self):
        cued, _ = _cued_stats()
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, locomotor=_locomotor_snapshot(), seed=7,
            tones={"high_tone_is_left": True,
                   "high_tone_hz": 8000, "low_tone_hz": 2000})
        text = summ.render_text()
        self.assertIn("Cued task", text)
        self.assertIn("Excursions / locomotion", text)
        self.assertIn("Completed:   4/6", text)
        self.assertIn("Non-stem:    1", text)
        # 3 of 4 correct -> 75%.
        self.assertIn("75%", text)
        # Tone mapping line rendered.
        self.assertIn("HIGH 8000 Hz -> LEFT", text)

    def test_text_placeholders_when_empty(self):
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=None)
        text = summ.render_text()
        self.assertIn("(no cued-task data)", text)
        self.assertIn("(no locomotor data)", text)
        self.assertIn("Duration: --", text)


if __name__ == "__main__":
    unittest.main()
