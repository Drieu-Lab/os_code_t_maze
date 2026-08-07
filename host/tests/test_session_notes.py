"""
test_session_notes.py - headless tests for the operator note-log core.

Covers the pure ``session_notes`` model (docs/decisions/0074): stamped entry
creation, in-place edit that preserves the original timestamps + flags an edit,
delete, stable/monotonic ids, JSON Lines round-trip, and the crash-tolerant parse
that skips a corrupt tail. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_session_notes
"""

from __future__ import annotations

import unittest

from tmaze.core.session.session_notes import NoteEntry, NoteLog, parse_jsonl


class TestAddAndStamp(unittest.TestCase):
    def test_add_populates_fields_and_returns_entry(self):
        log = NoteLog()
        e = log.add("dissuaded non-stem", iso="2026-08-05T14:00:00.000",
                    mono=123.5, trial_index=7)
        self.assertEqual(e.id, 0)
        self.assertEqual(e.text, "dissuaded non-stem")
        self.assertEqual(e.iso, "2026-08-05T14:00:00.000")
        self.assertEqual(e.mono, 123.5)
        self.assertEqual(e.trial_index, 7)
        self.assertIsNone(e.tag)
        self.assertEqual(e.count, 1)
        self.assertIsNone(e.edited_iso)
        self.assertEqual(len(log), 1)

    def test_ids_are_monotonic(self):
        log = NoteLog()
        a = log.add("a", iso="i1")
        b = log.add("b", iso="i2")
        self.assertEqual((a.id, b.id), (0, 1))

    def test_empty_text_quick_stamp_allowed(self):
        log = NoteLog()
        e = log.add("", iso="i", mono=1.0, trial_index=3)
        self.assertEqual(e.text, "")
        self.assertEqual(e.trial_index, 3)

    def test_tag_and_counter_entry(self):
        log = NoteLog()
        e = log.add("lift-backs", iso="i", tag="lift-backs", count=1)
        self.assertEqual(e.tag, "lift-backs")
        self.assertEqual(e.count, 1)


class TestEditDelete(unittest.TestCase):
    def test_edit_preserves_original_stamps(self):
        log = NoteLog()
        e = log.add("typo", iso="orig-iso", mono=5.0, trial_index=2)
        edited = log.edit(e.id, "fixed", edited_iso="edit-iso")
        self.assertIsNotNone(edited)
        self.assertEqual(edited.text, "fixed")
        self.assertEqual(edited.edited_iso, "edit-iso")
        # Original stamps untouched.
        self.assertEqual(edited.iso, "orig-iso")
        self.assertEqual(edited.mono, 5.0)
        self.assertEqual(edited.trial_index, 2)

    def test_edit_unknown_id_returns_none(self):
        log = NoteLog()
        log.add("a", iso="i")
        self.assertIsNone(log.edit(99, "x", edited_iso="e"))

    def test_delete_removes_entry(self):
        log = NoteLog()
        a = log.add("a", iso="i1")
        b = log.add("b", iso="i2")
        self.assertTrue(log.delete(a.id))
        self.assertEqual([e.id for e in log.entries()], [b.id])

    def test_delete_unknown_id_is_false(self):
        log = NoteLog()
        self.assertFalse(log.delete(0))


class TestJsonlRoundTrip(unittest.TestCase):
    def test_round_trip_preserves_entries(self):
        log = NoteLog()
        log.add("first", iso="i1", mono=1.0, trial_index=0)
        log.add("tagged", iso="i2", tag="non-stem", count=1)
        log.edit(0, "first edited", edited_iso="e1")
        text = log.render_jsonl()
        back = parse_jsonl(text)
        self.assertEqual(back.as_list(), log.as_list())

    def test_next_id_restored_after_reload(self):
        log = NoteLog()
        log.add("a", iso="i1")
        log.add("b", iso="i2")            # ids 0,1
        reloaded = parse_jsonl(log.render_jsonl())
        c = reloaded.add("c", iso="i3")
        self.assertEqual(c.id, 2)          # continues past the max id

    def test_empty_log_renders_empty_string(self):
        self.assertEqual(NoteLog().render_jsonl(), "")
        self.assertEqual(len(parse_jsonl("")), 0)


class TestCrashTolerantParse(unittest.TestCase):
    def test_skips_blank_and_corrupt_tail(self):
        good = NoteEntry(id=0, iso="i0", text="ok").as_dict()
        import json
        text = (json.dumps(good) + "\n"
                + "\n"                       # blank line
                + '{"id": 1, "iso": "i1", "text": "half'  # truncated JSON
                )
        log = parse_jsonl(text)
        self.assertEqual(len(log), 1)
        self.assertEqual(log.entries()[0].text, "ok")

    def test_skips_lines_without_id(self):
        text = '{"iso": "i", "text": "no id here"}\n'
        self.assertEqual(len(parse_jsonl(text)), 0)


if __name__ == "__main__":
    unittest.main()
