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

import dataclasses
import unittest

from tmaze.core.phase3.debias import DebiasBlockConfig
from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.phase3_ribbon import (LEFT_LABEL, RIGHT_LABEL, RIBBON_AFTER, RIBBON_BEFORE,
                           RibbonRow, all_ribbon_rows, cue_side, ribbon_rows)
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


class TestReturnEnabled(unittest.TestCase):
    """The return-cue on/off flag flows from the log into every ribbon row, so the
    view can suppress the return-cue readout when it is disabled."""

    def test_default_is_enabled(self):
        rows = ribbon_rows(_log(n=20).snapshot(0, armed=True))
        self.assertTrue(rows)
        self.assertTrue(all(r.return_enabled for r in rows))

    def test_disabled_propagates_to_rows(self):
        log = Phase3Log(_seq(n=20), animal="RAT600",
                        session_start_iso="2026-07-23T12:00:00",
                        return_enabled=False)
        snap = log.snapshot(0, armed=True)
        self.assertFalse(snap.return_enabled)
        rows = ribbon_rows(snap)
        self.assertTrue(rows)
        self.assertTrue(all(not r.return_enabled for r in rows))
        # The full-plan variant carries it too (the Rig-view scrollable strip).
        self.assertTrue(all(not r.return_enabled for r in all_ribbon_rows(snap)))


class TestAllRibbonRows(unittest.TestCase):
    """The unwindowed full-plan variant for the scrollable Rig-view strip
    (docs/decisions/0135)."""

    def test_returns_every_trial_in_order(self):
        rows = all_ribbon_rows(_log(n=20).snapshot(7, armed=True))
        self.assertEqual(len(rows), 20)
        self.assertEqual([r.trial for r in rows], list(range(1, 21)))

    def test_exactly_one_current_at_the_clamped_index(self):
        rows = all_ribbon_rows(_log(n=20).snapshot(7, armed=True))
        current = [r for r in rows if r.is_current]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].trial, 8)          # 1-based index of trial 7

    def test_completed_session_marks_the_last_trial(self):
        # current_index == total clamps the current marker onto the final trial.
        rows = all_ribbon_rows(_log(n=20).snapshot(20, armed=False))
        self.assertEqual(len(rows), 20)
        self.assertEqual(sum(r.is_current for r in rows), 1)
        self.assertTrue(rows[-1].is_current)

    def test_none_snapshot_is_empty(self):
        # Not armed yet -> nothing to show (same guard as ribbon_rows).
        self.assertEqual(all_ribbon_rows(None), [])

    def test_returns_ribbon_row_instances(self):
        rows = all_ribbon_rows(_log(n=5, block=2).snapshot(0, armed=True))
        self.assertTrue(all(isinstance(r, RibbonRow) for r in rows))


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


class TestUpcomingBlockMarkers(unittest.TestCase):
    """Provisional block/probe markers on UPCOMING (un-committed) trials, driven by
    the snapshot's block prediction (docs/decisions/0152). The persisted row columns
    stay blank for those trials (ADR-0096), so these come from ``probe_upcoming`` /
    ``intervention_next``, tested here in isolation via ``dataclasses.replace``."""

    def _snap(self, current=5, **fields):
        # A freshly armed 20-trial plan (every row un-scored) with the prediction
        # fields overridden - decouples the ribbon mapping from the scheduler.
        base = _log(n=20).snapshot(current, armed=True)
        return dataclasses.replace(base, **fields)

    def test_probe_upcoming_marks_the_whole_remaining_block(self):
        # current+1 .. current+probe_upcoming are marked probe; the trial after is not.
        snap = self._snap(current=5, probe_upcoming=3)
        by_trial = {r.trial: r for r in all_ribbon_rows(snap)}
        # trial numbers are 1-based (index+1): current index 5 == trial 6.
        self.assertFalse(by_trial[6].probe)                 # current (own column)
        self.assertTrue(by_trial[7].probe)                  # +1
        self.assertTrue(by_trial[8].probe)                  # +2
        self.assertTrue(by_trial[9].probe)                  # +3 (last of block)
        self.assertFalse(by_trial[10].probe)                # +4: past the block

    def test_intervention_next_marks_only_the_single_next_trial(self):
        snap = self._snap(current=5, intervention_next=True)
        by_trial = {r.trial: r for r in all_ribbon_rows(snap)}
        self.assertFalse(by_trial[6].corrective)            # current (own column)
        self.assertTrue(by_trial[7].corrective)             # +1 only
        self.assertFalse(by_trial[8].corrective)            # +2 stays normal

    def test_zero_prediction_marks_nothing(self):
        # No running block: neither an upcoming probe nor a corrective marker.
        snap = self._snap(current=5, probe_upcoming=0, intervention_next=False)
        rows = all_ribbon_rows(snap)
        self.assertFalse(any(r.probe for r in rows))
        self.assertFalse(any(r.corrective for r in rows))

    def test_prediction_never_marks_a_resolved_trial(self):
        # Score trials 6 and 7 (indices 6/7), then predict a probe over them: a
        # resolved row keeps its own (blank) status and is not retro-flagged.
        log = _log(n=20)
        _score(log, 6, "left", "CORRECT")
        _score(log, 7, "right", "INCORRECT")
        snap = dataclasses.replace(log.snapshot(5, armed=True), probe_upcoming=4)
        by_trial = {r.trial: r for r in all_ribbon_rows(snap)}
        self.assertTrue(by_trial[7].resolved)
        self.assertFalse(by_trial[7].probe)                 # resolved -> untouched
        self.assertFalse(by_trial[8].probe)
        self.assertTrue(by_trial[9].probe)                  # a later un-scored one

    def test_probe_takes_precedence_over_intervention_on_the_next_cell(self):
        # A probe suspends interventions, so an upcoming probe cell wins the styling.
        snap = self._snap(current=5, probe_upcoming=2, intervention_next=True)
        by_trial = {r.trial: r for r in all_ribbon_rows(snap)}
        self.assertTrue(by_trial[7].probe)
        self.assertFalse(by_trial[7].corrective)

    def test_windowed_ribbon_also_marks_upcoming(self):
        # The fixed 7-cell window (Phase 3 tab) uses the same helper as the strip.
        snap = self._snap(current=5, probe_upcoming=2)
        by_trial = {r.trial: r for r in ribbon_rows(snap)}
        self.assertTrue(by_trial[7].probe)
        self.assertTrue(by_trial[8].probe)


class TestProbeUpcomingIntegration(unittest.TestCase):
    """End-to-end: firing a real probe block makes the snapshot advertise the
    upcoming forced trials, and cancelling it clears them (docs/decisions/0152)."""

    def _probe_seq(self):
        from tmaze.core.phase3.probe_blocks import ProbeBlockConfig
        cfg = TrialConfig(max_trials=40, block_size=20, max_same_run=4,
                          probe=ProbeBlockConfig(enabled=True, block_len=4,
                                                 windows=((1, 40),)))
        return TrialSequence(cfg, seed=7)

    def test_firing_a_probe_advertises_the_upcoming_block(self):
        seq = self._probe_seq()
        log = Phase3Log(seq, animal="R", session_start_iso="t")
        seq.commit(0)
        # A correct choice inside the window fires the probe on the FOLLOWING trials.
        correct = seq.correct_arm(0)
        seq.probe_trigger_on_score(0, correct=True, intervention_active=False)
        snap = log.snapshot(0, armed=True)
        self.assertEqual(snap.probe_upcoming, 4)            # block_len forced trials
        by_trial = {r.trial: r for r in all_ribbon_rows(snap)}
        self.assertTrue(all(by_trial[t].probe for t in (2, 3, 4, 5)))
        self.assertFalse(by_trial[6].probe)

    def test_cancelling_the_probe_clears_the_upcoming_markers(self):
        seq = self._probe_seq()
        log = Phase3Log(seq, animal="R", session_start_iso="t")
        seq.commit(0)
        seq.probe_trigger_on_score(0, correct=True, intervention_active=False)
        seq.end_probe_block()                               # operator abort
        snap = log.snapshot(0, armed=True)
        self.assertEqual(snap.probe_upcoming, 0)
        self.assertFalse(any(r.probe for r in all_ribbon_rows(snap)))


if __name__ == "__main__":
    unittest.main()
