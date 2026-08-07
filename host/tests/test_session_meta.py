"""
test_session_meta.py - headless tests for the sticky session-metadata core.

Covers session_meta.build (auto-derived vs overridden stage), the SessionMeta
JSON round-trip, and the sticky-defaults load/save (missing + malformed
tolerance), per docs/decisions/0079. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_session_meta
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tmaze.core.session import session_meta


class TestBuild(unittest.TestCase):
    def test_stage_auto_derived_when_no_override(self):
        m = session_meta.build(
            "RAT600_20260806_120000", trainer="Celine",
            reward_type="sucrose", auto_stage="Phase 3")
        self.assertEqual(m.stage, "Phase 3")
        self.assertEqual(m.stage_source, session_meta.STAGE_SOURCE_AUTO)
        self.assertEqual(m.trainer, "Celine")
        self.assertEqual(m.reward_type, "sucrose")

    def test_override_wins_and_is_flagged(self):
        m = session_meta.build(
            "s1", trainer="Celine", auto_stage="Phase 3",
            stage_override="Reversal")
        self.assertEqual(m.stage, "Reversal")
        self.assertEqual(m.stage_source, session_meta.STAGE_SOURCE_OVERRIDE)

    def test_text_is_trimmed(self):
        m = session_meta.build(
            "s1", trainer="  Celine  ", reward_type=" water ",
            auto_stage="  Phase 3  ", stage_override="   ")
        # Blank (whitespace-only) override -> falls back to the auto stage.
        self.assertEqual(m.trainer, "Celine")
        self.assertEqual(m.reward_type, "water")
        self.assertEqual(m.stage, "Phase 3")
        self.assertEqual(m.stage_source, session_meta.STAGE_SOURCE_AUTO)


class TestSessionMetaJson(unittest.TestCase):
    def test_render_carries_schema_and_fields(self):
        m = session_meta.build(
            "s1", trainer="Celine", reward_type="sucrose", auto_stage="Phase 3")
        data = json.loads(m.render())
        self.assertEqual(data["schema_version"], session_meta.SCHEMA_VERSION)
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["trainer"], "Celine")
        self.assertEqual(data["stage"], "Phase 3")
        self.assertEqual(data["stage_source"], "auto")
        self.assertEqual(data["reward_type"], "sucrose")

    def test_round_trip_through_from_dict(self):
        m = session_meta.build(
            "s1", trainer="Celine", reward_type="sucrose",
            auto_stage="Phase 3", stage_override="Probe")
        again = session_meta.SessionMeta.from_dict(json.loads(m.render()))
        self.assertEqual(again, m)

    def test_from_dict_tolerates_missing_keys(self):
        m = session_meta.SessionMeta.from_dict({"session_id": "s1"})
        self.assertEqual(m.session_id, "s1")
        self.assertEqual(m.trainer, "")
        self.assertEqual(m.stage_source, session_meta.STAGE_SOURCE_AUTO)


class TestDefaults(unittest.TestCase):
    def test_missing_file_is_empty(self):
        d = session_meta.load_defaults(
            os.path.join(tempfile.gettempdir(), "no_such_session_meta.json"))
        self.assertEqual(d.trainer, "")
        self.assertEqual(d.reward_type, "")

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session_meta.json")
            session_meta.save_defaults(
                path, session_meta.SessionMetaDefaults(
                    trainer="Celine", reward_type="sucrose"))
            d = session_meta.load_defaults(path)
            self.assertEqual(d.trainer, "Celine")
            self.assertEqual(d.reward_type, "sucrose")
            # The self-describing block is present on disk.
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            self.assertIn("_schema", raw)

    def test_malformed_file_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session_meta.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("not json {")
            d = session_meta.load_defaults(path)
            self.assertEqual(d.trainer, "")

    def test_non_object_json_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session_meta.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2, 3]")
            d = session_meta.load_defaults(path)
            self.assertEqual(d.reward_type, "")


if __name__ == "__main__":
    unittest.main()
