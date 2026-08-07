"""
test_daily_log.py - headless tests for the daily-husbandry-table reader/writer.

Covers the pure ``daily_log`` core (docs/decisions/0076/0077): skipping the
`# schema_version` comment line, the exact `(animal, date)` lookup, the
most-recent-on-or-before lookup, the tolerant load of a missing file, and the
Records Manager writer path (``compute_pct_baseline``, ``upsert_rows``,
``write_rows`` round-trip / atomic on-disk shape). Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_daily_log
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.records.daily_log import (
    DailyLog,
    compute_pct_baseline,
    load,
    load_rows,
    upsert_rows,
    write_rows,
)

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


class TestComputePctBaseline(unittest.TestCase):
    def test_fraction_rounded_4dp(self):
        self.assertEqual(compute_pct_baseline(330, 348), round(330 / 348, 4))

    def test_accepts_strings(self):
        self.assertEqual(compute_pct_baseline("332", "348"), round(332 / 348, 4))

    def test_blank_or_zero_or_bad_is_none(self):
        self.assertIsNone(compute_pct_baseline("", 348))
        self.assertIsNone(compute_pct_baseline(330, 0))
        self.assertIsNone(compute_pct_baseline(330, ""))
        self.assertIsNone(compute_pct_baseline("abc", 348))


class TestWriteRows(unittest.TestCase):
    def test_roundtrip_and_on_disk_shape(self):
        rows = [
            {"animal_id": "600", "date": "2026-07-02", "weight_g": "332",
             "baseline_g": "348", "pct_baseline": "0.954", "water_on": "6pm",
             "water_off": "7pm", "water_ml": "", "supp_water": "", "notes": ""},
            {"animal_id": "601", "date": "2026-07-02", "weight_g": "338",
             "baseline_g": "350", "pct_baseline": "0.9657", "water_on": "6pm",
             "water_off": "7pm", "water_ml": "", "supp_water": "", "notes": ""},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "daily_log.csv")
            write_rows(rows, path)
            with open(path, "r", encoding="utf-8", newline="") as fh:
                first = fh.readline()
            self.assertEqual(first, "# schema_version=1\n")
            back = load_rows(path)
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0]["animal_id"], "600")

    def test_dedup_last_wins_and_sorted(self):
        rows = [
            {"animal_id": "601", "date": "2026-07-02", "weight_g": "338"},
            {"animal_id": "600", "date": "2026-07-03", "weight_g": "330"},
            {"animal_id": "600", "date": "2026-07-02", "weight_g": "1"},
            {"animal_id": "600", "date": "2026-07-02", "weight_g": "332"},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "daily_log.csv")
            written = write_rows(rows, path)
        keys = [(r["animal_id"], r["date"]) for r in written]
        self.assertEqual(keys, [("600", "2026-07-02"),
                                ("600", "2026-07-03"),
                                ("601", "2026-07-02")])
        self.assertEqual(written[0]["weight_g"], "332")

    def test_creates_missing_parent_dir(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nested", "sub", "daily_log.csv")
            write_rows([{"animal_id": "600", "date": "2026-07-02"}], path)
            self.assertTrue(os.path.exists(path))


class TestUpsertRows(unittest.TestCase):
    def test_edit_replaces_matching_key(self):
        existing = load_rows_text(_CSV)
        edit = {"animal_id": "600", "date": "2026-07-03", "weight_g": "999",
                "notes": "corrected"}
        merged = upsert_rows(existing, [edit])
        target = [r for r in merged
                  if r["animal_id"] == "600" and r["date"] == "2026-07-03"]
        self.assertEqual(len(target), 1)
        self.assertEqual(target[0]["weight_g"], "999")
        self.assertEqual(target[0]["notes"], "corrected")

    def test_new_key_is_added_without_dropping_others(self):
        existing = load_rows_text(_CSV)
        add = {"animal_id": "602", "date": "2026-07-02", "weight_g": "300"}
        merged = upsert_rows(existing, [add])
        keys = {(r["animal_id"], r["date"]) for r in merged}
        self.assertIn(("602", "2026-07-02"), keys)
        self.assertIn(("600", "2026-07-02"), keys)
        self.assertIn(("601", "2026-07-02"), keys)
        self.assertEqual(len(merged), len(existing) + 1)

    def test_does_not_mutate_inputs(self):
        existing = load_rows_text(_CSV)
        before = len(existing)
        upsert_rows(existing, [{"animal_id": "600", "date": "2026-07-02",
                                "weight_g": "0"}])
        self.assertEqual(len(existing), before)


if __name__ == "__main__":
    unittest.main()
