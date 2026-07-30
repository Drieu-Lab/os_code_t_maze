"""
test_reward_dispenser.py - headless unit tests for the reward "how"/"why" split
(reward_dispenser.RewardTrigger + WellDispenser). See docs/decisions/0068.

These exercise the two pure cores in ISOLATION with hand-built task-event frames,
complementing test_well_reward.py (which drives the composed rule end-to-end
through the real estimator + engine). No Tk / OpenCV / hardware. Run from host/:

    python -m unittest test_reward_dispenser
"""

from __future__ import annotations

import types
import unittest

from reward_dispenser import (
    FireReward, LogEvent, RewardTrigger, WellDispenser,
)


def frame(**kw):
    """A minimal task-event frame stand-in (only the fields the cores read)."""
    base = dict(
        valid=True, now=0.0, at_well=False,
        choice_committed=None, choice_entered=None, outbound_used_stem=False,
        same_side_choice_run=0, loop_closed=None, loop_clean=False,
        same_side_run=0,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def fires(actions):
    return [a for a in actions if isinstance(a, FireReward)]


def logs(actions, name=None):
    return [a for a in actions if isinstance(a, LogEvent)
            and (name is None or a.name == name)]


# ================================ the trigger ================================
class TestTriggerCredentials(unittest.TestCase):
    def test_choice_arms_on_any_entry(self):
        t = RewardTrigger({"credential": "choice"}, "r")
        out = t.earn(frame(choice_committed="left", choice_entered="left"))
        self.assertTrue(t.armed)
        self.assertEqual(logs(out, "armed")[0].detail.get("route"), "nonstem")

    def test_choice_family_gated_on_commit_edge(self):
        # A choice-family credential must NOT arm without the shared commit edge,
        # even if a choice arm is "entered" (the once-per-excursion gate).
        t = RewardTrigger({"credential": "choice"}, "r")
        out = t.earn(frame(choice_committed=None, choice_entered="left"))
        self.assertFalse(t.armed)
        self.assertEqual(len(logs(out, "armed")), 0)

    def test_choice_via_stem_denies_nonstem(self):
        t = RewardTrigger({"credential": "choice_via_stem"}, "r")
        out = t.earn(frame(choice_committed="left", choice_entered="left",
                           outbound_used_stem=False))
        self.assertFalse(t.armed)
        self.assertEqual(logs(out, "credit_denied")[0].detail.get("reason"),
                         "no_stem")

    def test_choice_via_stem_credits_on_stem(self):
        t = RewardTrigger({"credential": "choice_via_stem"}, "r")
        out = t.earn(frame(choice_committed="right", choice_entered="right",
                           outbound_used_stem=True, same_side_choice_run=1))
        self.assertTrue(t.armed)
        self.assertEqual(len(logs(out, "armed")), 1)

    def test_unknown_credential_raises(self):
        with self.assertRaises(ValueError):
            RewardTrigger({"credential": "nope"}, "r")

    def test_nonstem_fade_is_seeded_and_matches_rate(self):
        # p=0.5 over many independent non-stem choice events ~ 50% armed. Seeded
        # => deterministic. (Mirrors the composed-rule test, at the Trigger.)
        t = RewardTrigger(
            {"credential": "choice", "nonstem_reward_prob": 0.5, "seed": 1234},
            "r")
        armed = 0
        n = 600
        for _ in range(n):
            t.armed = False
            t.earn(frame(choice_committed="left", choice_entered="left",
                         outbound_used_stem=False))
            if t.armed:
                armed += 1
        self.assertAlmostEqual(armed / n, 0.5, delta=0.06)


# =============================== the dispenser ===============================
class TestDispenserVisitLifecycle(unittest.TestCase):
    def _disp(self, **params):
        p = {"port": 2, "pulse_ms": 300, "max_topups": 2,
             "topup_cooldown_ms": 0, "lick_debounce_ms": 0}
        p.update(params)
        return WellDispenser(p, default_topup_cooldown_ms=500, rule_id="d")

    def test_arrival_prime_spends_credit(self):
        d = self._disp()   # prime_on defaults to well_arrival
        res = d.visit_tick(frame(at_well=True, now=1.0), credit_pending=True)
        self.assertEqual(len(fires(res.actions)), 1)
        self.assertEqual(fires(res.actions)[0].reason, "prime")
        self.assertTrue(res.consumed_credit)
        self.assertEqual(d.state, "AT_WELL")

    def test_no_prime_without_credit(self):
        d = self._disp()
        res = d.visit_tick(frame(at_well=True, now=1.0), credit_pending=False)
        self.assertEqual(len(fires(res.actions)), 0)
        self.assertFalse(res.consumed_credit)

    def test_lick_topups_after_prime_and_cap(self):
        d = self._disp(max_topups=2)
        d.visit_tick(frame(at_well=True, now=1.0), credit_pending=True)   # prime
        r1 = d.on_lick(2, 1.01, credit_pending=False)
        r2 = d.on_lick(2, 1.02, credit_pending=False)
        r3 = d.on_lick(2, 1.03, credit_pending=False)
        self.assertEqual(len(fires(r1.actions)), 1)   # top-up 1
        self.assertEqual(len(fires(r2.actions)), 1)   # top-up 2
        self.assertEqual(len(fires(r3.actions)), 0)   # capped

    def test_wrong_port_lick_ignored(self):
        d = self._disp()
        d.visit_tick(frame(at_well=True, now=1.0), credit_pending=True)
        self.assertEqual(len(fires(d.on_lick(9, 1.01, credit_pending=False).actions)),
                         0)

    def test_visit_exits_only_on_confident_outside(self):
        d = self._disp()
        d.visit_tick(frame(at_well=True, now=1.0), credit_pending=True)
        # A brief track loss (invalid) must NOT drop the visit.
        d.visit_tick(frame(at_well=False, valid=False, now=1.1), credit_pending=False)
        self.assertEqual(d.state, "AT_WELL")
        # A confident outside reading exits it.
        d.visit_tick(frame(at_well=False, valid=True, now=1.2), credit_pending=False)
        self.assertEqual(d.state, "OUTSIDE")

    def test_lick_prime_mode_fires_on_first_lick(self):
        d = self._disp(prime_on="well_lick")
        res = d.visit_tick(frame(at_well=True, now=1.0), credit_pending=True)
        self.assertEqual(len(fires(res.actions)), 0)          # no arrival prime
        lr = d.on_lick(2, 1.01, credit_pending=True)
        self.assertEqual(len(fires(lr.actions)), 1)
        self.assertEqual(fires(lr.actions)[0].reason, "prime")
        self.assertTrue(lr.consumed_credit)

    def test_credential_prime_carries_into_visit(self):
        d = self._disp(prime_on="credential", max_topups=1)
        pr = d.prime_at_credential(0.5)                       # prime out at choice
        self.assertEqual(pr.reason, "prime")
        # Arrival must not re-prime, but the visit opens primed so a lick tops up.
        res = d.visit_tick(frame(at_well=True, now=1.0), credit_pending=False)
        self.assertEqual(len(fires(res.actions)), 0)
        lr = d.on_lick(2, 1.01, credit_pending=False)
        self.assertEqual(len(fires(lr.actions)), 1)
        self.assertEqual(fires(lr.actions)[0].reason, "topup")

    def test_unknown_prime_on_raises(self):
        with self.assertRaises(ValueError):
            self._disp(prime_on="whenever")

    def test_legacy_prime_on_arrival_bool(self):
        self.assertEqual(self._disp(prime_on_arrival=True).prime_on, "well_arrival")
        self.assertEqual(self._disp(prime_on_arrival=False).prime_on, "well_lick")

    def test_topup_cooldown_default_seeded_from_engine(self):
        # No topup_cooldown_ms / cooldown_ms in params -> uses the passed engine
        # default; a legacy cooldown_ms overrides it.
        d = WellDispenser({"port": 2}, default_topup_cooldown_ms=750, rule_id="d")
        self.assertEqual(d.topup_cooldown_ms, 750)
        d2 = WellDispenser({"port": 2, "cooldown_ms": 200},
                           default_topup_cooldown_ms=750, rule_id="d")
        self.assertEqual(d2.topup_cooldown_ms, 200)


if __name__ == "__main__":
    unittest.main()
