"""
test_records_csv.py - headless tests for the shared records-CSV reader + guard.

Covers ``tmaze.core.records.read_commented_csv`` (the ONE parser for the
``# schema_version``-prefixed CSVs: leading comments only, header, rows) and
``check_header`` (the write-path guard that refuses a renamed/reordered/unknown
column or a newer schema; ADR-0155). Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_records_csv
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.records import check_header, read_commented_csv


def _write(d, text, name="t.csv"):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


class TestReadCommentedCsv(unittest.TestCase):
    def test_parses_schema_header_rows_and_keeps_comments(self):
        text = ("# schema_version=3\n"
                "# hand note\n"
                "a,b\n"
                " 1 , x \n")
        with tempfile.TemporaryDirectory() as d:
            parsed = read_commented_csv(_write(d, text))
        self.assertEqual(parsed.schema_version, 3)
        self.assertEqual(parsed.comments, ["# schema_version=3", "# hand note"])
        self.assertEqual(parsed.header, ["a", "b"])
        self.assertEqual(parsed.rows, [{"a": "1", "b": "x"}])

    def test_no_schema_line(self):
        with tempfile.TemporaryDirectory() as d:
            parsed = read_commented_csv(_write(d, "a,b\n1,2\n"))
        self.assertIsNone(parsed.schema_version)
        self.assertEqual(parsed.rows, [{"a": "1", "b": "2"}])

    def test_empty_and_comment_only_files(self):
        with tempfile.TemporaryDirectory() as d:
            empty = read_commented_csv(_write(d, "", "e.csv"))
            only = read_commented_csv(_write(d, "# schema_version=1\n", "c.csv"))
        self.assertEqual((empty.header, empty.rows), ([], []))
        self.assertEqual(only.schema_version, 1)
        self.assertEqual((only.header, only.rows), ([], []))

    def test_hash_inside_quoted_cell_is_data(self):
        text = ("# schema_version=1\n"
                "k,notes\n"
                "1,\"top\n# not a comment\"\n"
                "2,plain\n")
        with tempfile.TemporaryDirectory() as d:
            parsed = read_commented_csv(_write(d, text))
        self.assertEqual([r["k"] for r in parsed.rows], ["1", "2"])
        self.assertEqual(parsed.rows[0]["notes"], "top\n# not a comment")

    def test_crlf_files_read_the_same(self):
        text = "# schema_version=2\r\na,b\r\n1,2\r\n"
        with tempfile.TemporaryDirectory() as d:
            parsed = read_commented_csv(_write(d, text))
        self.assertEqual(parsed.schema_version, 2)
        self.assertEqual(parsed.comments, ["# schema_version=2"])
        self.assertEqual(parsed.rows, [{"a": "1", "b": "2"}])

    def test_missing_file_raises_oserror(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(OSError):
                read_commented_csv(os.path.join(d, "absent.csv"))


class TestCheckHeader(unittest.TestCase):
    FIELDS = ["k", "a", "b", "c"]

    def test_exact_and_prefix_headers_are_safe(self):
        self.assertEqual(check_header(self.FIELDS, self.FIELDS, 3, 3), [])
        self.assertEqual(check_header(["k", "a"], self.FIELDS, 1, 3), [])
        self.assertEqual(check_header([], self.FIELDS, None, 3), [])

    def test_renamed_reordered_or_extra_columns_are_flagged(self):
        for hdr in (["k", "A", "b", "c"], ["a", "k", "b", "c"],
                    ["k", "a", "b", "c", "zz"], ["k", "b"]):
            problems = check_header(hdr, self.FIELDS, 3, 3)
            self.assertEqual(len(problems), 1, hdr)
            self.assertIn("header", problems[0])

    def test_newer_schema_is_flagged_and_older_or_none_is_not(self):
        self.assertEqual(check_header(self.FIELDS, self.FIELDS, None, 3), [])
        self.assertEqual(check_header(self.FIELDS, self.FIELDS, 1, 3), [])
        problems = check_header(self.FIELDS, self.FIELDS, 4, 3)
        self.assertEqual(len(problems), 1)
        self.assertIn("schema_version=4", problems[0])

    def test_header_whitespace_is_ignored(self):
        self.assertEqual(check_header([" k", "a ", "b", "c"], self.FIELDS, 3, 3),
                         [])


if __name__ == "__main__":
    unittest.main()
