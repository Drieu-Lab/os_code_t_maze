"""
test_reward_calibration.py - headless tests for the reward-calibration core.

Covers the per-pulse volume math (:func:`ul_per_pulse`, :func:`ml_to_ul`), liquid
name normalization (:func:`normalize_liquid`, incl. the "10% sucros" typo) and
port normalization, and the CSV round-trip / upsert / lookup grouping, per
docs/decisions/0091. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_reward_calibration
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.reward.reward_calibration import (
    DEFAULT_REWARD_LIQUID,
    SCHEMA_VERSION,
    CalibrationPoint,
    liquid_label,
    load_calibration,
    ml_to_ul,
    normalize_liquid,
    normalize_port,
    read_points,
    to_lookup,
    ul_per_pulse,
    upsert_points,
    write_points,
)


class TestMath(unittest.TestCase):
    def test_ul_per_pulse(self):
        self.assertAlmostEqual(ul_per_pulse(3000.0, 50), 60.0)
        self.assertAlmostEqual(ul_per_pulse(150.0, 3), 50.0)

    def test_ul_per_pulse_rejects_nonpositive_n(self):
        with self.assertRaises(ValueError):
            ul_per_pulse(1000.0, 0)
        with self.assertRaises(ValueError):
            ul_per_pulse(1000.0, -5)

    def test_ml_to_ul(self):
        self.assertAlmostEqual(ml_to_ul(3.1), 3100.0)


class TestNormalizeLiquid(unittest.TestCase):
    def test_known_tokens(self):
        self.assertEqual(normalize_liquid("water"), "water")
        self.assertEqual(normalize_liquid("sucrose_10pct"), "sucrose_10pct")

    def test_display_labels_and_aliases(self):
        self.assertEqual(normalize_liquid("10% sucrose"), "sucrose_10pct")
        self.assertEqual(normalize_liquid("Sucrose"), "sucrose_10pct")
        self.assertEqual(normalize_liquid("H2O"), "water")

    def test_typo_10_sucros(self):
        # The exact typo from the operator's screenshot must still key cleanly.
        self.assertEqual(normalize_liquid("10% sucros"), "sucrose_10pct")

    def test_empty_falls_back_to_default(self):
        self.assertEqual(normalize_liquid(""), DEFAULT_REWARD_LIQUID)
        self.assertEqual(normalize_liquid(None), DEFAULT_REWARD_LIQUID)

    def test_unknown_kept_as_stable_token(self):
        self.assertEqual(normalize_liquid("2% Sucrose"), "2pct_sucrose")

    def test_liquid_label(self):
        self.assertEqual(liquid_label("sucrose_10pct"), "10% sucrose")
        self.assertEqual(liquid_label("water"), "water")
        self.assertEqual(liquid_label("mystery"), "mystery")  # unknown -> itself


class TestNormalizePort(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(normalize_port("center"), "center")
        self.assertEqual(normalize_port("Central"), "center")
        self.assertEqual(normalize_port("left"), "left")

    def test_indices(self):
        self.assertEqual(normalize_port("0"), "left")
        self.assertEqual(normalize_port("1"), "right")
        self.assertEqual(normalize_port("2"), "center")

    def test_empty_defaults_center(self):
        self.assertEqual(normalize_port(""), "center")
        self.assertEqual(normalize_port(None), "center")


class TestCsvRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cal.csv")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_read_round_trip(self):
        pts = [
            CalibrationPoint("center", "water", 300, 60.0,
                             n_pulses=50, measured_ul=3000.0,
                             date="2026-08-11", operator="RJ", notes="ok"),
            CalibrationPoint("center", "sucrose_10pct", 300, 45.0,
                             n_pulses=50, measured_ul=2250.0),
        ]
        write_points(self.path, pts)
        # The schema-version comment line is present and readers skip it.
        with open(self.path, encoding="utf-8") as fh:
            first = fh.readline().strip()
        self.assertEqual(first, f"# schema_version={SCHEMA_VERSION}")

        back = read_points(self.path)
        self.assertEqual(len(back), 2)
        water = next(p for p in back if p.liquid == "water")
        self.assertEqual(water.pulse_ms, 300)
        self.assertAlmostEqual(water.volume_ul, 60.0)
        self.assertEqual(water.n_pulses, 50)
        self.assertAlmostEqual(water.measured_ul, 3000.0)
        self.assertEqual(water.operator, "RJ")

    def test_missing_file_is_empty(self):
        self.assertEqual(read_points(os.path.join(self.dir, "nope.csv")), [])
        self.assertEqual(load_calibration(os.path.join(self.dir, "nope.csv")), {})

    def test_upsert_replaces_same_key(self):
        base = [CalibrationPoint("center", "water", 300, 60.0)]
        new = [CalibrationPoint("center", "water", 300, 58.0),   # replaces
               CalibrationPoint("center", "water", 150, 30.0)]   # appends
        merged = upsert_points(base, new)
        by_ms = {p.pulse_ms: p.volume_ul for p in merged
                 if p.port == "center" and p.liquid == "water"}
        self.assertAlmostEqual(by_ms[300], 58.0)
        self.assertAlmostEqual(by_ms[150], 30.0)
        self.assertEqual(len(merged), 2)

    def test_to_lookup_groups_by_port_liquid(self):
        pts = [
            CalibrationPoint("center", "water", 150, 30.0),
            CalibrationPoint("center", "water", 300, 60.0),
            CalibrationPoint("center", "sucrose_10pct", 300, 45.0),
        ]
        lut = to_lookup(pts)
        self.assertEqual(lut[("center", "water")], {150: 30.0, 300: 60.0})
        self.assertEqual(lut[("center", "sucrose_10pct")], {300: 45.0})

    def test_free_typed_rows_are_normalized_on_read(self):
        # A hand-edited CSV using a display label + index still keys canonically.
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(f"# schema_version={SCHEMA_VERSION}\n")
            fh.write("port,liquid,pulse_ms,volume_ul,n_pulses,measured_ul,"
                     "date,operator,notes\n")
            fh.write("2,10% sucrose,300,45,50,2250,,,\n")
        pts = read_points(self.path)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0].port, "center")
        self.assertEqual(pts[0].liquid, "sucrose_10pct")


if __name__ == "__main__":
    unittest.main()
