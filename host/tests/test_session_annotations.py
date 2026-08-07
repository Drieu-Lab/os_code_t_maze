"""
test_session_annotations.py - headless tests for the per-session annotations core.

Covers the pure ``session_annotations`` core (ADR-0082): skipping the
`# schema_version` comment line, the ``session_id`` lookup + whole-table map,
the tolerant load of a missing file, and the Records Manager writer path
(``upsert_rows`` keyed by session_id, ``write_rows`` round-trip / on-disk shape /
skipping keyless rows). Pure stdlib; no hardware/Tk.

Run from host/tests:  python -m unittest test_session_annotations
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.records.session_annotations import (
    SessionAnnotations,
    load,
    load_rows,
    upsert_rows,
    write_rows,
)

_CSV = (
    "# schema_version=1\n"
    "session_id,animal,date,procedures,notes\n"
    "600_2026-08-05_1400,600,2026-08-05,weighed; probe check,good session\n"
    "601_2026-08-05_1500,601,2026-08-05,,quiet\n"
)


def _write(d, text=_CSV):
    path = os.path.join(d, "session_annotations.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def load_rows_text(text):
    with tempfile.TemporaryDirectory() as d:
        return load_rows(_write(d, text))


class TestLoad(unittest.TestCase):
    def test_skips_comment_and_reads_rows(self):
        with tempfile.TemporaryDirectory() as d:
            rows = load_rows(_write(d))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["session_id"], "600_2026-08-05_1400")
        self.assertEqual(rows[0]["procedures"], "weighed; probe check")

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            store = load(os.path.join(d, "absent.csv"))
        self.assertFalse(store.available)
        self.assertEqual(len(store), 0)


class TestLookups(unittest.TestCase):
    def setUp(self):
        self.store = SessionAnnotations(load_rows_text(_CSV))

    def test_for_session_hit(self):
        row = self.store.for_session("600_2026-08-05_1400")
        self.assertIsNotNone(row)
        self.assertEqual(row["notes"], "good session")

    def test_for_session_miss(self):
        self.assertIsNone(self.store.for_session("nope"))

    def test_as_map(self):
        m = self.store.as_map()
        self.assertEqual(set(m), {"600_2026-08-05_1400", "601_2026-08-05_1500"})
        self.assertEqual(m["601_2026-08-05_1500"]["notes"], "quiet")


class TestWriteAndUpsert(unittest.TestCase):
    def test_roundtrip_and_on_disk_shape(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "session_annotations.csv")
            write_rows(load_rows_text(_CSV), path)
            with open(path, "r", encoding="utf-8", newline="") as fh:
                first = fh.readline()
            self.assertEqual(first, "# schema_version=1\n")
            back = load_rows(path)
        self.assertEqual(len(back), 2)

    def test_sorted_by_session_id(self):
        rows = [
            {"session_id": "601_b"}, {"session_id": "600_a"},
            {"session_id": "600_a", "notes": "later wins"},
        ]
        with tempfile.TemporaryDirectory() as d:
            written = write_rows(rows, os.path.join(d, "a.csv"))
        self.assertEqual([r["session_id"] for r in written], ["600_a", "601_b"])
        self.assertEqual(written[0]["notes"], "later wins")

    def test_keyless_rows_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            written = write_rows([{"session_id": "", "notes": "orphan"},
                                  {"session_id": "x", "notes": "keep"}],
                                 os.path.join(d, "a.csv"))
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["session_id"], "x")

    def test_upsert_replaces_and_adds(self):
        existing = load_rows_text(_CSV)
        edits = [
            {"session_id": "600_2026-08-05_1400", "notes": "edited"},
            {"session_id": "602_new", "animal": "602", "notes": "brand new"},
        ]
        merged = upsert_rows(existing, edits)
        by = {r["session_id"]: r for r in merged}
        self.assertEqual(by["600_2026-08-05_1400"]["notes"], "edited")
        self.assertEqual(by["602_new"]["notes"], "brand new")
        self.assertIn("601_2026-08-05_1500", by)
        self.assertEqual(len(merged), 3)


if __name__ == "__main__":
    unittest.main()
