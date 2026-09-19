"""
test_records_changelog.py - headless tests for the append-only records change log.

Covers the pure ``records_changelog`` core (ADR-0155): ``diff_rows`` (changed /
added / removed rows, key handling, field ordering), ``append`` (creates the
file with comment + header, appends thereafter, one ts per call, never
rewrites), ``record`` (the two combined), ``load_entries`` (tolerates a partial
trailing line) and ``entries_since``. Pure stdlib; no hardware/Tk.

Run from host/tests:  python -m unittest test_records_changelog
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.records.records_changelog import (
    FIELDS,
    ROW_ADDED,
    ROW_MARKER,
    ROW_REMOVED,
    append,
    diff_rows,
    entries_since,
    load_entries,
    record,
    row_key,
)

KEY = ("animal_id", "date")


def _row(aid, date, **kw):
    r = {"animal_id": aid, "date": date, "weight_g": "", "notes": ""}
    r.update(kw)
    return r


class TestDiffRows(unittest.TestCase):
    def test_changed_fields_only(self):
        before = [_row("600", "2026-09-10", weight_g="400", notes="a")]
        after = [_row("600", "2026-09-10", weight_g="401", notes="a")]
        self.assertEqual(diff_rows(before, after, KEY),
                         [("600|2026-09-10", "weight_g", "400", "401")])

    def test_whitespace_only_difference_is_not_a_change(self):
        before = [_row("600", "2026-09-10", notes="a ")]
        after = [_row("600", "2026-09-10", notes="a")]
        self.assertEqual(diff_rows(before, after, KEY), [])

    def test_added_row_gets_marker_plus_non_blank_fields(self):
        after = [_row("601", "2026-09-10", weight_g="380")]
        self.assertEqual(diff_rows([], after, KEY), [
            ("601|2026-09-10", ROW_MARKER, "", ROW_ADDED),
            ("601|2026-09-10", "weight_g", "", "380"),
        ])

    def test_removed_row_gets_marker_plus_non_blank_fields(self):
        before = [_row("601", "2026-09-10", notes="gone")]
        self.assertEqual(diff_rows(before, [], KEY), [
            ("601|2026-09-10", ROW_MARKER, ROW_REMOVED, ""),
            ("601|2026-09-10", "notes", "gone", ""),
        ])

    def test_empty_added_row_still_logged_via_marker(self):
        self.assertEqual(diff_rows([], [_row("602", "2026-09-10")], KEY),
                         [("602|2026-09-10", ROW_MARKER, "", ROW_ADDED)])

    def test_fields_argument_restricts_and_orders(self):
        before = [_row("600", "2026-09-10", weight_g="1", notes="x")]
        after = [_row("600", "2026-09-10", weight_g="2", notes="y")]
        self.assertEqual(diff_rows(before, after, KEY, fields=["notes"]),
                         [("600|2026-09-10", "notes", "x", "y")])
        got = diff_rows(before, after, KEY, fields=["notes", "weight_g"])
        self.assertEqual([c[1] for c in got], ["notes", "weight_g"])

    def test_rows_without_key_are_ignored_and_output_sorted_by_key(self):
        before = [{"animal_id": "", "date": "", "notes": "junk"}]
        after = [_row("601", "2026-09-10", notes="b"),
                 _row("600", "2026-09-10", notes="a")]
        got = diff_rows(before, after, KEY)
        self.assertEqual([c[0] for c in got],
                         ["600|2026-09-10", "600|2026-09-10",
                          "601|2026-09-10", "601|2026-09-10"])

    def test_single_column_key(self):
        before = [{"session_id": "s1", "notes": "old"}]
        after = [{"session_id": "s1", "notes": "new"}]
        self.assertEqual(diff_rows(before, after, ("session_id",)),
                         [("s1", "notes", "old", "new")])
        self.assertEqual(row_key({"session_id": " s1 "}, ("session_id",)), "s1")


class TestAppendAndLoad(unittest.TestCase):
    def test_creates_file_with_header_then_appends(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "records_changes.csv")
            n = append([("600|2026-09-10", "weight_g", "400", "401")],
                       "RJ", "records_manager", "daily_log.csv", path=p,
                       ts="2026-09-11T10:00:00")
            self.assertEqual(n, 1)
            with open(p, "r", encoding="utf-8", newline="") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("# schema_version=1\n"
                                            + ",".join(FIELDS) + "\n"))
            self.assertNotIn("\r", text)
            append([("601|2026-09-10", "notes", "", "multi\nline")],
                   "RJ", "records_manager", "daily_log.csv", path=p,
                   ts="2026-09-11T10:05:00")
            with open(p, "r", encoding="utf-8", newline="") as fh:
                text2 = fh.read()
            # Append-only: the earlier content is a prefix of the later file.
            self.assertTrue(text2.startswith(text))
            entries = load_entries(p)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].operator, "RJ")
        self.assertEqual(entries[0].file, "daily_log.csv")
        self.assertEqual((entries[1].key, entries[1].new),
                         ("601|2026-09-10", "multi\nline"))

    def test_empty_changes_write_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "records_changes.csv")
            self.assertEqual(append([], "RJ", "x", "y", path=p), 0)
            self.assertFalse(os.path.exists(p))
            self.assertEqual(load_entries(p), [])

    def test_one_ts_per_call_and_default_ts_is_iso(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "records_changes.csv")
            append([("a", "f", "1", "2"), ("b", "f", "1", "2")], "RJ", "s",
                   "daily_log.csv", path=p)
            e = load_entries(p)
        self.assertEqual(e[0].ts, e[1].ts)
        # Offset-aware (docs/decisions/0182): trailing UTC offset (or Z) now present.
        self.assertRegex(
            e[0].ts,
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)$")

    def test_record_combines_diff_and_append(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "records_changes.csv")
            n = record([_row("600", "2026-09-10", weight_g="400")],
                       [_row("600", "2026-09-10", weight_g="402"),
                        _row("601", "2026-09-10")],
                       KEY, "RJ", "records_manager", "daily_log.csv", path=p)
            self.assertEqual(n, 2)
            keys = [(e.key, e.field) for e in load_entries(p)]
        self.assertEqual(keys, [("600|2026-09-10", "weight_g"),
                                ("601|2026-09-10", ROW_MARKER)])

    def test_partial_trailing_line_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "records_changes.csv")
            append([("a", "f", "1", "2")], "RJ", "s", "daily_log.csv", path=p,
                   ts="2026-09-11T10:00:00")
            with open(p, "a", encoding="utf-8", newline="") as fh:
                fh.write("2026-09-11T10:01:00,RJ,s")  # crash mid-line
            entries = load_entries(p)
        self.assertEqual(len(entries), 1)

    def test_entries_since(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "records_changes.csv")
            append([("a", "f", "1", "2")], "RJ", "s", "f", path=p,
                   ts="2026-09-11T10:00:00")
            append([("b", "f", "1", "2")], "RJ", "s", "f", path=p,
                   ts="2026-09-11T11:00:00")
            entries = load_entries(p)
        self.assertEqual([e.key for e in entries_since(entries, None)],
                         ["a", "b"])
        self.assertEqual([e.key for e in entries_since(
            entries, "2026-09-11T10:00:00")], ["b"])
        self.assertEqual(entries_since(entries, "2026-09-11T11:00:00"), [])


if __name__ == "__main__":
    unittest.main()
