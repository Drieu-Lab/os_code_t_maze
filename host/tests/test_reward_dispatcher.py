"""
test_reward_dispatcher.py - headless tests for the shared reward seam.

Drives reward_dispatcher.RewardDispatcher with an injected fake ``send`` (records
the wire commands) and a fake ``clock`` (so the rate backstop is deterministic,
no sleeps). Covers the global rate backstop (one fire through, the next dropped
until the interval elapses), the unchanged on-wire ``R<port>1,<ms>`` framing, the
calibrated volume in the record, ``set_min_interval_ms``, and the FW-0019
acceptance that a Phase-3 reward is rate-limited and recorded identically to a
rules reward. See docs/decisions/0054.

Run from host/:  python -m unittest test_reward_dispatcher
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from tmaze.core.reward.reward_dispatcher import (
    DEFAULT_GLOBAL_MIN_INTERVAL_MS, RewardDispatcher, RewardRecord,
)
from tmaze.core.reward.reward_volume import reward_volume_ul


class _Clock:
    """A manually advanced monotonic clock (seconds)."""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make(min_interval_ms: int = 500):
    sent: list = []
    clock = _Clock()
    disp = RewardDispatcher(sent.append, min_interval_ms=min_interval_ms,
                            clock=clock)
    return disp, sent, clock


class TestBackstop(unittest.TestCase):
    def test_first_fire_sends_and_records_volume(self):
        disp, sent, _clock = make()
        rec = disp.fire(2, 300, reason="prime", rule_id="center")
        self.assertTrue(rec.fired)
        self.assertIsNone(rec.suppressed_reason)
        self.assertEqual(sent, ["R21,300"])            # on-wire framing unchanged
        self.assertEqual(rec.port, 2)
        self.assertEqual(rec.pulse_ms, 300)
        self.assertAlmostEqual(rec.volume_ul, reward_volume_ul(300))
        self.assertGreater(rec.volume_ul, 0.0)

    def test_second_fire_within_interval_is_suppressed(self):
        disp, sent, clock = make(min_interval_ms=500)
        disp.fire(2, 300, reason="prime", rule_id="center")
        clock.advance(0.2)                             # 200 ms < 500 ms
        rec = disp.fire(2, 300, reason="topup", rule_id="center")
        self.assertFalse(rec.fired)
        self.assertEqual(rec.suppressed_reason, "global_backstop")
        self.assertEqual(rec.volume_ul, 0.0)
        self.assertEqual(sent, ["R21,300"])            # nothing new reached the wire

    def test_fire_after_interval_fires_again(self):
        disp, sent, clock = make(min_interval_ms=500)
        disp.fire(2, 300, reason="prime", rule_id="center")
        clock.advance(0.6)                             # 600 ms > 500 ms
        rec = disp.fire(2, 300, reason="topup", rule_id="center")
        self.assertTrue(rec.fired)
        self.assertEqual(sent, ["R21,300", "R21,300"])

    def test_zero_interval_never_suppresses(self):
        disp, sent, _clock = make(min_interval_ms=0)
        for _ in range(3):
            self.assertTrue(disp.fire(2, 300, reason="prime", rule_id="r").fired)
        self.assertEqual(len(sent), 3)

    def test_default_interval_matches_legacy(self):
        disp = RewardDispatcher(lambda _cmd: None)
        self.assertEqual(disp.min_interval_ms, DEFAULT_GLOBAL_MIN_INTERVAL_MS)

    def test_set_min_interval_ms(self):
        disp, sent, clock = make(min_interval_ms=500)
        disp.set_min_interval_ms(100)
        disp.fire(2, 300, reason="prime", rule_id="center")
        clock.advance(0.2)                             # 200 ms > 100 ms now
        self.assertTrue(disp.fire(2, 300, reason="topup", rule_id="center").fired)

    def test_send_failure_still_records_and_advances(self):
        clock = _Clock()

        def boom(_cmd):
            raise RuntimeError("serial down")

        disp = RewardDispatcher(boom, min_interval_ms=500, clock=clock)
        rec = disp.fire(2, 300, reason="prime", rule_id="center")
        self.assertTrue(rec.fired)                     # timer advanced despite error
        clock.advance(0.2)
        self.assertFalse(disp.fire(2, 300, reason="topup", rule_id="center").fired)


class TestRulesAndPhase3Parity(unittest.TestCase):
    """FW-0019: a Phase 3 reward is rate-limited and recorded identically to a
    rules reward, because both flow through the ONE shared dispatcher."""

    def test_phase3_reward_rate_limited_like_a_rules_reward(self):
        disp, sent, clock = make(min_interval_ms=500)
        # A rules reward fires; a Phase 3 reward 200 ms later hits the SAME
        # backstop and is dropped, exactly as a second rules reward would be.
        rules_rec = disp.fire(2, 300, reason="prime", rule_id="center")
        clock.advance(0.2)
        phase3_rec = disp.fire(2, 300, reason="phase3_reward", rule_id="phase3")
        self.assertTrue(rules_rec.fired)
        self.assertFalse(phase3_rec.fired)
        self.assertEqual(phase3_rec.suppressed_reason, "global_backstop")
        self.assertEqual(sent, ["R21,300"])

    def test_records_identical_except_reason_and_rule_id(self):
        # Same port/pulse -> identical record fields apart from what asked for it.
        disp_a, _sa, _ca = make(min_interval_ms=0)
        disp_b, _sb, _cb = make(min_interval_ms=0)
        rules_rec = disp_a.fire(2, 300, reason="prime", rule_id="center")
        phase3_rec = disp_b.fire(2, 300, reason="phase3_reward", rule_id="phase3")
        # Normalize the fields that legitimately differ, then compare the rest.
        norm_a = replace(rules_rec, reason="", rule_id="")
        norm_b = replace(phase3_rec, reason="", rule_id="")
        self.assertEqual(norm_a, norm_b)
        self.assertIsInstance(rules_rec, RewardRecord)


if __name__ == "__main__":
    unittest.main()
