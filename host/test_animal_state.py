"""
test_animal_state.py - headless tests for the pre-session animal-state core.

Covers ``animal_state`` (docs/decisions/0077): building from a daily-table row
(incl. computing % baseline when the stored value is blank), from a typed weight,
and the minimal manual record; plus the JSON render/round-trip. Pure stdlib.

Run from host/:  python -m unittest test_animal_state
"""

from __future__ import annotations

import json
import unittest

import animal_state
from animal_state import AnimalState


class TestFromDailyRow(unittest.TestCase):
    def test_uses_row_values(self):
        row = {"weight_g": "332", "baseline_g": "348", "pct_baseline": "0.954",
               "water_on": "6pm", "water_off": "7pm", "water_ml": "",
               "notes": "hi"}
        s = animal_state.from_daily_row("600_x", "600", "2026-07-02", row)
        self.assertEqual(s.source, animal_state.SOURCE_DAILY_TABLE)
        self.assertEqual(s.pre_session_weight_g, 332)
        self.assertEqual(s.baseline_g, 348)
        self.assertEqual(s.pct_baseline, 0.954)
        self.assertEqual(s.last_water_on, "6pm")
        self.assertEqual(s.note, "hi")
        self.assertIsNone(s.measured_water_ml)

    def test_computes_pct_when_blank(self):
        row = {"weight_g": "330", "baseline_g": "348", "pct_baseline": ""}
        s = animal_state.from_daily_row("x", "600", "2026-07-03", row)
        self.assertEqual(s.pct_baseline, round(330 / 348, 4))

    def test_blank_weight_leaves_pct_none(self):
        row = {"weight_g": "", "baseline_g": "348"}
        s = animal_state.from_daily_row("x", "600", "d", row)
        self.assertIsNone(s.pre_session_weight_g)
        self.assertIsNone(s.pct_baseline)


class TestFromWeightAndManual(unittest.TestCase):
    def test_from_weight_with_baseline(self):
        s = animal_state.from_weight("x", "601", "d", 340, baseline_g=350,
                                     note="typed")
        self.assertEqual(s.source, animal_state.SOURCE_START_DIALOG)
        self.assertEqual(s.pct_baseline, round(340 / 350, 4))
        self.assertEqual(s.note, "typed")

    def test_from_weight_without_baseline(self):
        s = animal_state.from_weight("x", "601", "d", 340)
        self.assertIsNone(s.pct_baseline)

    def test_unknown_is_minimal(self):
        s = animal_state.unknown("x", "602", "d", note="no data")
        self.assertEqual(s.source, animal_state.SOURCE_MANUAL)
        self.assertIsNone(s.pre_session_weight_g)
        self.assertEqual(s.note, "no data")


class TestSerialize(unittest.TestCase):
    def test_render_is_json_with_schema_and_id(self):
        s = animal_state.from_weight("600_20260805", "600", "2026-08-05", 345,
                                     baseline_g=348)
        obj = json.loads(s.render())
        self.assertEqual(obj["schema_version"], animal_state.SCHEMA_VERSION)
        self.assertEqual(obj["session_id"], "600_20260805")
        self.assertEqual(obj["animal"], "600")

    def test_round_trip(self):
        s = animal_state.from_weight("id", "600", "d", 345, baseline_g=348,
                                     note="n")
        back = AnimalState.from_dict(json.loads(s.render()))
        self.assertEqual(back.as_dict(), s.as_dict())


if __name__ == "__main__":
    unittest.main()
