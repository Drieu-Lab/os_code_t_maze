"""
test_cue_volume.py - headless tests for the per-cue volume calibration.

Covers the JSON round-trip, the missing-file / unknown-frequency fallback to
100 % (full volume, no change), clamping on both read and write, the cached
lookup via reload_calibration, and that a malformed file raises in load_gains but
is tolerated by reload_calibration (so a bad save can't crash the cue path). Per
docs/decisions/0125. Points cue_volume at a temp file and restores the default in
tearDown so later tests see the real host JSON. Pure stdlib; no Tk.

Run from host/:  python -m unittest test_cue_volume
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tmaze.core import cue_volume as cv


class _CalFile(unittest.TestCase):
    """Base that gives each test a temp calibration path + cache restore."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cue_calibration.json")

    def tearDown(self):
        # Restore the default (real host JSON) source so we don't leak our temp
        # path/cache into later tests.
        cv._GAINS_PATH = None
        cv._GAINS = None
        shutil.rmtree(self.dir, ignore_errors=True)


class RoundTripTests(_CalFile):
    def test_save_then_load_round_trips(self):
        cv.save_gains(self.path, {4000: 100, 8000: 82})
        self.assertEqual(cv.load_gains(self.path), {4000: 100, 8000: 82})

    def test_saved_file_has_schema_and_string_keys(self):
        cv.save_gains(self.path, {8000: 82})
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw["_schema"], cv.SCHEMA_VERSION)
        self.assertEqual(raw["gain_pct"], {"8000": 82})

    def test_save_clamps_and_normalizes(self):
        # Out-of-range gains clamp; float / non-positive freqs are normalized/dropped.
        cv.save_gains(self.path, {4000.0: 150, 8000: -5, 0: 50})
        self.assertEqual(cv.load_gains(self.path), {4000: 100, 8000: 0})

    def test_keys_sorted_numerically(self):
        cv.save_gains(self.path, {8000: 80, 4000: 90, 12000: 70})
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(list(raw["gain_pct"].keys()), ["4000", "8000", "12000"])


class LoadFallbackTests(_CalFile):
    def test_missing_file_is_empty(self):
        self.assertEqual(cv.load_gains(self.path), {})

    def test_load_clamps_bad_values(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"gain_pct": {"4000": 200, "8000": "oops"}}, fh)
        self.assertEqual(cv.load_gains(self.path), {4000: 100, 8000: cv.DEFAULT_GAIN_PCT})

    def test_non_object_raises(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump([1, 2, 3], fh)
        with self.assertRaises(ValueError):
            cv.load_gains(self.path)

    def test_bad_gain_block_raises(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"gain_pct": [1, 2]}, fh)
        with self.assertRaises(ValueError):
            cv.load_gains(self.path)


class ClampTests(unittest.TestCase):
    def test_clamp_gain_pct(self):
        self.assertEqual(cv.clamp_gain_pct(50), 50)
        self.assertEqual(cv.clamp_gain_pct(150), cv.GAIN_PCT_MAX)
        self.assertEqual(cv.clamp_gain_pct(-10), cv.GAIN_PCT_MIN)
        self.assertEqual(cv.clamp_gain_pct(73.6), 74)

    def test_clamp_non_numeric_is_default(self):
        self.assertEqual(cv.clamp_gain_pct(None), cv.DEFAULT_GAIN_PCT)
        self.assertEqual(cv.clamp_gain_pct("x"), cv.DEFAULT_GAIN_PCT)


class LookupTests(_CalFile):
    def test_gain_for_calibrated_and_uncalibrated(self):
        cv.save_gains(self.path, {8000: 82})
        cv.reload_calibration(self.path)
        self.assertEqual(cv.gain_pct_for(8000), 82)       # calibrated
        self.assertEqual(cv.gain_pct_for(4000), 100)      # unknown -> full
        self.assertEqual(cv.gain_pct_for(8000.0), 82)     # float freq normalizes

    def test_missing_file_all_full_volume(self):
        cv.reload_calibration(self.path)                  # file does not exist
        self.assertEqual(cv.gain_pct_for(4000), cv.DEFAULT_GAIN_PCT)
        self.assertEqual(cv.gain_pct_for(8000), cv.DEFAULT_GAIN_PCT)

    def test_bad_freq_is_full_volume(self):
        cv.reload_calibration(self.path)
        self.assertEqual(cv.gain_pct_for(None), cv.DEFAULT_GAIN_PCT)
        self.assertEqual(cv.gain_pct_for(0), cv.DEFAULT_GAIN_PCT)

    def test_reload_tolerates_malformed_file(self):
        # A first good load, then a corrupt file: reload must not raise and must
        # keep the previous map (a bad save can't crash the cue path mid-session).
        cv.save_gains(self.path, {8000: 82})
        cv.reload_calibration(self.path)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        cv.reload_calibration()                           # same path, now corrupt
        self.assertEqual(cv.gain_pct_for(8000), 82)


if __name__ == "__main__":
    unittest.main()
