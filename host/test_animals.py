"""
test_animals.py - headless tests for the canonical animal-id registry.

Covers the pure ``animals`` core (docs/decisions/0075): loose normalization
(case / spaces / punctuation), aliases, the ``available`` flag that distinguishes
an empty registry from a known id, and the tolerant load of a missing / malformed
file. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_animals
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.records.animals import (
    AnimalRegistry, animals_json_path, load_registry, parse_registry,
    resources_root)

_SAMPLE = (
    '{"schema_version": 1, "animals": ['
    '{"id": "600", "aliases": ["Rat 600"]},'
    '{"id": "601", "aliases": ["Rat 601"]},'
    '{"id": "602"}]}'
)


class TestNormalize(unittest.TestCase):
    def setUp(self):
        self.reg = parse_registry(_SAMPLE)

    def test_exact_id(self):
        self.assertEqual(self.reg.normalize("600"), "600")

    def test_loose_match_spaces_and_case(self):
        self.assertEqual(self.reg.normalize("  Rat 600 "), "600")
        self.assertEqual(self.reg.normalize("rat600"), "600")

    def test_alias_maps_to_canonical(self):
        self.assertEqual(self.reg.normalize("RAT 601"), "601")

    def test_unknown_is_none(self):
        self.assertIsNone(self.reg.normalize("6000"))
        self.assertIsNone(self.reg.normalize("60O"))   # letter O typo

    def test_blank_is_none(self):
        self.assertIsNone(self.reg.normalize(""))
        self.assertIsNone(self.reg.normalize("   "))

    def test_is_known(self):
        self.assertTrue(self.reg.is_known("602"))
        self.assertFalse(self.reg.is_known("999"))

    def test_ids_in_order(self):
        self.assertEqual(self.reg.ids(), ["600", "601", "602"])

    def test_available(self):
        self.assertTrue(self.reg.available)


class TestTolerantParse(unittest.TestCase):
    def test_empty_registry_unavailable(self):
        reg = AnimalRegistry()
        self.assertFalse(reg.available)
        self.assertIsNone(reg.normalize("600"))
        self.assertEqual(len(reg), 0)

    def test_malformed_json_is_empty(self):
        self.assertFalse(parse_registry("not json {").available)

    def test_non_dict_is_empty(self):
        self.assertFalse(parse_registry("[1, 2, 3]").available)

    def test_missing_animals_list_is_empty(self):
        self.assertFalse(parse_registry('{"schema_version": 1}').available)

    def test_skips_dupes_and_blank_ids(self):
        reg = parse_registry(
            '{"animals": [{"id": "600"}, {"id": "600"}, {"id": ""}, {"id": "601"}]}')
        self.assertEqual(reg.ids(), ["600", "601"])


class TestLoad(unittest.TestCase):
    def test_missing_file_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            reg = load_registry(os.path.join(d, "absent.json"))
        self.assertFalse(reg.available)

    def test_round_trip_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_SAMPLE)
            reg = load_registry(path)
        self.assertEqual(reg.normalize("Rat 601"), "601")
        self.assertEqual(reg.normalize(" 602 "), "602")


class TestResolver(unittest.TestCase):
    def test_env_override(self):
        prev = os.environ.get("OS_CODE_T_MAZE_RESOURCES")
        os.environ["OS_CODE_T_MAZE_RESOURCES"] = os.path.join("X", "res")
        try:
            self.assertEqual(resources_root(), os.path.join("X", "res"))
            self.assertTrue(animals_json_path().endswith(
                os.path.join("records", "animal", "animals.json")))
        finally:
            if prev is None:
                del os.environ["OS_CODE_T_MAZE_RESOURCES"]
            else:
                os.environ["OS_CODE_T_MAZE_RESOURCES"] = prev


if __name__ == "__main__":
    unittest.main()
