"""
test_note_tags.py - headless tests for the user-defined note-tag vocabulary.

Covers the pure ``note_tags`` core (docs/decisions/0074): add / rename / delete,
the counter flag, case-insensitive de-duplication, JSON round-trip, and the
tolerant load of a missing / malformed file. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_note_tags
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from note_tags import (
    DuplicateTagError, TagVocabulary, load_tags, save_tags)


class TestMutations(unittest.TestCase):
    def test_add_and_names_in_order(self):
        v = TagVocabulary()
        v.add("non-stem dissuasion")
        v.add("lift-backs", counter=True)
        self.assertEqual(v.names(), ["non-stem dissuasion", "lift-backs"])
        self.assertTrue(v.is_counter("lift-backs"))
        self.assertFalse(v.is_counter("non-stem dissuasion"))

    def test_add_empty_rejected(self):
        v = TagVocabulary()
        with self.assertRaises(ValueError):
            v.add("   ")

    def test_add_duplicate_case_insensitive_rejected(self):
        v = TagVocabulary()
        v.add("Groom")
        with self.assertRaises(DuplicateTagError):
            v.add("groom")

    def test_rename_preserves_order_and_counter(self):
        v = TagVocabulary()
        v.add("a")
        v.add("b", counter=True)
        v.rename("b", "b2")
        self.assertEqual(v.names(), ["a", "b2"])
        self.assertTrue(v.is_counter("b2"))

    def test_rename_missing_raises(self):
        v = TagVocabulary()
        with self.assertRaises(KeyError):
            v.rename("nope", "x")

    def test_rename_collision_raises(self):
        v = TagVocabulary()
        v.add("a")
        v.add("b")
        with self.assertRaises(DuplicateTagError):
            v.rename("b", "A")

    def test_set_counter(self):
        v = TagVocabulary()
        v.add("x")
        v.set_counter("x", True)
        self.assertTrue(v.is_counter("x"))

    def test_delete(self):
        v = TagVocabulary()
        v.add("a")
        self.assertTrue(v.delete("A"))          # case-insensitive
        self.assertEqual(v.names(), [])
        self.assertFalse(v.delete("a"))


class TestPersistence(unittest.TestCase):
    def test_round_trip(self):
        v = TagVocabulary()
        v.add("non-stem", counter=False)
        v.add("lift-backs", counter=True)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "note_tags.json")
            save_tags(path, v)
            back = load_tags(path)
        self.assertEqual(back.tags(), v.tags())

    def test_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            v = load_tags(os.path.join(d, "absent.json"))
        self.assertEqual(len(v), 0)

    def test_malformed_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "note_tags.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json {")
            self.assertEqual(len(load_tags(path)), 0)

    def test_bad_tags_list_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "note_tags.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"tags": "oops"}, fh)
            self.assertEqual(len(load_tags(path)), 0)

    def test_constructor_skips_dupes_and_blanks(self):
        v = TagVocabulary([{"name": "a"}, {"name": "A"}, {"name": " "},
                           {"name": "b", "counter": True}])
        self.assertEqual(v.names(), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
