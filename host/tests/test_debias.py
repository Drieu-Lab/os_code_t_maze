"""
test_debias.py - headless tests for the closed-loop debias core + debias_blocks
trial-sequence mode (see debias.py, trial_sequence.py, and the ADR).

Covers the DebiasBlockController state machine (bias measure, auto trigger, the
3-consecutive-correct success exit, the max_block_len cap, window reset, manual
override) and the stateful "debias_blocks" TrialSequence driven by simulated
agents (a stubborn left-biased agent triggers/repeats capped blocks forcing the
non-preferred side; a cue-following agent never triggers, so its schedule equals
the counterbalanced spine; determinism given seed + choices). Pure stdlib.

Run from host/:  python -m unittest test_debias
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3.debias import (
    AUTO, INTERVENTION, MANUAL, NORMAL, DebiasBlockConfig, DebiasBlockController,
)
from tmaze.core.phase3.trial_sequence import (
    LEFT, RIGHT, GEN_DEBIAS_BLOCKS, TrialConfig, TrialSequence,
)


class TestBiasMeasure(unittest.TestCase):
    def test_left_bias_is_positive(self):
        c = DebiasBlockController(DebiasBlockConfig(window=10))
        # Cue RIGHT but chose LEFT four times => left pull; some correct trials too.
        for _ in range(4):
            c.record(RIGHT, LEFT)
        for _ in range(4):
            c.record(LEFT, LEFT)     # correct, no directional error
        self.assertGreater(c.bias(), 0.0)

    def test_empty_is_zero(self):
        self.assertEqual(DebiasBlockController().bias(), 0.0)


class TestAutoTrigger(unittest.TestCase):
    def _ctl(self):
        return DebiasBlockController(DebiasBlockConfig(
            window=10, min_samples=4, trigger_bias=0.3,
            exit_consecutive=3, max_block_len=4))

    def test_no_trigger_before_min_samples(self):
        c = self._ctl()
        c.record(RIGHT, LEFT)                      # strong bias, but only 1 sample
        d = c.decide()
        self.assertEqual(d.state, NORMAL)

    def test_triggers_on_left_bias_and_forces_right(self):
        c = self._ctl()
        for _ in range(4):
            c.record(RIGHT, LEFT)                  # 4 left-pull errors, b = 1.0
        d = c.decide()
        self.assertEqual(d.state, INTERVENTION)
        self.assertEqual(d.side, RIGHT)            # correct the over-pulled side
        self.assertEqual(d.trigger, AUTO)
        self.assertEqual(d.block_id, 1)

    def test_success_exit_after_consecutive_correct(self):
        c = self._ctl()
        for _ in range(4):
            c.record(RIGHT, LEFT)
        self.assertEqual(c.decide().state, INTERVENTION)      # block trial 1
        # Animal now complies: choose RIGHT (== forced correct) three times.
        for _ in range(3):
            c.record(RIGHT, RIGHT)
            d = c.decide()
        self.assertEqual(d.state, NORMAL)          # exited after 3 consecutive
        self.assertEqual(c.bias(), 0.0)            # window cleared on exit

    def test_cap_exit_when_never_complies(self):
        c = self._ctl()                            # max_block_len = 4
        for _ in range(4):
            c.record(RIGHT, LEFT)
        states = []
        for _ in range(6):                         # keep choosing LEFT (wrong)
            d = c.decide()
            states.append(d.state)
            c.record(d.side if d.side is not None else LEFT, LEFT)
        # Exactly max_block_len block trials, then it reverts to NORMAL.
        self.assertEqual(states[:4], [INTERVENTION] * 4)
        self.assertEqual(states[4], NORMAL)

    def test_no_trigger_when_unbiased(self):
        c = self._ctl()
        for _ in range(10):
            c.record(LEFT, LEFT)                   # all correct, no bias
            c.record(RIGHT, RIGHT)
        for _ in range(5):
            self.assertEqual(c.decide().state, NORMAL)


class TestManualOverride(unittest.TestCase):
    def test_force_and_end_block(self):
        c = DebiasBlockController(DebiasBlockConfig(min_samples=100))  # never auto
        c.force_block(RIGHT)
        d = c.decide()
        self.assertEqual(d.state, INTERVENTION)
        self.assertEqual(d.side, RIGHT)
        self.assertEqual(d.trigger, MANUAL)
        c.end_block()
        self.assertEqual(c.decide().state, NORMAL)

    def test_force_infers_side_from_bias(self):
        c = DebiasBlockController(DebiasBlockConfig(min_samples=100, window=10))
        for _ in range(3):
            c.record(RIGHT, LEFT)                  # left-biased
        c.force_block()                            # no side given -> infer RIGHT
        self.assertEqual(c.decide().side, RIGHT)


# --------------------------------------------------------------------------- #
# TrialSequence in debias_blocks mode, driven like Phase3Task would.           #
# --------------------------------------------------------------------------- #
def _cfg(**kw):
    base = dict(max_trials=80, block_size=20, max_same_run=4,
                generator=GEN_DEBIAS_BLOCKS,
                debias=DebiasBlockConfig(window=10, min_samples=6,
                                         trigger_bias=0.3, exit_consecutive=3,
                                         max_block_len=4))
    base.update(kw)
    return TrialConfig(**base)


def _drive(ts, choose, n):
    """Commit + score n trials, choose(correct)->chosen. Returns (sides, states)."""
    sides, states = [], []
    for i in range(n):
        correct = ts.commit(i)
        sides.append(correct)
        states.append(ts.trial_meta(i)["sched_state"])
        ts.record_outcome(i, correct, choose(correct))
    return sides, states


class TestDebiasSequence(unittest.TestCase):
    def test_left_biased_agent_triggers_capped_blocks_forcing_right(self):
        ts = TrialSequence(_cfg(), seed=42)
        sides, states = _drive(ts, lambda _correct: LEFT, 60)
        self.assertIn(INTERVENTION, states)                 # a block happened
        # Every in-block trial forces the non-preferred (RIGHT) side.
        for side, state in zip(sides, states):
            if state == INTERVENTION:
                self.assertEqual(side, RIGHT)
        # Blocks recur for a stubborn animal (more than one block id seen).
        block_ids = {ts.trial_meta(i)["block_id"]
                     for i in range(60) if states[i] == INTERVENTION}
        self.assertTrue(max(block_ids) >= 2)

    def test_cue_following_agent_never_triggers_and_matches_spine(self):
        ts = TrialSequence(_cfg(), seed=7)
        sides, states = _drive(ts, lambda correct: correct, 60)
        self.assertTrue(all(s == NORMAL for s in states))
        # With no blocks, NORMAL trials consume the spine in order.
        self.assertEqual(sides, ts.seq[:60])

    def test_deterministic_given_seed_and_choices(self):
        a = TrialSequence(_cfg(), seed=99)
        b = TrialSequence(_cfg(), seed=99)
        sa, _ = _drive(a, lambda _c: LEFT, 60)
        sb, _ = _drive(b, lambda _c: LEFT, 60)
        self.assertEqual(sa, sb)

    def test_balanced_generator_unchanged(self):
        # The default generator is untouched: correct_arm == the spine, no meta.
        tc = TrialConfig(max_trials=40, block_size=20, max_same_run=3)
        ts = TrialSequence(tc, seed=5)
        self.assertFalse(ts.is_debias)
        self.assertEqual([ts.correct_arm(i) for i in range(40)], ts.seq)
        self.assertEqual(ts.trial_meta(0),
                         {"sched_state": NORMAL, "block_id": 0,
                          "block_trigger": "", "bias": 0.0})


if __name__ == "__main__":
    unittest.main()
