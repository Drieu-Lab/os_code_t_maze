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

from tmaze.core.records import RecordsFileError
from tmaze.core.records.daily_log import (
    REGIME_AD_LIB,
    WATER_METHODS,
    DailyLog,
    compute_pct_baseline,
    is_ad_lib,
    load,
    load_rows,
    load_rows_for_write,
    normalize_method,
    normalize_regime,
    row_has_weight,
    upsert_rows,
    validate_entry,
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


class TestLoadForWrite(unittest.TestCase):
    """The strict reader the Save path uses to avoid clobbering (#12)."""

    def test_absent_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_rows_for_write(os.path.join(d, "absent.csv")),
                             [])

    def test_present_file_reads_rows(self):
        with tempfile.TemporaryDirectory() as d:
            rows = load_rows_for_write(_write(d))
        self.assertEqual(len(rows), 3)

    def test_present_but_unreadable_raises(self):
        # Bytes that aren't valid UTF-8 -> a decode error on read. The strict
        # reader must raise (so the save aborts) rather than return [].
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "daily_log.csv")
            with open(path, "wb") as fh:
                fh.write(b"# schema_version=1\n\xff\xfe garbage not utf-8\n")
            with self.assertRaises(RecordsFileError):
                load_rows_for_write(path)

    # --------------------------------------------- header/schema guard (ADR-0155)
    def test_renamed_header_column_is_refused(self):
        # Before the guard, a renamed column read as blank for every row and ONE
        # save then wrote that blank back for the whole column.
        bad = _CSV.replace("weight_g", "Weight_g")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RecordsFileError) as cm:
                load_rows_for_write(_write(d, bad))
        self.assertIn("Weight_g", str(cm.exception))

    def test_reordered_header_is_refused(self):
        bad = _CSV.replace("animal_id,date,", "date,animal_id,")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RecordsFileError):
                load_rows_for_write(_write(d, bad))

    def test_unknown_extra_column_is_refused(self):
        # A column this code doesn't know would be dropped on rewrite.
        bad = _CSV.replace(",notes\n", ",notes,extra\n")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RecordsFileError):
                load_rows_for_write(_write(d, bad))

    def test_newer_schema_version_is_refused(self):
        bad = _CSV.replace("# schema_version=1", "# schema_version=99")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RecordsFileError) as cm:
                load_rows_for_write(_write(d, bad))
        self.assertIn("schema_version=99", str(cm.exception))

    def test_older_prefix_header_is_accepted(self):
        # The v1 fixture lacks water_regime/water_method: that's the documented
        # upgrade path (appended columns read as "" and are written back).
        with tempfile.TemporaryDirectory() as d:
            rows = load_rows_for_write(_write(d))
        self.assertEqual(rows[0]["water_method"], "")

    def test_missing_schema_line_is_tolerated(self):
        text = _CSV.split("\n", 1)[1]  # drop the comment line
        with tempfile.TemporaryDirectory() as d:
            rows = load_rows_for_write(_write(d, text))
        self.assertEqual(len(rows), 3)

    def test_hash_line_inside_quoted_notes_is_kept(self):
        # A multi-line notes cell whose second line starts with '#': the old
        # reader dropped that line as a "comment" and desynchronised the row.
        text = (
            "# schema_version=1\n"
            "animal_id,date,weight_g,baseline_g,pct_baseline,water_on,water_off,water_ml,supp_water,notes\n"
            "600,2026-07-02,332,348,0.954,6pm,7pm,,,\"line one\n#2 cage moved\"\n"
            "601,2026-07-02,338,350,0.9657,6pm,7pm,,,\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = _write(d, text)
            strict = load_rows_for_write(path)
            lenient = load_rows(path)
        for rows in (strict, lenient):
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["notes"], "line one\n#2 cage moved")
            self.assertEqual(rows[1]["animal_id"], "601")


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


class TestRowHasWeight(unittest.TestCase):
    """Session Start only prefills from a daily row that has a real weight (#4)."""

    def test_none_row_is_false(self):
        self.assertFalse(row_has_weight(None))

    def test_blank_weight_is_false(self):
        self.assertFalse(row_has_weight({"weight_g": ""}))
        self.assertFalse(row_has_weight({"weight_g": "   "}))
        self.assertFalse(row_has_weight({"baseline_g": "348"}))

    def test_non_numeric_weight_is_false(self):
        self.assertFalse(row_has_weight({"weight_g": "tbd"}))

    def test_numeric_weight_is_true(self):
        self.assertTrue(row_has_weight({"weight_g": "332"}))
        self.assertTrue(row_has_weight({"weight_g": "330.5"}))


class TestValidateEntry(unittest.TestCase):
    """Hard save/export validation shared by the GUI and the Excel round-trip."""

    def _row(self, **over):
        row = {"animal_id": "600", "date": "2026-08-11", "weight_g": "332",
               "baseline_g": "348", "water_ml": "8.5"}
        row.update(over)
        return row

    def test_valid_row_has_no_errors(self):
        self.assertEqual(validate_entry(self._row()), [])

    def test_blank_numeric_cells_are_allowed(self):
        # An incomplete row (no weight/water yet) is legitimate.
        row = self._row(weight_g="", baseline_g="", water_ml="")
        self.assertEqual(validate_entry(row), [])

    def test_missing_animal_and_date_required(self):
        errors = validate_entry({"animal_id": "", "date": ""})
        self.assertTrue(any("Animal id" in e for e in errors))
        self.assertTrue(any("Date is required" in e for e in errors))

    def test_non_iso_date_is_rejected(self):
        self.assertTrue(any("YYYY-MM-DD" in e
                            for e in validate_entry(self._row(date="2026-8-1"))))
        self.assertTrue(any("YYYY-MM-DD" in e
                            for e in validate_entry(self._row(date="08/11/2026"))))

    def test_non_numeric_weight_is_rejected(self):
        errors = validate_entry(self._row(weight_g="tbd"))
        self.assertTrue(any("Weight" in e and "number" in e for e in errors))

    def test_negative_values_are_rejected(self):
        errors = validate_entry(self._row(baseline_g="-5", water_ml="-1"))
        self.assertTrue(any("Baseline" in e for e in errors))
        self.assertTrue(any("Water" in e for e in errors))

    def test_supp_water_stays_free_text(self):
        # supp_water is not a hard-numeric field (free text like "2 min").
        self.assertEqual(validate_entry(self._row(supp_water="2 min")), [])

    def test_blank_and_known_regimes_are_allowed(self):
        for regime in ("", "restriction", "ad_lib", "Ad-Lib", "adlib"):
            self.assertEqual(validate_entry(self._row(water_regime=regime)), [],
                             f"regime {regime!r} should be accepted")

    def test_unknown_regime_is_rejected(self):
        errors = validate_entry(self._row(water_regime="weekend?"))
        self.assertTrue(any("regime" in e.lower() for e in errors))

    def test_blank_and_known_methods_are_allowed(self):
        for method in ("", "timed_access", "measured_bottle", "task_earned",
                       "bottle", "Task", "timed"):
            self.assertEqual(validate_entry(self._row(water_method=method)), [],
                             f"method {method!r} should be accepted")

    def test_unknown_method_is_rejected(self):
        errors = validate_entry(self._row(water_method="hose?"))
        self.assertTrue(any("method" in e.lower() for e in errors))


class TestWaterRegime(unittest.TestCase):
    """ad-lib (off-restriction) day classification (ADR-0100 / FW-0030)."""

    def test_normalize_regime_folds_variants(self):
        for value in ("ad_lib", "ad-lib", "Ad Lib", "ADLIB"):
            self.assertEqual(normalize_regime(value), REGIME_AD_LIB)
        for value in ("", "restriction", "normal", "weird", None):
            self.assertEqual(normalize_regime(value), "")

    def test_is_ad_lib(self):
        self.assertTrue(is_ad_lib({"water_regime": "ad_lib"}))
        self.assertTrue(is_ad_lib({"water_regime": "Ad-Lib"}))
        self.assertFalse(is_ad_lib({"water_regime": ""}))
        self.assertFalse(is_ad_lib({"water_regime": "restriction"}))
        self.assertFalse(is_ad_lib(None))
        self.assertFalse(is_ad_lib({}))

    def test_roundtrip_preserves_regime(self):
        rows = [{"animal_id": "600", "date": "2026-08-08",
                 "water_regime": "ad_lib"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "daily_log.csv")
            write_rows(rows, path)
            back = load_rows(path)
        self.assertTrue(is_ad_lib(back[0]))

    def test_v1_csv_without_column_reads_as_restriction(self):
        # A pre-v2 file (no water_regime column) still loads; the field is "".
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "daily_log.csv")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(_CSV)
            back = load_rows(path)
        self.assertFalse(any(is_ad_lib(r) for r in back))
        self.assertEqual(back[0].get("water_regime"), "")


class TestWaterMethod(unittest.TestCase):
    """Water-delivery method classification (schema v3, ADR-0104)."""

    def test_normalize_method_folds_variants(self):
        cases = {
            "timed_access": ("timed_access", "timed", "Timed-Access", "timed adlib"),
            "measured_bottle": ("measured_bottle", "measured", "Bottle"),
            "task_earned": ("task_earned", "task", "Earned", "task water"),
        }
        for canonical, variants in cases.items():
            for value in variants:
                self.assertEqual(normalize_method(value), canonical,
                                 f"{value!r} -> {canonical}")

    def test_normalize_method_blank_or_unknown_is_empty(self):
        for value in ("", "  ", "hose", "whatever", None):
            self.assertEqual(normalize_method(value), "")

    def test_methods_vocab_is_canonical(self):
        for m in WATER_METHODS:
            self.assertEqual(normalize_method(m), m)

    def test_roundtrip_preserves_method_and_is_v3(self):
        rows = [{"animal_id": "600", "date": "2026-08-08",
                 "water_method": "task_earned"}]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "daily_log.csv")
            write_rows(rows, path)
            with open(path, "r", encoding="utf-8", newline="") as fh:
                first = fh.readline()
            back = load_rows(path)
        self.assertEqual(first, "# schema_version=3\n")
        self.assertEqual(back[0].get("water_method"), "task_earned")

    def test_v2_csv_without_method_column_reads_blank(self):
        # A pre-v3 file (has water_regime but no water_method) still loads.
        csv_v2 = (
            "# schema_version=2\n"
            "animal_id,date,weight_g,baseline_g,pct_baseline,water_on,"
            "water_off,water_ml,supp_water,notes,water_regime\n"
            "600,2026-08-08,,,,,,,,,ad_lib\n"
        )
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "daily_log.csv")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(csv_v2)
            back = load_rows(path)
        self.assertEqual(back[0].get("water_method"), "")
        self.assertTrue(is_ad_lib(back[0]))


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
            self.assertEqual(first, "# schema_version=3\n")
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
