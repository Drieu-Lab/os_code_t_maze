"""
test_intervention_stack.py - headless tests for the intervention stack that
composes the side-bias (debias) and de-alternation blocks (see
intervention_stack.py, debias.py, alternation.py, and the ADR).

Covers: only one block active at a time with side-bias priority when both arm
together; record() fanned out to every enabled child; the switch-dominant veto on
the side-bias auto-trigger + its post-de-alt grace (ADR-0149); per-kind force/end
routing with a stack-owned monotonic block id; and the inert (balanced) stack when
neither child is enabled. Pure stdlib.

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
    # alt_veto_rate=0 turns the switch-dominant veto OFF so these arbitration
    # tests exercise the raw side-bias-first priority; the veto has its own class
    # (TestDebiasVetoWhenSwitchDominant).
    db = DebiasBlockConfig(window=10, min_errors=3, trigger_bias=0.3,
                           exit_consecutive=3, max_block_len=5, alt_veto_rate=0.0)
    al = AlternationBlockConfig(window=10, min_samples=3, trigger_rate=0.8,
                                exit_repeats_per_side=2, max_block_len=5)
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
                                         trigger_rate=0.8, exit_repeats_per_side=2,
                                         max_block_len=5))
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


class TestSideBiasFrozenDuringAlternation(unittest.TestCase):
    """While a de-alternation block owns the active block, the side-bias child is
    not fed (docs/decisions/0136): in that block the cued side is forced from the
    animal's own choices, so its directional-error bias is invalid. The window is
    frozen (not cleared) and resumes once the block exits."""

    def _stack(self):
        # High auto-trigger gates so neither child fires on its own - we drive the
        # alternation block by hand and watch what reaches the side-bias child.
        return InterventionStackController(
            DebiasBlockConfig(window=20, min_errors=100),
            AlternationBlockConfig(window=30, min_samples=100))

    def test_side_bias_window_frozen_then_resumes(self):
        s = self._stack()
        s.record(LEFT, LEFT)                       # NORMAL, correct -> no error
        s.force_block(kind=KIND_ALTERNATION)
        d = s.decide()
        self.assertEqual(d.kind, KIND_ALTERNATION)  # de-alt block now active
        self.assertEqual(s.window_errors(), 0)
        self.assertEqual(s.bias(), 0.0)

        # In-block trials that WOULD (under the old fan-out) look like left-pull
        # errors (cued RIGHT, chose LEFT) and drive bias to +1.0.
        for _ in range(3):
            s.record(RIGHT, LEFT)
        self.assertEqual(s.window_errors(), 0)      # frozen: nothing accumulated
        self.assertEqual(s.bias(), 0.0)

        # End the de-alt block; the side-bias detector resumes on the next trial.
        s.end_block()
        self.assertEqual(s.decide().state, NORMAL)
        s.record(RIGHT, LEFT)                       # now a real left-pull error
        self.assertEqual(s.window_errors(), 1)
        self.assertGreater(s.bias(), 0.0)

    def test_other_children_still_fed_during_alternation_block(self):
        # The freeze is scoped to the side-bias child only: the alternation child
        # keeps seeing every trial (its own detector must stay warm).
        s = self._stack()
        s.record(LEFT, LEFT)
        s.force_block(kind=KIND_ALTERNATION)
        s.decide()
        before = s.window_samples()
        for _ in range(4):                          # repeat-cued switches = samples
            s.record(LEFT, RIGHT)
            s.record(RIGHT, LEFT)
        self.assertGreater(s.window_samples(), before)


def _veto_stack(veto=0.5):
    """Both children with the switch-dominant veto ON (ADR-0149). Debias window is
    wide so refilling the alt window never ages the armed errors out."""
    db = DebiasBlockConfig(window=20, min_errors=3, trigger_bias=0.3,
                           exit_consecutive=3, max_block_len=5, alt_veto_rate=veto)
    al = AlternationBlockConfig(window=10, min_samples=3, trigger_rate=0.8,
                                exit_repeats_per_side=2, max_block_len=5)
    return InterventionStackController(db, al)


def _arm_bias_as_camper(s):
    """Arm the side-bias detector with a REPEAT-dominant rat: three left-pull errors
    (cued RIGHT, chose LEFT) then repeat-cued stays on RIGHT (alt_rate 0 with
    >= min_samples samples). Bias +1.0, errors 3; alt_rate 0.0, samples 3."""
    for _ in range(3):
        s.record(RIGHT, LEFT)                   # errors; not repeat-cued (prev LEFT)
    s.record(RIGHT, RIGHT)                      # correct; not a sample (prev LEFT)
    for _ in range(3):
        s.record(RIGHT, RIGHT)                  # repeat-cued stays -> samples, no switch


class TestDebiasVetoWhenSwitchDominant(unittest.TestCase):
    """A debias block forces a FIXED side - a reliable 50% oasis for an alternator.
    So the stack refuses to AUTO-start one while the rat reads switch-dominant
    (alt_rate >= alt_veto_rate with enough repeat-cued samples), staying NORMAL /
    letting de-alt fire on its own; a post-de-alt grace keeps the veto up while the
    cleared alt window refills; operator force bypasses it (ADR-0149)."""

    def test_switch_dominant_rat_is_not_given_a_debias_block(self):
        # Same stream that made side-bias win under raw priority: with the veto on,
        # the switch-dominant rat is routed to the de-alternation block instead.
        s = _veto_stack()
        _arm_both(s)                                # bias +1.0 AND alt_rate 1.0
        self.assertTrue(s.debias_vetoed)
        d = s.decide()
        self.assertEqual(d.state, INTERVENTION)
        self.assertEqual(d.kind, KIND_ALTERNATION)  # not KIND_SIDE_BIAS

    def test_between_veto_and_trigger_stays_normal(self):
        # alt_rate 0.6: above the veto (0.5) but below de-alt's trigger (0.8), with
        # the bias armed -> no block at all (NORMAL is no worse than the oasis).
        s = _veto_stack()
        s.record(RIGHT, RIGHT)
        s.record(RIGHT, LEFT)                       # sample, switch, left-pull error
        s.record(RIGHT, RIGHT)
        s.record(RIGHT, LEFT)                       # sample, switch, error
        s.record(RIGHT, RIGHT)
        s.record(RIGHT, RIGHT)                      # sample, stay
        s.record(RIGHT, RIGHT)                      # sample, stay
        s.record(RIGHT, LEFT)                       # sample, switch, error
        self.assertAlmostEqual(s.alt_rate(), 0.6)
        self.assertGreaterEqual(s.window_errors(), 3)
        self.assertGreaterEqual(abs(s.bias()), 0.3)  # debias WOULD fire...
        self.assertTrue(s.debias_vetoed)
        d = s.decide()
        self.assertEqual(d.state, NORMAL)             # ...but is vetoed

    def test_camper_still_gets_a_debias_block(self):
        # Low alt_rate with enough samples: the live read says "repeat-dominant",
        # so the veto is off and the established debias behaviour is unchanged.
        s = _veto_stack()
        _arm_bias_as_camper(s)
        self.assertGreaterEqual(s.window_samples(), 3)
        self.assertEqual(s.alt_rate(), 0.0)
        self.assertFalse(s.debias_vetoed)
        d = s.decide()
        self.assertEqual(d.kind, KIND_SIDE_BIAS)

    def test_manual_force_bypasses_veto(self):
        s = _veto_stack()
        _arm_both(s)
        self.assertTrue(s.debias_vetoed)
        s.force_block(RIGHT, kind=KIND_SIDE_BIAS)    # deliberate operator choice
        d = s.decide()
        self.assertEqual(d.kind, KIND_SIDE_BIAS)
        self.assertEqual(d.side, RIGHT)

    def test_too_few_samples_and_no_prior_block_is_not_vetoed(self):
        # Session start: bias armed but the alt window has no repeat-cued samples
        # yet and no block has run -> nothing to veto on, debias fires as before.
        s = _veto_stack()
        for _ in range(4):
            s.record(RIGHT, LEFT)                   # errors, never repeat-cued
        self.assertEqual(s.window_samples(), 0)
        self.assertFalse(s.debias_vetoed)
        self.assertEqual(s.decide().kind, KIND_SIDE_BIAS)

    def test_post_dealt_grace_holds_veto_while_alt_window_refills(self):
        # The handoff seam: a de-alt block clears its window on exit (0 samples)
        # while the ADR-0136-frozen bias is still over threshold. The grace keeps
        # the veto up until fresh evidence refills the window.
        s = _veto_stack()
        _arm_both(s)                                # both armed, switch-dominant
        self.assertEqual(s.decide().kind, KIND_ALTERNATION)
        s.end_block()                               # exit the de-alt block
        self.assertEqual(s.decide().state, NORMAL)
        self.assertEqual(s.window_samples(), 0)     # alt window cleared on exit
        self.assertGreaterEqual(s.window_errors(), 3)   # bias still armed (frozen)
        self.assertGreaterEqual(abs(s.bias()), 0.3)
        self.assertTrue(s.debias_vetoed)             # grace: last block was de-alt
        for _ in range(3):
            self.assertEqual(s.decide().state, NORMAL)   # no debias oasis
        # Fresh evidence: the rat now REPEATS when told to -> window refills with
        # alt_rate 0 -> veto lifts -> debias may fire on a real camper.
        for _ in range(3):
            s.record(RIGHT, RIGHT)                  # repeat-cued stays
        self.assertGreaterEqual(s.window_samples(), 3)
        self.assertFalse(s.debias_vetoed)
        self.assertEqual(s.decide().kind, KIND_SIDE_BIAS)

    def test_veto_off_restores_raw_priority(self):
        s = _veto_stack(veto=0.0)
        _arm_both(s)
        self.assertFalse(s.debias_vetoed)
        self.assertEqual(s.decide().kind, KIND_SIDE_BIAS)

    def test_no_alternation_child_means_no_veto(self):
        s = InterventionStackController(
            DebiasBlockConfig(window=10, min_errors=3, trigger_bias=0.3,
                              alt_veto_rate=0.5), None)
        for _ in range(4):
            s.record(RIGHT, LEFT)
        self.assertFalse(s.debias_vetoed)
        self.assertEqual(s.decide().kind, KIND_SIDE_BIAS)


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
