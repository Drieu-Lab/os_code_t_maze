"""
test_daily_log.py - headless tests for the daily-husbandry-table reader.

Covers the pure ``daily_log`` core (docs/decisions/0076/0077): skipping the
`# schema_version` comment line, the exact `(animal, date)` lookup, the
most-recent-on-or-before lookup, and the tolerant load of a missing file. Pure
stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_daily_log
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.records.daily_log import DailyLog, load, load_rows

_CSV = (
    "# schema_version=1\n"
    "animal_id,date,weight_g,baseline_g,pct_baseline,water_on,water_off,water_ml,supp_water,notes\n"
    "600,2026-07-02,332,348,0.954,6pm,7pm,,,\n"
    "600,2026-07-03,330,348,0.9483,5:30pm,Till Monday,,,a note\n"
    "601,2026-07-02,338,350,0.9657,6pm,7pm,,,\n"
)


def _write(d, text=_CSV):
    path = os.path.join(d, "daily_log.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


class TestLoad(unittest.TestCase):
    def test_skips_comment_line_and_reads_rows(self):
        with tempfile.TemporaryDirectory() as d:
            rows = load_rows(_write(d))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["animal_id"], "600")
        self.assertEqual(rows[0]["weight_g"], "332")

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            log = load(os.path.join(d, "absent.csv"))
        self.assertFalse(log.available)
        self.assertEqual(len(log), 0)


class TestLookups(unittest.TestCase):
    def setUp(self):
        self.log = DailyLog(load_rows_text(_CSV))

    def test_for_date_exact(self):
        row = self.log.for_date("600", "2026-07-03")
        self.assertIsNotNone(row)
        self.assertEqual(row["weight_g"], "330")
        self.assertEqual(row["notes"], "a note")

    def test_for_date_miss(self):
        self.assertIsNone(self.log.for_date("600", "2026-07-04"))
        self.assertIsNone(self.log.for_date("999", "2026-07-02"))

    def test_latest_for_overall(self):
        row = self.log.latest_for("600")
        self.assertEqual(row["date"], "2026-07-03")

    def test_latest_for_on_or_before(self):
        row = self.log.latest_for("600", on_or_before="2026-07-02")
        self.assertEqual(row["date"], "2026-07-02")

    def test_latest_for_unknown_animal(self):
        self.assertIsNone(self.log.latest_for("999"))


def load_rows_text(text):
    """Helper: parse CSV text through the same path as load_rows (via a temp file)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "daily_log.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return load_rows(path)


if __name__ == "__main__":
    unittest.main()
