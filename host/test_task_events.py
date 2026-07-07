"""
test_task_events.py - headless unit tests for the task-event layer.

No Tk, no OpenCV, no hardware: drives task_events.py with RatStates produced by
the real maze_state estimator over synthetic centroid trajectories. Run from
host/:

    python -m unittest test_task_events

Covers (see docs/decisions/0026): the well-arrival edge; the choice-entered edge
(left/right); outbound_used_stem (stem vs return-arm approach); clean vs unclean
loop detection; and the same-side run aggregate (counts every closed loop,
rewarded or not; resets on a side switch).
"""

from __future__ import annotations

import types
import unittest

from maze_state import (
    HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN,
    RIGHT_RETURN, LEFT, RIGHT, MazeStateEstimator, MazeStateParams, Zone, ZoneMap,
)
from task_events import TaskEvents

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


if __name__ == "__main__":
    unittest.main()
