"""
test_well_reward.py - headless unit tests for the credential-gated well reward
template (WellRewardWithExcursionRule) used by the reward-shaping phases.

No Tk, no OpenCV, no hardware: drives the real RulesEngine with task-event
frames produced by the maze_state estimator + task_events layer over synthetic
centroid trajectories, plus synthetic lick lines. Run from host/:

    python -m unittest test_well_reward

Covers (see docs/decisions/0026): Phase 1 (choice credit; no reward without an
excursion; credit consumed by the prime; lick top-ups + cap); Phase 2a
(stem-gated choice credit; a return-arm approach is denied); Phase 2b (clean-loop
credit; stem-reversal loop denied; fixed debias cap withholds an over-biased
run); and lick-gated priming (prime_on_arrival False).
"""

from __future__ import annotations

import types
import unittest

from maze_state import (
    HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN,
    RIGHT_RETURN, MazeStateEstimator, MazeStateParams, Zone, ZoneMap,
)
from task_events import TaskEvents
from rules_engine import FireReward, LogEvent, RulesEngine

FRAME_W, FRAME_H = 1000, 1000


def rect(x0, y0, x1, y1):
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def make_zone_map():
    return ZoneMap(
        zones=(
            Zone(HUB,          rect(0.35, 0.80, 0.65, 1.00)),
            Zone(CENTRAL_STEM, rect(0.40, 0.50, 0.60, 0.80)),
            Zone(JUNCTION,     rect(0.40, 0.34, 0.60, 0.50)),
            Zone(LEFT_CHOICE,  rect(0.00, 0.20, 0.40, 0.50)),
            Zone(RIGHT_CHOICE, rect(0.60, 0.20, 1.00, 0.50)),
            Zone(LEFT_RETURN,  rect(0.00, 0.50, 0.20, 0.80)),
            Zone(RIGHT_RETURN, rect(0.80, 0.50, 1.00, 0.80)),
        ),
        well=rect(0.40, 0.88, 0.60, 1.00),
        stem_base=(0.50, 0.85), stem_tip=(0.50, 0.42),
    )


P_HUB = (500, 900)
P_STEM = (500, 650)
P_JUNCTION = (500, 420)
P_LCHOICE = (200, 350)
P_RCHOICE = (800, 350)
P_LRETURN = (100, 650)
P_RRETURN = (900, 650)

LEFT_LAP = [P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_LRETURN, P_HUB]


def tr(cx, cy, conf=0.9, state="TRACKING"):
    return types.SimpleNamespace(
        detected=True, state=state, confidence=conf,
        frame_size=(FRAME_W, FRAME_H), centroid=(cx, cy), smoothed=(cx, cy))


def fires(actions):
    return [a for a in actions if isinstance(a, FireReward)]


def logs(actions, name=None):
    return [a for a in actions if isinstance(a, LogEvent)
            and (name is None or a.name == name)]


class Harness:
    def __init__(self, rule):
        base = {"id": "well", "template": "well_reward_with_excursion",
                "enabled": True, "port": 2, "pulse_ms": 300, "max_topups": 2,
                "topup_cooldown_ms": 0, "lick_debounce_ms": 0,
                "prime_on_arrival": True}
        base.update(rule)
        config = {"version": 2, "enabled": True,
                  "defaults": {"min_confidence": 0.5, "global_min_interval_ms": 0},
                  "rules": [base]}
        self.eng = RulesEngine.from_config(config)
        self.eng.enabled = True
        self.est = MazeStateEstimator(make_zone_map(),
                                      MazeStateParams(min_dwell_ticks=1))
        self.te = TaskEvents()
        self.t = 0.0
        self._last_tick = 0.0

    def step(self, pt):
        s = self.est.update(tr(*pt), self.t)
        f = self.te.update(s)
        acts = self.eng.tick(None, [], None, self.t, task=f)
        self._last_tick = self.t
        self.t += 0.1
        return acts

    def run(self, pts):
        last = None
        for pt in pts:
            last = self.step(pt)
        return last

    def lick(self):
        # Just after the most recent tick (well within the freshness window).
        acts = self.eng.event("1,L,2,1", self._last_tick + 0.01)
        return acts


class TestPhase1Choice(unittest.TestCase):
    def test_no_reward_at_well_without_excursion(self):
        h = Harness({"credential": "choice"})
        a = h.step(P_HUB)                      # arrive at the well, uncredited
        self.assertEqual(len(fires(a)), 0)

    def test_choice_credits_then_primes_next_visit(self):
        h = Harness({"credential": "choice"})
        h.step(P_HUB)                          # uncredited arrival
        a_choice = h.run([P_STEM, P_JUNCTION, P_LCHOICE])
        self.assertEqual(len(logs(a_choice, "armed")), 1)
        a = h.run([P_LRETURN, P_HUB])          # return to the well
        self.assertEqual(len(fires(a)), 1)
        self.assertEqual(fires(a)[0].reason, "prime")

    def test_prime_consumed_no_reward_without_new_excursion(self):
        h = Harness({"credential": "choice"})
        h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_LRETURN, P_HUB])  # prime
        a = h.run([P_STEM, P_HUB])             # leave + return, no choice arm
        self.assertEqual(len(fires(a)), 0)

    def test_lick_topups_after_prime_and_cap(self):
        h = Harness({"credential": "choice", "max_topups": 2})
        h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_LRETURN, P_HUB])  # prime
        self.assertEqual(len(fires(h.lick())), 1)   # top-up 1
        self.assertEqual(len(fires(h.lick())), 1)   # top-up 2
        self.assertEqual(len(fires(h.lick())), 0)   # capped

    def test_lick_without_prime_does_nothing(self):
        h = Harness({"credential": "choice"})
        h.step(P_HUB)                          # at well but uncredited
        self.assertEqual(len(fires(h.lick())), 0)


class TestPhase2aChoiceViaStem(unittest.TestCase):
    def test_return_arm_approach_is_denied(self):
        h = Harness({"credential": "choice_via_stem"})
        h.step(P_HUB)
        a = h.run([P_LRETURN, P_LCHOICE])      # reached choice arm without the stem
        self.assertEqual(len(logs(a, "credit_denied")), 1)
        self.assertEqual(len(logs(a, "armed")), 0)
        a = h.run([P_LRETURN, P_HUB])
        self.assertEqual(len(fires(a)), 0)

    def test_stem_approach_credits_and_primes(self):
        h = Harness({"credential": "choice_via_stem"})
        h.step(P_HUB)
        a = h.run([P_STEM, P_JUNCTION, P_RCHOICE])
        self.assertEqual(len(logs(a, "armed")), 1)
        a = h.run([P_RRETURN, P_HUB])
        self.assertEqual(len(fires(a)), 1)


class TestPhase2bCleanLoop(unittest.TestCase):
    def test_clean_loop_credits_and_rewards_on_arrival(self):
        h = Harness({"credential": "clean_loop"})
        # A clean loop closes exactly on the well arrival -> credit + prime same tick.
        a = h.run(LEFT_LAP)
        self.assertEqual(len(logs(a, "armed")), 1)
        self.assertEqual(len(fires(a)), 1)
        self.assertEqual(fires(a)[0].reason, "prime")

    def test_stem_reversal_loop_denied(self):
        h = Harness({"credential": "clean_loop"})
        a = h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_JUNCTION, P_STEM, P_HUB])
        self.assertEqual(len(logs(a, "credit_denied")), 1)
        self.assertEqual(len(fires(a)), 0)

    def test_fixed_debias_withholds_over_biased_run(self):
        h = Harness({"credential": "clean_loop",
                     "debias": {"mode": "fixed", "n": 2}})
        a1 = h.run(LEFT_LAP)                   # run 1 <= 2 -> armed + prime
        self.assertEqual(len(fires(a1)), 1)
        a2 = h.run(LEFT_LAP[1:])               # run 2 <= 2 -> armed + prime
        self.assertEqual(len(fires(a2)), 1)
        a3 = h.run(LEFT_LAP[1:])               # run 3 > 2 -> withheld
        self.assertEqual(len(logs(a3, "debias_withhold")), 1)
        self.assertEqual(len(fires(a3)), 0)


class TestLickGatedPrime(unittest.TestCase):
    def test_prime_on_first_lick_not_on_arrival(self):
        h = Harness({"credential": "choice", "prime_on_arrival": False})
        h.step(P_HUB)
        a_choice = h.run([P_STEM, P_JUNCTION, P_LCHOICE])
        self.assertEqual(len(logs(a_choice, "armed")), 1)
        a = h.run([P_LRETURN, P_HUB])          # arrive credited...
        self.assertEqual(len(fires(a)), 0)     # ...but no prime on arrival
        p = h.lick()                           # first lick fires the prime
        self.assertEqual(len(fires(p)), 1)
        self.assertEqual(fires(p)[0].reason, "prime")
        self.assertEqual(len(fires(h.lick())), 1)   # subsequent lick tops up


if __name__ == "__main__":
    unittest.main()
