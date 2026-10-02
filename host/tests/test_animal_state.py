"""
test_animal_state.py - headless tests for the pre-session animal-state core.

Covers ``animal_state`` (docs/decisions/0077): building from a daily-table row
(incl. computing % baseline when the stored value is blank), from a typed weight,
and the minimal manual record; plus the JSON render/round-trip. Pure stdlib.

Run from host/:  python -m unittest test_animal_state
"""

from __future__ import annotations

import datetime as dt
import json
import unittest

from tmaze.core.records import animal_state
from tmaze.core.records.animal_state import AnimalState


class TestFromDailyRow(unittest.TestCase):
    def test_uses_row_values(self):
        row = {"weight_g": "332", "baseline_g": "348", "pct_baseline": "0.954",
               "water_on": "6pm", "water_off": "7pm", "bottle_water_ml": "",
               "notes": "hi"}
        s = animal_state.from_daily_row("600_x", "600", "2026-07-02", row)
        self.assertEqual(s.source, animal_state.SOURCE_DAILY_TABLE)
        self.assertEqual(s.pre_session_weight_g, 332)
        self.assertEqual(s.baseline_g, 348)
        self.assertEqual(s.pct_baseline, 0.954)
        self.assertEqual(s.last_water_on, "6pm")
        self.assertEqual(s.note, "hi")
        self.assertIsNone(s.measured_water_ml)

    def test_measured_water_comes_from_bottle_water(self):
        s = animal_state.from_daily_row("x", "600", "2026-09-05",
                                        {"bottle_water_ml": "7"})
        self.assertEqual(s.measured_water_ml, 7)

    def test_computes_pct_when_blank(self):
        row = {"weight_g": "330", "baseline_g": "348", "pct_baseline": ""}
        s = animal_state.from_daily_row("x", "600", "2026-07-03", row)
        self.assertEqual(s.pct_baseline, round(330 / 348, 4))

    def test_blank_weight_leaves_pct_none(self):
        row = {"weight_g": "", "baseline_g": "348"}
        s = animal_state.from_daily_row("x", "600", "d", row)
        self.assertIsNone(s.pre_session_weight_g)
        self.assertIsNone(s.pct_baseline)


class TestWaterTimeParsing(unittest.TestCase):
    """Real water-cell shorthand -> time-of-day (ADR-0101 / FW-0029)."""

    def test_parses_lab_shorthand(self):
        cases = {
            "6pm": dt.time(18, 0),
            "5:30PM": dt.time(17, 30),
            "3:15pm": dt.time(15, 15),
            "11:30AM": dt.time(11, 30),
            "17:00": dt.time(17, 0),
            "6:50PM": dt.time(18, 50),
        }
        for text, want in cases.items():
            self.assertEqual(animal_state.parse_clock_time(text), want, text)

    def test_free_text_and_blank_are_none(self):
        for text in ("", "   ", None, "Till Monday evening",
                     "8.46ml rig; No suppl", "6:30PM till Sun"):
            self.assertIsNone(animal_state.parse_clock_time(text), repr(text))

    def test_water_datetime_combines_date_and_time(self):
        got = animal_state.water_datetime("2026-08-04", "6:50PM")
        self.assertEqual(got, dt.datetime(2026, 8, 4, 18, 50))
        self.assertIsNone(animal_state.water_datetime("bad-date", "6pm"))
        self.assertIsNone(animal_state.water_datetime("2026-08-04", "nope"))


class TestHoursSinceWater(unittest.TestCase):
    def test_positive_gap_rounds(self):
        off = dt.datetime(2026, 8, 4, 18, 50)
        start = dt.datetime(2026, 8, 5, 10, 20)          # next morning
        self.assertEqual(animal_state.hours_since_water(off, start), 15.5)

    def test_missing_or_negative_is_none(self):
        off = dt.datetime(2026, 8, 5, 18, 0)
        start = dt.datetime(2026, 8, 5, 9, 0)            # before the off-time
        self.assertIsNone(animal_state.hours_since_water(off, start))
        self.assertIsNone(animal_state.hours_since_water(None, start))
        self.assertIsNone(animal_state.hours_since_water(off, None))


class TestResolveLastWaterOff(unittest.TestCase):
    _ROWS = [
        {"date": "2026-08-03", "water_off": "6:15PM"},
        {"date": "2026-08-04", "water_off": "6:50PM"},        # previous evening
        {"date": "2026-08-05", "water_off": "Till Monday"},   # free text -> skip
    ]

    def test_picks_most_recent_before_start(self):
        start = dt.datetime(2026, 8, 5, 10, 0)                # morning session
        got = animal_state.resolve_last_water_off(self._ROWS, start)
        self.assertEqual(got, dt.datetime(2026, 8, 4, 18, 50))

    def test_excludes_future_off_times(self):
        # A same-day evening water_off is in the future for a morning session.
        rows = [{"date": "2026-08-05", "water_off": "7:00PM"}]
        start = dt.datetime(2026, 8, 5, 9, 0)
        self.assertIsNone(animal_state.resolve_last_water_off(rows, start))

    def test_none_when_nothing_parseable(self):
        rows = [{"date": "2026-08-05", "water_off": "Till Monday evening"}]
        start = dt.datetime(2026, 8, 6, 10, 0)
        self.assertIsNone(animal_state.resolve_last_water_off(rows, start))


class TestFromDailyRowHours(unittest.TestCase):
    def test_computes_hours_when_start_and_off_given(self):
        row = {"weight_g": "330", "baseline_g": "348", "water_off": "7pm"}
        start = dt.datetime(2026, 8, 5, 10, 0)
        off = dt.datetime(2026, 8, 4, 19, 0)
        s = animal_state.from_daily_row("x", "600", "2026-08-05", row,
                                        session_start=start, last_water_off=off)
        self.assertEqual(s.hours_since_water, 15.0)
        self.assertEqual(s.last_water_off, "7pm")            # verbatim cell kept

    def test_hours_none_without_start(self):
        row = {"weight_g": "330", "baseline_g": "348", "water_off": "7pm"}
        s = animal_state.from_daily_row("x", "600", "2026-08-05", row)
        self.assertIsNone(s.hours_since_water)


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
