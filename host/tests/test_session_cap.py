"""
test_session_cap.py - headless tests for the Phase 3 session cap arithmetic.

Covers the pure ``session_cap`` core (docs/decisions/0190 / 0191): when the cap
is reached (trials vs time, tie, disabled), the daily-water reward rule, the
per-animal resolution priority, and the water-target summary text.

Run from host/:  python -m unittest test_session_cap
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3 import session_cap as sc


class TestEvaluate(unittest.TestCase):
    cfg = sc.SessionCapConfig(enabled=True, max_trials=120, max_minutes=45.0)

    def test_running(self):
        st = sc.evaluate(87, 22 * 60 + 10, self.cfg)
        self.assertFalse(st.reached)
        self.assertIsNone(st.reason)
        self.assertEqual(st.trials_left, 33)
        self.assertAlmostEqual(st.seconds_left, 45 * 60 - (22 * 60 + 10))

    def test_trial_cap(self):
        st = sc.evaluate(120, 30 * 60, self.cfg)
        self.assertTrue(st.reached)
        self.assertEqual(st.reason, sc.REASON_TRIALS)
        self.assertEqual(st.trials_left, 0)
        # Past the cap never goes negative.
        self.assertEqual(sc.evaluate(125, 30 * 60, self.cfg).trials_left, 0)

    def test_time_cap(self):
        st = sc.evaluate(90, 45 * 60, self.cfg)
        self.assertTrue(st.reached)
        self.assertEqual(st.reason, sc.REASON_TIME)
        self.assertEqual(st.seconds_left, 0.0)
        self.assertEqual(st.trials_left, 30)

    def test_trials_win_the_tie(self):
        st = sc.evaluate(120, 45 * 60, self.cfg)
        self.assertEqual(st.reason, sc.REASON_TRIALS)

    def test_disabled_never_reaches(self):
        off = sc.SessionCapConfig(enabled=False, max_trials=10, max_minutes=1.0)
        st = sc.evaluate(500, 3600, off)
        self.assertFalse(st.reached)
        self.assertIsNone(st.reason)
        # Countdown figures are still computed (for a read-only readout).
        self.assertEqual(st.trials_left, 0)

    def test_defaults_are_the_cohort_values(self):
        d = sc.SessionCapConfig()
        self.assertTrue(d.enabled)
        self.assertEqual((d.max_trials, d.max_minutes, d.water_at_chance_fraction),
                         (120, 45.0, 0.5))


class TestRewardRule(unittest.TestCase):
    cfg = sc.SessionCapConfig(max_trials=120, water_at_chance_fraction=0.5)

    def test_chance_earns_the_fraction(self):
        # 8 mL/day, 120 trials, 0.5 -> 8000*0.5/(120*0.5) = 66.67 uL; 60 rewards
        # (chance) earn 4 mL = half the daily target, 120 earn all of it.
        ul = sc.reward_ul_for_target(8.0, self.cfg)
        self.assertAlmostEqual(ul, 8000.0 / 120.0)
        self.assertAlmostEqual(60 * ul / 1000.0, 4.0)
        self.assertAlmostEqual(120 * ul / 1000.0, 8.0)

    def test_other_targets(self):
        self.assertAlmostEqual(sc.reward_ul_for_target(9.0, self.cfg), 75.0)
        self.assertAlmostEqual(sc.reward_ul_for_target(6.0, self.cfg), 50.0)

    def test_fraction_scales(self):
        cfg = sc.SessionCapConfig(max_trials=120, water_at_chance_fraction=0.6)
        self.assertAlmostEqual(sc.reward_ul_for_target(8.0, cfg), 80.0)

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            sc.reward_ul_for_target(0.0, self.cfg)
        with self.assertRaises(ValueError):
            sc.reward_ul_for_target(8.0, sc.SessionCapConfig(max_trials=0))


class TestResolve(unittest.TestCase):
    cfg = sc.SessionCapConfig(max_trials=120, water_at_chance_fraction=0.5)

    def test_explicit_reward_ul_wins(self):
        # The operator pinning one magnitude (70 uL for every rat) overrides the
        # derived volume; the daily target is left for the supplement readout.
        ul, src = sc.resolve_reward_ul(8.0, 70.0, self.cfg)
        self.assertEqual((ul, src), (70.0, sc.SOURCE_REWARD_UL))
        for blank in (None, ""):
            ul, src = sc.resolve_reward_ul(blank, 70.0, self.cfg)
            self.assertEqual((ul, src), (70.0, sc.SOURCE_REWARD_UL))

    def test_daily_water_derives_when_no_explicit(self):
        for blank in (None, "", 0):
            ul, src = sc.resolve_reward_ul(8.0, blank, self.cfg)
            self.assertAlmostEqual(ul, 8000.0 / 120.0)
            self.assertEqual(src, sc.SOURCE_DAILY_WATER)

    def test_config_when_both_unset(self):
        self.assertEqual(sc.resolve_reward_ul(None, None, self.cfg),
                         (None, sc.SOURCE_CONFIG))
        self.assertEqual(sc.resolve_reward_ul("", "", self.cfg),
                         (None, sc.SOURCE_CONFIG))

    def test_zero_is_unset_not_a_volume(self):
        self.assertEqual(sc.resolve_reward_ul(0, 0, self.cfg),
                         (None, sc.SOURCE_CONFIG))


class TestWaterTarget(unittest.TestCase):
    def test_summary_and_text(self):
        s = sc.water_target_summary(8.0, 5500.0)
        self.assertEqual(s, {"daily_water_ml": 8.0, "earned_ml": 5.5,
                             "fraction": 0.688, "supplement_ml": 2.5})
        self.assertEqual(sc.format_water_target(s),
                         "earned 5.50 mL = 69% of 8.0 mL target, supplement 2.50 mL")

    def test_perfect_day_needs_no_supplement(self):
        s = sc.water_target_summary(8.0, 8000.0)
        self.assertEqual(s["supplement_ml"], 0.0)
        # Over-earning (e.g. manual top-ups) never yields a negative supplement.
        self.assertEqual(sc.water_target_summary(8.0, 9000.0)["supplement_ml"], 0.0)

    def test_no_target(self):
        self.assertIsNone(sc.water_target_summary(None, 5000.0))
        self.assertIsNone(sc.water_target_summary("", 5000.0))
        self.assertEqual(sc.format_water_target(None), "")


class TestCountdown(unittest.TestCase):
    def test_token(self):
        cfg = sc.SessionCapConfig(max_trials=120, max_minutes=45.0)
        self.assertEqual(sc.format_countdown(cfg, 87, 22 * 60 + 10),
                         "cap: 87/120 \u00b7 22:10/45:00")
        self.assertEqual(sc.mmss(-5), "0:00")


if __name__ == "__main__":
    unittest.main()
