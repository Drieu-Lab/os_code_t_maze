"""
test_phase3_probe_blocks.py - headless tests for unrewarded probe blocks.

Covers the pure core added for the acquisition/expression dissociation
(docs/decisions/0115-probe): the manual ``ProbeBlockController`` state machine
(probe_blocks.py), its integration into ``TrialSequence`` (a probe inserts on top
of the counterbalanced spine WITHOUT consuming it, takes priority over the
intervention stack, and is flagged in ``trial_meta``), and the reward gate in
``Phase3Task`` (a probe trial is scored as usual but never pays out).

Pure stdlib; no hardware/Tk. Run from host/:
    python -m unittest test_phase3_probe_blocks
"""

from __future__ import annotations

import types
import unittest

from tmaze.core.phase3.probe_blocks import (
    ProbeBlockConfig, ProbeBlockController)
from tmaze.core.phase3.phase3_task import (
    CORRECT, INCORRECT, Phase3Config, Phase3Task)
from tmaze.core.phase3.trial_sequence import (
    LEFT, RIGHT, TrialConfig, TrialSequence)


def _run_one_block(ctrl, *, spine_pos=0, block_size=20, intervention=False):
    """Drive ``ctrl`` until it hands back one full probe block, collecting the
    forced sides. Assumes it is already PENDING and ``spine_pos`` is a boundary."""
    sides = []
    for _ in range(64):  # generous cap; a block is ``block_len`` trials
        dec = ctrl.decide(spine_pos=spine_pos, block_size=block_size,
                           intervention_active=intervention)
        if not dec.active:
            break
        sides.append(dec.side)
    return sides


class TestProbeBlockController(unittest.TestCase):
    def _ctrl(self, block_len=10, seed=1):
        return ProbeBlockController(
            ProbeBlockConfig(enabled=True, block_len=block_len), seed=seed)

    def test_idle_by_default(self):
        c = self._ctrl()
        self.assertFalse(c.active)
        self.assertFalse(c.pending)
        self.assertEqual(c.state(), "NORMAL")
        # No pending request -> never starts, even at a boundary.
        dec = c.decide(spine_pos=0, block_size=20, intervention_active=False)
        self.assertFalse(dec.active)

    def test_force_is_pending_until_boundary(self):
        c = self._ctrl()
        c.force_block()
        self.assertTrue(c.pending)
        self.assertEqual(c.state(), "PENDING")
        # Mid-block position (not a multiple of block_size): stays pending.
        dec = c.decide(spine_pos=7, block_size=20, intervention_active=False)
        self.assertFalse(dec.active)
        self.assertTrue(c.pending)
        # At the next boundary it starts.
        dec = c.decide(spine_pos=20, block_size=20, intervention_active=False)
        self.assertTrue(dec.active)
        self.assertEqual(c.state(), "ACTIVE")
        self.assertEqual(dec.block_id, 1)
        self.assertEqual(dec.index_in_block, 1)

    def test_never_starts_over_active_intervention(self):
        c = self._ctrl()
        c.force_block()
        # Boundary reached, but an intervention block is running: probe waits.
        dec = c.decide(spine_pos=20, block_size=20, intervention_active=True)
        self.assertFalse(dec.active)
        self.assertTrue(c.pending)
        # Intervention clears -> probe starts on the next boundary decide.
        dec = c.decide(spine_pos=20, block_size=20, intervention_active=False)
        self.assertTrue(dec.active)

    def test_block_is_counterbalanced_and_correct_length(self):
        c = self._ctrl(block_len=10)
        c.force_block()
        sides = _run_one_block(c)
        self.assertEqual(len(sides), 10)
        self.assertEqual(sides.count(LEFT), 5)
        self.assertEqual(sides.count(RIGHT), 5)
        # Block auto-ended: back to NORMAL, ready for another.
        self.assertFalse(c.active)
        self.assertEqual(c.state(), "NORMAL")

    def test_second_block_increments_id_and_differs(self):
        c = self._ctrl(block_len=10)
        c.force_block()
        first = _run_one_block(c)
        c.force_block()
        # Advance to a boundary again (spine unchanged in this unit test).
        second = _run_one_block(c)
        self.assertEqual(c.block_id, 2)
        # Both balanced; independent plans off the seed + block ordinal.
        self.assertEqual(sorted(first), sorted(second))
        self.assertNotEqual(first, second)

    def test_deterministic_plan_from_seed(self):
        a = self._ctrl(seed=42)
        b = self._ctrl(seed=42)
        a.force_block()
        b.force_block()
        self.assertEqual(_run_one_block(a), _run_one_block(b))

    def test_end_while_pending_cancels(self):
        c = self._ctrl()
        c.force_block()
        c.end_block()
        self.assertFalse(c.pending)
        dec = c.decide(spine_pos=0, block_size=20, intervention_active=False)
        self.assertFalse(dec.active)

    def test_end_running_block_early(self):
        c = self._ctrl(block_len=10)
        c.force_block()
        c.decide(spine_pos=0, block_size=20, intervention_active=False)  # start
        c.decide(spine_pos=0, block_size=20, intervention_active=False)  # trial 2
        c.end_block()
        # The next decide aborts the block rather than issuing trial 3.
        dec = c.decide(spine_pos=0, block_size=20, intervention_active=False)
        self.assertFalse(dec.active)
        self.assertFalse(c.active)


class TestTrialSequenceProbe(unittest.TestCase):
    def _seq(self, seed=7, block_len=10, enabled=True):
        cfg = TrialConfig(
            max_trials=40, block_size=20, max_same_run=3,
            probe=ProbeBlockConfig(enabled=enabled, block_len=block_len))
        return TrialSequence(cfg, seed=seed)

    def test_probe_disabled_stays_eager(self):
        seq = self._seq(enabled=False)
        self.assertFalse(seq.probe_enabled)
        # Plain balanced: no lazy commit, side is the spine directly.
        self.assertFalse(seq.is_closed_loop)
        self.assertEqual(seq.correct_arm(0), seq.seq[0])

    def test_probe_enables_lazy_commit_under_balanced(self):
        seq = self._seq()
        self.assertTrue(seq.probe_enabled)
        self.assertTrue(seq.is_closed_loop)  # lazy-commit path on
        self.assertEqual(seq.probe_state(), "NORMAL")

    def test_probe_inserts_without_consuming_spine(self):
        seq = self._seq(block_len=10)
        seq.force_probe_block()  # spine_pos 0 is a boundary -> starts at index 0
        self.assertEqual(seq.probe_state(), "PENDING")

        probe_sides = []
        for i in range(10):
            seq.commit(i)
            meta = seq.trial_meta(i)
            self.assertTrue(meta["probe"], f"trial {i} should be a probe trial")
            self.assertEqual(meta["sched_state"], "NORMAL")
            probe_sides.append(seq.correct_arm(i))
        # The probe consumed no spine slots.
        self.assertEqual(seq._spine_pos, 0)
        self.assertEqual(seq.probe_state(), "ACTIVE")
        # Internally counterbalanced 5/5.
        self.assertEqual(probe_sides.count(LEFT), 5)
        self.assertEqual(probe_sides.count(RIGHT), 5)

        # The first NORMAL trial after the probe takes the first spine side.
        seq.commit(10)
        self.assertFalse(seq.trial_meta(10)["probe"])
        self.assertEqual(seq.correct_arm(10), seq.seq[0])
        self.assertEqual(seq._spine_pos, 1)
        self.assertEqual(seq.probe_state(), "NORMAL")

    def test_reset_clears_probe_state(self):
        seq = self._seq()
        seq.force_probe_block()
        for i in range(5):
            seq.commit(i)
        seq.reset_debias()
        self.assertEqual(seq.probe_state(), "NORMAL")
        self.assertEqual(seq._spine_pos, 0)
        self.assertEqual(seq._frontier, 0)
        self.assertFalse(seq.trial_meta(0)["probe"])


def _frame(**kw):
    """A duck-typed TaskEventFrame for driving Phase3Task.update()."""
    base = dict(valid=True, choice_committed=None, left_hub_via_stem=False,
                loop_closed=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestPhase3TaskProbeReward(unittest.TestCase):
    def _task(self, *, reward_incorrect=False, block_len=10, seed=7):
        cfg = TrialConfig(
            max_trials=40, block_size=20, max_same_run=3,
            probe=ProbeBlockConfig(enabled=True, block_len=block_len))
        seq = TrialSequence(cfg, seed=seed)
        return Phase3Task(seq, Phase3Config(reward_incorrect=reward_incorrect))

    def _score(self, task, side):
        return task.update(_frame(choice_committed=side, left_hub_via_stem=True))

    def _advance(self, task):
        task.update(_frame(loop_closed=True))

    def test_probe_correct_choice_is_not_rewarded(self):
        task = self._task()
        task.force_probe_block()
        task._ensure_committed()
        correct = task.current_correct_arm()
        side = "left" if correct == LEFT else "right"
        dec = self._score(task, side)
        self.assertTrue(dec.probe)
        self.assertEqual(dec.outcome, CORRECT)
        self.assertFalse(dec.reward, "a correct probe trial must NOT pay out")

    def test_reward_incorrect_is_ignored_under_probe(self):
        task = self._task(reward_incorrect=True)
        task.force_probe_block()
        task._ensure_committed()
        correct = task.current_correct_arm()
        wrong = "right" if correct == LEFT else "left"
        dec = self._score(task, wrong)
        self.assertTrue(dec.probe)
        self.assertEqual(dec.outcome, INCORRECT)
        self.assertFalse(dec.reward,
                         "reward_incorrect must be ignored during a probe")

    def test_normal_trial_after_probe_pays_out(self):
        task = self._task(block_len=10)
        task.force_probe_block()
        # Run the whole 10-trial probe block (score + return each).
        for _ in range(10):
            task._ensure_committed()
            correct = task.current_correct_arm()
            side = "left" if correct == LEFT else "right"
            dec = self._score(task, side)
            self.assertTrue(dec.probe)
            self.assertFalse(dec.reward)
            self._advance(task)
        # The next trial is a normal reinforced trial again.
        task._ensure_committed()
        correct = task.current_correct_arm()
        side = "left" if correct == LEFT else "right"
        dec = self._score(task, side)
        self.assertFalse(dec.probe)
        self.assertEqual(dec.outcome, CORRECT)
        self.assertTrue(dec.reward, "a correct reinforced trial should pay out")


if __name__ == "__main__":
    unittest.main()
