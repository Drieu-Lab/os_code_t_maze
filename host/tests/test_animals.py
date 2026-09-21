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

import json

from tmaze.core.records import RecordsFileError, file_mtime
from tmaze.core.records.animals import (
    ANIMALS_SCHEMA_VERSION, AnimalRegistry, animals_json_path, load_records,
    load_records_for_write, load_registry, normalize_records, parse_registry,
    resources_root, validate_records, write_records)

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


class TestRosterEditing(unittest.TestCase):
    def test_normalize_trims_and_dedups_aliases(self):
        recs = normalize_records([
            {"id": " 600 ", "aliases": ["Rat 600", "Rat 600", " ", "R600"]},
            {"id": "", "aliases": ["x"]},                     # dropped (blank id)
            {"id": "601"},                                     # no aliases key
        ])
        self.assertEqual(recs, [
            {"id": "600", "aliases": ["Rat 600", "R600"], "date_of_birth": "",
             "high_tone_is_left": None, "daily_water_ml": None, "reward_ul": None},
            {"id": "601", "aliases": [], "date_of_birth": "",
             "high_tone_is_left": None, "daily_water_ml": None, "reward_ul": None},
        ])

    def test_validate_ok(self):
        self.assertEqual(validate_records(
            [{"id": "600", "aliases": ["Rat 600"]}, {"id": "601"}]), [])

    def test_validate_blank_id(self):
        errs = validate_records([{"id": ""}])
        self.assertTrue(any("blank id" in e for e in errs))

    def test_validate_duplicate_and_ambiguous_ids(self):
        self.assertTrue(any("Duplicate" in e
                            for e in validate_records([{"id": "600"},
                                                       {"id": "600"}])))
        self.assertTrue(any("Ambiguous" in e
                            for e in validate_records([{"id": "600"},
                                                       {"id": "6 0 0"}])))

    def test_validate_alias_collision(self):
        errs = validate_records([{"id": "600", "aliases": ["601"]},
                                 {"id": "601"}])
        self.assertTrue(any("collides" in e for e in errs))

    def test_write_records_roundtrip_and_preserves_schema(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_SAMPLE)
            write_records([{"id": "600", "aliases": ["Rat 600", "Kalmia"]},
                           {"id": "700", "aliases": []}], path)
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            recs = load_records(path)
            # A round-tripped file still loads as a working registry.
            normalized = load_registry(path).normalize("Kalmia")
        # A write always produces a current-layout file (ADR-0161): the v1
        # sample is upgraded, and its stock note refreshed to describe v2.
        self.assertEqual(doc["schema_version"], ANIMALS_SCHEMA_VERSION)
        self.assertIn("date_of_birth", doc["_schema"]["note"])
        self.assertEqual([r["id"] for r in recs], ["600", "700"])
        self.assertIn("Kalmia", recs[0]["aliases"])
        self.assertEqual(normalized, "600")

    def test_write_records_rejects_invalid_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with self.assertRaises(ValueError):
                write_records([{"id": "600"}, {"id": "600"}], path)
            self.assertFalse(os.path.exists(path))

    def test_write_records_defaults_schema_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            write_records([{"id": "600"}], path)
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        self.assertIn("_schema", doc)
        self.assertEqual(doc["schema_version"], ANIMALS_SCHEMA_VERSION)

    def test_write_records_keeps_a_hand_edited_note(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"_schema": {"note": "our lab's own wording"},
                           "schema_version": 1, "animals": []}, fh)
            write_records([{"id": "600"}], path)
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        self.assertEqual(doc["_schema"]["note"], "our lab's own wording")
        self.assertEqual(doc["schema_version"], ANIMALS_SCHEMA_VERSION)

    def test_write_records_stale_mtime_is_refused(self):
        # ADR-0155: a roster changed on disk since it was loaded is not
        # overwritten; the caller must reload and re-apply.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            write_records([{"id": "600"}], path)
            seen = file_mtime(path)
            write_records([{"id": "600"}, {"id": "601"}], path)
            os.utime(path, ns=(seen + 1_000_000_000, seen + 1_000_000_000))
            with self.assertRaises(RecordsFileError):
                write_records([{"id": "602"}], path, expect_mtime=seen)
            self.assertEqual([r["id"] for r in load_records(path)],
                             ["600", "601"])
            write_records([{"id": "602"}], path, expect_mtime=file_mtime(path))
            self.assertEqual([r["id"] for r in load_records(path)], ["602"])


class TestDateOfBirth(unittest.TestCase):
    """Schema v2 (ADR-0161): an optional per-animal date_of_birth."""

    def setUp(self):
        self.reg = AnimalRegistry([
            {"id": "600", "aliases": ["Rat 600"], "date_of_birth": "2026-01-15"},
            {"id": "601", "aliases": []},                      # v1-style entry
            {"id": "602", "date_of_birth": ""},                # not recorded
        ])

    def test_date_of_birth_lookup_by_id_or_alias(self):
        self.assertEqual(self.reg.date_of_birth("600"), "2026-01-15")
        self.assertEqual(self.reg.date_of_birth("Rat 600"), "2026-01-15")
        self.assertIsNone(self.reg.date_of_birth("601"))
        self.assertIsNone(self.reg.date_of_birth("602"))
        self.assertIsNone(self.reg.date_of_birth("999"))

    def test_age_days(self):
        import datetime as dt
        self.assertEqual(self.reg.age_days("600", "2026-08-01"), 198)
        self.assertEqual(self.reg.age_days("600", dt.date(2026, 1, 15)), 0)
        self.assertEqual(self.reg.age_days("600", "2026-01-10"), -5)  # data error
        self.assertIsNone(self.reg.age_days("601", "2026-08-01"))
        self.assertIsNone(self.reg.age_days("600", "not a date"))

    def test_validate_dob_blank_ok_bad_or_future_rejected(self):
        import datetime as dt
        today = dt.date(2026, 9, 11)
        self.assertEqual(validate_records(
            [{"id": "600", "date_of_birth": ""},
             {"id": "601", "date_of_birth": "2026-01-15"}], today=today), [])
        errs = validate_records([{"id": "600", "date_of_birth": "15/01/2026"}],
                                today=today)
        self.assertTrue(any("YYYY-MM-DD" in e for e in errs))
        errs = validate_records([{"id": "600", "date_of_birth": "2026-09-12"}],
                                today=today)
        self.assertTrue(any("future" in e for e in errs))

    def test_roundtrip_keeps_dob_and_v1_file_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_SAMPLE)                               # v1: no dob
            recs = load_records(path)
            self.assertEqual(recs[0]["date_of_birth"], "")
            recs[0]["date_of_birth"] = "2026-01-15"
            write_records(recs, path)
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["animals"][0]["date_of_birth"], "2026-01-15")
            self.assertEqual(doc["animals"][1]["date_of_birth"], "")
            self.assertEqual(load_registry(path).date_of_birth("Rat 600"),
                             "2026-01-15")


class TestHighToneMapping(unittest.TestCase):
    """Schema v3 (ADR-0186): an optional tri-state per-animal high_tone_is_left."""

    def setUp(self):
        self.reg = AnimalRegistry([
            {"id": "600", "aliases": ["Rat 600"], "high_tone_is_left": True},
            {"id": "601", "high_tone_is_left": False},
            {"id": "602", "high_tone_is_left": None},          # explicit unset
            {"id": "603"},                                     # v2-style: no field
        ])

    def test_lookup_by_id_or_alias(self):
        self.assertIs(self.reg.high_tone_is_left("600"), True)
        self.assertIs(self.reg.high_tone_is_left("Rat 600"), True)
        self.assertIs(self.reg.high_tone_is_left("601"), False)

    def test_unset_and_unknown_are_none(self):
        # None (unset) must be distinguishable from False (HIGH means RIGHT).
        self.assertIsNone(self.reg.high_tone_is_left("602"))
        self.assertIsNone(self.reg.high_tone_is_left("603"))
        self.assertIsNone(self.reg.high_tone_is_left("999"))

    def test_normalize_coerces_tri_state(self):
        recs = normalize_records([
            {"id": "600", "high_tone_is_left": True},
            {"id": "601", "high_tone_is_left": False},
            {"id": "602", "high_tone_is_left": ""},            # blank -> unset
            {"id": "603"},                                     # missing -> unset
        ])
        self.assertEqual([r["high_tone_is_left"] for r in recs],
                         [True, False, None, None])

    def test_validate_rejects_non_bool_mapping(self):
        # A hand-edit typo like the string "true" survives normalize and must be
        # flagged rather than silently dropped.
        errs = validate_records([{"id": "600", "high_tone_is_left": "true"}])
        self.assertTrue(any("high_tone_is_left" in e for e in errs))
        # true / false / blank are all accepted.
        self.assertEqual(validate_records([
            {"id": "600", "high_tone_is_left": True},
            {"id": "601", "high_tone_is_left": False},
            {"id": "602"}]), [])

    def test_roundtrip_keeps_mapping_and_older_file_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_SAMPLE)                               # v1: no field
            recs = load_records(path)
            self.assertIsNone(recs[0]["high_tone_is_left"])
            recs[0]["high_tone_is_left"] = True
            recs[1]["high_tone_is_left"] = False
            write_records(recs, path)
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertIs(doc["animals"][0]["high_tone_is_left"], True)
            self.assertIs(doc["animals"][1]["high_tone_is_left"], False)
            self.assertIsNone(doc["animals"][2]["high_tone_is_left"])
            self.assertEqual(doc["schema_version"], ANIMALS_SCHEMA_VERSION)
            self.assertIs(load_registry(path).high_tone_is_left("Rat 600"), True)


class TestWaterBudget(unittest.TestCase):
    """Schema v4 (ADR-0191): optional per-animal daily_water_ml + reward_ul."""

    def setUp(self):
        self.reg = AnimalRegistry([
            {"id": "600", "aliases": ["Rat 600"], "daily_water_ml": 8,
             "reward_ul": 70},
            {"id": "601", "daily_water_ml": None, "reward_ul": 65.5},
            {"id": "602", "daily_water_ml": "", "reward_ul": 0},   # both unset
            {"id": "603"},                                          # v3-style
        ])

    def test_lookup_by_id_or_alias(self):
        self.assertEqual(self.reg.daily_water_ml("Rat 600"), 8.0)
        self.assertEqual(self.reg.reward_ul("600"), 70.0)
        self.assertIsNone(self.reg.daily_water_ml("601"))
        self.assertEqual(self.reg.reward_ul("601"), 65.5)

    def test_unset_zero_and_unknown_are_none(self):
        self.assertIsNone(self.reg.daily_water_ml("602"))
        self.assertIsNone(self.reg.reward_ul("602"))       # 0 is not a volume
        self.assertIsNone(self.reg.daily_water_ml("603"))
        self.assertIsNone(self.reg.reward_ul("603"))
        self.assertIsNone(self.reg.daily_water_ml("999"))

    def test_normalize_coerces_numbers(self):
        recs = normalize_records([
            {"id": "600", "daily_water_ml": 8, "reward_ul": "70"},
            {"id": "601", "daily_water_ml": "", "reward_ul": None},
            {"id": "602"},
        ])
        self.assertEqual((recs[0]["daily_water_ml"], recs[0]["reward_ul"]), (8.0, 70.0))
        self.assertEqual((recs[1]["daily_water_ml"], recs[1]["reward_ul"]), (None, None))
        self.assertEqual((recs[2]["daily_water_ml"], recs[2]["reward_ul"]), (None, None))

    def test_validate_rejects_bad_numbers(self):
        errs = validate_records(normalize_records(
            [{"id": "600", "daily_water_ml": "eight"}]))
        self.assertTrue(any("daily_water_ml" in e for e in errs))
        errs = validate_records(normalize_records([{"id": "600", "reward_ul": -5}]))
        self.assertTrue(any("reward_ul" in e for e in errs))
        errs = validate_records(normalize_records([{"id": "600", "daily_water_ml": 0}]))
        self.assertTrue(any("daily_water_ml" in e for e in errs))
        errs = validate_records(normalize_records([{"id": "600", "reward_ul": True}]))
        self.assertTrue(any("reward_ul" in e for e in errs))
        # Positive numbers / blank are accepted.
        self.assertEqual(validate_records(normalize_records([
            {"id": "600", "daily_water_ml": 8.0, "reward_ul": 70},
            {"id": "601"}])), [])

    def test_roundtrip_and_older_file_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_SAMPLE)                               # v1: no field
            recs = load_records(path)
            self.assertIsNone(recs[0]["daily_water_ml"])
            self.assertIsNone(recs[0]["reward_ul"])
            recs[0]["daily_water_ml"] = 8
            recs[1]["reward_ul"] = 70
            write_records(recs, path)
            with open(path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["animals"][0]["daily_water_ml"], 8.0)
            self.assertIsNone(doc["animals"][0]["reward_ul"])
            self.assertEqual(doc["animals"][1]["reward_ul"], 70.0)
            self.assertEqual(doc["schema_version"], ANIMALS_SCHEMA_VERSION)
            self.assertEqual(load_registry(path).daily_water_ml("Rat 600"), 8.0)
            with self.assertRaises(ValueError):
                recs[0]["daily_water_ml"] = "eight"
                write_records(recs, path)


class TestLoadRecordsForWrite(unittest.TestCase):
    """The strict roster reader that guards the Save path against clobbering a
    present-but-corrupt animals.json (#12)."""

    def test_absent_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                load_records_for_write(os.path.join(d, "absent.json")), [])

    def test_valid_empty_roster_is_readable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"schema_version": 1, "animals": []}')
            self.assertEqual(load_records_for_write(path), [])

    def test_present_valid_file_reads_records(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_SAMPLE)
            recs = load_records_for_write(path)
        self.assertEqual([r["id"] for r in recs], ["600", "601", "602"])

    def test_present_but_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{ this is not valid json")
            with self.assertRaises(RecordsFileError):
                load_records_for_write(path)

    def test_present_but_wrong_structure_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "animals.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"not_animals": 1}')
            with self.assertRaises(RecordsFileError):
                load_records_for_write(path)


if __name__ == "__main__":
    unittest.main()
