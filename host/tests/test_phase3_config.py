"""
test_phase3_config.py - headless tests for the config-driven Phase 3 core
(phase3_config.py; FW-0024 / docs/decisions/0070).

Covers: defaults derived from the dataclasses, partial-file merge over defaults,
unknown-key (_schema) preservation, load/save round-trip, the resolvers +
range validation, and the optional-seed policy. Pure stdlib; no Tk / hardware.

Run from host/:  python -m unittest test_phase3_config
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tmaze.core.cue_gate import CueParams
from tmaze.core.phase3.phase3_config import (
    DEFAULT_MAX_TOPUPS, default_config, load_phase3_config, resolve_seed,
    reward_max_topups, save_phase3_config, to_cue_params, to_phase3_config,
    to_session_cap_config, to_trial_config, validate_phase3_config)
from tmaze.core.phase3.session_cap import SessionCapConfig
from tmaze.core.phase3.phase3_task import Phase3Config
from tmaze.core.phase3.trial_sequence import TrialConfig


class TestDefaults(unittest.TestCase):
    def test_defaults_match_dataclasses(self):
        cfg = default_config()
        pc, cp, tc = Phase3Config(), CueParams(), TrialConfig()
        self.assertEqual(cfg["reward"]["port"], pc.reward_port)
        self.assertEqual(cfg["reward"]["pulse_ms"], pc.reward_pulse_ms)
        self.assertEqual(cfg["reward"]["max_topups"], DEFAULT_MAX_TOPUPS)
        self.assertEqual(cfg["reward"]["reward_incorrect"], pc.reward_incorrect)
        self.assertEqual(cfg["cue"]["min_confidence"], cp.min_confidence)
        self.assertEqual(cfg["cue"]["require_up_stem"], cp.require_up_stem)
        self.assertEqual(cfg["sequence"]["block_size"], tc.block_size)
        self.assertEqual(cfg["sequence"]["high_tone_hz"], tc.high_tone_hz)
        self.assertIsNone(cfg["sequence"]["seed"])

    def test_defaults_validate_and_resolve(self):
        cfg = default_config()
        validate_phase3_config(cfg)              # must not raise
        self.assertEqual(to_phase3_config(cfg), Phase3Config())
        self.assertEqual(to_cue_params(cfg), CueParams())
        self.assertEqual(to_trial_config(cfg), TrialConfig())
        self.assertEqual(reward_max_topups(cfg), DEFAULT_MAX_TOPUPS)

    def test_max_alt_run_round_trips(self):
        cfg = default_config()
        self.assertEqual(cfg["sequence"]["max_alt_run"], TrialConfig().max_alt_run)
        cfg["sequence"]["max_alt_run"] = 4
        self.assertEqual(to_trial_config(cfg).max_alt_run, 4)

    def test_session_cap_defaults_and_resolve(self):
        """The session_cap section (ADR-0190) mirrors SessionCapConfig and a file
        without it (pre-cap phase3.json) resolves to the defaults."""
        cfg = default_config()
        sc = SessionCapConfig()
        self.assertEqual(cfg["session_cap"],
                         {"enabled": sc.enabled, "max_trials": sc.max_trials,
                          "max_minutes": sc.max_minutes,
                          "water_at_chance_fraction": sc.water_at_chance_fraction})
        self.assertEqual(to_session_cap_config(cfg), sc)
        self.assertEqual(to_session_cap_config({}), sc)
        cfg["session_cap"].update({"enabled": False, "max_trials": 80,
                                   "max_minutes": 30, "water_at_chance_fraction": 0.6})
        got = to_session_cap_config(cfg)
        self.assertEqual(got, SessionCapConfig(enabled=False, max_trials=80,
                                               max_minutes=30.0,
                                               water_at_chance_fraction=0.6))


class TestLoadMerge(unittest.TestCase):
    def _write(self, obj) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        self.addCleanup(lambda: os.remove(path))
        return path

    def test_missing_file_returns_defaults(self):
        cfg = load_phase3_config(os.path.join(tempfile.gettempdir(), "nope_p3.json"))
        self.assertEqual(cfg, default_config())

    def test_partial_file_merges_over_defaults(self):
        path = self._write({"reward": {"pulse_ms": 250}})
        cfg = load_phase3_config(path)
        self.assertEqual(cfg["reward"]["pulse_ms"], 250)          # overridden
        self.assertEqual(cfg["reward"]["max_topups"], DEFAULT_MAX_TOPUPS)  # default
        self.assertEqual(cfg["cue"], default_config()["cue"])     # section default

    def test_unknown_keys_preserved(self):
        path = self._write({"_schema": {"note": "hi"}, "extra": 7})
        cfg = load_phase3_config(path)
        self.assertEqual(cfg["_schema"], {"note": "hi"})
        self.assertEqual(cfg["extra"], 7)

    def test_non_object_rejected(self):
        path = self._write([1, 2, 3])
        with self.assertRaises(ValueError):
            load_phase3_config(path)

    def test_round_trip(self):
        path = self._write({})
        cfg = load_phase3_config(path)
        cfg["reward"]["pulse_ms"] = 333
        save_phase3_config(path, cfg)
        again = load_phase3_config(path)
        self.assertEqual(again["reward"]["pulse_ms"], 333)
        self.assertIn("_schema", again)          # doc block survived the write


class TestSeedPolicy(unittest.TestCase):
    def test_null_seed_is_none(self):
        self.assertIsNone(resolve_seed(default_config()))

    def test_int_seed_masked(self):
        cfg = default_config()
        cfg["sequence"]["seed"] = 0x1_0000_0007      # > 32 bits
        self.assertEqual(resolve_seed(cfg), 7)


class TestValidation(unittest.TestCase):
    def _bad(self, section, key, value):
        cfg = default_config()
        cfg[section][key] = value
        with self.assertRaises((ValueError, TypeError)):
            validate_phase3_config(cfg)

    def test_bad_pulse(self):
        self._bad("reward", "pulse_ms", 0)

    def test_bad_topups(self):
        self._bad("reward", "max_topups", -1)

    def test_bad_confidence(self):
        self._bad("cue", "min_confidence", 1.5)

    def test_bad_block_size(self):
        self._bad("sequence", "block_size", 0)

    def test_bad_max_alt_run(self):
        self._bad("sequence", "max_alt_run", -1)

    def test_bad_alt_exit_repeats_per_side(self):
        # Zero, and greater than max_block_len, are both out of range (ADR-0129).
        for bad in (0, 999):
            cfg = default_config()
            cfg["sequence"]["alternation_blocks"]["exit_repeats_per_side"] = bad
            with self.assertRaises((ValueError, TypeError)):
                validate_phase3_config(cfg)

    def test_bad_debias_alt_veto_rate(self):
        # A rate in [0, 1]; 0 is allowed (veto off, ADR-0149), out-of-range is not.
        for bad in (-0.1, 1.5):
            cfg = default_config()
            cfg["sequence"]["debias_blocks"]["alt_veto_rate"] = bad
            with self.assertRaises((ValueError, TypeError)):
                validate_phase3_config(cfg)
        cfg = default_config()
        cfg["sequence"]["debias_blocks"]["alt_veto_rate"] = 0
        validate_phase3_config(cfg)                    # off is valid

    def test_bad_tone(self):
        self._bad("sequence", "high_tone_hz", -1)

    def test_non_numeric_pulse_raises(self):
        self._bad("reward", "pulse_ms", "abc")

    def test_bad_session_cap(self):
        # Validated even when disabled (ADR-0190), so a bad value is caught on
        # Save rather than the day the cap is switched on.
        self._bad("session_cap", "max_trials", 0)
        self._bad("session_cap", "max_minutes", 0)
        self._bad("session_cap", "water_at_chance_fraction", 0)
        self._bad("session_cap", "water_at_chance_fraction", 1.5)
        cfg = default_config()
        cfg["session_cap"]["enabled"] = False
        cfg["session_cap"]["max_trials"] = -1
        with self.assertRaises(ValueError):
            validate_phase3_config(cfg)


if __name__ == "__main__":
    unittest.main()
