"""
test_phase3_ribbon.py - headless tests for the Phase 3 trial-ribbon helper.

Covers the pure display-row builder (phase3_ribbon.py; see docs/decisions/0051)
over deterministic snapshots (build a Phase3Log, fill some trials, .snapshot()):
the window around the current trial, near-start / near-end / completed clamping,
the HIGH/LOW -> arm mapping (both high_tone_is_left orientations, cross-checked
against the plan's correct_arm), and resolved-trial outcome fields. Pure stdlib;
no hardware/Tk.

Run from host/:  python -m unittest test_phase3_ribbon
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3.debias import DebiasBlockConfig
from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.phase3_ribbon import (LEFT_LABEL, RIGHT_LABEL, RIBBON_AFTER, RIBBON_BEFORE,
                           RibbonRow, cue_side, ribbon_rows)
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.phase3.trial_sequence import (
    GEN_DEBIAS_BLOCKS, HIGH, LEFT, LOW, RIGHT, TrialConfig, TrialSequence)


def _seq(n=20, block=20, seed=123, high_tone_is_left=True):
    return TrialSequence(
        TrialConfig(max_trials=n, block_size=block, max_same_run=3,
                    high_tone_is_left=high_tone_is_left),
        seed=seed)


def _log(**kw):
    return Phase3Log(_seq(**kw), animal="RAT600",
                     session_start_iso="2026-07-23T12:00:00")


def _score(log, index, chosen, outcome):
    """Resolve trial ``index`` (fills chosen_arm/outcome like the controller)."""
    log.choice_scored(index, chosen, outcome, f"t_choice{index}")


class TestCueSide(unittest.TestCase):
    def test_high_is_left_mapping(self):
        # high tone on the left: HIGH -> LEFT, LOW -> RIGHT.
        self.assertEqual(cue_side(HIGH, True), LEFT_LABEL)
        self.assertEqual(cue_side(LOW, True), RIGHT_LABEL)

    def test_high_is_right_mapping(self):
        # high tone on the right flips both.
        self.assertEqual(cue_side(HIGH, False), RIGHT_LABEL)
        self.assertEqual(cue_side(LOW, False), LEFT_LABEL)

    def test_choice_side_matches_plan_correct_arm(self):
        # The side read back from the cue must equal the trial's planned correct
        # arm, for both tone orientations - the invariant cue_side encodes.
        for h_left in (True, False):
            log = _log(high_tone_is_left=h_left)
            snap = log.snapshot(0, armed=True)
            for row in snap.rows:
                self.assertEqual(cue_side(row.choice_cue, h_left), row.correct_arm)


class TestWindow(unittest.TestCase):
    def test_full_window_in_the_middle(self):
        # A trial with room on both sides yields before + current + after rows.
        snap = _log(n=20).snapshot(10, armed=True)
        rows = ribbon_rows(snap)
        self.assertEqual(len(rows), RIBBON_BEFORE + 1 + RIBBON_AFTER)
        self.assertEqual([r.trial for r in rows], [8, 9, 10, 11, 12, 13, 14])
        current = [r for r in rows if r.is_current]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].trial, 11)          # 1-based index of trial 10

    def test_near_start_clamps_the_front(self):
        # At trial 0 there is nothing behind it: only current + after.
        rows = ribbon_rows(_log(n=20).snapshot(0, armed=True))
        self.assertEqual([r.trial for r in rows], [1, 2, 3, 4])
        self.assertTrue(rows[0].is_current)             # trial 1 is current
        self.assertEqual(sum(r.is_current for r in rows), 1)

    def test_near_end_clamps_the_back(self):
        # At the last trial there is nothing ahead: only before + current.
        snap = _log(n=20).snapshot(19, armed=True)
        rows = ribbon_rows(snap)
        self.assertEqual([r.trial for r in rows], [17, 18, 19, 20])
        self.assertTrue(rows[-1].is_current)            # trial 20 is current

    def test_completed_session_shows_tail(self):
        # current_index == total (all trials done) clamps to the final trial.
        snap = _log(n=20).snapshot(20, armed=False)
        rows = ribbon_rows(snap)
        self.assertEqual([r.trial for r in rows], [17, 18, 19, 20])
        self.assertTrue(rows[-1].is_current)

    def test_custom_window_size(self):
        rows = ribbon_rows(_log(n=20).snapshot(10, armed=True), before=1, after=2)
        self.assertEqual([r.trial for r in rows], [10, 11, 12, 13])

    def test_tiny_plan_never_out_of_range(self):
        # A plan smaller than the window still clamps cleanly (no IndexError).
        rows = ribbon_rows(_log(n=2, block=2).snapshot(0, armed=True))
        self.assertEqual([r.trial for r in rows], [1, 2])


class TestEmpty(unittest.TestCase):
    def test_none_snapshot_is_empty(self):
        self.assertEqual(ribbon_rows(None), [])

    def test_returns_ribbon_row_instances(self):
        rows = ribbon_rows(_log(n=5, block=2).snapshot(0, armed=True))
        self.assertTrue(all(isinstance(r, RibbonRow) for r in rows))


class TestResolvedFields(unittest.TestCase):
    def test_unresolved_trial_has_no_outcome(self):
        # A freshly armed session: nothing scored yet.
        rows = ribbon_rows(_log(n=20).snapshot(0, armed=True))
        for r in rows:
            self.assertFalse(r.resolved)
            self.assertIsNone(r.chosen_arm)
            self.assertIsNone(r.outcome)

    def test_resolved_trials_carry_choice_and_outcome(self):
        log = _log(n=20)
        _score(log, 0, "left", "CORRECT")
        _score(log, 1, "right", "INCORRECT")
        rows = ribbon_rows(log.snapshot(2, armed=True))
        by_trial = {r.trial: r for r in rows}
        self.assertTrue(by_trial[1].resolved)
        self.assertEqual(by_trial[1].chosen_arm, "LEFT")     # normalized upper
        self.assertEqual(by_trial[1].outcome, "CORRECT")
        self.assertTrue(by_trial[2].resolved)
        self.assertEqual(by_trial[2].chosen_arm, "RIGHT")
        self.assertEqual(by_trial[2].outcome, "INCORRECT")
        # the current trial (index 2 -> trial 3) is still unresolved
        self.assertFalse(by_trial[3].resolved)
        self.assertIsNone(by_trial[3].outcome)

    def test_return_cues_come_from_the_plan(self):
        seq = _seq(n=20)
        rows = ribbon_rows(Phase3Log(seq, animal="R",
                                     session_start_iso="t").snapshot(5, armed=True))
        for r in rows:
            i = r.trial - 1
            self.assertEqual(r.return_cue_left, seq.return_cue(0, i))   # LEFT arm
            self.assertEqual(r.return_cue_right, seq.return_cue(1, i))  # RIGHT arm
            self.assertIn(r.choice_cue, (HIGH, LOW))


class TestCorrectiveFlag(unittest.TestCase):
    def test_corrective_matches_intervention_state(self):
        # Drive a debias session so some trials fall inside a corrective block, then
        # check the ribbon flags exactly those rows (sched_state == INTERVENTION).
        cfg = TrialConfig(max_trials=60, block_size=20, max_same_run=4,
                          generator=GEN_DEBIAS_BLOCKS,
                          debias=DebiasBlockConfig(window=8, min_errors=4,
                                                   trigger_bias=0.3,
                                                   exit_consecutive=3,
                                                   max_block_len=6))
        seq = TrialSequence(cfg, seed=3)
        log = Phase3Log(seq, animal="RAT600", session_start_iso="t")
        n = 40
        for i in range(n):
            seq.commit(i)
            log.set_decision(i)
            state = seq.trial_meta(i)["sched_state"]
            correct = seq.correct_arm(i)
            chosen = RIGHT if state == "INTERVENTION" else LEFT
            side = "left" if chosen == LEFT else "right"
            outcome = CORRECT if chosen == correct else INCORRECT
            log.choice_scored(i, side, outcome, f"t{i}")
            seq.record_outcome(i, correct, chosen)
            log.set_decision(i)

        snap = log.snapshot(n, armed=True)
        rows = ribbon_rows(snap, before=n, after=0)
        by_trial = {r.trial: r for r in rows}
        self.assertTrue(any(r.corrective for r in rows))   # a block appeared
        for row in snap.rows:
            ribbon = by_trial.get(row.trial)
            if ribbon is None:
                continue
            self.assertEqual(ribbon.corrective, row.sched_state == "INTERVENTION")


if __name__ == "__main__":
    unittest.main()
