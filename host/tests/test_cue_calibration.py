"""
test_cue_calibration.py - headless tests for the per-cue calibration (volume +
frequency).

Covers, for BOTH the gain_pct and commanded_hz blocks: the JSON round-trip and
schema-2 shape, the missing-file / unknown-key fallback (100 % gain, identity
frequency), clamping on read and write, that writing one block PRESERVES the
other, the cached lookups via reload_calibration, and that a malformed file
raises in the loaders but is tolerated by reload_calibration (so a bad save can't
crash the cue path). Per docs/decisions/0125 (volume) and 0148 (frequency).
Points the module at a temp file and restores the default in tearDown so later
tests see the real host JSON. Pure stdlib; no Tk.

Run from host/tests/:  python -m unittest test_cue_calibration
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tmaze.core import cue_calibration as cc


class _CalFile(unittest.TestCase):
    """Base that gives each test a temp calibration path + cache restore."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cue_calibration.json")

    def tearDown(self):
        # Restore the default (real host JSON) source so we don't leak our temp
        # path/cache into later tests.
        cc._GAINS_PATH = None
        cc._GAINS = None
        cc._COMMANDED = None
        shutil.rmtree(self.dir, ignore_errors=True)


class RoundTripTests(_CalFile):
    def test_save_then_load_round_trips(self):
        cc.save_calibration(self.path, {4000: 100, 8000: 82},
                            {4000: 4050, 8000: 8100})
        self.assertEqual(cc.load_gains(self.path), {4000: 100, 8000: 82})
        self.assertEqual(cc.load_commanded(self.path), {4000: 4050, 8000: 8100})

    def test_saved_file_has_schema_and_both_blocks(self):
        cc.save_calibration(self.path, {8000: 82}, {8000: 8100})
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw["_schema"], cc.SCHEMA_VERSION)
        self.assertEqual(raw["gain_pct"], {"8000": 82})
        self.assertEqual(raw["commanded_hz"], {"8000": 8100})

    def test_save_clamps_and_normalizes(self):
        # Out-of-range gains/commanded clamp; float / non-positive keys normalize/drop.
        cc.save_calibration(self.path, {4000.0: 150, 8000: -5, 0: 50},
                            {4000: 30, 8000: 99999, 0: 5000})
        self.assertEqual(cc.load_gains(self.path), {4000: 100, 8000: 0})
        # 30 clamps up to FREQ_HZ_MIN, 99999 down to FREQ_HZ_MAX, key 0 dropped.
        self.assertEqual(cc.load_commanded(self.path),
                         {4000: cc.FREQ_HZ_MIN, 8000: cc.FREQ_HZ_MAX})

    def test_keys_sorted_numerically(self):
        cc.save_calibration(self.path, {8000: 80, 4000: 90, 12000: 70},
                            {8000: 8000, 4000: 4000, 12000: 12000})
        with open(self.path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(list(raw["gain_pct"].keys()), ["4000", "8000", "12000"])
        self.assertEqual(list(raw["commanded_hz"].keys()),
                         ["4000", "8000", "12000"])


class BlockPreservationTests(_CalFile):
    def test_save_gains_preserves_commanded(self):
        cc.save_calibration(self.path, {8000: 82}, {8000: 8100})
        cc.save_gains(self.path, {8000: 50})  # only touches volume
        self.assertEqual(cc.load_gains(self.path), {8000: 50})
        self.assertEqual(cc.load_commanded(self.path), {8000: 8100})

    def test_save_commanded_preserves_gains(self):
        cc.save_calibration(self.path, {8000: 82}, {8000: 8100})
        cc.save_commanded(self.path, {8000: 8200})  # only touches frequency
        self.assertEqual(cc.load_commanded(self.path), {8000: 8200})
        self.assertEqual(cc.load_gains(self.path), {8000: 82})


class LoadFallbackTests(_CalFile):
    def test_missing_file_is_empty(self):
        self.assertEqual(cc.load_gains(self.path), {})
        self.assertEqual(cc.load_commanded(self.path), {})

    def test_absent_commanded_block_is_empty(self):
        # A v1-style file (gain_pct only) loads commanded as empty (identity).
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"gain_pct": {"8000": 82}}, fh)
        self.assertEqual(cc.load_commanded(self.path), {})

    def test_load_clamps_bad_values(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"gain_pct": {"4000": 200, "8000": "oops"},
                       "commanded_hz": {"4000": 30, "8000": "oops"}}, fh)
        self.assertEqual(cc.load_gains(self.path),
                         {4000: 100, 8000: cc.DEFAULT_GAIN_PCT})
        # 30 clamps up to FREQ_HZ_MIN; the non-numeric commanded is dropped.
        self.assertEqual(cc.load_commanded(self.path), {4000: cc.FREQ_HZ_MIN})

    def test_non_object_raises(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump([1, 2, 3], fh)
        with self.assertRaises(ValueError):
            cc.load_gains(self.path)
        with self.assertRaises(ValueError):
            cc.load_commanded(self.path)

    def test_bad_gain_block_raises(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"gain_pct": [1, 2]}, fh)
        with self.assertRaises(ValueError):
            cc.load_gains(self.path)

    def test_bad_commanded_block_raises(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"commanded_hz": [1, 2]}, fh)
        with self.assertRaises(ValueError):
            cc.load_commanded(self.path)


class ClampTests(unittest.TestCase):
    def test_clamp_gain_pct(self):
        self.assertEqual(cc.clamp_gain_pct(50), 50)
        self.assertEqual(cc.clamp_gain_pct(150), cc.GAIN_PCT_MAX)
        self.assertEqual(cc.clamp_gain_pct(-10), cc.GAIN_PCT_MIN)
        self.assertEqual(cc.clamp_gain_pct(73.6), 74)

    def test_clamp_gain_non_numeric_is_default(self):
        self.assertEqual(cc.clamp_gain_pct(None), cc.DEFAULT_GAIN_PCT)
        self.assertEqual(cc.clamp_gain_pct("x"), cc.DEFAULT_GAIN_PCT)

    def test_clamp_freq_hz(self):
        self.assertEqual(cc.clamp_freq_hz(8100), 8100)
        self.assertEqual(cc.clamp_freq_hz(30), cc.FREQ_HZ_MIN)
        self.assertEqual(cc.clamp_freq_hz(99999), cc.FREQ_HZ_MAX)
        self.assertEqual(cc.clamp_freq_hz(8100.4), 8100)

    def test_clamp_freq_non_numeric_uses_default(self):
        # Bad measured value falls back to the (normalized) default target.
        self.assertEqual(cc.clamp_freq_hz(None, default=8000), 8000)
        self.assertEqual(cc.clamp_freq_hz("x", default=4000), 4000)
        self.assertIsNone(cc.clamp_freq_hz(None))


class GainLookupTests(_CalFile):
    def test_gain_for_calibrated_and_uncalibrated(self):
        cc.save_calibration(self.path, {8000: 82}, {})
        cc.reload_calibration(self.path)
        self.assertEqual(cc.gain_pct_for(8000), 82)       # calibrated
        self.assertEqual(cc.gain_pct_for(4000), 100)      # unknown -> full
        self.assertEqual(cc.gain_pct_for(8000.0), 82)     # float freq normalizes

    def test_missing_file_all_full_volume(self):
        cc.reload_calibration(self.path)                  # file does not exist
        self.assertEqual(cc.gain_pct_for(4000), cc.DEFAULT_GAIN_PCT)
        self.assertEqual(cc.gain_pct_for(8000), cc.DEFAULT_GAIN_PCT)

    def test_bad_freq_is_full_volume(self):
        cc.reload_calibration(self.path)
        self.assertEqual(cc.gain_pct_for(None), cc.DEFAULT_GAIN_PCT)
        self.assertEqual(cc.gain_pct_for(0), cc.DEFAULT_GAIN_PCT)


class CommandedLookupTests(_CalFile):
    def test_commanded_for_calibrated_and_uncalibrated(self):
        cc.save_calibration(self.path, {}, {8000: 8100})
        cc.reload_calibration(self.path)
        self.assertEqual(cc.commanded_hz_for(8000), 8100)   # calibrated
        self.assertEqual(cc.commanded_hz_for(4000), 4000)   # unknown -> identity
        self.assertEqual(cc.commanded_hz_for(8000.0), 8100)  # float normalizes

    def test_missing_file_is_identity(self):
        cc.reload_calibration(self.path)                    # file does not exist
        self.assertEqual(cc.commanded_hz_for(4000), 4000)
        self.assertEqual(cc.commanded_hz_for(8000), 8000)

    def test_bad_target_is_none(self):
        cc.reload_calibration(self.path)
        self.assertIsNone(cc.commanded_hz_for(None))
        self.assertIsNone(cc.commanded_hz_for(0))


class ReloadToleranceTests(_CalFile):
    def test_reload_tolerates_malformed_file(self):
        # A first good load, then a corrupt file: reload must not raise and must
        # keep the previous maps (a bad save can't crash the cue path mid-session).
        cc.save_calibration(self.path, {8000: 82}, {8000: 8100})
        cc.reload_calibration(self.path)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        cc.reload_calibration()                             # same path, now corrupt
        self.assertEqual(cc.gain_pct_for(8000), 82)
        self.assertEqual(cc.commanded_hz_for(8000), 8100)


if __name__ == "__main__":
    unittest.main()
