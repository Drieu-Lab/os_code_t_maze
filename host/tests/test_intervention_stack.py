"""
test_intervention_stack.py - headless tests for the intervention stack that
composes the side-bias (debias) and de-alternation blocks (see
intervention_stack.py, debias.py, alternation.py, and the ADR).

Covers: only one block active at a time with side-bias priority when both arm
together; record() fanned out to every enabled child; per-kind force/end routing
with a stack-owned monotonic block id; and the inert (balanced) stack when neither
child is enabled. Pure stdlib.

Run from host/:  python -m unittest test_intervention_stack
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3.alternation import AlternationBlockConfig
from tmaze.core.phase3.debias import (
    INTERVENTION, NORMAL, DebiasBlockConfig,
)
from tmaze.core.phase3.intervention_stack import (
    KIND_ALTERNATION, KIND_SIDE_BIAS, InterventionStackController,
)
from tmaze.core.phase3.trial_sequence import LEFT, RIGHT


def _both(**kw):
    db = DebiasBlockConfig(window=10, min_errors=3, trigger_bias=0.3,
                           exit_consecutive=3, max_block_len=5)
    al = AlternationBlockConfig(window=10, min_samples=3, trigger_rate=0.8,
                                exit_rate=0.5, exit_min_samples=3, max_block_len=5)
    return InterventionStackController(db, al)


def _arm_both(s):
    """Feed a stream (cue always RIGHT, choices alternate L,R,...) that arms BOTH
    detectors: every (RIGHT, LEFT) is a debias left-pull error, and the repeat-cued
    switches drive the alternation rate to 1.0."""
    for ch in [LEFT, RIGHT, LEFT, RIGHT, LEFT, RIGHT, LEFT, RIGHT]:
        s.record(RIGHT, ch)


class TestArbitration(unittest.TestCase):
    def test_side_bias_wins_when_both_arm(self):
        s = _both()
        _arm_both(s)
        d = s.decide()
        self.assertEqual(d.state, INTERVENTION)
        self.assertEqual(d.kind, KIND_SIDE_BIAS)             # priority: side-bias
        self.assertEqual(s.active_kind, KIND_SIDE_BIAS)
        self.assertEqual(d.side, RIGHT)                      # corrects a left bias
        self.assertNotEqual(d.bias, 0.0)                     # both signals carried
        self.assertNotEqual(d.alt_rate, 0.0)

    def test_only_one_active_at_a_time(self):
        s = _both()
        _arm_both(s)
        s.decide()                                           # side-bias block opens
        # While it is active, further decisions stay in the same block/kind - the
        # alternation child never opens a concurrent block.
        for _ in range(3):
            d = s.decide()
            self.assertEqual(d.kind, KIND_SIDE_BIAS)
        self.assertEqual(s.active_kind, KIND_SIDE_BIAS)

    def test_record_fans_out_to_both_children(self):
        s = _both()
        _arm_both(s)
        self.assertNotEqual(s.bias(), 0.0)                   # side-bias child saw it
        self.assertNotEqual(s.alt_rate(), 0.0)               # alternation child too

    def test_window_gate_counts_delegate_to_children(self):
        s = _both()
        _arm_both(s)
        # Both detectors' gate counts are surfaced from their children (the stream
        # arms both), for the readout's "errors N/min" / "samples N/min" progress.
        self.assertGreater(s.window_errors(), 0)
        self.assertGreater(s.window_samples(), 0)

    def test_window_gate_counts_zero_when_child_disabled(self):
        # Alternation only: no side-bias child, so its window_errors() is 0.
        s = InterventionStackController(
            None, AlternationBlockConfig(window=10, min_samples=3))
        s.record(LEFT, LEFT)
        self.assertEqual(s.window_errors(), 0)
        self.assertEqual(InterventionStackController(None, None).window_samples(), 0)


class TestAlternationOnly(unittest.TestCase):
    def test_alternation_child_triggers_when_debias_disabled(self):
        s = InterventionStackController(
            None, AlternationBlockConfig(window=10, min_samples=3,
                                         trigger_rate=0.8, exit_rate=0.5,
                                         exit_min_samples=3, max_block_len=5))
        chosen = LEFT
        s.record(LEFT, chosen)
        for _ in range(4):
            cued = chosen
            chosen = RIGHT if chosen == LEFT else LEFT
            s.record(cued, chosen)
        d = s.decide()
        self.assertEqual(d.state, INTERVENTION)
        self.assertEqual(d.kind, KIND_ALTERNATION)
        self.assertEqual(s.bias(), 0.0)                      # no side-bias child


class TestForceRouting(unittest.TestCase):
    def _never_auto(self):
        return InterventionStackController(
            DebiasBlockConfig(min_errors=100),
            AlternationBlockConfig(min_samples=100))

    def test_force_alternation_then_side_bias_ids_are_monotonic(self):
        s = self._never_auto()
        s.record(LEFT, LEFT)                                 # a last choice to repeat
        s.force_block(kind=KIND_ALTERNATION)
        d = s.decide()
        self.assertEqual(d.kind, KIND_ALTERNATION)
        self.assertEqual(d.side, LEFT)
        self.assertEqual(d.block_id, 1)
        s.end_block()                                        # ends the active block
        self.assertEqual(s.decide().state, NORMAL)
        s.force_block(RIGHT, kind=KIND_SIDE_BIAS)
        d = s.decide()
        self.assertEqual(d.kind, KIND_SIDE_BIAS)
        self.assertEqual(d.side, RIGHT)
        self.assertEqual(d.block_id, 2)                      # stack-owned, monotonic

    def test_default_force_is_side_bias(self):
        s = self._never_auto()
        s.force_block(RIGHT)                                 # no kind -> side-bias
        d = s.decide()
        self.assertEqual(d.kind, KIND_SIDE_BIAS)


class TestInert(unittest.TestCase):
    def test_neither_child_is_balanced(self):
        s = InterventionStackController(None, None)
        s.record(RIGHT, LEFT)                                # no-op, no children
        for _ in range(5):
            d = s.decide()
            self.assertEqual(d.state, NORMAL)
            self.assertIsNone(d.side)
        self.assertEqual(s.bias(), 0.0)
        self.assertEqual(s.alt_rate(), 0.0)
        self.assertEqual(s.active_kind, "")


if __name__ == "__main__":
    unittest.main()
