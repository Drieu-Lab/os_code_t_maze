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

from phase3_task import CORRECT, INCORRECT, Phase3Config, Phase3Task
from trial_sequence import LEFT, TrialConfig, TrialSequence


def frame(valid=True, choice_entered=None, outbound_used_stem=False,
          loop_closed=None):
    """A minimal TaskEventFrame stand-in (only the fields Phase3Task reads)."""
    return types.SimpleNamespace(
        valid=valid, choice_entered=choice_entered,
        outbound_used_stem=outbound_used_stem, loop_closed=loop_closed)


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
        d = t.update(frame(choice_entered=side_of(correct), outbound_used_stem=True))
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
        d = t.update(frame(choice_entered=side_of(wrong), outbound_used_stem=True))
        self.assertEqual(d.outcome, INCORRECT)
        self.assertFalse(d.reward)

    def test_incorrect_choice_rewarded_when_shaping(self):
        t = make_task(reward_incorrect=True)
        correct = t.current_correct_arm()
        wrong = 1 - correct
        d = t.update(frame(choice_entered=side_of(wrong), outbound_used_stem=True))
        self.assertEqual(d.outcome, INCORRECT)
        self.assertTrue(d.reward)

    def test_choice_without_stem_is_not_scored(self):
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(choice_entered=side_of(correct), outbound_used_stem=False))
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(t.index, 0)

    def test_invalid_frame_is_inert(self):
        t = make_task()
        correct = t.current_correct_arm()
        d = t.update(frame(valid=False, choice_entered=side_of(correct),
                           outbound_used_stem=True))
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(t.index, 0)

    def test_single_score_per_trial(self):
        t = make_task()
        correct = t.current_correct_arm()
        t.update(frame(choice_entered=side_of(correct), outbound_used_stem=True))
        # a second choice-arm entry before returning must NOT re-score
        d = t.update(frame(choice_entered=side_of(correct), outbound_used_stem=True))
        self.assertIsNone(d.outcome)
        self.assertFalse(d.reward)
        self.assertEqual(t.index, 0)

    def test_runs_to_completion(self):
        t = make_task()
        n = len(t.seq)
        for _ in range(n):
            correct = t.current_correct_arm()
            t.update(frame(choice_entered=side_of(correct), outbound_used_stem=True))
            t.update(frame(loop_closed=side_of(correct)))
        self.assertTrue(t.complete)
        # once complete, further frames are inert
        d = t.update(frame(choice_entered="left", outbound_used_stem=True))
        self.assertTrue(d.complete)
        self.assertIsNone(d.outcome)
        self.assertIsNone(t.current_correct_arm())


if __name__ == "__main__":
    unittest.main()
