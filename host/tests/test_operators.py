"""
test_operators.py - headless tests for the operator list (host/operators.json).

Covers ``tmaze.core.records.operators`` (ADR-0155): best-effort load (missing /
malformed / wrong shape -> []), trimming + de-duplication, ``is_operator``, and
that the committed ``host/operators.json`` parses and is non-empty.

Run from host/tests:  python -m unittest test_operators
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.records.operators import (
    default_operators_path,
    is_operator,
    load_operators,
)


def _write(d, text):
    p = os.path.join(d, "operators.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


class TestLoadOperators(unittest.TestCase):
    def test_reads_trims_and_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            ops = load_operators(_write(
                d, '{"schema_version": 1, "operators": [" RJ", "CD", "RJ", ""]}'))
        self.assertEqual(ops, ["RJ", "CD"])

    def test_missing_or_malformed_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_operators(os.path.join(d, "absent.json")), [])
            self.assertEqual(load_operators(_write(d, "{not json")), [])
            self.assertEqual(load_operators(_write(d, '["RJ"]')), [])
            self.assertEqual(load_operators(_write(d, '{"operators": "RJ"}')), [])

    def test_is_operator(self):
        self.assertTrue(is_operator(" RJ ", ["RJ"]))
        self.assertFalse(is_operator("", ["RJ"]))
        self.assertFalse(is_operator("XX", ["RJ"]))

    def test_committed_file_parses(self):
        ops = load_operators(default_operators_path())
        self.assertIn("RJ", ops)


if __name__ == "__main__":
    unittest.main()
