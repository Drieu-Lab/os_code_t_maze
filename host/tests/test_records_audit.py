"""
test_records_audit.py - headless tests for the read-only records-audit core.

Covers the pure ``records_audit`` core (ADR-0097; bug #260810): the clock-time
recogniser + the four detectors (water cells that aren't times, sessions missing
a daily row, non-canonical animal ids, near-zero-duration churn). Pure stdlib;
no hardware/Tk.

Run from host/tests:  python -m unittest test_records_audit
"""

from __future__ import annotations

import unittest

from tmaze.core.records import records_audit
from tmaze.core.records.animals import AnimalRegistry
from tmaze.core.records.daily_log import DailyLog


class TestLooksLikeTime(unittest.TestCase):
    def test_accepts_clock_times(self):
        for good in ("4:30pm", "16:04", "3:15PM", "5:30", "6pm", "1 PM", " 6PM "):
            self.assertTrue(records_audit.looks_like_time(good), good)

    def test_rejects_non_times(self):
        for bad in ("", "8.46ml rig; No suppl", "Till Monday evening",
                    "Free water for week", "6", "600", "Till Monday 11:30AM"):
            self.assertFalse(records_audit.looks_like_time(bad), bad)


class TestSuspectWaterCells(unittest.TestCase):
    def test_flags_volume_and_freetext_only(self):
        rows = [
            {"animal_id": "600", "date": "2026-07-14",
             "water_on": "8.46ml rig; No suppl", "water_off": ""},
            {"animal_id": "601", "date": "2026-07-02",
             "water_on": "6pm", "water_off": "7pm"},          # both fine
            {"animal_id": "600", "date": "2026-07-10",
             "water_on": "4:30PM", "water_off": "Till Monday 11:30AM"},
        ]
        got = records_audit.suspect_water_cells(rows)
        self.assertEqual(got, [
            ("600", "2026-07-14", "water_on", "8.46ml rig; No suppl"),
            ("600", "2026-07-10", "water_off", "Till Monday 11:30AM"),
        ])


class TestSessionsMissingDaily(unittest.TestCase):
    def setUp(self):
        self.daily = DailyLog([
            {"animal_id": "600", "date": "2026-08-07"},
        ])

    def test_flags_only_joinable_misses(self):
        triples = [
            ("600_a", "600", "2026-08-07"),   # present -> ok
            ("600_b", "600", "2026-08-10"),   # missing
            ("blank", "", ""),                # unjoinable -> skipped
        ]
        self.assertEqual(records_audit.sessions_missing_daily(triples, self.daily),
                         [("600_b", "600", "2026-08-10")])

    def test_none_daily_flags_all_joinable(self):
        triples = [("600_a", "600", "2026-08-07"), ("x", "", "2026-01-01")]
        self.assertEqual(records_audit.sessions_missing_daily(triples, None),
                         [("600_a", "600", "2026-08-07")])


class TestNoncanonicalAnimals(unittest.TestCase):
    def test_returns_unknown_sorted_distinct(self):
        reg = AnimalRegistry([{"id": "600", "aliases": ["Rat 600"]},
                              {"id": "601"}])
        got = records_audit.noncanonical_animals(
            ["600", "Rat 600", "123", "000", "123", "601"], reg)
        self.assertEqual(got, ["000", "123"])

    def test_unavailable_registry_warns_nothing(self):
        self.assertEqual(
            records_audit.noncanonical_animals(["123"], AnimalRegistry()), [])


class TestShortSessions(unittest.TestCase):
    def test_flags_sub_threshold_numeric_only(self):
        rows = [
            {"session_id": "s_short", "duration_s": "12"},
            {"session_id": "s_zero", "duration_s": "0"},
            {"session_id": "s_long", "duration_s": "300"},
            {"session_id": "s_blank", "duration_s": ""},
            {"session_id": "s_bad", "duration_s": "abc"},
        ]
        got = records_audit.short_sessions(rows, threshold_s=60)
        self.assertEqual(got, [("s_short", 12.0), ("s_zero", 0.0)])


if __name__ == "__main__":
    unittest.main()
