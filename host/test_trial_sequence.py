"""
test_trial_sequence.py - headless tests for the host trial-sequence core.

Mirrors the firmware's ``test_task_core.cpp`` sequence/cue asserts so the Python
port stays faithful (see docs/decisions/0046): the 32-bit LCG, per-block L/R
balance, the ``MAX_SAME_RUN`` anti-bias cap, seed-determinism, and the
``cue_for`` identity mapping. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_trial_sequence
"""

from __future__ import annotations

import unittest

from trial_sequence import (
    LEFT, RIGHT, TrialConfig, TrialSequence, cue_for, generate_sequence, lcg,
    max_run_ok,
)


class TestLcg(unittest.TestCase):
    def test_known_vector(self):
        # Anchors the arithmetic to the firmware's uint32_t s*1664525+1013904223.
        self.assertEqual(lcg(12345), 87628868)

    def test_stays_in_u32(self):
        s = 0xFFFFFFFF
        for _ in range(1000):
            s = lcg(s)
            self.assertTrue(0 <= s <= 0xFFFFFFFF)


class TestCueFor(unittest.TestCase):
    def test_mapping(self):
        c = TrialConfig(high_tone_is_left=True, high_tone_hz=8000, low_tone_hz=4000)
        self.assertEqual(cue_for(LEFT, c), 8000)    # correct=LEFT  -> high
        self.assertEqual(cue_for(RIGHT, c), 4000)   # correct=RIGHT -> low
        c = TrialConfig(high_tone_is_left=False, high_tone_hz=8000, low_tone_hz=4000)
        self.assertEqual(cue_for(LEFT, c), 4000)    # correct=LEFT  -> low
        self.assertEqual(cue_for(RIGHT, c), 8000)   # correct=RIGHT -> high


class TestMaxRunOk(unittest.TestCase):
    def test_basic(self):
        self.assertTrue(max_run_ok([0, 0, 1, 1, 0], 5, 3))
        self.assertFalse(max_run_ok([0, 0, 0, 0], 4, 3))
        self.assertTrue(max_run_ok([0, 0, 0, 0], 4, 0))   # 0 disables
        self.assertTrue(max_run_ok([1], 1, 3))            # n < 2


class TestGenerateSequence(unittest.TestCase):
    def test_block_balanced(self):
        c = TrialConfig(max_trials=20, block_size=20, max_same_run=3)
        seq, _ = generate_sequence(c, 12345)
        self.assertEqual(len(seq), 20)
        self.assertTrue(all(v in (LEFT, RIGHT) for v in seq))
        self.assertEqual(seq.count(LEFT), 10)
        self.assertEqual(seq.count(RIGHT), 10)

    def test_anti_bias_and_balance_over_blocks(self):
        c = TrialConfig(max_trials=200, block_size=20, max_same_run=3)
        seq, r1 = generate_sequence(c, 24680)
        # never more than 3 consecutive same-side trials
        self.assertTrue(max_run_ok(seq, 200, 3))
        # exact balance within each block
        for off in range(0, 200, 20):
            block = seq[off:off + 20]
            self.assertEqual(block.count(LEFT), 10)
            self.assertEqual(block.count(RIGHT), 10)
        # deterministic from the seed (sequence AND advanced rng)
        seq2, r2 = generate_sequence(c, 24680)
        self.assertEqual(seq, seq2)
        self.assertEqual(r1, r2)

    def test_different_seeds_differ(self):
        c = TrialConfig(max_trials=200, block_size=20, max_same_run=3)
        seq_a, _ = generate_sequence(c, 1)
        seq_b, _ = generate_sequence(c, 2)
        self.assertNotEqual(seq_a, seq_b)


class TestTrialSequence(unittest.TestCase):
    def test_lookups(self):
        c = TrialConfig(max_trials=20, block_size=20, max_same_run=3,
                        high_tone_is_left=True)
        ts = TrialSequence(c, seed=99)
        self.assertEqual(len(ts), 20)
        self.assertIsNone(ts.correct_arm(-1))
        self.assertIsNone(ts.correct_arm(20))
        self.assertIsNone(ts.choice_freq(20))
        for i in range(20):
            arm = ts.correct_arm(i)
            self.assertIn(arm, (LEFT, RIGHT))
            self.assertEqual(ts.choice_freq(i), cue_for(arm, c))

    def test_return_freq_is_hi_or_lo_and_deterministic(self):
        c = TrialConfig(high_tone_hz=8000, low_tone_hz=4000)
        a = TrialSequence(c, seed=777)
        b = TrialSequence(c, seed=777)
        seq_a = [a.next_return_freq() for _ in range(50)]
        seq_b = [b.next_return_freq() for _ in range(50)]
        self.assertEqual(seq_a, seq_b)                       # deterministic
        self.assertTrue(all(f in (8000, 4000) for f in seq_a))
        self.assertIn(8000, seq_a)                           # both values appear
        self.assertIn(4000, seq_a)


if __name__ == "__main__":
    unittest.main()
