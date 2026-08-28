"""
test_trial_sequence.py - headless tests for the host trial-sequence core.

Covers the schedule invariants that must hold regardless of the RNG: per-block
L/R balance, the ``MAX_SAME_RUN`` / alternation anti-bias caps, seed-determinism
(now off stdlib ``random.Random``; see docs/decisions/0140), and the ``cue_for``
identity mapping. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_trial_sequence
"""

from __future__ import annotations

import random
import unittest

from tmaze.core.phase3.trial_sequence import (
    HIGH, LEFT, LOW, RIGHT, TrialConfig, TrialSequence, alt_run_ok, cue_for,
    generate_balanced, generate_sequence, max_run_ok,
)


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


class TestAltRunOk(unittest.TestCase):
    def test_basic(self):
        # counting is by trial: L R L R is a run of 4 alternating trials.
        self.assertTrue(alt_run_ok([0, 1, 0, 1], 4, 4))      # 4 allowed at cap 4
        self.assertFalse(alt_run_ok([0, 1, 0, 1, 0], 5, 4))  # 5th extends -> reject
        self.assertTrue(alt_run_ok([0, 0, 1, 1, 0], 5, 3))   # non-alternating is fine
        self.assertTrue(alt_run_ok([0, 1, 0, 1, 0], 5, 0))   # 0 disables
        self.assertTrue(alt_run_ok([1], 1, 3))               # n < 2

    def test_run_resets_after_repeat(self):
        # A repeated pair resets the alternating run, so L R L R | L R L R passes
        # at cap 4 even though there are 8 alternating-ish trials total.
        self.assertTrue(alt_run_ok([0, 1, 0, 1, 1, 0, 1, 0], 8, 4))


class TestGenerateSequence(unittest.TestCase):
    def test_block_balanced(self):
        c = TrialConfig(max_trials=20, block_size=20, max_same_run=3)
        seq = generate_sequence(c, random.Random(12345))
        self.assertEqual(len(seq), 20)
        self.assertTrue(all(v in (LEFT, RIGHT) for v in seq))
        self.assertEqual(seq.count(LEFT), 10)
        self.assertEqual(seq.count(RIGHT), 10)

    def test_anti_bias_and_balance_over_blocks(self):
        c = TrialConfig(max_trials=200, block_size=20, max_same_run=3)
        seq = generate_sequence(c, random.Random(24680))
        # never more than 3 consecutive same-side trials
        self.assertTrue(max_run_ok(seq, 200, 3))
        # exact balance within each block
        for off in range(0, 200, 20):
            block = seq[off:off + 20]
            self.assertEqual(block.count(LEFT), 10)
            self.assertEqual(block.count(RIGHT), 10)
        # deterministic from the seed: a fresh generator on the same seed matches
        seq2 = generate_sequence(c, random.Random(24680))
        self.assertEqual(seq, seq2)

    def test_different_seeds_differ(self):
        c = TrialConfig(max_trials=200, block_size=20, max_same_run=3)
        seq_a = generate_sequence(c, random.Random(1))
        seq_b = generate_sequence(c, random.Random(2))
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
            # choice_cue is the HIGH/LOW identity consistent with choice_freq.
            self.assertEqual(ts.choice_cue(i),
                             HIGH if ts.choice_freq(i) == c.high_tone_hz else LOW)

    def test_choice_cue_out_of_range(self):
        ts = TrialSequence(TrialConfig(max_trials=20, block_size=20), seed=1)
        self.assertIsNone(ts.choice_cue(-1))
        self.assertIsNone(ts.choice_cue(20))


class TestGenerateBalanced(unittest.TestCase):
    def test_balanced_binary_and_run_cap(self):
        seq = generate_balanced(200, 20, 3, random.Random(12345))
        self.assertEqual(len(seq), 200)
        self.assertTrue(all(v in (0, 1) for v in seq))
        self.assertTrue(max_run_ok(seq, 200, 3))
        for off in range(0, 200, 20):
            self.assertEqual(seq[off:off + 20].count(0), 10)  # exact per-block balance

    def test_choice_is_balanced_binary(self):
        # generate_sequence is just the choice-flavored generate_balanced: fed the
        # same fresh generator + params, they draw identically.
        c = TrialConfig(max_trials=40, block_size=20, max_same_run=3)
        self.assertEqual(generate_sequence(c, random.Random(999)),
                         generate_balanced(40, 20, 3, random.Random(999)))

    def test_alt_run_cap_enforced_and_balance_kept(self):
        # With an alternation cap set, no block alternates for more than the cap,
        # yet every block keeps its exact per-block balance.
        seq = generate_balanced(200, 20, 4, random.Random(12345), max_alt=4)
        self.assertTrue(alt_run_ok(seq, 200, 4))
        self.assertTrue(max_run_ok(seq, 200, 4))
        for off in range(0, 200, 20):
            self.assertEqual(seq[off:off + 20].count(0), 10)

    def test_alt_default_disabled_is_backcompat(self):
        # Default max_alt (0) must leave the schedule identical to a call that omits
        # the arg (same seed), so existing seeds/sessions are unaffected.
        self.assertEqual(generate_balanced(200, 20, 3, random.Random(555)),
                         generate_balanced(200, 20, 3, random.Random(555), max_alt=0))


class TestAltRunSequence(unittest.TestCase):
    def test_generate_sequence_respects_alt_cap(self):
        c = TrialConfig(max_trials=200, block_size=20, max_same_run=4,
                        max_alt_run=4)
        seq = generate_sequence(c, random.Random(24680))
        self.assertTrue(alt_run_ok(seq, 200, 4))
        self.assertTrue(max_run_ok(seq, 200, 4))

    def test_return_schedules_respect_alt_cap(self):
        c = TrialConfig(max_trials=200, block_size=20, max_same_run=4,
                        max_alt_run=4)
        ts = TrialSequence(c, seed=777)
        for side in (LEFT, RIGHT):
            bits = [1 if ts.return_cue(side, i) == HIGH else 0
                    for i in range(len(ts))]
            self.assertTrue(alt_run_ok(bits, len(bits), 4))


class TestReturnSchedules(unittest.TestCase):
    def _cfg(self):
        return TrialConfig(max_trials=200, block_size=20, max_same_run=3,
                           high_tone_hz=8000, low_tone_hz=4000)

    def test_balanced_run_capped_and_freq_maps(self):
        c = self._cfg()
        ts = TrialSequence(c, seed=777)
        for side in (LEFT, RIGHT):
            cues = [ts.return_cue(side, i) for i in range(len(ts))]
            self.assertTrue(all(v in (HIGH, LOW) for v in cues))
            self.assertEqual(cues.count(HIGH), 100)           # overall balance
            self.assertEqual(cues.count(LOW), 100)
            # per-block balance + anti-bias run cap (same engine as the choice seq)
            bits = [1 if v == HIGH else 0 for v in cues]
            self.assertTrue(max_run_ok(bits, len(bits), 3))
            for off in range(0, 200, 20):
                self.assertEqual(bits[off:off + 20].count(1), 10)
            # return_freq agrees with return_cue
            for i in range(len(ts)):
                self.assertEqual(
                    ts.return_freq(side, i),
                    c.high_tone_hz if cues[i] == HIGH else c.low_tone_hz)

    def test_deterministic_from_seed(self):
        c = self._cfg()
        a, b = TrialSequence(c, seed=42), TrialSequence(c, seed=42)
        for side in (LEFT, RIGHT):
            self.assertEqual([a.return_cue(side, i) for i in range(len(a))],
                             [b.return_cue(side, i) for i in range(len(b))])

    def test_columns_are_independent(self):
        # choice, left-return and right-return are distinct shuffles (threaded off
        # the one seed), so they should not be identical sequences.
        c = self._cfg()
        ts = TrialSequence(c, seed=13)
        choice = [ts.choice_cue(i) for i in range(len(ts))]
        left = [ts.return_cue(LEFT, i) for i in range(len(ts))]
        right = [ts.return_cue(RIGHT, i) for i in range(len(ts))]
        self.assertNotEqual(left, right)
        self.assertNotEqual(choice, left)

    def test_out_of_range(self):
        ts = TrialSequence(self._cfg(), seed=1)
        self.assertIsNone(ts.return_cue(LEFT, -1))
        self.assertIsNone(ts.return_cue(RIGHT, 999))
        self.assertIsNone(ts.return_freq(LEFT, 999))


class TestExtend(unittest.TestCase):
    """Append-only, seed-deterministic extension (docs/decisions/0059)."""

    def _cfg(self, n=40):
        return TrialConfig(max_trials=n, block_size=20, max_same_run=3)

    def test_appends_whole_blocks_and_preserves_prefix(self):
        ts = TrialSequence(self._cfg(40), seed=555)
        before_choice = list(ts.seq)
        before_left = [ts.return_cue(LEFT, i) for i in range(len(ts))]
        before_right = [ts.return_cue(RIGHT, i) for i in range(len(ts))]
        self.assertEqual(ts.extend(20), 60)
        self.assertEqual(len(ts), 60)
        # existing rows are byte-identical (append-only)
        self.assertEqual(ts.seq[:40], before_choice)
        self.assertEqual([ts.return_cue(LEFT, i) for i in range(40)], before_left)
        self.assertEqual([ts.return_cue(RIGHT, i) for i in range(40)], before_right)
        # the appended block keeps exact per-block balance on all three schedules
        self.assertEqual(ts.seq[40:60].count(LEFT), 10)
        self.assertEqual(ts.seq[40:60].count(RIGHT), 10)
        for side in (LEFT, RIGHT):
            tail = [ts.return_cue(side, i) for i in range(40, 60)]
            self.assertEqual(tail.count(HIGH), 10)
            self.assertEqual(tail.count(LOW), 10)

    def test_rounds_up_to_whole_block(self):
        ts = TrialSequence(self._cfg(20), seed=1)
        self.assertEqual(ts.extend(5), 40)     # 5 rounds up to one 20-block
        self.assertEqual(ts.extend(21), 80)    # 21 rounds up to two 20-blocks

    def test_default_adds_one_block(self):
        ts = TrialSequence(self._cfg(20), seed=1)
        self.assertEqual(ts.extend(), 40)

    def test_deterministic_per_seed_and_call_pattern(self):
        # Same seed + same extend calls -> identical schedules (reproducible).
        a = TrialSequence(self._cfg(40), seed=99)
        b = TrialSequence(self._cfg(40), seed=99)
        a.extend(20)
        b.extend(20)
        self.assertEqual(a.seq, b.seq)
        for side in (LEFT, RIGHT):
            self.assertEqual([a.return_cue(side, i) for i in range(len(a))],
                             [b.return_cue(side, i) for i in range(len(b))])

    def test_noop_on_nonpositive(self):
        ts = TrialSequence(self._cfg(20), seed=1)
        self.assertEqual(ts.extend(0), 20)
        self.assertEqual(ts.extend(-5), 20)
        self.assertEqual(len(ts), 20)


if __name__ == "__main__":
    unittest.main()
