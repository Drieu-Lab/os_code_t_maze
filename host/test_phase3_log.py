"""
test_phase3_log.py - headless tests for the Phase 3 session data table.

Covers the plan built from the seed-deterministic sequence, the live event
fills, the corrected-timeline lick attribution (a reward lick lands after the
trial has advanced, so it must attach to the trial whose reward it collects),
and the CSV rendering. Pure stdlib; no hardware/Tk. See docs/decisions/0047.

Run from host/:  python -m unittest test_phase3_log
"""

from __future__ import annotations

import unittest
from dataclasses import fields

from phase3_log import COLUMNS, Phase3Log, _Row
from trial_sequence import HIGH, LEFT, LOW, RIGHT, TrialConfig, TrialSequence


def _seq(n=20, block=20, seed=123):
    return TrialSequence(TrialConfig(max_trials=n, block_size=block,
                                     max_same_run=3), seed=seed)


def _log(**kw):
    return Phase3Log(_seq(**kw), animal="RAT600",
                     session_start_iso="2026-07-23T12:00:00")


class TestColumns(unittest.TestCase):
    def test_columns_align_with_row_fields(self):
        # COLUMNS is "trial" + the _Row fields, in order; guards drift between the
        # header and the rendered cells.
        self.assertEqual(list(COLUMNS), ["trial"] + [f.name for f in fields(_Row)])


class TestPlan(unittest.TestCase):
    def test_plan_matches_sequence(self):
        s = _seq()
        log = Phase3Log(s, animal="R", session_start_iso="t")
        self.assertEqual(len(log), len(s))
        for i, r in enumerate(log._rows):
            self.assertEqual(r.correct_arm,
                             "LEFT" if s.correct_arm(i) == LEFT else "RIGHT")
            self.assertEqual(r.choice_cue, s.choice_cue(i))
            self.assertEqual(r.return_cue_left, s.return_cue(LEFT, i))
            self.assertEqual(r.return_cue_right, s.return_cue(RIGHT, i))
            self.assertIn(r.choice_cue, (HIGH, LOW))
            # outcome fields start empty
            self.assertIsNone(r.chosen_arm)
            self.assertIsNone(r.rewarded)


class TestLifecycle(unittest.TestCase):
    def test_full_rewarded_trial_fills_fields(self):
        log = _log()
        log.trial_started(0, "t_start")
        log.choice_cue_started(0, "cc_on")
        log.choice_cue_stopped(0, "cc_off")
        log.choice_scored(0, "left", "CORRECT", "t_choice")
        log.reward_fired(0, "t_reward")
        log.return_cue_started(0, "right", HIGH, "rc_on")
        log.return_cue_stopped(0, "rc_off")
        log.trial_ended(0, "t_end")
        log.lick("t_lick", 12345)
        r = log._rows[0]
        self.assertEqual(r.chosen_arm, "LEFT")            # normalized to upper
        self.assertEqual(r.outcome, "CORRECT")
        self.assertEqual(r.rewarded, "1")
        self.assertEqual(r.return_arm_used, "RIGHT")
        self.assertEqual(r.return_cue_played, HIGH)
        self.assertEqual(r.trial_start_iso, "t_start")
        self.assertEqual(r.choice_time_iso, "t_choice")
        self.assertEqual(r.reward_cmd_iso, "t_reward")
        self.assertEqual(r.trial_end_iso, "t_end")
        self.assertEqual(r.lick_time_iso, "t_lick")
        self.assertEqual(r.lick_fw_ms, "12345")

    def test_unrewarded_trial_marks_zero_and_no_lick(self):
        log = _log()
        log.choice_scored(0, "right", "INCORRECT", "t_choice")
        # no reward_fired -> a stray lick must not attach anywhere
        log.lick("t_lick", 99)
        r = log._rows[0]
        self.assertEqual(r.rewarded, "0")
        self.assertIsNone(r.lick_time_iso)

    def test_trial_start_keeps_first(self):
        log = _log()
        log.trial_started(0, "first")
        log.trial_started(0, "second")     # a later poke-and-retreat
        self.assertEqual(log._rows[0].trial_start_iso, "first")

    def test_skipped_return_records_none(self):
        log = _log()
        log.return_cue_started(0, "left", None, "rc_on")  # gate skipped the tone
        self.assertEqual(log._rows[0].return_cue_played, "NONE")
        self.assertEqual(log._rows[0].return_arm_used, "LEFT")


class TestLickAttribution(unittest.TestCase):
    def test_lick_attaches_across_advancement(self):
        # Reward fires on trial 0; the trial ends (index advances); the collection
        # lick arrives during trial 1 but must attach to trial 0.
        log = _log()
        log.reward_fired(0, "t_reward0")
        log.trial_ended(0, "t_end0")
        log.trial_started(1, "t_start1")
        log.lick("t_lick0", 111)
        self.assertEqual(log._rows[0].lick_time_iso, "t_lick0")
        self.assertIsNone(log._rows[1].lick_time_iso)

    def test_only_first_lick_counts(self):
        log = _log()
        log.reward_fired(0, "t_reward")
        log.lick("first", 1)
        log.lick("second", 2)             # extra licks at the well are ignored
        self.assertEqual(log._rows[0].lick_time_iso, "first")
        self.assertEqual(log._rows[0].lick_fw_ms, "1")

    def test_lick_without_fw_ms(self):
        log = _log()
        log.reward_fired(0, "t")
        log.lick("t_lick", None)
        self.assertEqual(log._rows[0].lick_fw_ms, "")


class TestRender(unittest.TestCase):
    def test_csv_structure(self):
        log = _log(n=20)
        log.choice_scored(0, "left", "CORRECT", "t_choice")
        text = log.render_csv()
        lines = text.strip("\n").split("\n")
        comments = [ln for ln in lines if ln.startswith("#")]
        self.assertTrue(any("animal=RAT600" in ln for ln in comments))
        self.assertTrue(any("high_tone_is_left" in ln for ln in comments))
        # header row + 20 data rows follow the comments
        body = [ln for ln in lines if not ln.startswith("#")]
        self.assertEqual(body[0], ",".join(COLUMNS))
        self.assertEqual(len(body), 1 + 20)
        # each data row has exactly len(COLUMNS) cells
        for row in body[1:]:
            self.assertEqual(len(row.split(",")), len(COLUMNS))
        # trial 1's scored fields rendered
        first = body[1].split(",")
        self.assertEqual(first[0], "1")
        self.assertEqual(first[COLUMNS.index("chosen_arm")], "LEFT")
        self.assertEqual(first[COLUMNS.index("outcome")], "CORRECT")


if __name__ == "__main__":
    unittest.main()
