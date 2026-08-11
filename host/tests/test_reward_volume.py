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


if __name__ == "__main__":
    unittest.main()
