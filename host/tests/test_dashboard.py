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

    def test_recomputes_from_weight_baseline(self):
        s = dashboard.weight_status(self.log, "600", "2026-07-01")
        self.assertAlmostEqual(s["pct_baseline"], round(340 / 348, 4))
        self.assertFalse(s["below_threshold"])
        self.assertEqual(s["grams_to_threshold"], 0.0)

    def test_recompute_overrides_a_stale_stored_pct(self):
        # weight/baseline are the source of truth: a stored mirror that disagrees
        # is ignored, not trusted (docs/decisions/0163).
        log = DailyLog([{"animal_id": "600", "date": "2026-07-10",
                         "weight_g": "300", "baseline_g": "348",
                         "pct_baseline": "0.999"}])
        s = dashboard.weight_status(log, "600", "2026-07-10")
        self.assertAlmostEqual(s["pct_baseline"], round(300 / 348, 4))
        self.assertTrue(s["below_threshold"])

    def test_falls_back_to_stored_when_not_computable(self):
        # Baseline missing -> can't recompute, so the stored mirror is used.
        log = DailyLog([{"animal_id": "600", "date": "2026-07-11",
                         "weight_g": "", "baseline_g": "",
                         "pct_baseline": "0.9123"}])
        s = dashboard.weight_status(log, "600", "2026-07-11")
        self.assertAlmostEqual(s["pct_baseline"], 0.9123)

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
        daily_row = {"supp_water_ml": "3", "water_ml": "10"}
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
        # supp_water_ml may be blank/garbage - treated as 0 mL.
        water = dashboard.collect_water({"supp_water_ml": "n/a", "water_ml": ""},
                                        [])
        self.assertEqual(water["supplemental"], 0.0)
        self.assertEqual(water["cage"], 0.0)

    def test_legacy_supp_water_text_is_never_counted(self):
        # ADR-0160: the legacy free-text cell ("2 min", or even a bare "3") is
        # not a volume; only the v4 supp_water_ml cell contributes.
        for legacy in ("2 min", "3", "6:30pm"):
            water = dashboard.collect_water(
                {"supp_water": legacy, "supp_water_ml": "", "water_ml": ""}, [])
            self.assertEqual(water["supplemental"], 0.0, legacy)

    def test_task_earned_day_counts_entered_water_once(self):
        # ADR-0106: on a task_earned day the entered water_ml IS the rig reward,
        # so the rig task/manual are dropped (no double count). supp still counts.
        reg = [{"animal": "602", "date": "2026-08-10",
                "task_reward_ml": "4.4", "manual_reward_ml": "0.12"}]
        day = dashboard.registry_rows_for(reg, "602", "2026-08-10")
        water = dashboard.collect_water(
            {"water_ml": "4.52", "supp_water_ml": "1.0",
             "water_method": "task_earned"}, day)
        self.assertEqual(water, {"task": 0.0, "manual": 0.0,
                                 "supplemental": 1.0, "cage": 4.52})
        self.assertEqual(dashboard.water_breakdown(water)["total_ml"], 5.52)

    def test_task_earned_blank_water_ml_falls_back_to_rig_reward(self):
        # No typed amount on a session day -> keep the rig reward so it isn't lost.
        reg = [{"animal": "602", "date": "2026-08-10",
                "task_reward_ml": "4.4", "manual_reward_ml": "0.12"}]
        day = dashboard.registry_rows_for(reg, "602", "2026-08-10")
        water = dashboard.collect_water(
            {"water_ml": "", "water_method": "task_earned"}, day)
        self.assertEqual(water["task"], 4.4)
        self.assertEqual(water["manual"], 0.12)
        self.assertEqual(water["cage"], 0.0)

    def test_measured_bottle_day_still_sums_cage_and_reward(self):
        # A non-task_earned method leaves the summing behaviour unchanged.
        reg = [{"animal": "602", "date": "2026-08-10",
                "task_reward_ml": "2.0", "manual_reward_ml": "0.0"}]
        day = dashboard.registry_rows_for(reg, "602", "2026-08-10")
        water = dashboard.collect_water(
            {"water_ml": "5.0", "water_method": "measured_bottle"}, day)
        self.assertEqual(water["task"], 2.0)
        self.assertEqual(water["cage"], 5.0)
        self.assertEqual(dashboard.water_breakdown(water)["total_ml"], 7.0)


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
         "water_ml": "10", "supp_water_ml": "3"},       # 13 mL (cage + supp)
        {"animal_id": "600", "date": "2026-07-02",
         "water_ml": "", "supp_water": "6:30pm"},        # legacy text only -> 0
        {"animal_id": "600", "date": "2026-07-03",
         "water_ml": "8", "supp_water_ml": ""},          # 8 mL, but registry adds task
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


class TestAdLibRegime(unittest.TestCase):
    """Ad-lib (off-restriction) days change how the signals read (ADR-0100)."""

    _ROWS = [
        {"animal_id": "600", "date": "2026-08-07", "weight_g": "340",
         "baseline_g": "348"},                                    # restriction
        {"animal_id": "600", "date": "2026-08-08", "weight_g": "",
         "water_regime": "ad_lib", "water_on": "5pm"},            # ad-lib, no weight
        {"animal_id": "600", "date": "2026-08-09", "weight_g": "345",
         "baseline_g": "348", "water_regime": "ad_lib"},          # ad-lib, weighed
        {"animal_id": "600", "date": "2026-08-10", "weight_g": "338",
         "baseline_g": "348", "water_regime": "ad_lib", "water_off": "2pm"},
    ]

    def setUp(self):
        self.log = DailyLog(self._ROWS)

    def test_weight_status_flags_ad_lib(self):
        s = dashboard.weight_status(self.log, "600", "2026-08-08")
        self.assertTrue(s["ad_lib"])
        self.assertFalse(dashboard.weight_status(self.log, "600",
                                                 "2026-08-07")["ad_lib"])

    def test_ad_lib_day_suppresses_not_weighed_alarm(self):
        # 08-08 is ad-lib with no weight -> not an alarm; a plain no-row day is.
        out = dashboard.not_logged_today(self.log, ["600"], "2026-08-08")
        self.assertEqual(out, {})
        missing = dashboard.not_logged_today(self.log, ["600"], "2026-08-11")
        self.assertEqual(missing["600"], "not logged")

    def test_ad_lib_days_excluded_from_water_trend(self):
        series = dashboard.water_ml_trend(self.log, [], "600",
                                          "2026-08-10", days=14)
        self.assertEqual([r["date"] for r in series], ["2026-08-07"])

    def test_weight_trend_keeps_ad_lib_with_flag(self):
        series = dashboard.weight_trend(self.log, "600", "2026-08-10", days=14)
        dates = {r["date"]: r["ad_lib"] for r in series}
        # The ad-lib days that HAVE a weight still plot (08-08 has no weight).
        self.assertIn("2026-08-09", dates)
        self.assertTrue(dates["2026-08-09"])
        self.assertFalse(dates["2026-08-07"])


class TestExplicitStartWindow(unittest.TestCase):
    """weight_trend / water_ml_trend honour an explicit start_date override."""

    _ROWS = [
        {"animal_id": "600", "date": "2026-07-01", "weight_g": "340",
         "baseline_g": "348"},
        {"animal_id": "600", "date": "2026-07-05", "weight_g": "342",
         "baseline_g": "348"},
        {"animal_id": "600", "date": "2026-07-10", "weight_g": "345",
         "baseline_g": "348"},
    ]

    def setUp(self):
        self.log = DailyLog(self._ROWS)

    def test_start_date_overrides_days(self):
        # days would say 07-04..07-10, but the explicit start clips to 07-05.
        series = dashboard.weight_trend(self.log, "600", "2026-07-10", days=7,
                                        start_date="2026-07-05")
        self.assertEqual([r["date"] for r in series],
                         ["2026-07-05", "2026-07-10"])

    def test_water_trend_start_date_override(self):
        series = dashboard.water_ml_trend(
            DailyLog([{"animal_id": "600", "date": "2026-07-01", "water_ml": "5"},
                      {"animal_id": "600", "date": "2026-07-09", "water_ml": "7"}]),
            [], "600", "2026-07-10", days=3, start_date="2026-07-05")
        self.assertEqual([r["date"] for r in series], ["2026-07-09"])


class TestWeeklySpans(unittest.TestCase):
    # 2026-08-07 is a Friday; 08-08 Sat, 08-09 Sun, 08-10 Mon.
    _ROWS = [
        {"animal_id": "600", "date": "2026-08-08", "water_regime": "ad_lib"},
        {"animal_id": "600", "date": "2026-08-09", "water_regime": "ad_lib"},
        {"animal_id": "601", "date": "2026-08-06", "water_regime": "ad_lib"},  # Thu deviation
    ]

    def setUp(self):
        self.log = DailyLog(self._ROWS)

    def test_ad_lib_spans_contiguous_run(self):
        spans = dashboard.ad_lib_spans(self.log, ["600"],
                                       "2026-08-05", "2026-08-11")
        import datetime as dt
        self.assertEqual(spans, [(dt.date(2026, 8, 8), dt.date(2026, 8, 9))])

    def test_ad_lib_spans_union_across_rats_and_deviation(self):
        import datetime as dt
        spans = dashboard.ad_lib_spans(self.log, ["600", "601"],
                                       "2026-08-05", "2026-08-11")
        # 601's Thu (08-06) is separate; 600's Sat-Sun is its own run.
        self.assertEqual(spans, [(dt.date(2026, 8, 6), dt.date(2026, 8, 6)),
                                 (dt.date(2026, 8, 8), dt.date(2026, 8, 9))])

    def test_ad_lib_spans_empty_when_none_or_bad_range(self):
        self.assertEqual(dashboard.ad_lib_spans(self.log, ["602"],
                                                "2026-08-05", "2026-08-11"), [])
        self.assertEqual(dashboard.ad_lib_spans(self.log, ["600"],
                                                "2026-08-11", "2026-08-05"), [])

    def test_weekend_spans_runs_and_clamping(self):
        import datetime as dt
        spans = dashboard.weekend_spans("2026-08-05", "2026-08-11")
        self.assertEqual(spans, [(dt.date(2026, 8, 8), dt.date(2026, 8, 9))])
        # A window starting on Sunday clips the Sat-Sun run to just Sunday.
        self.assertEqual(dashboard.weekend_spans("2026-08-09", "2026-08-12"),
                         [(dt.date(2026, 8, 9), dt.date(2026, 8, 9))])


class TestTableHelpers(unittest.TestCase):
    _ROWS = [
        {"animal_id": "600", "date": "2026-08-07", "weight_g": "340"},
        {"animal_id": "600", "date": "2026-08-08", "weight_g": ""},   # unweighed gap
        {"animal_id": "600", "date": "2026-08-10", "weight_g": "346"},
    ]

    def setUp(self):
        self.log = DailyLog(self._ROWS)

    def test_weekday_label(self):
        self.assertEqual(dashboard.weekday_label("2026-08-08"), "Sat")
        self.assertEqual(dashboard.weekday_label("nope"), "")

    def test_weight_delta_skips_unweighed_prior(self):
        # 08-10 compares to the last real weight (08-07), skipping the blank 08-08.
        self.assertEqual(dashboard.weight_delta(self.log, "600", "2026-08-10"),
                         6.0)
        # No earlier weighed day -> None; unweighed day -> None.
        self.assertIsNone(dashboard.weight_delta(self.log, "600", "2026-08-07"))
        self.assertIsNone(dashboard.weight_delta(self.log, "600", "2026-08-08"))

    def test_missing_days_gaps_in_range(self):
        gaps = dashboard.missing_days(self.log, "600", "2026-08-07", "2026-08-10")
        self.assertEqual(gaps, ["2026-08-09"])   # 07,08,10 exist; 09 missing
        self.assertEqual(
            dashboard.missing_days(self.log, "600", "2026-08-10", "2026-08-07"),
            [])


if __name__ == "__main__":
    unittest.main()
