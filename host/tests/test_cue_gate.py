"""
test_cue_gate.py - headless unit tests for the pure cue-gate core (cue_gate.py).
No hardware, no Tk. See docs/decisions/0045.

Run from host/:
    python -m unittest test_cue_gate
"""

from __future__ import annotations

import unittest

from tmaze.core.cue_gate import CHOICE, RETURN, CueGate, CueParams
from tmaze.core.maze_state import (CENTRAL_STEM, DOWN_STEM, HUB, JUNCTION, LEFT_CHOICE,
                        LEFT_RETURN, RIGHT_RETURN, UNKNOWN, UP_STEM)


def _valid(gate, zone, direction=None, conf=1.0, outbound=True, on_leg=True,
           off_track=False):
    # outbound == the shared outbound_used_stem credential the RETURN cue needs
    # (docs/decisions/0061); on_leg == the left_hub_via_stem credential the CHOICE
    # cue needs (docs/decisions/0067); off_track == the perch flag that releases a
    # tone held on a stale gap zone (docs/decisions/0170). All default so tests
    # about the OTHER knobs read unchanged.
    return gate.step(zone, direction, conf, True, outbound, on_leg, off_track)


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


class TestOffTrack(unittest.TestCase):
    """off_track (docs/decisions/0170) forces the point OUT of both windows so a rat
    perched in a polygon gap - whose stale zone the estimator holds - releases the
    tone instead of holding it. It fails OPEN: the CHOICE cue re-fires once the rat
    moves back onto the track."""

    def test_perch_in_stem_stops_the_choice_tone(self):
        g = CueGate()
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)
        # Rat parks in a gap: the estimator still reports CENTRAL_STEM but flags
        # off_track. The tone must stop.
        d = _valid(g, CENTRAL_STEM, UP_STEM, off_track=True)
        self.assertIsNone(d.desired)
        self.assertTrue(d.stopped)

    def test_choice_tone_refires_when_back_on_track(self):
        g = CueGate()
        _valid(g, CENTRAL_STEM, UP_STEM)
        _valid(g, CENTRAL_STEM, UP_STEM, off_track=True)   # stopped
        # Moves back onto the track, still in the window -> fires again (fail-open).
        d = _valid(g, CENTRAL_STEM, UP_STEM, off_track=False)
        self.assertEqual(d.desired, CHOICE)
        self.assertEqual(d.started, CHOICE)

    def test_off_track_blocks_choice_onset(self):
        g = CueGate()
        # Enter the window already off_track -> no onset at all.
        self.assertIsNone(_valid(g, CENTRAL_STEM, UP_STEM, off_track=True).desired)

    def test_perch_in_return_stops_the_return_tone(self):
        g = CueGate()
        self.assertEqual(_valid(g, LEFT_RETURN).desired, RETURN)
        d = _valid(g, LEFT_RETURN, off_track=True)
        self.assertIsNone(d.desired)
        self.assertTrue(d.stopped)


class TestChoiceCueOutboundLeg(unittest.TestCase):
    """The hub-departure credential (docs/decisions/0067).

    These run with ``require_up_stem=False``, which is how the rig is operated:
    the direction gate is off, so the credential is the ONLY thing distinguishing
    an outbound trial leg from a lap being run backwards.
    """

    def setUp(self):
        self.gate = CueGate(CueParams(require_up_stem=False))

    def test_backwards_lap_is_silent_through_the_stem(self):
        # Out along a return arm, round through the choice arm, home down the
        # stem. The credentials are the values task_events really reports along
        # that route: no stem-out excursion until the stem itself is reached (so
        # the return cue is correctly suppressed on the way out), and the trip
        # never departed up the stem, so the choice cue stays silent throughout -
        # including on the descent, where outbound_used_stem has become true.
        g = self.gate
        lap = ((HUB, False), (LEFT_RETURN, False), (LEFT_CHOICE, False),
               (JUNCTION, False), (CENTRAL_STEM, True))
        for zone, outbound in lap:
            d = _valid(g, zone, DOWN_STEM, outbound=outbound, on_leg=False)
            self.assertIsNone(d.desired, zone)
            self.assertIsNone(d.started, zone)

    def test_next_hub_departure_fires_normally(self):
        # The suppression above must not linger: the next proper run is cued.
        g = self.gate
        self.assertIsNone(_valid(g, CENTRAL_STEM, DOWN_STEM, on_leg=False).desired)
        _valid(g, HUB, on_leg=False)
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM, on_leg=True).desired,
                         CHOICE)

    def test_reversal_after_the_choice_still_plays(self):
        # The credential is held for the whole trip, so a rat that commits a
        # choice and then backs into the stem hears the tone again. Deliberate:
        # the cue is a function of position within the trip (ADR-0067).
        g = self.gate
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)
        self.assertTrue(_valid(g, JUNCTION, UP_STEM).stopped)
        _valid(g, LEFT_CHOICE, None)
        d = _valid(g, CENTRAL_STEM, DOWN_STEM)
        self.assertEqual(d.desired, CHOICE)
        self.assertEqual(d.started, CHOICE)

    def test_retreat_down_the_stem_still_plays(self):
        # With the direction gate off, a partial retreat toward the hub does not
        # cut the tone. Accepted trade-off of running require_up_stem off; pinned
        # here so it is not "fixed" by accident.
        g = self.gate
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)
        d = _valid(g, CENTRAL_STEM, DOWN_STEM)
        self.assertEqual(d.desired, CHOICE)
        self.assertFalse(d.stopped)

    def test_credential_cuts_a_playing_tone(self):
        # Belt and braces: the gate's ON/OFF state stays coherent if the
        # credential ever clears while the rat is still in the window.
        g = self.gate
        self.assertEqual(_valid(g, CENTRAL_STEM, UP_STEM).desired, CHOICE)
        d = _valid(g, CENTRAL_STEM, UP_STEM, on_leg=False)
        self.assertIsNone(d.desired)
        self.assertTrue(d.stopped)

    def test_hub_departure_credential_gates_the_return_cue(self):
        # docs/decisions/0099 supersedes the old "credentials are independent"
        # stance: the return cue now shares the reward's hub-departure credential,
        # so a trip that visited the stem but never DEPARTED the hub up it (the
        # divider-jump case: outbound_used_stem true, left_hub_via_stem false) gets
        # no return cue.
        g = self.gate
        d = _valid(g, LEFT_RETURN, outbound=True, on_leg=False)
        self.assertIsNone(d.desired)
        self.assertIsNone(d.started)
        # Latched for the whole visit: a later frame this visit stays silent too.
        self.assertIsNone(_valid(g, LEFT_RETURN, outbound=True, on_leg=False).desired)


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

    def test_no_fire_without_outbound_excursion(self):
        # A run that STARTS up a return arm from the hub (no stem-out) must not
        # trigger the return cue (docs/decisions/0061). It is not a fail-safe
        # skip - it is simply not a genuine return.
        for zone in (LEFT_RETURN, RIGHT_RETURN):
            g = CueGate()
            d = _valid(g, zone, outbound=False)
            self.assertIsNone(d.desired)
            self.assertIsNone(d.started)
            self.assertFalse(d.skipped_return)
            # Latched for the whole visit: a later frame this visit still silent.
            self.assertIsNone(_valid(g, zone, outbound=False).desired)

    def test_fires_when_outbound_excursion_used_stem(self):
        g = CueGate()
        d = _valid(g, LEFT_RETURN, outbound=True)
        self.assertEqual(d.desired, RETURN)
        self.assertEqual(d.started, RETURN)

    def test_direct_return_suppressed_then_real_lap_fires(self):
        # Visit 1: entered a return arm straight from the hub -> suppressed.
        g = CueGate()
        self.assertIsNone(_valid(g, LEFT_RETURN, outbound=False).desired)
        # Back to the hub, then a proper stem-out lap ends in the return arm.
        _valid(g, HUB)
        self.assertEqual(_valid(g, LEFT_RETURN, outbound=True).desired, RETURN)

    def test_no_fire_without_hub_departure(self):
        # docs/decisions/0099: even with a stem-out excursion, a trip that never
        # DEPARTED the hub up the stem (the divider-jump: outbound true, on_leg
        # false) earns no return cue - it shares the reward's credential now.
        for zone in (LEFT_RETURN, RIGHT_RETURN):
            g = CueGate()
            d = _valid(g, zone, outbound=True, on_leg=False)
            self.assertIsNone(d.desired)
            self.assertIsNone(d.started)
            self.assertFalse(d.skipped_return)


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
