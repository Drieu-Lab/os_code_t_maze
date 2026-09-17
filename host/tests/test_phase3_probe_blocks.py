"""
test_phase3_probe_blocks.py - headless tests for unrewarded probe blocks.

Covers the pure core for the acquisition/expression dissociation (docs/decisions/
0115-probe) with the correct-choice WINDOW trigger (docs/decisions/0134): the
``ProbeBlockController`` state machine (probe_blocks.py) that fires on the first
correct choice inside a configured trial-number window, its integration into
``TrialSequence`` (the trigger trial is an already-committed spine trial reclassified
at scoring; the following ``block_len`` trials insert on top of the spine WITHOUT
consuming it, take priority over the intervention stack, and are flagged in
``trial_meta``), and the reward gate in ``Phase3Task`` (the trigger and every block
trial are scored as usual but never pay out).

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
    LEFT, RIGHT, TrialConfig, TrialSequence, alt_run_ok, max_run_ok)


def _drain_block(ctrl):
    """Drive an ACTIVE ``ctrl`` until its forced block ends, collecting the sides."""
    sides = []
    for _ in range(64):  # generous cap; a block is ``block_len`` trials
        dec = ctrl.advance()
        if not dec.active:
            break
        sides.append(dec.side)
    return sides


class TestProbeBlockController(unittest.TestCase):
    def _ctrl(self, block_len=10, seed=1, windows=((40, 60),),
             max_same_run=0, max_alt_run=0):
        return ProbeBlockController(
            ProbeBlockConfig(enabled=True, block_len=block_len, windows=windows),
            seed=seed, max_same_run=max_same_run, max_alt_run=max_alt_run)

    def test_idle_by_default(self):
        c = self._ctrl()
        self.assertFalse(c.active)
        self.assertEqual(c.state(), "NORMAL")
        # A correct choice OUTSIDE any window does not fire.
        self.assertFalse(c.on_scored(trial_no=10, correct=True,
                                     intervention_active=False))
        self.assertFalse(c.active)

    def test_correct_choice_in_window_fires(self):
        c = self._ctrl(windows=((40, 60),))
        # Just inside the window, a correct non-intervention choice fires.
        self.assertTrue(c.on_scored(trial_no=40, correct=True,
                                    intervention_active=False))
        self.assertTrue(c.active)
        self.assertEqual(c.state(), "ACTIVE")
        self.assertEqual(c.block_id, 1)

    def test_incorrect_choice_does_not_fire(self):
        c = self._ctrl()
        self.assertFalse(c.on_scored(trial_no=45, correct=False,
                                     intervention_active=False))
        self.assertFalse(c.active)

    def test_intervention_choice_does_not_fire(self):
        c = self._ctrl()
        # A correct choice inside the window but DURING an intervention block never
        # triggers (docs/decisions/0134).
        self.assertFalse(c.on_scored(trial_no=45, correct=True,
                                     intervention_active=True))
        self.assertFalse(c.active)

    def test_window_fires_at_most_once(self):
        c = self._ctrl(windows=((40, 60),))
        self.assertTrue(c.on_scored(trial_no=41, correct=True,
                                    intervention_active=False))
        _drain_block(c)                      # finish the block, back to NORMAL
        self.assertFalse(c.active)
        # A later correct choice STILL inside the same window does not re-fire.
        self.assertFalse(c.on_scored(trial_no=55, correct=True,
                                     intervention_active=False))
        self.assertFalse(c.active)

    def test_multiple_windows_each_fire_once(self):
        c = self._ctrl(windows=((40, 60), (120, 160)))
        self.assertTrue(c.on_scored(trial_no=42, correct=True,
                                    intervention_active=False))
        _drain_block(c)
        # The second window fires independently.
        self.assertTrue(c.on_scored(trial_no=130, correct=True,
                                    intervention_active=False))
        self.assertEqual(c.block_id, 2)

    def test_block_is_counterbalanced_and_correct_length(self):
        c = self._ctrl(block_len=10)
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        sides = _drain_block(c)
        self.assertEqual(len(sides), 10)
        self.assertEqual(sides.count(LEFT), 5)
        self.assertEqual(sides.count(RIGHT), 5)
        self.assertFalse(c.active)
        self.assertEqual(c.state(), "NORMAL")

    def test_upcoming_forced_counts_down_and_clears(self):
        # The ribbon's upcoming-block count (docs/decisions/0152): block_len when a
        # probe fires, decremented as trials are handed out, 0 when idle.
        c = self._ctrl(block_len=4)
        self.assertEqual(c.upcoming_forced(), 0)             # idle
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        self.assertEqual(c.upcoming_forced(), 4)             # full block ahead
        c.advance()
        self.assertEqual(c.upcoming_forced(), 3)             # one issued
        _drain_block(c)
        self.assertEqual(c.upcoming_forced(), 0)             # block finished

    def test_upcoming_forced_zero_after_operator_end(self):
        # Aborting a running probe clears the upcoming count immediately, before the
        # next advance() actually tears the block down.
        c = self._ctrl(block_len=6)
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        self.assertEqual(c.upcoming_forced(), 6)
        c.end_block()                                        # latch _manual_end
        self.assertEqual(c.upcoming_forced(), 0)

    def test_upcoming_forced_zero_after_disarm(self):
        c = self._ctrl(block_len=6)
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        self.assertEqual(c.upcoming_forced(), 6)
        c.set_armed(False)                                   # operator disable
        self.assertEqual(c.upcoming_forced(), 0)

    def test_block_honors_max_same_run(self):
        # The forced block re-rolls so no single-side run exceeds the session cap
        # (docs/decisions/0138). Check across several seeds, still 5/5 balanced.
        for seed in range(1, 30):
            c = self._ctrl(block_len=10, seed=seed, max_same_run=3)
            c.on_scored(trial_no=40, correct=True, intervention_active=False)
            sides = _drain_block(c)
            self.assertEqual(len(sides), 10)
            self.assertEqual(sides.count(LEFT), 5)
            self.assertTrue(max_run_ok(sides, len(sides), 3),
                            f"seed={seed} run cap violated: {sides}")

    def test_block_honors_max_alt_run(self):
        for seed in range(1, 30):
            c = self._ctrl(block_len=10, seed=seed, max_alt_run=3)
            c.on_scored(trial_no=40, correct=True, intervention_active=False)
            sides = _drain_block(c)
            self.assertEqual(len(sides), 10)
            self.assertTrue(alt_run_ok(sides, len(sides), 3),
                            f"seed={seed} alt cap violated: {sides}")

    def test_no_cap_leaves_block_unconstrained(self):
        # With both caps 0 (the phase3.json default) the block is only balanced, and
        # at least one seed produces a run longer than 3 - proving the cap is real.
        saw_long_run = False
        for seed in range(1, 60):
            c = self._ctrl(block_len=10, seed=seed)
            c.on_scored(trial_no=40, correct=True, intervention_active=False)
            sides = _drain_block(c)
            if not max_run_ok(sides, len(sides), 3):
                saw_long_run = True
                break
        self.assertTrue(saw_long_run)

    def test_second_block_increments_id_and_differs(self):
        c = self._ctrl(block_len=10, windows=((40, 60), (120, 160)))
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        first = _drain_block(c)
        c.on_scored(trial_no=120, correct=True, intervention_active=False)
        second = _drain_block(c)
        self.assertEqual(c.block_id, 2)
        # Both balanced; independent plans off the seed + block ordinal.
        self.assertEqual(sorted(first), sorted(second))
        self.assertNotEqual(first, second)

    def test_deterministic_plan_from_seed(self):
        a = self._ctrl(seed=42)
        b = self._ctrl(seed=42)
        a.on_scored(trial_no=40, correct=True, intervention_active=False)
        b.on_scored(trial_no=40, correct=True, intervention_active=False)
        self.assertEqual(_drain_block(a), _drain_block(b))

    def test_no_fire_while_active(self):
        c = self._ctrl(windows=((40, 60),))
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        # Another correct choice while a probe is running does not start a second.
        self.assertFalse(c.on_scored(trial_no=41, correct=True,
                                     intervention_active=False))
        self.assertEqual(c.block_id, 1)

    def test_end_running_block_early(self):
        c = self._ctrl(block_len=10)
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        c.advance()                          # block trial 1
        c.advance()                          # block trial 2
        c.end_block()
        dec = c.advance()                    # aborts rather than issuing trial 3
        self.assertFalse(dec.active)
        self.assertFalse(c.active)

    # ---- mid-session arm/disarm (docs/decisions/0137) ----
    def test_armed_by_default(self):
        c = self._ctrl()
        self.assertTrue(c.armed)
        self.assertFalse(c.ever_disarmed)

    def test_disarmed_never_fires_in_window(self):
        c = self._ctrl(windows=((40, 60),))
        c.set_armed(False)
        self.assertFalse(c.armed)
        self.assertTrue(c.ever_disarmed)
        self.assertFalse(c.on_scored(trial_no=45, correct=True,
                                     intervention_active=False))
        self.assertFalse(c.active)

    def test_rearm_allows_a_later_window(self):
        c = self._ctrl(windows=((40, 60), (120, 160)))
        c.set_armed(False)
        self.assertFalse(c.on_scored(trial_no=45, correct=True,
                                     intervention_active=False))
        c.set_armed(True)
        self.assertTrue(c.armed)
        self.assertTrue(c.on_scored(trial_no=130, correct=True,
                                    intervention_active=False))
        self.assertTrue(c.active)

    def test_disarm_aborts_running_block(self):
        c = self._ctrl(block_len=10)
        c.on_scored(trial_no=40, correct=True, intervention_active=False)
        c.advance()                          # issue one block trial
        c.set_armed(False)                   # "off" also aborts the running block
        dec = c.advance()
        self.assertFalse(dec.active)
        self.assertFalse(c.active)
        self.assertTrue(c.ever_disarmed)

    def test_reset_rearms_and_clears_ever_disarmed(self):
        c = self._ctrl()
        c.set_armed(False)
        self.assertTrue(c.ever_disarmed)
        c.reset()
        self.assertTrue(c.armed)
        self.assertFalse(c.ever_disarmed)


class TestTrialSequenceProbe(unittest.TestCase):
    def _seq(self, seed=7, block_len=10, enabled=True, windows=((1, 40),)):
        cfg = TrialConfig(
            max_trials=60, block_size=20, max_same_run=3,
            probe=ProbeBlockConfig(enabled=enabled, block_len=block_len,
                                   windows=windows))
        return TrialSequence(cfg, seed=seed)

    def test_probe_disabled_stays_eager(self):
        seq = self._seq(enabled=False)
        self.assertFalse(seq.probe_enabled)
        self.assertFalse(seq.is_closed_loop)
        self.assertEqual(seq.correct_arm(0), seq.seq[0])

    def test_probe_enables_lazy_commit_under_balanced(self):
        seq = self._seq()
        self.assertTrue(seq.probe_enabled)
        self.assertTrue(seq.is_closed_loop)  # lazy-commit path on
        self.assertEqual(seq.probe_state(), "NORMAL")

    def test_correct_choice_trigger_reclassifies_and_inserts_block(self):
        seq = self._seq(block_len=10, windows=((1, 40),))
        seq.commit(0)                        # a normal spine trigger candidate
        self.assertFalse(seq.trial_meta(0)["probe"])
        # The animal is correct on trial 0 (1-based trial_no 1, inside [1,40]).
        fired = seq.probe_trigger_on_score(0, correct=True,
                                           intervention_active=False)
        self.assertTrue(fired)
        m0 = seq.trial_meta(0)
        self.assertTrue(m0["probe"])
        self.assertTrue(m0["probe_trigger"])
        # The trigger trial IS a spine trial, so it consumed one spine slot.
        self.assertEqual(seq._spine_pos, 1)

        probe_sides = []
        for i in range(1, 11):
            seq.commit(i)
            m = seq.trial_meta(i)
            self.assertTrue(m["probe"], f"trial {i} should be a probe trial")
            self.assertFalse(m["probe_trigger"], f"trial {i} is not the trigger")
            self.assertEqual(m["sched_state"], "NORMAL")
            probe_sides.append(seq.correct_arm(i))
        # The following block consumed NO further spine slots.
        self.assertEqual(seq._spine_pos, 1)
        self.assertEqual(seq.probe_state(), "ACTIVE")
        self.assertEqual(probe_sides.count(LEFT), 5)
        self.assertEqual(probe_sides.count(RIGHT), 5)

        # The first NORMAL trial after the block takes the NEXT spine side.
        seq.commit(11)
        self.assertFalse(seq.trial_meta(11)["probe"])
        self.assertEqual(seq.correct_arm(11), seq.seq[1])
        self.assertEqual(seq._spine_pos, 2)
        self.assertEqual(seq.probe_state(), "NORMAL")

    def test_block_honors_session_max_same_run(self):
        # _seq() sets max_same_run=3; the forced probe block should inherit it
        # (docs/decisions/0138), across several seeds.
        for seed in range(1, 20):
            seq = self._seq(seed=seed, block_len=10, windows=((1, 40),))
            seq.commit(0)
            seq.probe_trigger_on_score(0, correct=True, intervention_active=False)
            sides = []
            for i in range(1, 11):
                seq.commit(i)
                sides.append(seq.correct_arm(i))
            self.assertTrue(max_run_ok(sides, len(sides), 3),
                            f"seed={seed} run cap violated: {sides}")

    def test_incorrect_or_out_of_window_does_not_trigger(self):
        seq = self._seq(windows=((1, 40),))
        seq.commit(0)
        self.assertFalse(seq.probe_trigger_on_score(0, correct=False,
                                                    intervention_active=False))
        self.assertFalse(seq.trial_meta(0)["probe"])
        # A window of (5, 40) skips trial_no 1..4.
        seq2 = self._seq(windows=((5, 40),))
        seq2.commit(0)
        self.assertFalse(seq2.probe_trigger_on_score(0, correct=True,
                                                     intervention_active=False))
        self.assertFalse(seq2.trial_meta(0)["probe"])

    def test_reset_clears_probe_state(self):
        seq = self._seq()
        seq.commit(0)
        seq.probe_trigger_on_score(0, correct=True, intervention_active=False)
        seq.reset_debias()
        self.assertEqual(seq.probe_state(), "NORMAL")
        self.assertEqual(seq._spine_pos, 0)
        self.assertEqual(seq._frontier, 0)
        self.assertFalse(seq.trial_meta(0)["probe"])

    def test_set_probe_armed_gates_trigger(self):
        seq = self._seq(windows=((1, 40),))
        self.assertTrue(seq.probe_armed)
        self.assertFalse(seq.probe_ever_disarmed)
        seq.set_probe_armed(False)           # operator disables mid-session
        self.assertFalse(seq.probe_armed)
        self.assertTrue(seq.probe_ever_disarmed)
        seq.commit(0)
        self.assertFalse(seq.probe_trigger_on_score(0, correct=True,
                                                    intervention_active=False))
        self.assertFalse(seq.trial_meta(0)["probe"])
        # reset_debias re-arms (fresh session).
        seq.reset_debias()
        self.assertTrue(seq.probe_armed)
        self.assertFalse(seq.probe_ever_disarmed)

    def test_probe_armed_false_when_disabled(self):
        seq = self._seq(enabled=False)
        self.assertFalse(seq.probe_armed)
        self.assertFalse(seq.probe_ever_disarmed)
        seq.set_probe_armed(False)           # no-op, must not raise
        self.assertFalse(seq.probe_armed)


def _frame(**kw):
    """A duck-typed TaskEventFrame for driving Phase3Task.update().

    Phase3Task now scores on the shared ``trial_trip`` credential (== the
    hub-departure ``left_hub_via_stem`` for now; docs/decisions/0168), so
    ``trial_trip`` mirrors ``left_hub_via_stem`` unless a test sets it."""
    base = dict(valid=True, choice_committed=None, left_hub_via_stem=False,
                loop_closed=None)
    base.update(kw)
    base.setdefault("trial_trip", base["left_hub_via_stem"])
    return types.SimpleNamespace(**base)


class TestPhase3TaskProbeReward(unittest.TestCase):
    def _task(self, *, reward_incorrect=False, block_len=10, seed=7,
              windows=((1, 40),)):
        cfg = TrialConfig(
            max_trials=60, block_size=20, max_same_run=3,
            probe=ProbeBlockConfig(enabled=True, block_len=block_len,
                                   windows=windows))
        seq = TrialSequence(cfg, seed=seed)
        return Phase3Task(seq, Phase3Config(reward_incorrect=reward_incorrect))

    def _score(self, task, side):
        return task.update(_frame(choice_committed=side, left_hub_via_stem=True))

    def _score_correct(self, task):
        task._ensure_committed()
        correct = task.current_correct_arm()
        side = "left" if correct == LEFT else "right"
        return self._score(task, side)

    def _advance(self, task):
        task.update(_frame(loop_closed=True))

    def test_trigger_trial_is_flagged_and_not_rewarded(self):
        task = self._task()
        dec = self._score_correct(task)      # first correct choice, trial_no 1
        self.assertTrue(dec.probe)
        self.assertTrue(dec.probe_trigger)
        self.assertEqual(dec.outcome, CORRECT)
        self.assertFalse(dec.reward, "the correct-choice trigger must NOT pay out")

    def test_incorrect_first_choice_does_not_trigger_and_pays_out(self):
        task = self._task()
        task._ensure_committed()
        correct = task.current_correct_arm()
        side = "left" if correct == LEFT else "right"
        # Score CORRECT on the very first trial to trigger... actually score wrong:
        wrong = "right" if correct == LEFT else "left"
        dec = self._score(task, wrong)
        self.assertFalse(dec.probe)
        self.assertFalse(dec.probe_trigger)
        self.assertEqual(dec.outcome, INCORRECT)

    def test_block_follows_trigger_all_unrewarded(self):
        task = self._task(block_len=10)
        # Trigger on the first correct choice.
        dec = self._score_correct(task)
        self.assertTrue(dec.probe_trigger)
        self.assertFalse(dec.reward)
        self._advance(task)
        # The following block_len trials are all probe, none pay out.
        for _ in range(10):
            dec = self._score_correct(task)
            self.assertTrue(dec.probe)
            self.assertFalse(dec.probe_trigger)
            self.assertFalse(dec.reward)
            self._advance(task)
        # The next trial is a normal reinforced trial again.
        dec = self._score_correct(task)
        self.assertFalse(dec.probe)
        self.assertEqual(dec.outcome, CORRECT)
        self.assertTrue(dec.reward, "a correct reinforced trial should pay out")

    def test_disarm_prevents_trigger_and_pays_out(self):
        task = self._task(windows=((1, 40),))
        task.set_probe_armed(False)          # operator turns probes off
        dec = self._score_correct(task)
        self.assertFalse(dec.probe)
        self.assertFalse(dec.probe_trigger)
        self.assertEqual(dec.outcome, CORRECT)
        self.assertTrue(dec.reward,
                        "a correct trial should pay out when probes are disabled")

    def test_reward_incorrect_is_ignored_under_probe(self):
        task = self._task(reward_incorrect=True)
        # Trigger first (correct choice), then score the following block wrong.
        self._score_correct(task)
        self._advance(task)
        task._ensure_committed()
        correct = task.current_correct_arm()
        wrong = "right" if correct == LEFT else "left"
        dec = self._score(task, wrong)
        self.assertTrue(dec.probe)
        self.assertEqual(dec.outcome, INCORRECT)
        self.assertFalse(dec.reward,
                         "reward_incorrect must be ignored during a probe")


if __name__ == "__main__":
    unittest.main()
