"""
test_dashboard.py - headless tests for the welfare-dashboard signals.

Covers the pure ``dashboard`` core: signal 1 (% baseline + <90% flag +
grams-to-threshold deficit), signal 2 (not-logged / not-weighed today), signal 3
(water intake by source + total, no flag), and signal 4 (weight/% baseline trend
over a window). Pure stdlib; no hardware/Tk.

Run from host/tests:  python -m unittest test_dashboard
"""

from __future__ import annotations

import unittest

from tmaze.core.records import dashboard
from tmaze.core.records.daily_log import DailyLog

_ROWS = [
    {"animal_id": "600", "date": "2026-07-01", "weight_g": "340",
     "baseline_g": "348", "pct_baseline": "0.977"},
    {"animal_id": "600", "date": "2026-07-02", "weight_g": "312",
     "baseline_g": "348", "pct_baseline": ""},          # 312/348 = 0.8966 (<90%)
    {"animal_id": "600", "date": "2026-07-03", "weight_g": "",
     "baseline_g": "348", "pct_baseline": ""},           # logged, not weighed
    {"animal_id": "601", "date": "2026-07-02", "weight_g": "338",
     "baseline_g": "350", "pct_baseline": "0.9657"},
]


class TestSignal1(unittest.TestCase):
    def setUp(self):
        self.log = DailyLog(_ROWS)

    def test_uses_stored_pct_when_present(self):
        s = dashboard.weight_status(self.log, "600", "2026-07-01")
        self.assertAlmostEqual(s["pct_baseline"], 0.977)
        self.assertFalse(s["below_threshold"])
        self.assertEqual(s["grams_to_threshold"], 0.0)

    def test_computes_pct_and_flags_below_90(self):
        s = dashboard.weight_status(self.log, "600", "2026-07-02")
        self.assertAlmostEqual(s["pct_baseline"], round(312 / 348, 4))
        self.assertTrue(s["below_threshold"])
        # 90% of 348 = 313.2; deficit = 313.2 - 312 = 1.2 g
        self.assertEqual(s["grams_to_threshold"], 1.2)

    def test_unlogged_day(self):
        s = dashboard.weight_status(self.log, "600", "2026-07-09")
        self.assertFalse(s["logged"])
        self.assertIsNone(s["pct_baseline"])
        self.assertIsNone(s["grams_to_threshold"])


class TestSignal2(unittest.TestCase):
    def setUp(self):
        self.log = DailyLog(_ROWS)

    def test_not_logged_and_not_weighed(self):
        out = dashboard.not_logged_today(self.log, ["600", "601", "602"],
                                         "2026-07-03")
        self.assertEqual(out["600"], "not weighed")   # row exists, blank weight
        self.assertEqual(out["601"], "not logged")     # no row that day
        self.assertEqual(out["602"], "not logged")     # unknown/absent

    def test_all_present_is_empty(self):
        out = dashboard.not_logged_today(self.log, ["600"], "2026-07-01")
        self.assertEqual(out, {})


class TestSignal3(unittest.TestCase):
    def test_registry_filter_and_collect(self):
        reg = [
            {"animal": "600", "date": "2026-07-02",
             "task_reward_ml": "1.5", "manual_reward_ml": "0.5"},
            {"animal": "600", "date": "2026-07-02",
             "task_reward_ml": "2.0", "manual_reward_ml": ""},
            {"animal": "601", "date": "2026-07-02",
             "task_reward_ml": "9.0", "manual_reward_ml": "9.0"},
        ]
        day = dashboard.registry_rows_for(reg, "600", "2026-07-02")
        self.assertEqual(len(day), 2)
        daily_row = {"supp_water": "3", "water_ml": "10"}
        water = dashboard.collect_water(daily_row, day)
        self.assertEqual(water["task"], 3.5)
        self.assertEqual(water["manual"], 0.5)
        self.assertEqual(water["supplemental"], 3.0)
        self.assertEqual(water["cage"], 10.0)

    def test_breakdown_drops_zero_and_totals(self):
        out = dashboard.water_breakdown(
            {"task": 3.5, "manual": 0.0, "supplemental": 3.0, "cage": 0.0})
        self.assertEqual(out["sources"], [("task", 3.5), ("supplemental", 3.0)])
        self.assertEqual(out["total_ml"], 6.5)

    def test_non_numeric_supp_is_zero(self):
        # supp_water may hold a *time* (from the paper log) - treated as 0 mL.
        water = dashboard.collect_water({"supp_water": "6:30pm", "water_ml": ""},
                                        [])
        self.assertEqual(water["supplemental"], 0.0)
        self.assertEqual(water["cage"], 0.0)


class TestSignal4(unittest.TestCase):
    def setUp(self):
        self.log = DailyLog(_ROWS)

    def test_trend_window_and_order(self):
        series = dashboard.weight_trend(self.log, "600", "2026-07-03", days=14)
        # 07-03 has no weight -> excluded; 07-01 and 07-02 remain, oldest first.
        self.assertEqual([r["date"] for r in series],
                         ["2026-07-01", "2026-07-02"])
        self.assertAlmostEqual(series[1]["pct_baseline"], round(312 / 348, 4))

    def test_window_excludes_out_of_range(self):
        series = dashboard.weight_trend(self.log, "600", "2026-07-02", days=1)
        self.assertEqual([r["date"] for r in series], ["2026-07-02"])

    def test_bad_end_date_returns_all_dated_weighed(self):
        series = dashboard.weight_trend(self.log, "600", "not-a-date")
        self.assertEqual([r["date"] for r in series],
                         ["2026-07-01", "2026-07-02"])


class TestWaterMlTrend(unittest.TestCase):
    """The optional water overlay: per-day total mL over the window."""

    _WATER_ROWS = [
        {"animal_id": "600", "date": "2026-07-01",
         "water_ml": "10", "supp_water": "3"},          # 13 mL (cage + supp)
        {"animal_id": "600", "date": "2026-07-02",
         "water_ml": "", "supp_water": "6:30pm"},        # nothing numeric -> 0
        {"animal_id": "600", "date": "2026-07-03",
         "water_ml": "8", "supp_water": ""},             # 8 mL, but registry adds task
    ]

    def setUp(self):
        self.log = DailyLog(self._WATER_ROWS)
        self.reg = [
            {"animal": "600", "date": "2026-07-03",
             "task_reward_ml": "2.5", "manual_reward_ml": "0.5"},
            {"animal": "601", "date": "2026-07-03",
             "task_reward_ml": "99", "manual_reward_ml": "99"},
        ]

    def test_sums_channels_per_day_oldest_first(self):
        series = dashboard.water_ml_trend(self.log, self.reg, "600",
                                          "2026-07-03", days=14)
        self.assertEqual([r["date"] for r in series],
                         ["2026-07-01", "2026-07-02", "2026-07-03"])
        totals = {r["date"]: r["total_ml"] for r in series}
        self.assertEqual(totals["2026-07-01"], 13.0)   # 10 cage + 3 supp
        self.assertEqual(totals["2026-07-02"], 0.0)    # time string -> 0
        self.assertEqual(totals["2026-07-03"], 11.0)   # 8 cage + 2.5 + 0.5 reward

    def test_window_excludes_out_of_range(self):
        series = dashboard.water_ml_trend(self.log, self.reg, "600",
                                          "2026-07-01", days=1)
        self.assertEqual([r["date"] for r in series], ["2026-07-01"])

    def test_registry_only_date_is_included(self):
        # A day with no daily row but a registry row still appears in the trend.
        log = DailyLog([])
        reg = [{"animal": "600", "date": "2026-07-05",
                "task_reward_ml": "4", "manual_reward_ml": ""}]
        series = dashboard.water_ml_trend(log, reg, "600", "2026-07-05", days=14)
        self.assertEqual(series, [{"date": "2026-07-05", "total_ml": 4.0}])


if __name__ == "__main__":
    unittest.main()
