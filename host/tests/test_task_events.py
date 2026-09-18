"""
test_task_events.py - headless unit tests for the task-event layer.

No Tk, no OpenCV, no hardware: drives task_events.py with RatStates produced by
the real maze_state estimator over synthetic centroid trajectories. Run from
host/:

    python -m unittest test_task_events

Covers (see docs/decisions/0026): the well-arrival edge; the choice-entered edge
(left/right); outbound_used_stem (stem vs return-arm approach); left_hub_via_stem
(how the trip DEPARTED, docs/decisions/0067) and its rising edge
departed_hub_via_stem (the Phase 3 trial-start edge, FW-0027); trial_trip (the one
shared trial credential = hub-stem departure AND outbound leg not broken by a
return arm; docs/decisions/0168, 0169); clean vs unclean loop detection; and the
same-side run aggregate (counts every closed loop, rewarded or not; resets on a
side switch).
"""

from __future__ import annotations

import types
import unittest

from tmaze.core.maze_state import (
    HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN,
    RIGHT_RETURN, LEFT, RIGHT, MazeStateEstimator, MazeStateParams, RatState,
    Zone, ZoneMap,
)
from tmaze.core.task_events import TaskEvents

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
        stem_base=(0.50, 0.85),
        stem_tip=(0.50, 0.42),
    )


P_HUB = (500, 900)
P_STEM = (500, 650)
P_JUNCTION = (500, 420)
P_LCHOICE = (200, 350)
P_RCHOICE = (800, 350)
P_LRETURN = (100, 650)
P_RRETURN = (900, 650)


def tr(cx, cy, conf=0.9, state="TRACKING"):
    return types.SimpleNamespace(
        detected=True, state=state, confidence=conf,
        frame_size=(FRAME_W, FRAME_H), centroid=(cx, cy), smoothed=(cx, cy))


class _Harness:
    """Drives centroids through the estimator + task-event layer."""

    def __init__(self):
        self.est = MazeStateEstimator(make_zone_map(), MazeStateParams(min_dwell_ticks=1))
        self.te = TaskEvents()
        self.t = 0.0

    def step(self, pt):
        s = self.est.update(tr(*pt), self.t)
        f = self.te.update(s)
        self.t += 0.1
        return f

    def run(self, pts):
        last = None
        for pt in pts:
            last = self.step(pt)
        return last


LEFT_LAP = [P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_LRETURN, P_HUB]
RIGHT_LAP = [P_HUB, P_STEM, P_JUNCTION, P_RCHOICE, P_RRETURN, P_HUB]


class TestEdges(unittest.TestCase):
    def test_well_arrival_edge(self):
        h = _Harness()
        f0 = h.step(P_HUB)          # first localization inside the well
        self.assertTrue(f0.at_well)
        self.assertTrue(f0.well_arrived)
        f1 = h.step(P_HUB)          # still in the well: not a new arrival
        self.assertTrue(f1.at_well)
        self.assertFalse(f1.well_arrived)
        f2 = h.step(P_STEM)         # left the well
        self.assertFalse(f2.at_well)

    def test_choice_entered_edges(self):
        h = _Harness()
        f = h.run([P_HUB, P_STEM, P_JUNCTION])
        self.assertIsNone(f.choice_entered)
        f = h.step(P_LCHOICE)
        self.assertEqual(f.choice_entered, LEFT)
        # only an edge: staying in the arm does not re-fire
        f = h.step(P_LCHOICE)
        self.assertIsNone(f.choice_entered)


class TestOutboundStem(unittest.TestCase):
    def test_outbound_via_stem_is_true(self):
        h = _Harness()
        f = h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE])
        self.assertEqual(f.choice_entered, LEFT)
        self.assertTrue(f.outbound_used_stem)

    def test_outbound_via_return_arm_is_not_stem(self):
        # Reach the left choice arm by going up the return arm (backward), never
        # touching the stem: outbound_used_stem must be False at choice entry.
        h = _Harness()
        f = h.run([P_HUB, P_LRETURN, P_LCHOICE])
        self.assertEqual(f.choice_entered, LEFT)
        self.assertFalse(f.outbound_used_stem)


class TestLeftHubViaStem(unittest.TestCase):
    """How the trip DEPARTED the hub (docs/decisions/0067) - the credential the
    Phase 3 choice cue needs. Distinct from outbound_used_stem, which only says
    the stem has been visited at some point since the hub and is therefore true
    on a backwards lap too, once the animal walks into the stem from the junction.
    """

    def test_armed_by_the_departure_and_held_for_the_whole_trip(self):
        h = _Harness()
        self.assertFalse(h.step(P_HUB).left_hub_via_stem)
        self.assertTrue(h.step(P_STEM).left_hub_via_stem)
        for pt in (P_JUNCTION, P_LCHOICE, P_LRETURN):
            self.assertTrue(h.step(pt).left_hub_via_stem, pt)
        # Home again -> cleared, ready for the next trip's departure to decide.
        self.assertFalse(h.step(P_HUB).left_hub_via_stem)

    def test_survives_a_reversal_back_into_the_stem(self):
        # The case the choice cue is built around: after committing a choice the
        # animal backs into the stem, and the credential must still hold so the
        # tone plays again.
        h = _Harness()
        f = h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_JUNCTION, P_STEM])
        self.assertTrue(f.left_hub_via_stem)

    def test_backwards_lap_never_arms_it(self):
        # Out along the return arm and home down the stem: the trip did not set
        # off up the stem, so the credential stays False the whole way - even
        # though outbound_used_stem turns true once the stem is reached.
        h = _Harness()
        for pt in (P_HUB, P_LRETURN, P_LCHOICE, P_JUNCTION):
            self.assertFalse(h.step(pt).left_hub_via_stem, pt)
        f = h.step(P_STEM)
        self.assertFalse(f.left_hub_via_stem)
        self.assertTrue(f.outbound_used_stem)   # the two really do differ here

    def test_starts_false_before_the_animal_has_been_home(self):
        # A session that starts with the animal already in the stem is silent
        # until it has visited the hub and set off again (a missed cue, not a
        # spurious one).
        h = _Harness()
        self.assertFalse(h.step(P_STEM).left_hub_via_stem)
        self.assertFalse(h.step(P_JUNCTION).left_hub_via_stem)
        self.assertTrue(h.run([P_STEM, P_HUB, P_STEM]).left_hub_via_stem)


class TestDepartedHubViaStem(unittest.TestCase):
    """The single-tick rising edge of left_hub_via_stem - the trial-start edge the
    Phase 3 controller stamps off (FW-0027). Mirrors well_arrived: true only on the
    tick the departure happens, not while the credential is held afterwards."""

    def test_fires_once_on_the_hub_to_stem_departure(self):
        h = _Harness()
        self.assertFalse(h.step(P_HUB).departed_hub_via_stem)   # at the hub: no edge
        self.assertTrue(h.step(P_STEM).departed_hub_via_stem)   # the departure tick
        # Level is held for the trip, but the edge is spent.
        for pt in (P_JUNCTION, P_LCHOICE, P_LRETURN):
            self.assertFalse(h.step(pt).departed_hub_via_stem, pt)

    def test_refires_after_returning_home_and_departing_again(self):
        h = _Harness()
        self.assertTrue(h.run([P_HUB, P_STEM]).departed_hub_via_stem)
        # Finish the lap home, then set off up the stem again -> a fresh edge.
        h.run([P_JUNCTION, P_LCHOICE, P_LRETURN, P_HUB])
        self.assertTrue(h.step(P_STEM).departed_hub_via_stem)

    def test_reversal_back_into_the_stem_does_not_refire(self):
        # After committing a choice the animal backs into the stem; the level is
        # held True, so re-entering the stem is NOT a fresh hub departure.
        h = _Harness()
        h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_JUNCTION])
        self.assertFalse(h.step(P_STEM).departed_hub_via_stem)

    def test_backwards_lap_never_fires(self):
        # Out along the return arm and home down the stem: the level never arms
        # (the stem was entered from the junction, not the hub), so no edge - no
        # spurious trial start on a reverse stem crossing.
        h = _Harness()
        for pt in (P_HUB, P_LRETURN, P_LCHOICE, P_JUNCTION, P_STEM):
            self.assertFalse(h.step(pt).departed_hub_via_stem, pt)


class TestTrialTrip(unittest.TestCase):
    """``trial_trip`` is the ONE credential the Phase 3 scorer, the cue gate, and
    the reward's choice_via_stem all read (docs/decisions/0168). A trip is a trial
    iff it DEPARTED the hub up the stem AND its outbound leg was not broken by a
    return arm before the choice commit (docs/decisions/0169). These tests pin the
    clean case (trial_trip == left_hub_via_stem), the two divergences where a
    stem-departed trip is still NOT a trial (G2 pre-commit return dip; G1 own-
    return-arm commit backstop), and that a POST-commit return visit does not
    break it (so the return cue still fires)."""

    def _assert_mirrors_left_hub(self, f, pt=None):
        self.assertEqual(f.trial_trip, f.left_hub_via_stem, pt)

    def test_clean_trip_mirrors_left_hub_via_stem(self):
        h = _Harness()
        # Hub -> depart up the stem -> around and home; on a CLEAN trip trial_trip
        # tracks the hub-departure level tick by tick.
        self.assertFalse(h.step(P_HUB).trial_trip)
        self.assertTrue(h.step(P_STEM).trial_trip)
        for pt in (P_JUNCTION, P_LCHOICE, P_LRETURN):
            f = h.step(pt)
            self.assertTrue(f.trial_trip, pt)
            self._assert_mirrors_left_hub(f, pt)
        # Home again -> the credential clears, ready for the next departure.
        self.assertFalse(h.step(P_HUB).trial_trip)

    def test_backwards_lap_is_not_a_trial_trip(self):
        # Set off up a return arm and later cross into the stem from the junction:
        # the stem IS visited (outbound_used_stem) but the trip never departed the
        # hub up the stem, so it is NOT a trial_trip (the divider-jump case).
        h = _Harness()
        for pt in (P_HUB, P_LRETURN, P_LCHOICE, P_JUNCTION):
            self.assertFalse(h.step(pt).trial_trip, pt)
        f = h.step(P_STEM)
        self.assertFalse(f.trial_trip)
        self.assertTrue(f.outbound_used_stem)   # the two genuinely differ here
        self._assert_mirrors_left_hub(f)

    def test_return_arm_on_the_outbound_leg_breaks_the_trip(self):
        # G2: departed up the stem (a real start) but veered into a return arm
        # BEFORE committing a choice - the trip is broken, so trial_trip and
        # left_hub_via_stem now DIVERGE (the trip did depart via the stem).
        h = _Harness()
        h.step(P_HUB)
        self.assertTrue(h.step(P_STEM).trial_trip)      # clean so far
        f = h.step(P_LRETURN)                           # return arm, pre-commit
        self.assertFalse(f.trial_trip)                  # broken (G2)
        self.assertTrue(f.left_hub_via_stem)            # ...but it DID depart up the stem
        # Stays broken for the rest of the trip (a "correct" choice now scores nothing).
        self.assertFalse(h.step(P_LCHOICE).trial_trip)
        # Home clears it; the next clean hub->stem departure is a trial again.
        h.step(P_HUB)
        self.assertTrue(h.step(P_STEM).trial_trip)

    def test_return_arm_after_the_commit_does_not_break(self):
        # The normal inbound leg: commit a choice from the junction, THEN take the
        # return arm home. A post-commit return-arm visit must NOT break the trip,
        # or the return cue could never fire on a clean trial.
        h = _Harness()
        h.step(P_HUB)
        h.step(P_STEM)
        h.step(P_JUNCTION)
        self.assertTrue(h.step(P_LCHOICE).trial_trip)   # commit from the junction
        self.assertTrue(h.step(P_LRETURN).trial_trip)   # inbound return arm: still a trial

    def test_choice_committed_from_own_return_arm_backstop(self):
        # G1 backstop for a MISSED return-arm entry edge: drive task_events with
        # RatStates directly so no LEFT_RETURN 'entered' edge is ever seen, yet the
        # choice commits with exited == LEFT_RETURN (came straight from its own
        # return arm). trial_trip must still be False.
        te = TaskEvents()
        te.update(RatState(zone=HUB, valid=True, now=0.0))          # arm / reset
        f1 = te.update(RatState(zone=CENTRAL_STEM, entered=CENTRAL_STEM,
                                exited=HUB, valid=True, now=0.1))
        self.assertTrue(f1.trial_trip)                              # clean stem departure
        f2 = te.update(RatState(zone=LEFT_CHOICE, entered=LEFT_CHOICE,
                                exited=LEFT_RETURN, valid=True, now=0.2))
        self.assertEqual(f2.choice_committed, LEFT)
        self.assertTrue(f2.left_hub_via_stem)
        self.assertFalse(f2.trial_trip)                             # broken (G1 backstop)


class TestLoopClean(unittest.TestCase):
    def test_clean_left_loop(self):
        h = _Harness()
        f = h.run(LEFT_LAP)
        self.assertEqual(f.loop_closed, LEFT)
        self.assertTrue(f.loop_clean)

    def test_clean_right_loop(self):
        h = _Harness()
        f = h.run(RIGHT_LAP)
        self.assertEqual(f.loop_closed, RIGHT)
        self.assertTrue(f.loop_clean)

    def test_stem_reversal_loop_is_unclean(self):
        # HUB -> stem -> junction -> left choice -> back down the stem -> HUB:
        # a choice arm was visited (so a loop closes) but no return arm was used,
        # so it is NOT a clean unidirectional lap.
        h = _Harness()
        f = h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE, P_JUNCTION, P_STEM, P_HUB])
        self.assertEqual(f.loop_closed, LEFT)
        self.assertFalse(f.loop_clean)


class TestSameSideRun(unittest.TestCase):
    def test_run_increments_same_side_and_resets_on_switch(self):
        h = _Harness()
        f = h.run(LEFT_LAP)
        self.assertEqual(f.loop_closed, LEFT)
        self.assertEqual(f.same_side_run, 1)
        self.assertEqual(f.left_loops, 1)
        f = h.run(LEFT_LAP[1:])          # already at HUB; do another left lap
        self.assertEqual(f.same_side_run, 2)
        self.assertEqual(f.left_loops, 2)
        f = h.run(RIGHT_LAP[1:])         # switch sides -> run resets to 1
        self.assertEqual(f.loop_closed, RIGHT)
        self.assertEqual(f.same_side_run, 1)
        self.assertEqual(f.right_loops, 1)

    def test_run_counts_every_closed_loop(self):
        # The run counts closed loops regardless of any downstream reward policy
        # (this layer is policy-free): three left laps -> run 3.
        h = _Harness()
        h.run(LEFT_LAP)
        h.run(LEFT_LAP[1:])
        f = h.run(LEFT_LAP[1:])
        self.assertEqual(f.same_side_run, 3)


class TestSameSideChoiceRun(unittest.TestCase):
    """The stem-gated choice-entry run used by the Phase 2b choice-side anti-bias
    (see docs/decisions/0030). Counts consecutive same-side stem-gated entries;
    a switch resets to 1; a non-stem (denied) entry does not reset it."""

    def _left_stem_choice(self, h):
        # HUB -> stem -> junction -> left choice, then back to HUB for the next.
        return h.run([P_STEM, P_JUNCTION, P_LCHOICE, P_LRETURN, P_HUB])

    def test_increments_on_same_side_and_resets_on_switch(self):
        h = _Harness()
        h.step(P_HUB)
        f = h.run([P_STEM, P_JUNCTION, P_LCHOICE])
        self.assertEqual(f.choice_entered, LEFT)
        self.assertEqual(f.same_side_choice_run, 1)
        f = h.run([P_LRETURN, P_HUB, P_STEM, P_JUNCTION, P_LCHOICE])
        self.assertEqual(f.same_side_choice_run, 2)
        f = h.run([P_LRETURN, P_HUB, P_STEM, P_JUNCTION, P_RCHOICE])
        self.assertEqual(f.choice_entered, RIGHT)
        self.assertEqual(f.same_side_choice_run, 1)

    def test_nonstem_entry_does_not_count_or_reset(self):
        h = _Harness()
        h.step(P_HUB)
        f = h.run([P_STEM, P_JUNCTION, P_LCHOICE])       # stem-gated -> run 1
        self.assertEqual(f.same_side_choice_run, 1)
        # A non-stem entry (up the return arm) is not a valid trial: run unchanged.
        f = h.run([P_LRETURN, P_HUB, P_LRETURN, P_LCHOICE])
        self.assertEqual(f.choice_entered, LEFT)
        self.assertFalse(f.outbound_used_stem)
        self.assertEqual(f.same_side_choice_run, 1)


class TestChoiceCommitted(unittest.TestCase):
    """The shared once-per-outbound-excursion choice-commit edge (FW-0020,
    docs/decisions/0055): fires the FIRST choice-arm entry per trip regardless of
    route, re-arms on HUB presence, and bumps trip_id per commit."""

    def test_commits_once_per_trip_and_rearms_at_hub(self):
        h = _Harness()
        f = h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE])
        self.assertEqual(f.choice_committed, LEFT)
        self.assertEqual(f.trip_id, 1)
        # Staying in the arm does not re-fire the commit.
        f = h.step(P_LCHOICE)
        self.assertIsNone(f.choice_committed)
        # Close the lap and start a fresh trip -> commits again, new trip_id.
        f = h.run([P_LRETURN, P_HUB, P_STEM, P_JUNCTION, P_RCHOICE])
        self.assertEqual(f.choice_committed, RIGHT)
        self.assertEqual(f.trip_id, 2)

    def test_figure8_second_arm_same_trip_does_not_recommit(self):
        # HUB -> stem -> left choice (commit) -> junction -> right choice: the
        # second arm of the SAME trip must NOT commit again.
        h = _Harness()
        f = h.run([P_HUB, P_STEM, P_JUNCTION, P_LCHOICE])
        self.assertEqual(f.choice_committed, LEFT)
        f = h.run([P_JUNCTION, P_RCHOICE])
        self.assertEqual(f.choice_entered, RIGHT)   # a choice entry edge...
        self.assertIsNone(f.choice_committed)       # ...but the trip already committed
        self.assertEqual(f.trip_id, 1)

    def test_nonstem_first_entry_still_commits(self):
        # Reaching a choice arm via the return arm (no stem) still commits the
        # trip: the edge is route-agnostic (the stem gate is a consumer's job).
        h = _Harness()
        f = h.run([P_HUB, P_LRETURN, P_LCHOICE])
        self.assertEqual(f.choice_committed, LEFT)
        self.assertFalse(f.outbound_used_stem)
        self.assertEqual(f.trip_id, 1)


if __name__ == "__main__":
    unittest.main()
