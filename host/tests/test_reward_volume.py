"""
test_reward_volume.py - headless tests for the pulse-duration -> volume lookup.

Covers the single-point proportional fallback (unchanged from ADR-0019),
multi-point linear inter/extrapolation, per-(port, liquid) lookup, the
liquid -> water -> in-code fallback chain, and the missing-file fallback, per
docs/decisions/0091. Points reward_volume at a temp calibration file via
``reload_calibration`` and restores the default in tearDown so other tests
(e.g. test_reward_dispatcher) still see the real host CSV. Pure stdlib; no Tk.

Run from host/:  python -m unittest test_reward_volume
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from tmaze.core.reward import reward_volume as rv
from tmaze.core.reward.reward_calibration import CalibrationPoint, write_points


class _CalFile(unittest.TestCase):
    """Base that writes a temp calibration file and points reward_volume at it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cal.csv")

    def tearDown(self):
        # Restore the default (real host CSV) source so we don't leak our temp
        # path/cache into later tests.
        rv._CALIBRATION_PATH = None
        rv.reload_calibration()
        shutil.rmtree(self.dir, ignore_errors=True)

    def load(self, points):
        write_points(self.path, points)
        rv.reload_calibration(self.path)


class TestSinglePoint(_CalFile):
    def test_proportional_when_one_point(self):
        self.load([CalibrationPoint("center", "water", 300, 60.0)])
        self.assertAlmostEqual(rv.reward_volume_ul(300, liquid="water"), 60.0)
        self.assertAlmostEqual(rv.reward_volume_ul(150, liquid="water"), 30.0)
        self.assertAlmostEqual(rv.reward_volume_ul(600, liquid="water"), 120.0)

    def test_nonpositive_and_none(self):
        self.load([CalibrationPoint("center", "water", 300, 60.0)])
        self.assertEqual(rv.reward_volume_ul(0, liquid="water"), 0.0)
        self.assertEqual(rv.reward_volume_ul(-10, liquid="water"), 0.0)
        self.assertEqual(rv.reward_volume_ul(None, liquid="water"), 0.0)


class TestMultiPoint(_CalFile):
    def test_interpolation_and_extrapolation(self):
        self.load([
            CalibrationPoint("center", "water", 100, 20.0),
            CalibrationPoint("center", "water", 300, 60.0),
            CalibrationPoint("center", "water", 500, 90.0),
        ])
        self.assertAlmostEqual(rv.reward_volume_ul(100, liquid="water"), 20.0)
        # Between 300 and 500: halfway -> (60+90)/2 = 75.
        self.assertAlmostEqual(rv.reward_volume_ul(400, liquid="water"), 75.0)
        # Below the first point extrapolates along the first segment (slope 0.2).
        self.assertAlmostEqual(rv.reward_volume_ul(50, liquid="water"), 10.0)
        # Above the last point extrapolates along the last segment (slope 0.15).
        self.assertAlmostEqual(rv.reward_volume_ul(600, liquid="water"), 105.0)


class TestPerLiquidAndPort(_CalFile):
    def test_liquid_selects_its_own_curve(self):
        self.load([
            CalibrationPoint("center", "water", 300, 60.0),
            CalibrationPoint("center", "sucrose_10pct", 300, 45.0),
        ])
        self.assertAlmostEqual(
            rv.reward_volume_ul(300, liquid="sucrose_10pct"), 45.0)
        self.assertAlmostEqual(rv.reward_volume_ul(300, liquid="water"), 60.0)

    def test_liquid_fallback_to_water(self):
        # Only water measured; asking for sucrose falls back to the water curve.
        self.load([CalibrationPoint("center", "water", 300, 60.0)])
        self.assertAlmostEqual(
            rv.reward_volume_ul(300, liquid="sucrose_10pct"), 60.0)

    def test_port_index_accepted(self):
        self.load([CalibrationPoint("center", "water", 300, 60.0)])
        # Port index 2 == center.
        self.assertAlmostEqual(rv.reward_volume_ul(300, port=2, liquid="water"),
                               60.0)

    def test_uncharacterized_port_uses_incode_fallback(self):
        # A port with no data (and no water for it) falls back to the in-code
        # central bench point (300 ms -> 60 uL, proportional).
        self.load([CalibrationPoint("center", "water", 300, 60.0)])
        self.assertAlmostEqual(rv.reward_volume_ul(150, port="left",
                                                   liquid="sucrose_10pct"), 30.0)


class TestMissingFile(_CalFile):
    def test_missing_file_uses_incode_fallback(self):
        missing = os.path.join(self.dir, "nope.csv")
        rv.reload_calibration(missing)
        self.assertAlmostEqual(rv.reward_volume_ul(300), 60.0)
        self.assertAlmostEqual(rv.reward_volume_ul(150), 30.0)


class TestInverse(_CalFile):
    """``pulse_ms_for_volume`` is the inverse of ``reward_volume_ul`` over the
    same curve (docs/decisions/0191): a per-animal target volume maps back to
    the pulse width that dispenses it."""

    def test_single_point_is_proportional(self):
        self.load([CalibrationPoint("center", "water", 300, 60.0)])
        self.assertAlmostEqual(rv.pulse_ms_for_volume(30.0, liquid="water"), 150.0)
        self.assertAlmostEqual(rv.pulse_ms_for_volume(120.0, liquid="water"), 600.0)

    def test_multi_point_round_trips(self):
        self.load([
            CalibrationPoint("center", "water", 100, 20.0),
            CalibrationPoint("center", "water", 300, 60.0),
            CalibrationPoint("center", "water", 500, 90.0),
        ])
        # Measured points come back exactly; interior targets land on the segment.
        self.assertAlmostEqual(rv.pulse_ms_for_volume(60.0, liquid="water"), 300.0)
        self.assertAlmostEqual(rv.pulse_ms_for_volume(75.0, liquid="water"), 400.0)
        # Outside the measured range extrapolates along the end segments.
        self.assertAlmostEqual(rv.pulse_ms_for_volume(10.0, liquid="water"), 50.0)
        self.assertAlmostEqual(rv.pulse_ms_for_volume(105.0, liquid="water"), 600.0)
        # Round trip through the forward lookup for arbitrary targets.
        for target in (25.0, 47.5, 66.7, 88.0):
            ms = rv.pulse_ms_for_volume(target, liquid="water")
            self.assertAlmostEqual(rv.reward_volume_ul(ms, liquid="water"), target)

    def test_nonpositive_target(self):
        self.load([CalibrationPoint("center", "water", 300, 60.0)])
        self.assertEqual(rv.pulse_ms_for_volume(0.0, liquid="water"), 0.0)
        self.assertEqual(rv.pulse_ms_for_volume(-5.0, liquid="water"), 0.0)
        self.assertEqual(rv.pulse_ms_for_volume("abc", liquid="water"), 0.0)

    def test_round_pulse_ms(self):
        self.assertEqual(rv.round_pulse_ms(351.4), 350)
        self.assertEqual(rv.round_pulse_ms(387.6), 390)
        self.assertEqual(rv.round_pulse_ms(272.5), 270)
        # Never rounds to 0 ms (that would be "no reward" to the firmware).
        self.assertEqual(rv.round_pulse_ms(1.0), rv.PULSE_MS_STEP)
        self.assertEqual(rv.round_pulse_ms(123.4, step=0), 123)


class TestRealCalibration(unittest.TestCase):
    """Sanity check against the committed host/reward_calibration.csv so a
    hand-computed per-animal width (ADR-0191) agrees with the code."""

    def setUp(self):
        rv._CALIBRATION_PATH = None
        rv.reload_calibration()

    def test_seventy_ul_is_about_365_ms(self):
        ms = rv.pulse_ms_for_volume(70.0, liquid="sucrose_10pct")
        self.assertEqual(rv.round_pulse_ms(ms), 365)
        # 8 / 9 / 6 mL per day over 120 trials (docs/decisions/0191 worked example).
        self.assertEqual(rv.round_pulse_ms(
            rv.pulse_ms_for_volume(8000 / 120, liquid="sucrose_10pct")), 350)
        self.assertEqual(rv.round_pulse_ms(
            rv.pulse_ms_for_volume(75.0, liquid="sucrose_10pct")), 390)
        self.assertEqual(rv.round_pulse_ms(
            rv.pulse_ms_for_volume(50.0, liquid="sucrose_10pct")), 275)


if __name__ == "__main__":
    unittest.main()
