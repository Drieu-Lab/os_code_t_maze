"""
test_protocol.py - headless unit tests for the shared serial-line parser
(protocol.py). No hardware, no Tk. Line examples follow firmware/PROTOCOL.md.

Run from host/:
    python -m unittest test_protocol
"""

from __future__ import annotations

import unittest

import dataclasses

import protocol
from protocol import (animal_id, classify, cue_info, lick_edge, mode_name,
                      parse, reward_info, sanitize_animal_id, sensor_edge,
                      sol_pulse)


class TestParse(unittest.TestCase):
    def test_basic_line(self):
        p = parse("123,TRIAL,4,LEFT")
        self.assertIsNotNone(p)
        self.assertEqual(p.ms, 123)
        self.assertEqual(p.type, "TRIAL")
        self.assertEqual(p.fields, ("4", "LEFT"))

    def test_no_payload(self):
        p = parse("0,SEQ_END")
        self.assertEqual((p.ms, p.type, p.fields), (0, "SEQ_END", ()))

    def test_malformed(self):
        for bad in ("", "hello", "TRIAL,1", "x,TRIAL,1", "12", "12,",
                    "-5,MODE,FREEROAM"):
            self.assertIsNone(parse(bad), bad)

    def test_never_raises(self):
        for helper in (parse, lick_edge, sensor_edge, sol_pulse, mode_name,
                       animal_id, reward_info, cue_info):
            for bad in ("", ",,,,", "garbage", "1,2,3", "\x00,\x00"):
                helper(bad)  # must not raise


class TestEdges(unittest.TestCase):
    def test_lick_rising_and_falling(self):
        self.assertEqual(lick_edge("100,L,2,1"), (2, True))
        self.assertEqual(lick_edge("100,L,0,0"), (0, False))

    def test_sensor_edge(self):
        self.assertEqual(sensor_edge("55,S,3,1"), (3, True))

    def test_wrong_type_or_shape(self):
        self.assertIsNone(lick_edge("55,S,3,1"))          # sensor, not lick
        self.assertIsNone(sensor_edge("100,L,2,1"))       # lick, not sensor
        self.assertIsNone(lick_edge("100,L,2"))           # missing state
        self.assertIsNone(lick_edge("100,L,2,1,extra"))   # too many fields
        self.assertIsNone(lick_edge("100,L,x,1"))         # bad index
        self.assertIsNone(lick_edge("100,L,2,2"))         # state not 0/1


class TestSol(unittest.TestCase):
    def test_v2_ack(self):
        self.assertEqual(sol_pulse("10,SOL,1,3"), (1, 3))

    def test_v4_trailing_duration_ignored(self):
        self.assertEqual(sol_pulse("10,SOL,2,4,500"), (2, 4))

    def test_malformed(self):
        self.assertIsNone(sol_pulse("10,SOL,2"))
        self.assertIsNone(sol_pulse("10,SOL,a,b"))
        self.assertIsNone(sol_pulse("10,REWARD,CENTER"))


class TestHandshake(unittest.TestCase):
    def test_mode(self):
        self.assertEqual(mode_name("0,MODE,FREEROAM"), "FREEROAM")
        self.assertEqual(mode_name("0,MODE, FULLTASK "), "FULLTASK")
        self.assertIsNone(mode_name("0,MODE"))
        self.assertIsNone(mode_name("0,MODE,"))
        self.assertIsNone(mode_name("0,ANIMAL,RAT001"))

    def test_animal(self):
        self.assertEqual(animal_id("0,ANIMAL,RAT001"), "RAT001")
        self.assertEqual(animal_id("0,ANIMAL, rat602 "), "rat602")
        self.assertIsNone(animal_id("0,ANIMAL,"))
        self.assertIsNone(animal_id("0,MODE,FREEROAM"))


class TestSanitizeAnimalId(unittest.TestCase):
    def test_keeps_slug_chars(self):
        self.assertEqual(sanitize_animal_id("RAT001"), "RAT001")
        self.assertEqual(sanitize_animal_id("rat-602_a"), "rat-602_a")

    def test_strips_and_drops_disallowed(self):
        self.assertEqual(sanitize_animal_id("  rat 602!  "), "rat602")
        self.assertEqual(sanitize_animal_id("a/b\\c:d"), "abcd")

    def test_empty_falls_back_to_unknown(self):
        self.assertEqual(sanitize_animal_id(""), "unknown")
        self.assertEqual(sanitize_animal_id("   "), "unknown")
        self.assertEqual(sanitize_animal_id("!@#"), "unknown")


class TestReward(unittest.TestCase):
    def test_automatic_v4(self):
        self.assertEqual(reward_info("500,REWARD,CENTER,150"), (False, 150))

    def test_manual_v4(self):
        self.assertEqual(reward_info("500,REWARD,2,MANUAL,80"), (True, 80))

    def test_pre_v4_missing_duration(self):
        # Old firmware / logs have no trailing <dur_ms>; caller supplies the
        # fallback pulse width. see docs/decisions/0019
        self.assertEqual(reward_info("500,REWARD,CENTER"), (False, None))
        self.assertEqual(reward_info("500,REWARD,2,MANUAL"), (True, None))

    def test_not_a_reward(self):
        self.assertIsNone(reward_info("500,SOL,2,1,150"))
        self.assertIsNone(reward_info("500,REWARD"))


class TestCue(unittest.TestCase):
    def test_on_choice_and_return(self):
        self.assertEqual(cue_info("120,CUE,ON,CHOICE,8000"), ("ON", "CHOICE", 8000))
        self.assertEqual(cue_info("120,CUE,ON,RETURN,4000"), ("ON", "RETURN", 4000))

    def test_on_tolerates_whitespace_and_case(self):
        self.assertEqual(cue_info("1,CUE, on , choice , 8000 "), ("ON", "CHOICE", 8000))

    def test_off(self):
        self.assertEqual(cue_info("999,CUE,OFF"), ("OFF", None, None))

    def test_malformed(self):
        self.assertIsNone(cue_info("1,CUE"))                 # no state
        self.assertIsNone(cue_info("1,CUE,ON,CHOICE"))       # missing freq
        self.assertIsNone(cue_info("1,CUE,ON,BOGUS,8000"))   # bad kind
        self.assertIsNone(cue_info("1,CUE,ON,CHOICE,hz"))    # non-numeric freq
        self.assertIsNone(cue_info("1,CUE,SIDEWAYS"))        # unknown state
        self.assertIsNone(cue_info("120,MODE,FULLTASK"))     # not a CUE line

    def test_never_raises(self):
        for bad in ("", ",,,,", "garbage", "1,CUE,,,", "\x00,CUE,\x00"):
            cue_info(bad)  # must not raise


class TestClassify(unittest.TestCase):
    """classify() parses ONCE and pre-computes every typed field, matching the
    individual helpers on the same line (see docs/decisions/0056)."""

    # One representative line per type, exercising every LineInfo field.
    LINES = (
        "0,MODE,FREEROAM",
        "100,L,2,1",
        "55,S,3,1",
        "10,SOL,2,4,500",
        "500,REWARD,CENTER,150",
        "0,ANIMAL,RAT001",
        "120,CUE,ON,CHOICE,8000",
        "123,TRIAL,4,LEFT",   # a line no per-type helper claims
    )

    def test_fields_match_helpers(self):
        for line in self.LINES:
            info = classify(line)
            self.assertEqual(info.raw, line)
            self.assertEqual(info.parsed, parse(line))
            self.assertEqual(info.mode, mode_name(line), line)
            self.assertEqual(info.lick, lick_edge(line), line)
            self.assertEqual(info.sensor, sensor_edge(line), line)
            self.assertEqual(info.sol, sol_pulse(line), line)
            self.assertEqual(info.reward, reward_info(line), line)
            self.assertEqual(info.animal, animal_id(line), line)
            self.assertEqual(info.cue, cue_info(line), line)

    def test_malformed_all_none_but_raw_preserved(self):
        for bad in ("", "garbage", "hello,world", "\x00,\x00"):
            info = classify(bad)
            self.assertEqual(info.raw, bad, bad)
            self.assertIsNone(info.parsed, bad)
            for field in ("mode", "lick", "sensor", "sol", "reward",
                          "animal", "cue"):
                self.assertIsNone(getattr(info, field), f"{bad}:{field}")

    def test_frozen(self):
        info = classify("0,MODE,FREEROAM")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            info.mode = "FULLTASK"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
