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
    require_operator,
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


class TestRequireOperator(unittest.TestCase):
    """ADR-0162: the builders' --operator check lives in this core."""

    def test_blank_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            require_operator("", "--export")
        self.assertIn("--operator", str(ctx.exception))
        self.assertIn("Nothing was written", str(ctx.exception))

    def test_listed_name_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, '{"schema_version": 1, "operators": ["RJ"]}')
            self.assertEqual(require_operator(" RJ ", "--export", path=p), "RJ")

    def test_unknown_name_is_refused_when_file_is_readable(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(d, '{"schema_version": 1, "operators": ["RJ"]}')
            with self.assertRaises(ValueError) as ctx:
                require_operator("XX", "--export", path=p)
            self.assertIn("XX", str(ctx.exception))

    def test_any_nonblank_accepted_when_file_is_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "absent.json")
            self.assertEqual(require_operator("CD", "--export", path=p), "CD")


if __name__ == "__main__":
    unittest.main()
