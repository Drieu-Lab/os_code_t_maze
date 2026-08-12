"""
test_session_ignore.py - headless tests for the session-exclusion list core.

Covers the pure ``session_ignore`` core (ADR-0097): loading the ``ignore`` list
best-effort (trimming + dropping blanks + de-duping), and degrading to an empty
set for a missing / malformed / wrong-shaped file so the pipeline just excludes
nothing. Pure stdlib; no hardware/Tk.

Run from host/tests:  python -m unittest test_session_ignore
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tmaze.core.records import session_ignore


def _write(d, obj):
    p = os.path.join(d, "session_ignore.json")
    with open(p, "w", encoding="utf-8") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)
    return p


class TestLoadIgnored(unittest.TestCase):
    def test_reads_trims_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, {"schema_version": 1,
                           "ignore": ["a", " b ", "", "a", "  "]})
            self.assertEqual(session_ignore.load_ignored(p), {"a", "b"})

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            got = session_ignore.load_ignored(os.path.join(d, "absent.json"))
        self.assertEqual(got, set())

    def test_malformed_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(session_ignore.load_ignored(_write(d, "not json{")),
                             set())

    def test_missing_key_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                session_ignore.load_ignored(_write(d, {"schema_version": 1})),
                set())

    def test_non_list_ignore_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                session_ignore.load_ignored(_write(d, {"ignore": "a,b"})), set())

    def test_default_path_points_at_host_file(self):
        self.assertTrue(session_ignore.default_ignore_path().endswith(
            os.path.join("host", "session_ignore.json")))


if __name__ == "__main__":
    unittest.main()
