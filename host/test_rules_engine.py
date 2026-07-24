"""
test_rules_engine.py - headless unit tests for the pure Rules Engine core.

No Tk, no OpenCV, no hardware: drives rules_engine.py over synthetic tracker
snapshots and lick lines. Run from host/:

    python -m unittest test_rules_engine

Covers (see docs/decisions/0023): enter fires prime once with re-arm on exit;
cm->px distance thresholds; per-visit top-up count cap; the within-zone-at-lick
conjunction; the confidence gate; lick debounce (the global rate backstop now
lives in reward_dispatcher, ADR-0054, tested in test_reward_dispatcher.py);
the split timing model (short topup_cooldown_ms within a visit vs the longer
rearm_refractory_ms between visits, incl. the anti-camp guarantee that a
re-entry inside the refractory pays nothing) with legacy cooldown_ms back-compat;
and the homography distance path.
"""

from __future__ import annotations

import types
import unittest

from rules_engine import (
    EngineDefaults, FireReward, LogEvent, RulesEngine, Scale, resolve_landmark,
)

# A single center-well landmark at the middle of a 1000x1000 frame.
FRAME_W, FRAME_H = 1000, 1000
CENTER_LANDMARKS = [
    {"kind": "well", "index": 2, "label": "Center", "x": 0.5, "y": 0.5},
]
# 0.1 cm per px -> 8 cm radius == 80 px around (500, 500).
SCALE = Scale(cm_per_px=0.1)


def tr(cx, cy, conf=0.9, state="TRACKING", w=FRAME_W, h=FRAME_H):
    """Build a duck-typed TrackResult-like snapshot."""
    return types.SimpleNamespace(
        detected=True, state=state, confidence=conf,
        frame_size=(w, h), centroid=(cx, cy), smoothed=(cx, cy))


def make_engine(rule=None, defaults=None):
    base_rule = {
        "id": "center", "template": "prime_and_lick_topup", "enabled": True,
        "landmark": {"kind": "well", "index": 2}, "radius_cm": 8.0, "port": 2,
        "pulse_ms": 150, "max_topups": 2, "min_confidence": 0.5,
        "lick_debounce_ms": 150, "cooldown_ms": 1000, "rearm_on_exit": True,
    }
    base_rule.update(rule or {})
    base_defaults = {"min_confidence": 0.5, "cooldown_ms": 1000,
                     "global_min_interval_ms": 500}
    base_defaults.update(defaults or {})
    config = {"version": 1, "enabled": True, "defaults": base_defaults,
              "rules": [base_rule]}
    eng = RulesEngine.from_config(config)
    eng.enabled = True
    return eng


def fires(actions):
    return [a for a in actions if isinstance(a, FireReward)]


def logs(actions, name=None):
    return [a for a in actions if isinstance(a, LogEvent)
            and (name is None or a.name == name)]


class TestScale(unittest.TestCase):
    def test_global_scale_distance(self):
        self.assertAlmostEqual(SCALE.distance_cm((0, 0), (100, 0)), 10.0)

    def test_cm_to_px(self):
        self.assertAlmostEqual(SCALE.cm_to_px(8.0), 80.0)

    def test_homography_distance(self):
        # A pure 0.2 world-cm-per-px scaling expressed as a homography.
        H = [[0.2, 0.0, 0.0], [0.0, 0.2, 0.0], [0.0, 0.0, 1.0]]
        s = Scale(homography=H)
        self.assertTrue(s.valid())
        self.assertAlmostEqual(s.distance_cm((0, 0), (100, 0)), 20.0)
        # No global scale -> cm_to_px is position-dependent -> None.
        self.assertIsNone(s.cm_to_px(8.0))

    def test_invalid_scale(self):
        self.assertFalse(Scale().valid())

    def test_resolve_landmark(self):
        px = resolve_landmark(CENTER_LANDMARKS, {"kind": "well", "index": 2},
                              (FRAME_W, FRAME_H))
        self.assertEqual(px, (500.0, 500.0))
        self.assertIsNone(resolve_landmark(CENTER_LANDMARKS,
                                           {"kind": "well", "index": 0},
                                           (FRAME_W, FRAME_H)))


class TestPrimeAndReArm(unittest.TestCase):
    def test_enter_fires_prime_once_then_rearms(self):
        eng = make_engine()
        # Enter the zone (50 px = 5 cm < 8 cm) -> prime once.
        a = eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)
        self.assertEqual(len(fires(a)), 1)
        # The FireReward still carries its reason; the paired "prime" LogEvent is
        # now emitted by the executor from the dispatcher record, not the engine
        # (ADR-0054), so it is asserted in test_reward_dispatcher.py instead.
        self.assertEqual(fires(a)[0].reason, "prime")

        # Staying inside does not re-fire.
        a = eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.1)
        self.assertEqual(len(fires(a)), 0)

        # Leaving the zone re-arms (200 px = 20 cm).
        a = eng.tick(tr(800, 500), CENTER_LANDMARKS, SCALE, 0.2)
        self.assertEqual(len(fires(a)), 0)
        self.assertEqual(len(logs(a, "rearm")), 1)

        # Re-entering after the cooldown fires a fresh prime.
        a = eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 1.5)
        self.assertEqual(len(fires(a)), 1)
        self.assertEqual(fires(a)[0].reason, "prime")

    def test_confidence_gate(self):
        eng = make_engine()
        # Low confidence -> not confident -> no prime.
        a = eng.tick(tr(550, 500, conf=0.1), CENTER_LANDMARKS, SCALE, 0.0)
        self.assertEqual(len(fires(a)), 0)
        # Not TRACKING -> no prime.
        a = eng.tick(tr(550, 500, state="ACQUIRING"), CENTER_LANDMARKS, SCALE, 0.1)
        self.assertEqual(len(fires(a)), 0)

    def test_disabled_and_uncalibrated(self):
        eng = make_engine()
        eng.enabled = False
        self.assertEqual(eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0), [])
        eng.enabled = True
        # No / invalid scale -> nothing fires.
        self.assertEqual(eng.tick(tr(550, 500), CENTER_LANDMARKS, None, 0.0), [])
        self.assertEqual(eng.tick(tr(550, 500), CENTER_LANDMARKS, Scale(), 0.0), [])


class TestDistanceThreshold(unittest.TestCase):
    def test_just_inside_fires(self):
        eng = make_engine()
        # 79 px = 7.9 cm < 8 cm.
        a = eng.tick(tr(579, 500), CENTER_LANDMARKS, SCALE, 0.0)
        self.assertEqual(len(fires(a)), 1)

    def test_just_outside_does_not_fire(self):
        eng = make_engine()
        # 81 px = 8.1 cm > 8 cm.
        a = eng.tick(tr(581, 500), CENTER_LANDMARKS, SCALE, 0.0)
        self.assertEqual(len(fires(a)), 0)


class TestLickTopups(unittest.TestCase):
    def test_topup_count_caps(self):
        eng = make_engine(rule={"cooldown_ms": 0, "lick_debounce_ms": 0,
                                "max_topups": 2},
                          defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)  # prime
        r1 = eng.event("10,L,2,1", 0.01)
        r2 = eng.event("11,L,2,1", 0.02)
        r3 = eng.event("12,L,2,1", 0.03)  # exceeds max_topups
        self.assertEqual(len(fires(r1)), 1)
        self.assertEqual(fires(r1)[0].reason, "topup")
        self.assertEqual(len(fires(r2)), 1)
        self.assertEqual(len(fires(r3)), 0)

    def test_lick_outside_zone_does_not_fire(self):
        eng = make_engine(rule={"cooldown_ms": 0, "lick_debounce_ms": 0},
                          defaults={"global_min_interval_ms": 0})
        # Never entered the zone -> not primed.
        eng.tick(tr(800, 500), CENTER_LANDMARKS, SCALE, 0.0)
        r = eng.event("1,L,2,1", 0.01)
        self.assertEqual(len(fires(r)), 0)

    def test_conjunction_lick_after_leaving(self):
        eng = make_engine(rule={"cooldown_ms": 0, "lick_debounce_ms": 0},
                          defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)   # prime, primed
        eng.tick(tr(800, 500), CENTER_LANDMARKS, SCALE, 0.1)   # leave -> re-arm
        r = eng.event("1,L,2,1", 0.11)
        self.assertEqual(len(fires(r)), 0)

    def test_wrong_port_lick_ignored(self):
        eng = make_engine(rule={"cooldown_ms": 0, "lick_debounce_ms": 0},
                          defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)
        r = eng.event("1,L,0,1", 0.01)  # left port, rule watches center (2)
        self.assertEqual(len(fires(r)), 0)

    def test_lick_debounce(self):
        eng = make_engine(rule={"cooldown_ms": 0, "lick_debounce_ms": 150,
                                "max_topups": 5},
                          defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)
        r1 = eng.event("1,L,2,1", 0.01)
        r2 = eng.event("2,L,2,1", 0.05)   # 40 ms < 150 ms -> debounced
        self.assertEqual(len(fires(r1)), 1)
        self.assertEqual(len(fires(r2)), 0)


class TestCooldownAndBackstop(unittest.TestCase):
    def test_per_rule_cooldown(self):
        eng = make_engine(rule={"cooldown_ms": 1000, "lick_debounce_ms": 0,
                                "max_topups": 5},
                          defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)  # prime @ t=0
        r1 = eng.event("1,L,2,1", 0.1)   # 100 ms after prime < 1000 ms cooldown
        self.assertEqual(len(fires(r1)), 0)
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 1.05)  # keep fresh
        r2 = eng.event("2,L,2,1", 1.06)  # > 1000 ms since last fire
        self.assertEqual(len(fires(r2)), 1)

    def test_engine_no_longer_rate_limits(self):
        """The global rate backstop moved out of the engine to the shared
        reward_dispatcher (ADR-0054, FW-0019): the engine now returns EVERY
        rule's FireReward and emits no 'suppressed' LogEvent. The actual
        rate-limiting (one fire through, the next dropped) is asserted in
        test_reward_dispatcher.py against the shared seam."""
        rules = [
            {"id": "a", "template": "prime_and_lick_topup", "enabled": True,
             "landmark": {"kind": "well", "index": 2}, "radius_cm": 8.0,
             "port": 2, "pulse_ms": 150, "max_topups": 0, "cooldown_ms": 0},
            {"id": "b", "template": "prime_and_lick_topup", "enabled": True,
             "landmark": {"kind": "well", "index": 2}, "radius_cm": 8.0,
             "port": 2, "pulse_ms": 150, "max_topups": 0, "cooldown_ms": 0},
        ]
        config = {"version": 1, "enabled": True,
                  "defaults": {"min_confidence": 0.5,
                               "global_min_interval_ms": 500},
                  "rules": rules}
        eng = RulesEngine.from_config(config)
        eng.enabled = True
        a = eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)
        # Both rules prime; the engine passes both through (no backstop here).
        self.assertEqual(len(fires(a)), 2)
        self.assertEqual(len(logs(a, "suppressed")), 0)


class TestSplitTiming(unittest.TestCase):
    """topup_cooldown_ms (within a visit) vs rearm_refractory_ms (between
    visits), and the anti-camp guarantee that a re-entry inside the refractory
    pays nothing (see ADR-0023)."""

    def test_topup_uses_short_cooldown_not_refractory(self):
        # Long refractory must NOT block top-ups within the same visit.
        eng = make_engine(
            rule={"topup_cooldown_ms": 100, "rearm_refractory_ms": 5000,
                  "max_topups": 5, "lick_debounce_ms": 0},
            defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)      # prime
        r = eng.event("1,L,2,1", 0.2)   # 200 ms > 100 ms top-up cooldown
        self.assertEqual(len(fires(r)), 1)
        self.assertEqual(fires(r)[0].reason, "topup")

    def test_reentry_inside_refractory_pays_nothing(self):
        eng = make_engine(
            rule={"topup_cooldown_ms": 100, "rearm_refractory_ms": 5000,
                  "max_topups": 5, "lick_debounce_ms": 0},
            defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)      # prime, fire
        eng.tick(tr(800, 500), CENTER_LANDMARKS, SCALE, 0.3)      # leave -> re-arm
        # Re-enter only 0.5 s after the last fire (< 5 s refractory): no prime...
        a = eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.5)
        self.assertEqual(len(fires(a)), 0)
        # ...and no top-ups either (no prime happened this visit).
        r = eng.event("1,L,2,1", 0.6)
        self.assertEqual(len(fires(r)), 0)

    def test_reentry_after_refractory_primes_again(self):
        eng = make_engine(
            rule={"topup_cooldown_ms": 100, "rearm_refractory_ms": 5000,
                  "lick_debounce_ms": 0},
            defaults={"global_min_interval_ms": 0})
        eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0)      # prime @0
        eng.tick(tr(800, 500), CENTER_LANDMARKS, SCALE, 0.3)      # leave
        # Re-enter well past the refractory -> primes again.
        a = eng.tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 6.0)
        self.assertEqual(len(fires(a)), 1)
        self.assertEqual(fires(a)[0].reason, "prime")

    def test_legacy_cooldown_ms_seeds_both_timers(self):
        # A pre-refinement rule using the single cooldown_ms still loads and
        # applies that value to both timers (make_engine's base rule supplies
        # only cooldown_ms, so no split key overrides it here).
        eng = make_engine(rule={"cooldown_ms": 800},
                          defaults={"global_min_interval_ms": 0})
        rule = eng.rules[0]
        self.assertEqual(rule.topup_cooldown_ms, 800)
        self.assertEqual(rule.rearm_refractory_ms, 800)


class _StubRule:
    """Minimal duck-typed rule to probe the engine's per-capability gating.

    Records whether tick() ran so a test can assert the engine skipped it.
    """

    def __init__(self, needs_scale=False, needs_task=False):
        self.id = "stub"
        self.enabled = True
        self.needs_scale = needs_scale
        self.needs_task = needs_task
        self.tick_calls = 0

    def tick(self, frame):
        self.tick_calls += 1
        return [LogEvent("stub_tick", self.id, {})]

    def on_lick(self, port, now):
        return []


class TestCapabilityGating(unittest.TestCase):
    """The engine skips a rule whose declared capability is missing (ADR-0026):
    a needs_scale rule requires a valid Scale, and a needs_task rule requires a
    task-event context. A rule declaring neither always runs. Gating lives in the
    engine, not (only) in each rule's own defensive guards."""

    @staticmethod
    def _engine(rule):
        return RulesEngine([rule], EngineDefaults(), enabled=True)

    def test_needs_task_rule_skipped_without_task(self):
        rule = _StubRule(needs_task=True)
        a = self._engine(rule).tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0,
                                    task=None)
        self.assertEqual(rule.tick_calls, 0)
        self.assertEqual(a, [])

    def test_needs_task_rule_runs_with_task(self):
        rule = _StubRule(needs_task=True)
        task = types.SimpleNamespace()   # any non-None context
        a = self._engine(rule).tick(tr(550, 500), CENTER_LANDMARKS, SCALE, 0.0,
                                    task=task)
        self.assertEqual(rule.tick_calls, 1)
        self.assertEqual(len(logs(a, "stub_tick")), 1)

    def test_needs_scale_rule_skipped_without_scale(self):
        rule = _StubRule(needs_scale=True)
        a = self._engine(rule).tick(tr(550, 500), CENTER_LANDMARKS, None, 0.0,
                                    task=types.SimpleNamespace())
        self.assertEqual(rule.tick_calls, 0)
        self.assertEqual(a, [])

    def test_plain_rule_runs_without_scale_or_task(self):
        rule = _StubRule()
        a = self._engine(rule).tick(None, [], None, 0.0, task=None)
        self.assertEqual(rule.tick_calls, 1)
        self.assertEqual(len(logs(a, "stub_tick")), 1)


class TestParsing(unittest.TestCase):
    def test_parse_lick(self):
        self.assertEqual(RulesEngine._parse_lick("123,L,2,1"), 2)
        self.assertIsNone(RulesEngine._parse_lick("123,L,2,0"))  # falling edge
        self.assertIsNone(RulesEngine._parse_lick("123,S,0,1"))  # beam, not lick
        self.assertIsNone(RulesEngine._parse_lick("123,REWARD,CENTER,150"))


if __name__ == "__main__":
    unittest.main()
