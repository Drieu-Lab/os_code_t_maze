"""
test_phase3_task.py - headless tests for the host-owned Phase 3 trial FSM.

Drives Phase3Task with hand-built TaskEventFrame-like objects (duck-typed) to
check scoring, reward policy, advancement on the return lap, the one-score-per-
trial latch, invalid-frame inertness, and completion (see docs/decisions/0046).
Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_phase3_task
"""

from __future__ import annotations

import types
import unittest

from tmaze.core.maze_state import (
    HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN,
    RIGHT_RETURN, MazeStateEstimator, MazeStateParams, Zone, ZoneMap,
)
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT, UNCUED, Phase3Config, Phase3Task
from tmaze.core.task_events import TaskEvents
from tmaze.core.phase3.trial_sequence import LEFT, RIGHT, TrialConfig, TrialSequence


def frame(valid=True, choice_entered=None, outbound_used_stem=False,
          loop_closed=None, choice_committed=None, left_hub_via_stem=None,
          trial_trip=None):
    """A minimal TaskEventFrame stand-in (only the fields Phase3Task reads).

    ``choice_committed`` is the shared once-per-excursion commit edge Phase3Task
    scores off (task_events.py, FW-0020); ``choice_entered`` is retained for the
    fields the estimator sets on the same tick. Phase3Task now scores on the
    shared ``trial_trip`` credential (docs/decisions/0168, extending 0099), which
    currently equals ``left_hub_via_stem``; both default to mirroring
    ``outbound_used_stem`` (all three are true on a genuine cued trial) so the
    existing scoring tests read unchanged, and the divider-jump case sets them
    apart explicitly (left_hub_via_stem/trial_trip False while stem is True)."""
    if left_hub_via_stem is None:
        left_hub_via_stem = outbound_used_stem
    if trial_trip is None:
        trial_trip = left_hub_via_stem
    return types.SimpleNamespace(
        valid=valid, choice_entered=choice_entered,
        outbound_used_stem=outbound_used_stem, loop_closed=loop_closed,
        choice_committed=choice_committed, left_hub_via_stem=left_hub_via_stem,
        trial_trip=trial_trip)


def side_of(arm):
    return "left" if arm == LEFT else "right"


def make_task(reward_incorrect=False, seed=42):
    ts = TrialSequence(TrialConfig(max_trials=20, block_size=20, max_same_run=3),
                       seed=seed)
    return Phase3Task(ts, Phase3Config(reward_incorrect=reward_incorrect))


class TestPhase3Task(unittest.TestCase):
    def test_correct_choice_rewards_then_advances(self):
        t = make_task()
        correct = t.current_correct_arm()
        # cue identity for the trial matches the schedule
        self.assertEqual(t.current_choice_freq(), t.seq.choice_freq(0))
        d = t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True))
        self.assertEqual(d.outcome, CORRECT)
        self.assertTrue(d.reward)
        self.assertEqual(d.choice_side, side_of(correct))
        self.assertFalse(d.advanced)
        # advance on the return lap
        d2 = t.update(frame(loop_closed=side_of(correct)))
        self.assertTrue(d2.advanced)
        self.assertEqual(t.index, 1)

    def test_incorrect_choice_no_reward_by_default(self):
        t = make_task(reward_incorrect=False)
        correct = t.current_correct_arm()
        wrong = 1 - correct
        d = t.update(frame(choice_committed=side_of(wrong), outbound_used_stem=True))
        self.assertEqual(d.outcome, INCORRECT)
        self.assertFalse(d.reward)

    def test_incorrect_choice_rewarded_when_shaping(self):
        t = make_task(reward_incorrect=True)
        correct = t.current_correct_arm()
        wrong = 1 - correct
        d = t.update(frame(choice_committed=side_of(wrong), outbound_used_stem=True))
        self.assertEqual(d.outcome, INCORRECT)
        self.assertTrue(d.reward)

    def test_choice_without_stem_is_not_scored(self):
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(choice_committed=side_of(correct), outbound_used_stem=False))
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(t.index, 0)

    def test_divider_jump_choice_is_not_scored(self):
        # docs/decisions/0099: the trip visited the stem (outbound_used_stem) but
        # did NOT depart the hub up the stem (left_hub_via_stem is False) - e.g. it
        # set off up a side arm and hopped over the divider into the stem. That is
        # not a genuine trial start, so a "correct" commit must not score/reward.
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(choice_committed=side_of(correct),
                           outbound_used_stem=True, left_hub_via_stem=False))
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(t.index, 0)

    def test_invalid_frame_is_inert(self):
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(valid=False, choice_committed=side_of(correct),
                           outbound_used_stem=True))
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(t.index, 0)

    def test_single_score_per_trial(self):
        t = make_task()
        correct = t.current_correct_arm()
        t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True))
        # A second choice-arm entry before returning does NOT re-commit (the shared
        # once-per-trip edge is None on it), so it must not re-score.
        d = t.update(frame(choice_committed=None, choice_entered=side_of(correct),
                           outbound_used_stem=True))
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(t.index, 0)

    def test_runs_to_completion(self):
        t = make_task()
        n = len(t.seq)
        for _ in range(n):
            correct = t.current_correct_arm()
            t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True))
            t.update(frame(loop_closed=side_of(correct)))
        self.assertTrue(t.complete)
        # once complete, further frames are inert
        d = t.update(frame(choice_committed="left", outbound_used_stem=True))
        self.assertTrue(d.complete)
        self.assertIsNone(d.outcome)
        self.assertIsNone(t.current_correct_arm())


class TestUncued(unittest.TestCase):
    """A genuine trial_trip commit with no CHOICE cue that trip is UNCUED: not
    scored, not rewarded, and the planned trial re-runs (docs/decisions/0171)."""

    def test_uncued_commit_is_not_scored_or_rewarded(self):
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True),
                     choice_cue_on=False)
        self.assertEqual(d.outcome, UNCUED)
        self.assertEqual(d.choice_side, side_of(correct))   # what the rat did is kept
        self.assertFalse(d.reward)
        self.assertFalse(d.advanced)
        self.assertEqual(t.index, 0)                        # trial not consumed

    def test_uncued_return_lap_clears_without_advancing(self):
        t = make_task()
        correct = t.current_correct_arm()
        t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True),
                 choice_cue_on=False)
        d = t.update(frame(loop_closed=side_of(correct)))
        self.assertTrue(d.uncued_cleared)
        self.assertFalse(d.advanced)
        self.assertEqual(t.index, 0)                        # SAME trial still current

    def test_planned_trial_reruns_and_scores_once_cued(self):
        t = make_task()
        correct = t.current_correct_arm()
        # Uncued trip, then home: index preserved.
        t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True),
                 choice_cue_on=False)
        t.update(frame(loop_closed=side_of(correct)))
        self.assertEqual(t.index, 0)
        # Re-run the SAME trial, this time cued -> scores + advances normally.
        d = t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True),
                     choice_cue_on=True)
        self.assertEqual(d.outcome, CORRECT)
        self.assertTrue(d.reward)
        d2 = t.update(frame(loop_closed=side_of(correct)))
        self.assertTrue(d2.advanced)
        self.assertEqual(t.index, 1)

    def test_cue_on_default_scores_normally(self):
        # The default (no choice_cue_on arg) is "cued", so pre-0171 callers/tests
        # keep scoring - only an explicit choice_cue_on=False yields UNCUED.
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True))
        self.assertEqual(d.outcome, CORRECT)

    def test_uncued_only_on_a_trial_trip(self):
        # A non-trial-trip commit (divider jump) is still just unscored, NOT UNCUED
        # - UNCUED is reserved for a GENUINE trip that simply wasn't cued.
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True,
                           left_hub_via_stem=False), choice_cue_on=False)
        self.assertIsNone(d.outcome)
        self.assertEqual(t.index, 0)


class TestManualControls(unittest.TestCase):
    """Operator buttons drive the cursor directly (docs/decisions/0058)."""

    def test_abandon_advances_and_clears_latch(self):
        t = make_task()
        correct = t.current_correct_arm()
        # Score a choice so the awaiting-return latch is set, then abandon.
        t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True))
        self.assertEqual(t.abandon_current(), 0)      # returns abandoned index
        self.assertEqual(t.index, 1)
        # Latch cleared: the next trial scores immediately on a stem choice (no
        # need to wait for a return lap first).
        c1 = t.current_correct_arm()
        d = t.update(frame(choice_committed=side_of(c1), outbound_used_stem=True))
        self.assertIsNotNone(d.outcome)
        self.assertEqual(d.trial_index, 1)

    def test_abandon_clamps_at_end(self):
        t = make_task()
        for _ in range(len(t.seq)):
            t.abandon_current()
        self.assertEqual(t.index, len(t.seq))
        self.assertTrue(t.complete)
        self.assertEqual(t.abandon_current(), len(t.seq))   # clamped, no overrun
        self.assertEqual(t.index, len(t.seq))

    def test_repeat_stays_on_trial_and_clears_latch(self):
        t = make_task()
        correct = t.current_correct_arm()
        t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True))
        self.assertEqual(t.repeat_current(), 0)
        self.assertEqual(t.index, 0)
        # The SAME trial can be re-scored after a repeat.
        d = t.update(frame(choice_committed=side_of(correct), outbound_used_stem=True))
        self.assertIsNotNone(d.outcome)
        self.assertEqual(d.trial_index, 0)

    def test_step_back_decrements_and_floors_at_zero(self):
        t = make_task()
        c0 = t.current_correct_arm()
        t.update(frame(choice_committed=side_of(c0), outbound_used_stem=True))
        t.update(frame(loop_closed=side_of(c0)))
        self.assertEqual(t.index, 1)
        self.assertEqual(t.step_back(), 0)
        self.assertEqual(t.index, 0)
        self.assertEqual(t.step_back(), 0)             # floored at 0

    def test_step_back_reenables_scoring_after_advance(self):
        t = make_task()
        c0 = t.current_correct_arm()
        t.update(frame(choice_committed=side_of(c0), outbound_used_stem=True))
        t.update(frame(loop_closed=side_of(c0)))       # advanced to trial 1
        t.step_back()                                  # back to trial 0
        d = t.update(frame(choice_committed=side_of(t.current_correct_arm()),
                           outbound_used_stem=True))
        self.assertEqual(d.trial_index, 0)
        self.assertIsNotNone(d.outcome)


# --------------------------------------------------------------------------- #
# End-to-end integration: real MazeStateEstimator -> TaskEvents -> Phase3Task  #
# over synthetic centroid trajectories. This pins the CURRENT scoring/reward/  #
# advancement behavior across the shared-edge refactor (FW-0020): it drives    #
# REAL task_events frames, so it is agnostic to which per-tick field Phase3Task #
# reads internally (choice_entered vs choice_committed).                        #
# --------------------------------------------------------------------------- #
FRAME_W, FRAME_H = 1000, 1000


def _rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def make_zone_map():
    return ZoneMap(
        zones=(
            Zone(HUB,          _rect(0.35, 0.80, 0.65, 1.00)),
            Zone(CENTRAL_STEM, _rect(0.40, 0.50, 0.60, 0.80)),
            Zone(JUNCTION,     _rect(0.40, 0.34, 0.60, 0.50)),
            Zone(LEFT_CHOICE,  _rect(0.00, 0.20, 0.40, 0.50)),
            Zone(RIGHT_CHOICE, _rect(0.60, 0.20, 1.00, 0.50)),
            Zone(LEFT_RETURN,  _rect(0.00, 0.50, 0.20, 0.80)),
            Zone(RIGHT_RETURN, _rect(0.80, 0.50, 1.00, 0.80)),
        ),
        well=_rect(0.40, 0.88, 0.60, 1.00),
        stem_base=(0.50, 0.85), stem_tip=(0.50, 0.42),
    )


P_HUB = (500, 900)
P_STEM = (500, 650)
P_JUNCTION = (500, 420)
P_LCHOICE = (200, 350)
P_RCHOICE = (800, 350)
P_LRETURN = (100, 650)
P_RRETURN = (900, 650)

_ARM_PT = {LEFT: P_LCHOICE, RIGHT: P_RCHOICE}
_RET_PT = {LEFT: P_LRETURN, RIGHT: P_RRETURN}


def _tr(cx, cy, conf=0.9, state="TRACKING"):
    return types.SimpleNamespace(
        detected=True, state=state, confidence=conf,
        frame_size=(FRAME_W, FRAME_H), centroid=(cx, cy), smoothed=(cx, cy))


class IntegrationHarness:
    """Drives centroids through the estimator + task-event layer + Phase3Task."""

    def __init__(self, reward_incorrect=False, seed=42):
        self.est = MazeStateEstimator(make_zone_map(),
                                      MazeStateParams(min_dwell_ticks=1))
        self.te = TaskEvents()
        ts = TrialSequence(TrialConfig(max_trials=20, block_size=20,
                                       max_same_run=3), seed=seed)
        self.task = Phase3Task(ts, Phase3Config(reward_incorrect=reward_incorrect))
        self.t = 0.0

    def step(self, pt):
        s = self.est.update(_tr(*pt), self.t)
        f = self.te.update(s)
        d = self.task.update(f)
        self.t += 0.1
        return d

    def run(self, pts):
        last = None
        for pt in pts:
            last = self.step(pt)
        return last

    def out_and_choose(self, arm):
        """HUB -> stem -> junction -> the given choice arm (returns the arm-entry
        decision, i.e. where a stem-gated choice is scored)."""
        return self.run([P_HUB, P_STEM, P_JUNCTION, _ARM_PT[arm]])

    def jump_and_choose(self, arm, via=LEFT):
        """The divider-jump route (docs/decisions/0099): HUB -> a return arm ->
        (hop over the divider into) stem -> junction -> the given choice arm. The
        stem IS visited (outbound_used_stem becomes true) but the trip never
        DEPARTED the hub up the stem (left_hub_via_stem stays false)."""
        return self.run([P_HUB, _RET_PT[via], P_STEM, P_JUNCTION, _ARM_PT[arm]])

    def return_home(self, arm):
        """Choice arm -> matching return arm -> HUB (closes the lap)."""
        return self.run([_RET_PT[arm], P_HUB])


class TestPhase3Integration(unittest.TestCase):
    def test_correct_choice_scores_rewards_then_advances(self):
        h = IntegrationHarness()
        correct = h.task.current_correct_arm()
        d = h.out_and_choose(correct)
        self.assertEqual(d.outcome, CORRECT)
        self.assertTrue(d.reward)
        d2 = h.return_home(correct)
        self.assertTrue(d2.advanced)
        self.assertEqual(h.task.index, 1)

    def test_incorrect_choice_no_reward_by_default(self):
        h = IntegrationHarness(reward_incorrect=False)
        correct = h.task.current_correct_arm()
        wrong = RIGHT if correct == LEFT else LEFT
        d = h.out_and_choose(wrong)
        self.assertEqual(d.outcome, INCORRECT)
        self.assertFalse(d.reward)

    def test_figure8_second_arm_does_not_rescore(self):
        h = IntegrationHarness()
        correct = h.task.current_correct_arm()
        wrong = RIGHT if correct == LEFT else LEFT
        h.out_and_choose(correct)                    # scored once
        # Turn back and enter the OTHER arm within the SAME trip: no re-score.
        d = h.run([P_JUNCTION, _ARM_PT[wrong]])
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(h.task.index, 0)

    def test_nonstem_choice_is_not_scored(self):
        # Reach a choice arm via the return arm (no stem): not a valid cued trial.
        h = IntegrationHarness()
        d = h.run([P_HUB, P_LRETURN, P_LCHOICE])
        self.assertIsNone(d.outcome)
        self.assertEqual(h.task.index, 0)

    def test_divider_jump_choice_is_not_scored_then_real_trip_scores(self):
        # docs/decisions/0099: the rat sets off up a return arm, hops over the
        # divider into the stem, then makes the "correct" choice. The stem is
        # visited (outbound_used_stem true) but the trip never departed the hub up
        # the stem, so it is not a genuine trial: no score, no reward, and the
        # trial is preserved. A subsequent genuine hub->stem trip scores that same
        # trial.
        h = IntegrationHarness()
        correct = h.task.current_correct_arm()
        d = h.jump_and_choose(correct)
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(h.task.index, 0)
        # Back home, then a proper stem departure scores the preserved trial 0.
        h.return_home(correct)
        d = h.out_and_choose(correct)
        self.assertEqual(d.outcome, CORRECT)
        self.assertTrue(d.reward)
        self.assertEqual(d.trial_index, 0)

    def test_runs_to_completion(self):
        h = IntegrationHarness()
        n = len(h.task.seq)
        for _ in range(n):
            correct = h.task.current_correct_arm()
            h.out_and_choose(correct)
            h.return_home(correct)
        self.assertTrue(h.task.complete)

    def test_nonstem_first_entry_preserves_the_trial(self):
        # Option A edge case (docs/decisions/0055, FW-0020): if the FIRST choice
        # entry of a trip is non-stem, the trip's single commit is spent (no
        # score), a later stem entry the SAME trip is ignored, but the trial index
        # does NOT advance - so the sequence trial is preserved and scored on the
        # next genuine stem trip (it is not burnt).
        h = IntegrationHarness()
        correct = h.task.current_correct_arm()
        # Trip 1: reach a choice arm via the return arm (no stem) -> commit, no score.
        d = h.run([P_HUB, P_LRETURN, P_LCHOICE])
        self.assertIsNone(d.outcome)
        self.assertEqual(h.task.index, 0)
        # Same trip: reach a choice arm via the stem -> already committed, ignored.
        d = h.run([P_JUNCTION, P_STEM, P_JUNCTION, _ARM_PT[correct]])
        self.assertIsNone(d.outcome)
        self.assertEqual(h.task.index, 0)
        # Return home: no score is awaiting, so the trial does NOT advance.
        h.return_home(correct)
        self.assertEqual(h.task.index, 0)
        # A fresh trip's stem choice now scores that SAME preserved trial 0.
        d = h.out_and_choose(correct)
        self.assertEqual(d.outcome, CORRECT)
        self.assertEqual(d.trial_index, 0)


if __name__ == "__main__":
    unittest.main()
