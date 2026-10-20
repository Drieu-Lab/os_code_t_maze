"""
test_cue_command.py - headless tests for the per-session cue stimulus core
(tmaze.core.cue_command; docs/decisions/0222).

Run from host/:  python -m unittest tests.test_cue_command
"""

from __future__ import annotations

import unittest

from tmaze.core.cue_command import (
    STIM_AM_NOISE, STIM_TONE, CueStimulus, arm_block_reason, cue_label,
    cue_on_command, describe, format_rate, make_stimulus, short_cue_label)
from tmaze.core.phase3.trial_sequence import HIGH, LOW

AM = CueStimulus(kind=STIM_AM_NOISE, am_fast_hz=32.0, am_slow_hz=2.0)
TONE = CueStimulus()


class TestMakeStimulus(unittest.TestCase):
    def test_valid(self):
        st = make_stimulus(STIM_AM_NOISE, 32.5, "2.125")
        self.assertEqual((st.am_fast_hz, st.am_slow_hz), (32.5, 2.125))

    def test_rejects(self):
        for args in [("chirp", 32, 2), (STIM_TONE, 129, 2), (STIM_TONE, 32, 0.9),
                     (STIM_TONE, 32, 2.0001), (STIM_TONE, 2, 32), (STIM_TONE, 5, 5)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                make_stimulus(*args)

    def test_edges_allowed(self):
        make_stimulus(STIM_AM_NOISE, 128, 1)


class TestFormatRate(unittest.TestCase):
    def test_matches_firmware_echo(self):
        self.assertEqual(format_rate(32.0), "32")
        self.assertEqual(format_rate(2.5), "2.5")
        self.assertEqual(format_rate(2.125), "2.125")
        self.assertEqual(format_rate(2.10), "2.1")


class TestCueOnCommand(unittest.TestCase):
    def _cmd(self, kind, label, st, hz=4000, gain=80, noise=60):
        return cue_on_command(kind, label, st, tone_commanded_hz=hz,
                              tone_gain_pct=gain, noise_gain_pct=noise)

    def test_tone_unchanged(self):
        self.assertEqual(self._cmd("CHOICE", HIGH, TONE, hz=8030),
                         "CUE_ON,CHOICE,8030,80")

    def test_am_high_is_fast(self):
        self.assertEqual(self._cmd("CHOICE", HIGH, AM), "CUE_ON,CHOICE,32,60,AM")
        self.assertEqual(self._cmd("RETURN", LOW, AM), "CUE_ON,RETURN,2,60,AM")

    def test_am_fractional_rate(self):
        st = CueStimulus(STIM_AM_NOISE, 32.5, 2.125)
        self.assertEqual(self._cmd("CHOICE", LOW, st), "CUE_ON,CHOICE,2.125,60,AM")

    def test_nothing_to_play(self):
        self.assertIsNone(self._cmd("CHOICE", None, AM))
        self.assertIsNone(self._cmd("CHOICE", HIGH, TONE, hz=None))
        # An AM cue doesn't need a tone frequency.
        self.assertIsNotNone(self._cmd("CHOICE", HIGH, AM, hz=None))


class TestLabels(unittest.TestCase):
    def test_tone_keeps_high_low(self):
        self.assertEqual(cue_label(HIGH, TONE), HIGH)
        self.assertEqual(short_cue_label(LOW, TONE), LOW)
        self.assertEqual(describe(TONE), "tone")

    def test_am_shows_fast_slow(self):
        self.assertEqual(cue_label(HIGH, AM), "FAST 32 Hz")
        self.assertEqual(cue_label(LOW, AM), "SLOW 2 Hz")
        self.assertEqual(short_cue_label(HIGH, AM), "FAST")
        self.assertEqual(describe(AM), "AM noise (fast 32 Hz / slow 2 Hz)")

    def test_no_identity(self):
        self.assertEqual(cue_label(None, AM), "")
        self.assertEqual(short_cue_label("", TONE), "")


class TestArmBlock(unittest.TestCase):
    def test_tone_never_blocked(self):
        self.assertIsNone(arm_block_reason(TONE, None))
        self.assertIsNone(arm_block_reason(TONE, 9))

    def test_am_needs_v11(self):
        self.assertIsNone(arm_block_reason(AM, 11))
        self.assertIn("reports 10", arm_block_reason(AM, 10))
        self.assertIn("unknown", arm_block_reason(AM, None))


if __name__ == "__main__":
    unittest.main()
