"""
test_records_audit_ack.py - headless tests for the acknowledged-findings store.

Covers the best-effort load, the atomic acknowledge/unacknowledge round-trip, the
tolerant parsing of a loose/legacy file, and the keys convenience. Pure stdlib;
no Tk. Run from host/tests:  python -m unittest test_records_audit_ack
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tmaze.core.records import records_audit_ack as ack


class TestLoad(unittest.TestCase):
    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ack.load_ack(os.path.join(d, "absent.json")), {})
            self.assertEqual(ack.load_acked_keys(os.path.join(d, "absent.json")),
                             set())

    def test_malformed_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            self.assertEqual(ack.load_ack(p), {})

    def test_non_dict_acknowledged_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"_schema": 1, "acknowledged": ["x"]}, fh)
            self.assertEqual(ack.load_ack(p), {})

    def test_tolerates_bare_string_value(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"acknowledged": {"k1": "just a reason"}}, fh)
            got = ack.load_ack(p)
            self.assertEqual(got["k1"], {"reason": "just a reason", "date": ""})


class TestAcknowledge(unittest.TestCase):
    def test_acknowledge_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "records_audit_ack.json")
            ack.acknowledge("short_sessions:600_a", "real short run",
                            path=p, today="2026-08-13")
            got = ack.load_ack(p)
            self.assertEqual(got, {"short_sessions:600_a":
                                   {"reason": "real short run",
                                    "date": "2026-08-13"}})
            self.assertIn("short_sessions:600_a", ack.load_acked_keys(p))

    def test_acknowledge_merges_and_defaults_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            ack.acknowledge("k1", "one", path=p, today="2026-08-13")
            ack.acknowledge("k2", path=p, today="2026-08-13")   # blank reason
            got = ack.load_ack(p)
            self.assertEqual(set(got), {"k1", "k2"})
            self.assertEqual(got["k2"]["reason"], "")
            self.assertEqual(got["k2"]["date"], "2026-08-13")

    def test_blank_key_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            ack.acknowledge("   ", "x", path=p)
            self.assertEqual(ack.load_ack(p), {})

    def test_written_file_has_schema_and_is_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            ack.acknowledge("b_key", path=p, today="2026-08-13")
            ack.acknowledge("a_key", path=p, today="2026-08-13")
            with open(p, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertEqual(raw["_schema"], ack.SCHEMA_VERSION)
            self.assertEqual(list(raw["acknowledged"]), ["a_key", "b_key"])


class TestUnacknowledge(unittest.TestCase):
    def test_unacknowledge_removes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            ack.acknowledge("k1", "one", path=p, today="2026-08-13")
            ack.acknowledge("k2", "two", path=p, today="2026-08-13")
            ack.unacknowledge("k1", path=p)
            self.assertEqual(set(ack.load_ack(p)), {"k2"})

    def test_unacknowledge_missing_key_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.json")
            ack.acknowledge("k1", path=p, today="2026-08-13")
            ack.unacknowledge("nope", path=p)
            self.assertEqual(set(ack.load_ack(p)), {"k1"})


if __name__ == "__main__":
    unittest.main()
