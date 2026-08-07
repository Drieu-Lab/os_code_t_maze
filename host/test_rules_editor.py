"""test_rules_editor.py - pure-core tests for the Rules-tab field editor.

Covers the editor support behind the GUI (docs/decisions/0031): value coercion,
the debias sub-config helper, whole-profile validation, and round-tripping a
config through save/load while preserving the ``_schema`` block and any key the
editor did not touch.
"""

import json
import os
import tempfile
import unittest

from tmaze.core.rules_engine import (
    coerce_rule_field, load_rules_config, save_rules_config, set_rule_debias,
    validate_profile_rules,
)


class TestCoerce(unittest.TestCase):
    def test_int_float_bool_str(self):
        self.assertEqual(coerce_rule_field("int", "3"), 3)
        self.assertEqual(coerce_rule_field("float", "0.25"), 0.25)
        self.assertIs(coerce_rule_field("bool", True), True)
        self.assertIs(coerce_rule_field("bool", False), False)
        self.assertEqual(coerce_rule_field("str", "choice"), "choice")

    def test_bad_int_raises(self):
        with self.assertRaises(ValueError):
            coerce_rule_field("int", "not-a-number")


class TestDebias(unittest.TestCase):
    def test_none_removes_key(self):
        rule = {"debias": {"mode": "fixed", "n": 3}}
        set_rule_debias(rule, "none")
        self.assertNotIn("debias", rule)
        # Removing an absent key is a no-op, not an error.
        set_rule_debias(rule, None)
        self.assertNotIn("debias", rule)

    def test_fixed_and_uniform(self):
        rule: dict = {}
        set_rule_debias(rule, "fixed", n=4)
        self.assertEqual(rule["debias"], {"mode": "fixed", "n": 4})
        set_rule_debias(rule, "uniform", lo=2, hi=6)
        self.assertEqual(rule["debias"], {"mode": "uniform", "min": 2, "max": 6})

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            set_rule_debias({}, "bananas")


class TestValidate(unittest.TestCase):
    def _rule(self, **over):
        rule = {"id": "w", "template": "well_reward_with_excursion",
                "credential": "choice", "prime_on": "well_arrival", "port": 2}
        rule.update(over)
        return rule

    def test_valid_profile_ok(self):
        validate_profile_rules([self._rule()], {"min_confidence": 0.5})

    def test_unknown_credential_raises(self):
        with self.assertRaises(ValueError):
            validate_profile_rules([self._rule(credential="bogus")], None)

    def test_unknown_prime_on_raises(self):
        with self.assertRaises(ValueError):
            validate_profile_rules([self._rule(prime_on="whenever")], None)


class TestRoundTrip(unittest.TestCase):
    def _config(self):
        return {
            "_schema": {"about": "docs block that must survive a save"},
            "version": 2,
            "active_profile": "phase1",
            "defaults": {"min_confidence": 0.5, "global_min_interval_ms": 500},
            "profiles": {
                "phase1": {
                    "label": "P1",
                    "rules": [{
                        "id": "w", "template": "well_reward_with_excursion",
                        "credential": "choice", "prime_on": "well_arrival",
                        "port": 2, "pulse_ms": 300, "min_confidence": 0.5,
                    }],
                },
                "phase2b": {
                    "label": "P2b",
                    "rules": [{
                        "id": "b", "template": "well_reward_with_excursion",
                        "credential": "choice_via_stem", "prime_on": "credential",
                        "port": 2, "pulse_ms": 300,
                        "debias": {"mode": "uniform", "min": 3, "max": 5},
                    }],
                },
            },
        }

    def test_edit_in_place_preserves_schema_and_untouched(self):
        config = self._config()
        # Edit one field the way the GUI does: mutate the live rule dict.
        rule = config["profiles"]["phase1"]["rules"][0]
        rule["pulse_ms"] = coerce_rule_field("int", "250")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rules.json")
            save_rules_config(path, config)
            back = load_rules_config(path)
        self.assertEqual(back["_schema"], {"about": "docs block that must survive a save"})
        self.assertEqual(back["version"], 2)
        self.assertEqual(back["defaults"]["global_min_interval_ms"], 500)
        r = back["profiles"]["phase1"]["rules"][0]
        self.assertEqual(r["pulse_ms"], 250)
        # A field the editor never touched is preserved verbatim.
        self.assertEqual(r["min_confidence"], 0.5)
        # An untouched OTHER profile is preserved verbatim.
        self.assertEqual(back["profiles"]["phase2b"]["rules"][0]["debias"],
                         {"mode": "uniform", "min": 3, "max": 5})

    def test_clearing_debias_is_persisted(self):
        config = self._config()
        set_rule_debias(config["profiles"]["phase2b"]["rules"][0], "none")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rules.json")
            save_rules_config(path, config)
            back = load_rules_config(path)
        self.assertNotIn("debias", back["profiles"]["phase2b"]["rules"][0])

    def test_saved_file_is_valid_json_with_trailing_newline(self):
        config = self._config()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "rules.json")
            save_rules_config(path, config)
            with open(path, encoding="utf-8") as f:
                text = f.read()
        self.assertTrue(text.endswith("\n"))
        json.loads(text)   # parses


if __name__ == "__main__":
    unittest.main()
