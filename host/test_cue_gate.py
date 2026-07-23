"""
test_cue_gate.py - headless unit tests for the pure cue-gate core (cue_gate.py).
No hardware, no Tk. See docs/decisions/0045.

Run from host/:
    python -m unittest test_cue_gate
"""

from __future__ import annotations

import unittest

from cue_gate import CHOICE, RETURN, CueGate, CueParams
from maze_state import (CENTRAL_STEM, DOWN_STEM, HUB, JUNCTION, LEFT_RETURN,
                        RIGHT_RETURN, UNKNOWN, UP_STEM)


def _valid(gate, zone, direction=None, conf=1.0):
    return gate.step(zone, direction, conf, True)


def _invalid(gate):
    # UNKNOWN zone, not valid -> a tracking dropout.
    return gate.step(UNKNOWN, None, 0.0, False)


class TestChoiceCue(unittest.TestCase):
    def test_on_in_stem_up_then_off_on_leaving(self):
        g = CueGate()
        # Approaching in the hub: nothing.
        self.assertIsNone(_valid(g, HUB).desired)
        # Enter the stem moving up -> CHOICE on.
        d = _valid(g, CENTRAL_STEM, UP_STEM)
        self.assertEqual(d.desired, CHOICE)
        self.assertEqual(d.started, CHOICE)
        # Still in the stem -> stays on, no new edge.
        d = _valid(g, CENTRAL_STEM, UP_STEM)
        self.assertEqual(d.desired, CHOICE)
        self.assertIsNone(d.started)
        # Reach the junction (left the stem) -> off.
        d = _valid(g, JUNCTION, UP_STEM)
        self.assertIsNone(d.desired)
        self.assertTrue(d.stopped)

    def test_reversal_cuts_the_tone(self):
        # A rat that commits then reverses simply leaves the window (partial cue).
        g = CueGate()
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)
        # Turns back down the stem: require_up_stem makes it leave the window.
        d = _valid(g, CENTRAL_STEM, DOWN_STEM)
        self.assertIsNone(d.desired)
        self.assertTrue(d.stopped)

    def test_no_fire_when_running_down_stem(self):
        g = CueGate()
        self.assertIsNone(_valid(g, CENTRAL_STEM, DOWN_STEM).desired)

    def test_fail_open_holds_through_dropout(self):
        g = CueGate()
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)
        # A confidence dropout must NOT cut the tone.
        d = _invalid(g)
        self.assertEqual(d.desired, CHOICE)
        self.assertFalse(d.stopped)
        # Re-lock still in the stem -> still on.
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)

    def test_fail_open_onset_retries_after_low_confidence(self):
        g = CueGate(CueParams(min_confidence=0.5))
        # In the window but below the extra confidence gate -> no onset yet.
        self.assertIsNone(_valid(g, CENTRAL_STEM, UP_STEM, conf=0.2).desired)
        # Later confident frame still in the window -> fires (no per-visit latch).
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM, conf=0.9).desired, CHOICE)

    def test_require_up_stem_disabled(self):
        g = CueGate(CueParams(require_up_stem=False))
        self.assertEqual(_valid(g, CENTRAL_STEM, DOWN_STEM).desired, CHOICE)

    def test_choice_disabled(self):
        g = CueGate(CueParams(choice_enabled=False))
        self.assertIsNone(_valid(g, CENTRAL_STEM, UP_STEM).desired)


class TestReturnCue(unittest.TestCase):
    def test_fires_on_entry_and_off_on_leaving(self):
        for zone, side in ((LEFT_RETURN, "left"), (RIGHT_RETURN, "right")):
            g = CueGate()
            self.assertIsNone(_valid(g, JUNCTION).desired)
            d = _valid(g, zone)
            self.assertEqual(d.desired, RETURN)
            self.assertEqual(d.started, RETURN)
            # the physical arm is reported on the RETURN onset (ADR-0047)
            self.assertEqual(d.return_arm, side)
            # stays on inside the zone
            self.assertEqual(_valid(g, zone).desired, RETURN)
            # leaving to the hub cuts it
            d = _valid(g, HUB)
            self.assertIsNone(d.desired)
            self.assertTrue(d.stopped)

    def test_fail_safe_skips_whole_visit_when_unconfident_at_entry(self):
        g = CueGate(CueParams(min_confidence=0.5))
        # Enter the return zone with low confidence -> skip, latched for the visit.
        d = _valid(g, LEFT_RETURN, conf=0.1)
        self.assertIsNone(d.desired)
        self.assertTrue(d.skipped_return)
        # Even a confident frame later in the SAME visit must not fire.
        self.assertIsNone(_valid(g, LEFT_RETURN, conf=0.9).desired)
        # Leave and re-enter -> a new visit CAN fire.
        _valid(g, HUB)
        self.assertEqual(_valid(g, LEFT_RETURN, conf=0.9).desired, RETURN)

    def test_dropout_is_not_a_new_visit(self):
        g = CueGate(CueParams(min_confidence=0.5))
        self.assertEqual(_valid(g, LEFT_RETURN, conf=0.9).desired, RETURN)
        # Dropout holds the tone...
        self.assertEqual(_invalid(g).desired, RETURN)
        # ...and re-locking in the SAME zone does not re-run the entry decision.
        d = _valid(g, LEFT_RETURN, conf=0.9)
        self.assertEqual(d.desired, RETURN)
        self.assertIsNone(d.started)

    def test_return_disabled(self):
        g = CueGate(CueParams(return_enabled=False))
        self.assertIsNone(_valid(g, LEFT_RETURN).desired)


class TestReset(unittest.TestCase):
    def test_reset_clears_active_and_latches(self):
        g = CueGate()
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)
        g.reset()
        self.assertIsNone(g.active)
        # After reset, re-entering fires fresh.
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)


class TestNeverRaises(unittest.TestCase):
    def test_odd_inputs(self):
        g = CueGate()
        for args in (("", None, 0.0, False), (CENTRAL_STEM, None, 0.0, True),
                     (None, "sideways", -1.0, True), (LEFT_RETURN, UP_STEM, 2.0, True)):
            g.step(*args)  # must not raise


if __name__ == "__main__":
    unittest.main()
