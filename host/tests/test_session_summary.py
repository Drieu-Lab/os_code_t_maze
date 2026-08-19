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

from tmaze.core.phase3 import phase3_stats
from tmaze.core.reward import reward_volume
from tmaze.core.session import session_summary
from tmaze.core.behavior_stats import BehaviorStats
from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.phase3.trial_sequence import TrialConfig, TrialSequence


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


def _cued_stats(topups=0):
    """A small scored table -> a Phase3Stats with a mix of correct/incorrect.

    Three of four trials are rewarded (the correct ones); ``topups`` extra lick
    top-up pulses are recorded on the first rewarded trial to exercise the water
    estimate's pulse count."""
    log = Phase3Log(_seq(n=6), animal="RAT600",
                    session_start_iso="2026-07-31T12:00:00")
    outcomes = [CORRECT, CORRECT, INCORRECT, CORRECT]
    for i, oc in enumerate(outcomes):
        log.choice_scored(i, _side_for(log, i, oc == CORRECT), oc,
                          f"t_choice{i}")
        if oc == CORRECT:
            log.reward_fired(i, f"t_reward{i}")
    for _ in range(topups):
        log.topup_fired(0)
    return phase3_stats.compute(log.snapshot(len(outcomes), armed=True)), log


# The Phase 3 shaping params the controller folds from config/phase3.json.
_PARAMS = {
    "max_same_run": 4, "block_size": 20, "reward_incorrect": False,
    "reward_pulse_ms": 250, "max_topups": 1, "require_up_stem": False,
    "min_confidence": 0.0,
}


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
        # Later additions default to empty / None when nothing was provided.
        self.assertEqual(data["schema_version"], 6)
        self.assertEqual(data["task_params"], {})
        self.assertIsNone(data["estimated_water"])
        # manual_water is always a definite figure (0 when no manual dispenses).
        self.assertEqual(data["manual_water"], {"ul": 0.0, "pulses": 0})
        self.assertEqual(data["notes"], [])


class TestTaskParams(unittest.TestCase):
    def test_params_folded_into_json_and_text(self):
        cued, _ = _cued_stats()
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, params=_PARAMS)
        data = json.loads(summ.render_json())
        self.assertEqual(data["task_params"]["block_size"], 20)
        self.assertEqual(data["task_params"]["max_same_run"], 4)
        self.assertEqual(data["task_params"]["reward_pulse_ms"], 250)
        text = summ.render_text()
        self.assertIn("Task params", text)
        self.assertIn("block 20", text)
        self.assertIn("max-run 4", text)
        self.assertIn("pulse 250 ms", text)

    def test_missing_params_placeholder(self):
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END)
        self.assertIn("(no task params)", summ.render_text())


class TestEstimatedWater(unittest.TestCase):
    def test_water_from_rewarded_pulses(self):
        cued, _ = _cued_stats()                       # 3 rewarded, 0 top-ups
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, params=_PARAMS)
        water = summ.estimated_water
        # 3 prime pulses at 250 ms; 60 uL/300 ms -> 50 uL each -> 150 uL.
        self.assertEqual(water["pulses"], 3)
        self.assertEqual(water["rewarded"], 3)
        self.assertEqual(water["topups"], 0)
        self.assertAlmostEqual(water["ul"], reward_volume.reward_volume_ul(250) * 3)
        data = json.loads(summ.render_json())
        self.assertEqual(data["estimated_water"]["pulses"], 3)
        self.assertIn("Water (est):", summ.render_text())

    def test_water_counts_topups(self):
        cued, _ = _cued_stats(topups=2)               # 3 rewarded + 2 top-ups
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, params=_PARAMS)
        water = summ.estimated_water
        self.assertEqual(water["topups"], 2)
        self.assertEqual(water["pulses"], 5)          # 3 primes + 2 top-ups
        # estimated_water["ul"] is rounded to 1 dp (session_summary), so compare
        # against the same rounding rather than the raw product.
        self.assertAlmostEqual(water["ul"],
                               round(reward_volume.reward_volume_ul(250) * 5, 1))

    def test_water_none_without_cued_or_pulse(self):
        # No cued stats -> no estimate.
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END, params=_PARAMS)
        self.assertIsNone(summ.estimated_water)
        # Cued present but no pulse param -> no estimate.
        cued, _ = _cued_stats()
        summ2 = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END, cued=cued)
        self.assertIsNone(summ2.estimated_water)
        self.assertIn("Water (est): --", summ2.render_text())


class TestEstimatedWaterPerWidth(unittest.TestCase):
    """docs/decisions/0110: when the cued stats carry the per-width pulse tally,
    estimated_water sums volume PER WIDTH (exact under a mid-run width change),
    not by multiplying one configured width by the pulse count."""

    def _cued_widths(self):
        # Two rewarded trials at DIFFERENT prime widths (500 + 400 ms) plus two
        # 300 ms top-ups on trial 0, so the tally spans three widths.
        log = Phase3Log(_seq(n=6), animal="RAT600",
                        session_start_iso="2026-07-31T12:00:00")
        log.choice_scored(0, _side_for(log, 0, True), CORRECT, "t0")
        log.reward_fired(0, "r0", 500)
        log.topup_fired(0, 300)
        log.topup_fired(0, 300)
        log.choice_scored(1, _side_for(log, 1, True), CORRECT, "t1")
        log.reward_fired(1, "r1", 400)
        return phase3_stats.compute(log.snapshot(2, armed=True))

    def test_per_width_sum_is_exact(self):
        cued = self._cued_widths()
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, params=_PARAMS)
        water = summ.estimated_water
        expected = (reward_volume.reward_volume_ul(500)
                    + reward_volume.reward_volume_ul(400)
                    + 2 * reward_volume.reward_volume_ul(300))
        self.assertEqual(water["pulses"], 4)          # 2 primes + 2 top-ups
        self.assertAlmostEqual(water["ul"], round(expected, 1))
        # The per-width breakdown is exposed, and pulse_ms lists the widths - NOT
        # the single configured 250 ms from _PARAMS (which the fallback would use).
        self.assertEqual(water["by_ms"], {"300": 2, "400": 1, "500": 1})
        self.assertNotEqual(water["pulse_ms"], 250)
        data = json.loads(summ.render_json())
        self.assertEqual(data["estimated_water"]["by_ms"]["300"], 2)


class TestManualWater(unittest.TestCase):
    # manual_water totals OPERATOR reward dispenses (the GUI button), passed as
    # manual_pulses_ms by the shell which collects them at their source; it is
    # disjoint from and additive with the task estimate (docs/decisions/0084).
    def test_zero_when_no_operator_pulses(self):
        # A session with no operator dispenses still reports a definite zero (so
        # the day's intake reconciles), unlike estimated_water's None.
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END)
        self.assertEqual(summ.manual_water, {"ul": 0.0, "pulses": 0})
        # No manual line clutters the text when there were no operator pulses.
        self.assertNotIn("Water (man):", summ.render_text())

    def test_totals_operator_pulses_at_their_widths(self):
        # Two operator-fired pulses (250 ms + 300 ms) summed at their own widths.
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            manual_pulses_ms=[250.0, 300.0])
        expected = (reward_volume.reward_volume_ul(250)
                    + reward_volume.reward_volume_ul(300))
        self.assertEqual(summ.manual_water["pulses"], 2)
        self.assertAlmostEqual(summ.manual_water["ul"], round(expected, 1))
        data = json.loads(summ.render_json())
        self.assertEqual(data["manual_water"]["pulses"], 2)
        self.assertIn("Water (man):", summ.render_text())

    def test_manual_is_independent_of_task_estimate(self):
        # Operator water is a separate channel: the task estimate does not change,
        # and both appear side by side (docs/decisions/0084).
        cued, _ = _cued_stats()
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, params=_PARAMS, manual_pulses_ms=[300.0])
        self.assertEqual(summ.estimated_water["pulses"], 3)   # unchanged
        self.assertEqual(summ.manual_water["pulses"], 1)
        text = summ.render_text()
        self.assertIn("Water (est):", text)
        self.assertIn("Water (man):", text)

    def test_task_and_operator_channels_are_additive(self):
        # The two channels are disjoint, so the day's reward intake is their sum
        # (docs/decisions/0084). Task = 3 primes @ 250 ms; operator = one 300 ms
        # button press; neither figure includes the other.
        cued, _ = _cued_stats()
        summ = session_summary.build_summary(
            session_id="s1", animal="RAT600", t0=_T0, t_end=_T_END,
            cued=cued, params=_PARAMS, manual_pulses_ms=[300.0])
        task_ul = summ.estimated_water["ul"]
        operator_ul = summ.manual_water["ul"]
        self.assertAlmostEqual(task_ul, reward_volume.reward_volume_ul(250) * 3)
        self.assertAlmostEqual(operator_ul, round(reward_volume.reward_volume_ul(300), 1))
        # Additive: the total is exactly task + operator, with no overlap. Each
        # channel is rounded to 1 dp independently (session_summary), so the
        # expected total sums those rounded components.
        self.assertAlmostEqual(task_ul + operator_ul,
                               round(reward_volume.reward_volume_ul(250) * 3, 1)
                               + round(reward_volume.reward_volume_ul(300), 1))


def _note_entries():
    """A small ordered list of timestamped note entries (as session_notes would
    render them): a plain note, an edited note, and a tag press."""
    return [
        {"id": 0, "iso": "2026-07-31T12:00:05.000", "text": "dissuaded non-stem",
         "mono": 5.0, "trial_index": 3, "tag": None, "count": 1,
         "edited_iso": None},
        {"id": 1, "iso": "2026-07-31T12:00:20.000", "text": "hand-guided (fixed)",
         "mono": 20.0, "trial_index": 4, "tag": None, "count": 1,
         "edited_iso": "2026-07-31T12:05:00.000"},
        {"id": 2, "iso": "2026-07-31T12:00:30.000", "text": "lift-backs",
         "mono": 30.0, "trial_index": 4, "tag": "lift-backs", "count": 1,
         "edited_iso": None},
    ]


class TestNotes(unittest.TestCase):
    def test_notes_are_structured_list_in_json(self):
        entries = _note_entries()
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END, notes=entries)
        data = json.loads(summ.render_json())
        self.assertEqual(data["schema_version"], 6)
        self.assertEqual(data["notes"], entries)          # verbatim list

    def test_notes_render_timestamped_list(self):
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END,
            notes=_note_entries())
        text = summ.render_text()
        self.assertIn("Notes", text)
        # Time-of-day + trial index rendered per entry.
        self.assertIn("12:00:05  trial 3  dissuaded non-stem", text)
        # Edited entry flagged, original stamp preserved.
        self.assertIn("(edited)", text)
        # Tag rendered on its entry + summarised in the tally.
        self.assertIn("#lift-backs", text)
        self.assertIn("Tags: lift-backs x1", text)

    def test_notes_placeholder_when_blank(self):
        summ = session_summary.build_summary(
            session_id="s", animal="a", t0=_T0, t_end=_T_END)
        self.assertIn("(none)", summ.render_text())


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
