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


class TestSuspectWaterMethods(unittest.TestCase):
    def test_flags_only_unknown_nonblank_methods(self):
        rows = [
            {"animal_id": "600", "date": "2026-08-07", "water_method": "task_earned"},
            {"animal_id": "600", "date": "2026-08-08", "water_method": "Bottle"},  # synonym ok
            {"animal_id": "600", "date": "2026-08-09", "water_method": ""},         # blank ok
            {"animal_id": "601", "date": "2026-08-09", "water_method": "hose"},     # unknown
        ]
        got = records_audit.suspect_water_methods(rows)
        self.assertEqual(got, [("601", "2026-08-09", "hose")])


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


class TestSessionTriples(unittest.TestCase):
    def test_registry_first_then_annot_only_skip_ignored_and_dups(self):
        registry = [{"session_id": "a", "animal": "600", "date": "2026-08-07"},
                    {"session_id": "dup", "animal": "601", "date": "2026-08-08"}]
        annot = [{"session_id": "dup", "animal": "601", "date": "x"},  # dup skip
                 {"session_id": "b", "animal": "602", "date": "2026-08-09"},
                 {"session_id": "ig", "animal": "600", "date": "2026-08-10"}]
        got = records_audit.session_triples(registry, annot, {"ig"})
        self.assertEqual(got, [
            ("a", "600", "2026-08-07"),
            ("dup", "601", "2026-08-08"),
            ("b", "602", "2026-08-09"),
        ])


class TestBuildReport(unittest.TestCase):
    def _sections_by_title(self, sections):
        return {s.title: s for s in sections}

    def test_flags_across_sections(self):
        reg = AnimalRegistry([{"id": "600"}])
        daily_rows = [{"animal_id": "600", "date": "2026-08-07",
                       "water_on": "8ml note", "water_off": "6pm"}]
        daily = DailyLog(daily_rows)
        triples = [("600_a", "600", "2026-08-07"),   # has daily
                   ("123_b", "123", "2026-08-09")]    # unknown + missing daily
        kept = [{"session_id": "600_a", "duration_s": "12"},   # short
                {"session_id": "600_a2", "duration_s": "900"}]
        sections = records_audit.build_report(
            triples, daily_rows, kept, daily, reg)
        by = self._sections_by_title(sections)
        self.assertIn("123", by["Animal ids not in animals.json"].items)
        self.assertEqual(
            records_audit.total_flagged(sections),
            sum(s.count for s in sections))
        self.assertGreaterEqual(records_audit.total_flagged(sections), 3)

    def test_skips_sections_when_sources_unavailable(self):
        sections = records_audit.build_report(
            [("s", "600", "2026-08-07")], [], [], DailyLog([]), AnimalRegistry())
        by = self._sections_by_title(sections)
        self.assertTrue(by["Animal ids not in animals.json"].skipped)
        self.assertEqual(records_audit.total_flagged(sections), 0)

    def test_findings_have_stable_section_prefixed_keys(self):
        reg = AnimalRegistry([{"id": "600"}])
        daily = DailyLog([{"animal_id": "600", "date": "2026-08-07"}])
        triples = [("123_b", "123", "2026-08-09")]
        sections = records_audit.build_report(triples, [], [], daily, reg)
        by = {s.id: s for s in sections}
        keys = [f.key for f in by["noncanonical_animals"].findings]
        self.assertEqual(keys, ["noncanonical_animals:123"])

    def test_flags_unknown_water_method_section(self):
        reg = AnimalRegistry([{"id": "600"}])
        daily_rows = [{"animal_id": "600", "date": "2026-08-07",
                       "water_method": "hose"}]
        daily = DailyLog(daily_rows)
        sections = records_audit.build_report(
            [("600_a", "600", "2026-08-07")], daily_rows, [], daily, reg)
        by = {s.id: s for s in sections}
        self.assertIn("suspect_water_methods", by)
        self.assertEqual([f.key for f in by["suspect_water_methods"].findings],
                         ["suspect_water_methods:600:2026-08-07"])

    def test_acknowledged_findings_are_partitioned_out(self):
        reg = AnimalRegistry([{"id": "600"}])
        daily = DailyLog([{"animal_id": "600", "date": "2026-08-07"}])
        triples = [("123_b", "123", "2026-08-09")]      # unknown + missing daily
        kept = [{"session_id": "600_a", "duration_s": "12"}]   # short
        acked = {"short_sessions:600_a"}
        sections = records_audit.build_report(
            triples, [], kept, daily, reg, acknowledged=acked)
        by = {s.id: s for s in sections}
        short = by["short_sessions"]
        self.assertEqual(short.count, 0)                 # dismissed from active
        self.assertEqual(short.acknowledged_count, 1)
        self.assertEqual(records_audit.total_acknowledged(sections), 1)
        # The still-active unknown-animal finding is untouched.
        self.assertEqual(by["noncanonical_animals"].count, 1)


if __name__ == "__main__":
    unittest.main()
