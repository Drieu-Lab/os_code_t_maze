"""
test_alternation.py - headless tests for the closed-loop de-alternation core
(see alternation.py, intervention_stack.py, trial_sequence.py, and the ADR).

Covers the cue-aware alternation-rate measure (only repeat-cued trials count, so a
cue-follower is never flagged even when its CHOICES alternate - the off-phase
false-positive guard), the AlternationBlockController state machine (auto trigger
forcing the last choice, the bias-aware two-sided-repeats success exit + the
one-sided-camp guard, the max_block_len cap, window reset, manual override), and an
"intervention_blocks" TrialSequence driven by a simulated pure alternator. Pure
stdlib.

Run from host/:  python -m unittest test_alternation
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3.alternation import (
    AlternationBlockConfig, AlternationBlockController,
)
from tmaze.core.phase3.debias import AUTO, INTERVENTION, MANUAL, NORMAL
from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.phase3_stats import compute
from tmaze.core.phase3.trial_sequence import (
    LEFT, RIGHT, GEN_INTERVENTION_BLOCKS, TrialConfig, TrialSequence,
)


class TestAltRateMeasure(unittest.TestCase):
    def test_pure_alternator_is_one(self):
        c = AlternationBlockController(AlternationBlockConfig(window=10))
        # chosen alternates L,R,L,R,L; each trial after the first is repeat-cued
        # (cued == prev choice) and is a switch -> alt_rate == 1.0.
        chosen = [LEFT, RIGHT, LEFT, RIGHT, LEFT]
        for i, ch in enumerate(chosen):
            cued = chosen[i - 1] if i else LEFT   # cue says "repeat your last choice"
            c.record(cued, ch)
        self.assertEqual(c.alt_rate(), 1.0)

    def test_cue_follower_on_alternating_schedule_is_zero(self):
        # The off-phase false-positive guard: the SCHEDULE alternates and the animal
        # FOLLOWS it, so its choices alternate too - but the cue never told it to
        # repeat its last choice, so there are no samples and alt_rate stays 0.
        c = AlternationBlockController(AlternationBlockConfig(window=10))
        sched = [LEFT, RIGHT, LEFT, RIGHT, LEFT, RIGHT]
        for cued in sched:
            c.record(cued, cued)                  # follows the cue exactly
        self.assertEqual(c.alt_rate(), 0.0)

    def test_empty_is_zero(self):
        self.assertEqual(AlternationBlockController().alt_rate(), 0.0)

    def test_window_samples_counts_repeat_cued_only(self):
        c = AlternationBlockController(AlternationBlockConfig(window=10))
        self.assertEqual(c.window_samples(), 0)
        # A cue-follower on an alternating schedule generates NO repeat-cued samples.
        for cued in (LEFT, RIGHT, LEFT, RIGHT):
            c.record(cued, cued)
        self.assertEqual(c.window_samples(), 0)
        # A pure alternator: every trial after the first is a repeat-cued sample.
        c.reset()
        chosen = [LEFT, RIGHT, LEFT, RIGHT, LEFT]
        for i, ch in enumerate(chosen):
            cued = chosen[i - 1] if i else LEFT
            c.record(cued, ch)
        self.assertEqual(c.window_samples(), 4)   # 4 repeat-cued samples


def _prime_alternator(c, n):
    """Feed ``n`` repeat-cued switching samples (alt_rate -> 1.0) WITHOUT calling
    decide(); returns the last chosen side."""
    chosen = LEFT
    c.record(LEFT, chosen)                         # seed prev choice (no sample yet)
    for _ in range(n):
        cued = chosen                              # cue says repeat the last choice
        chosen = RIGHT if chosen == LEFT else LEFT  # alternator switches anyway
        c.record(cued, chosen)
    return chosen


class TestAutoTrigger(unittest.TestCase):
    def _ctl(self, **kw):
        base = dict(window=10, min_samples=4, trigger_rate=0.8,
                    exit_repeats_per_side=2, max_block_len=4)
        base.update(kw)
        return AlternationBlockController(AlternationBlockConfig(**base))

    def test_no_trigger_before_min_samples(self):
        c = self._ctl(min_samples=5)
        _prime_alternator(c, 4)                    # only 4 samples, need 5
        self.assertEqual(c.decide().state, NORMAL)

    def test_triggers_and_forces_last_choice(self):
        c = self._ctl()
        last = _prime_alternator(c, 4)             # 4 switching samples, rate 1.0
        d = c.decide()
        self.assertEqual(d.state, INTERVENTION)
        self.assertEqual(d.side, last)             # force a repeat of the last choice
        self.assertEqual(d.trigger, AUTO)
        self.assertEqual(d.block_id, 1)

    def test_success_exit_requires_repeats_on_both_arms(self):
        # A perfect cue-follower always chooses the forced side. It exits only after
        # earning exit_repeats_per_side rewarded repeats on EACH arm - so the forced
        # side must have switched arms mid-block (the bias-aware two-sided exit).
        c = self._ctl(max_block_len=50)
        _prime_alternator(c, 4)                    # prev choice = LEFT
        forced = []
        for _ in range(20):
            d = c.decide()
            if d.state == NORMAL:
                break                              # block exited on success
            forced.append(d.side)
            c.record(d.side, d.side)               # follows the cue (repeat/bridge)
        self.assertEqual(c.state, NORMAL)
        self.assertIn(LEFT, forced)                # earned repeats on both arms
        self.assertIn(RIGHT, forced)
        self.assertEqual(c.alt_rate(), 0.0)        # window cleared on exit

    def test_bias_camper_cannot_exit_one_sided(self):
        # A rat that camps ONE arm (its bias) earns its repeats there and is offered
        # a one-off "bridge" to the OTHER arm each time a run completes (ADR-0139);
        # it never repeats on that far arm, so it can't exit by one-sided camping and
        # rides to the welfare cap. (The camper IS rewarded on its camp trials now -
        # the accepted trade-off - but still cannot end the block one-sided.)
        c = self._ctl(max_block_len=10)
        _prime_alternator(c, 4)
        forced, states = [], []
        for _ in range(20):
            d = c.decide()
            states.append(d.state)
            if d.state == INTERVENTION:
                forced.append(d.side)
                c.record(d.side, RIGHT)            # cue = forced side; camper -> RIGHT
            else:
                c.record(RIGHT, RIGHT)
        self.assertIn(LEFT, forced)                # forced onto the avoided arm
        self.assertIn(NORMAL, states)              # the block did end...
        self.assertEqual(states.index(NORMAL), 10)  # ...only at max_block_len (cap)

    def test_alternator_not_rewarded_every_other_trial_after_one_arm(self):
        # Regression for the ADR-0129 held-far-arm leak (ADR-0139): a rat complies
        # on ONE arm, then reverts to alternating. The old code held the far arm as
        # a fixed target, so the alternator landed on it every other trial and
        # collected ~50% to the cap. Now the far arm is only a one-off bridge and the
        # forced side REVERTS to the last choice, so alternation earns ~0% (at most
        # the single bridge catch) and the forced side tracks the last choice rather
        # than staying pinned on the far arm.
        c = self._ctl(max_block_len=30)
        _prime_alternator(c, 4)                    # prev choice = LEFT, block arms
        # Satisfy the LEFT arm with two rewarded stays.
        for _ in range(2):
            d = c.decide()
            self.assertEqual(d.side, LEFT)
            c.record(d.side, LEFT)
        # Now alternate forever, ignoring the cue.
        chosen, rewards, forced_after = LEFT, 0, []
        for _ in range(12):
            d = c.decide()
            self.assertEqual(d.state, INTERVENTION)
            forced_after.append(d.side)
            chosen = RIGHT if chosen == LEFT else LEFT
            if d.side == chosen:
                rewards += 1
            c.record(d.side, chosen)
        self.assertLessEqual(rewards, 1)           # only the single bridge catch
        self.assertIn(LEFT, forced_after)          # reverts to last choice, not held R

    def test_cap_exit_when_never_complies(self):
        c = self._ctl()                            # only the cap can end it
        _prime_alternator(c, 4)
        states = []
        for _ in range(6):                         # stubborn: keep switching
            d = c.decide()
            states.append(d.state)
            other = RIGHT if d.side == LEFT else LEFT
            c.record(d.side if d.side is not None else LEFT, other)
        self.assertEqual(states[:4], [INTERVENTION] * 4)     # exactly max_block_len
        self.assertEqual(states[4], NORMAL)

    def test_cue_follower_never_triggers(self):
        c = self._ctl(min_samples=1)
        for cued in [LEFT, RIGHT, LEFT, RIGHT, LEFT, RIGHT] * 3:
            c.record(cued, cued)                   # follows an alternating schedule
            self.assertEqual(c.decide().state, NORMAL)


class TestManualOverride(unittest.TestCase):
    def test_force_and_end_block(self):
        c = AlternationBlockController(AlternationBlockConfig(min_samples=100))
        c.record(LEFT, LEFT)                       # a last choice to repeat
        c.force_block()
        d = c.decide()
        self.assertEqual(d.state, INTERVENTION)
        self.assertEqual(d.side, LEFT)
        self.assertEqual(d.trigger, MANUAL)
        c.end_block()
        self.assertEqual(c.decide().state, NORMAL)

    def test_force_without_a_last_choice_is_noop(self):
        c = AlternationBlockController(AlternationBlockConfig(min_samples=100))
        c.force_block()                            # no choice recorded yet
        self.assertEqual(c.decide().state, NORMAL)


class TestWillContinue(unittest.TestCase):
    """The non-mutating prediction the ribbon uses to mark the upcoming trial
    (docs/decisions/0152): True only while an open block will not exit next."""

    def _ctl(self, **kw):
        base = dict(window=10, min_samples=4, trigger_rate=0.8,
                    exit_repeats_per_side=2, max_block_len=4)
        base.update(kw)
        return AlternationBlockController(AlternationBlockConfig(**base))

    def test_false_when_normal(self):
        self.assertFalse(self._ctl().will_continue())

    def test_true_while_block_open_and_not_exiting(self):
        c = self._ctl()
        _prime_alternator(c, 4)
        self.assertEqual(c.decide().state, INTERVENTION)
        self.assertTrue(c.will_continue())

    def test_false_once_two_sided_exit_is_met(self):
        # Drive the two-sided repeats exit, then predict: no continue after success.
        c = self._ctl(max_block_len=50)
        _prime_alternator(c, 4)
        exited = False
        for _ in range(20):
            d = c.decide()
            if d.state == NORMAL:
                exited = True
                break
            c.record(d.side, d.side)               # comply (repeat/bridge)
            if not c.will_continue():
                # The success criterion is now met; the next decide would exit.
                break
        # Either it already exited, or will_continue correctly predicts the exit.
        self.assertTrue(exited or not c.will_continue())

    def test_false_when_manual_end_latched(self):
        c = self._ctl()
        _prime_alternator(c, 4)
        c.decide()
        self.assertTrue(c.will_continue())
        c.end_block()                              # latched, not yet applied
        self.assertFalse(c.will_continue())

    def test_false_at_the_welfare_cap(self):
        c = self._ctl(max_block_len=4)
        _prime_alternator(c, 4)
        # Ride to the cap without complying (stubborn alternator).
        for _ in range(4):
            d = c.decide()
            if d.state != INTERVENTION:
                break
            other = RIGHT if d.side == LEFT else LEFT
            c.record(d.side, other)
        self.assertFalse(c.will_continue())        # block_len hit the cap


# --------------------------------------------------------------------------- #
# TrialSequence in intervention_blocks mode with ONLY the alternation child.   #
# --------------------------------------------------------------------------- #
def _cfg(**kw):
    base = dict(max_trials=80, block_size=20, max_same_run=4,
                generator=GEN_INTERVENTION_BLOCKS, debias=None,
                alternation=AlternationBlockConfig(
                    window=10, min_samples=4, trigger_rate=0.8,
                    exit_repeats_per_side=2, max_block_len=4))
    base.update(kw)
    return TrialConfig(**base)


def _drive_alternator(ts, n):
    """Score n trials with a pure alternator (switch every trial, ignore the cue)."""
    sides, states, kinds, chosen = [], [], [], []
    for i in range(n):
        correct = ts.commit(i)
        m = ts.trial_meta(i)
        sides.append(correct)
        states.append(m["sched_state"])
        kinds.append(m["block_kind"])
        ch = LEFT if not chosen else (RIGHT if chosen[-1] == LEFT else LEFT)
        ts.record_outcome(i, correct, ch)
        chosen.append(ch)
    return sides, states, kinds, chosen


class TestAlternationSequence(unittest.TestCase):
    def test_alternator_triggers_blocks_forcing_last_choice(self):
        ts = TrialSequence(_cfg(), seed=42)
        sides, states, kinds, chosen = _drive_alternator(ts, 60)
        self.assertIn(INTERVENTION, states)                  # a block happened
        for i, state in enumerate(states):
            if state == INTERVENTION:
                self.assertEqual(kinds[i], "alternation")
                self.assertEqual(sides[i], chosen[i - 1])    # forced = last choice
        block_ids = {ts.trial_meta(i)["block_id"]
                     for i in range(60) if states[i] == INTERVENTION}
        self.assertTrue(max(block_ids) >= 2)                 # recurs for a stubborn rat

    def test_both_disabled_matches_spine(self):
        # intervention_blocks with neither child enabled is inert (balanced spine).
        ts = TrialSequence(_cfg(debias=None, alternation=None), seed=7)
        self.assertTrue(ts.is_closed_loop)
        sides, states, _kinds, _ch = _drive_alternator(ts, 60)
        self.assertTrue(all(s == NORMAL for s in states))
        self.assertEqual(sides, ts.seq[:60])

    def test_deterministic_given_seed_and_choices(self):
        a = TrialSequence(_cfg(), seed=99)
        b = TrialSequence(_cfg(), seed=99)
        sa, _sta, _ka, _ca = _drive_alternator(a, 60)
        sb, _stb, _kb, _cb = _drive_alternator(b, 60)
        self.assertEqual(sa, sb)

    def test_alt_gate_fields_surface_in_stats(self):
        # The de-alternation gate-progress readout: with only the alternation child
        # enabled, the snapshot->stats carries its enabled flag, trigger_rate,
        # min_samples and the live window sample count (for "samples N/min").
        ts = TrialSequence(_cfg(), seed=42)
        log = Phase3Log(ts, animal="RAT600",
                        session_start_iso="2026-08-18T12:00:00")
        _drive_alternator(ts, 30)
        st = compute(log.snapshot(30, armed=True))
        self.assertTrue(st.alt_enabled)
        self.assertAlmostEqual(st.alt_trigger_rate, 0.8)
        self.assertEqual(st.alt_min_samples, 4)
        self.assertGreaterEqual(st.alt_window_samples, 0)
        # No side-bias child in this config.
        self.assertFalse(st.debias_enabled)


if __name__ == "__main__":
    unittest.main()
