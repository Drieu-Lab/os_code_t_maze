"""
test_maze_state.py - headless unit tests for the pure maze-state core.

No Tk, no OpenCV, no hardware: drives maze_state.py over synthetic centroid
trajectories (point sequences -> expected zone / transition / direction / loop
streams). Run from host/:

    python -m unittest test_maze_state

Covers (see docs/decisions/0025): point-in-polygon; zone classification;
first-localization commit; min-dwell hysteresis (border jitter does not flicker
the state); UNKNOWN + no false transition on low-confidence / COASTING / LOST;
gap stickiness; the in_well substate; direction (up_stem vs down_stem) from the
stem-axis projection; completed stem->choice-arm->stem loop detection (L and R);
the visited_since() history query; zones.json round-trip; and the CSV helpers.
"""

from __future__ import annotations

import types
import unittest

from tmaze.core.maze_state import (
    HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN,
    RIGHT_RETURN, UNKNOWN, IN_WELL, OUTSIDE_WELL, UP_STEM, DOWN_STEM, LEFT, RIGHT,
    MazeStateEstimator, MazeStateParams, ZoneMap,
    point_in_polygon, project_scalar, state_timeline_header, state_row,
)
# The synthetic maze layout lives in the validation tool so the tool's --selftest
# and these tests share one fixture (test -> tool -> core direction; see ADR-0083).
from tmaze.tools.maze_state_validate import make_zone_map

FRAME_W, FRAME_H = 1000, 1000


def rect(x0, y0, x1, y1):
    """Axis-aligned rectangle as a normalized 4-vertex polygon."""
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


# Representative interior points, in FULL-frame px (norm * 1000).
P_HUB = (500, 900)         # HUB, inside the well sub-region
P_HUB_OUT = (500, 830)     # HUB, outside the well
P_STEM = (500, 650)        # central stem
P_JUNCTION = (500, 420)
P_LCHOICE = (200, 350)
P_RCHOICE = (800, 350)
P_LRETURN = (100, 650)
P_RRETURN = (900, 650)
P_GAP = (500, 100)         # inside no zone (above every polygon)


def tr(cx, cy, conf=0.9, state="TRACKING", w=FRAME_W, h=FRAME_H):
    """Duck-typed TrackResult-like snapshot (smoothed == centroid here)."""
    return types.SimpleNamespace(
        detected=True, state=state, confidence=conf,
        frame_size=(w, h), centroid=(cx, cy), smoothed=(cx, cy))


def make_estimator(min_dwell=1, **pk):
    params = MazeStateParams(min_dwell_ticks=min_dwell, **pk)
    return MazeStateEstimator(make_zone_map(), params)


class TestGeometry(unittest.TestCase):
    def test_point_in_polygon(self):
        square = rect(0.0, 0.0, 1.0, 1.0)
        self.assertTrue(point_in_polygon(0.5, 0.5, square))
        self.assertFalse(point_in_polygon(1.5, 0.5, square))
        self.assertFalse(point_in_polygon(0.5, -0.1, square))
        # Fewer than 3 vertices is never "inside".
        self.assertFalse(point_in_polygon(0.5, 0.5, [(0, 0), (1, 1)]))

    def test_project_scalar(self):
        # base (0,0) -> tip (0,1): t is just the y coordinate.
        self.assertAlmostEqual(project_scalar(0.0, 0.0, (0, 0), (0, 1)), 0.0)
        self.assertAlmostEqual(project_scalar(0.0, 1.0, (0, 0), (0, 1)), 1.0)
        self.assertAlmostEqual(project_scalar(0.3, 0.5, (0, 0), (0, 1)), 0.5)
        self.assertIsNone(project_scalar(0.0, 0.0, (0, 0), (0, 0)))  # degenerate

    def test_classify_point(self):
        zm = make_zone_map()
        self.assertEqual(zm.classify_point(0.5, 0.90), HUB)
        self.assertEqual(zm.classify_point(0.5, 0.65), CENTRAL_STEM)
        self.assertEqual(zm.classify_point(0.5, 0.42), JUNCTION)
        self.assertEqual(zm.classify_point(0.2, 0.35), LEFT_CHOICE)
        self.assertIsNone(zm.classify_point(0.5, 0.1))  # gap


class TestFirstLocalizationAndValidity(unittest.TestCase):
    def test_first_point_commits_immediately(self):
        eng = make_estimator()
        s = eng.update(tr(*P_STEM), 0.0)
        self.assertEqual(s.zone, CENTRAL_STEM)
        self.assertEqual(s.entered, CENTRAL_STEM)
        self.assertIsNone(s.exited)
        self.assertTrue(s.valid)

    def test_low_confidence_is_unknown(self):
        eng = make_estimator()
        s = eng.update(tr(*P_STEM, conf=0.1), 0.0)
        self.assertEqual(s.zone, UNKNOWN)
        self.assertFalse(s.valid)
        self.assertIsNone(s.entered)

    def test_non_tracking_states_are_unknown(self):
        for st in ("ACQUIRING", "COASTING", "LOST"):
            eng = make_estimator()
            s = eng.update(tr(*P_STEM, state=st), 0.0)
            self.assertEqual(s.zone, UNKNOWN, st)
            self.assertFalse(s.valid, st)

    def test_no_map_is_unknown(self):
        eng = MazeStateEstimator(ZoneMap(), MazeStateParams())
        s = eng.update(tr(*P_STEM), 0.0)
        self.assertEqual(s.zone, UNKNOWN)
        self.assertFalse(s.valid)


class TestHysteresis(unittest.TestCase):
    def test_min_dwell_suppresses_flicker(self):
        eng = make_estimator(min_dwell=3)
        eng.update(tr(*P_STEM), 0.0)                 # commit stem
        s1 = eng.update(tr(*P_JUNCTION), 0.1)        # candidate count 1
        s2 = eng.update(tr(*P_JUNCTION), 0.2)        # count 2
        self.assertEqual(s1.zone, CENTRAL_STEM)
        self.assertIsNone(s1.entered)
        self.assertEqual(s2.zone, CENTRAL_STEM)
        s3 = eng.update(tr(*P_JUNCTION), 0.3)        # count 3 -> commit
        self.assertEqual(s3.zone, JUNCTION)
        self.assertEqual(s3.entered, JUNCTION)
        self.assertEqual(s3.exited, CENTRAL_STEM)

    def test_interrupted_candidate_resets(self):
        eng = make_estimator(min_dwell=3)
        eng.update(tr(*P_STEM), 0.0)
        eng.update(tr(*P_JUNCTION), 0.1)             # count 1
        # Back to committed zone -> candidate resets.
        eng.update(tr(*P_STEM), 0.2)
        s = eng.update(tr(*P_JUNCTION), 0.3)         # count 1 again, no commit
        self.assertEqual(s.zone, CENTRAL_STEM)
        self.assertIsNone(s.entered)

    def test_gap_is_sticky(self):
        eng = make_estimator()
        eng.update(tr(*P_STEM), 0.0)
        s = eng.update(tr(*P_GAP), 0.1)              # outside all polygons
        self.assertEqual(s.zone, CENTRAL_STEM)       # holds current zone
        self.assertIsNone(s.entered)


class TestOffTrack(unittest.TestCase):
    """The off_track "perch" flag (docs/decisions/0170): a valid point sitting in a
    polygon gap past off_track_after_s. The held zone stays sticky (unchanged), so
    off_track is an ADDITIVE flag a gap-perched rat trips without breaking any
    zone/validity consumer."""

    def test_gap_under_threshold_not_off_track(self):
        eng = make_estimator(off_track_after_s=1.0)
        eng.update(tr(*P_STEM), 0.0)
        s = eng.update(tr(*P_GAP), 0.5)              # 0.5s in the gap < 1.0s
        self.assertEqual(s.zone, CENTRAL_STEM)       # still sticky
        self.assertTrue(s.valid)
        self.assertFalse(s.off_track)

    def test_gap_over_threshold_sets_off_track(self):
        eng = make_estimator(off_track_after_s=1.0)
        eng.update(tr(*P_STEM), 0.0)
        eng.update(tr(*P_GAP), 0.2)                  # gap timer starts here
        s = eng.update(tr(*P_GAP), 1.3)              # 1.1s in the gap >= 1.0s
        self.assertEqual(s.zone, CENTRAL_STEM)       # zone unchanged
        self.assertTrue(s.valid)                     # validity unchanged
        self.assertTrue(s.off_track)

    def test_returning_to_a_zone_clears_off_track(self):
        eng = make_estimator(off_track_after_s=1.0)
        eng.update(tr(*P_STEM), 0.0)
        eng.update(tr(*P_GAP), 0.2)
        self.assertTrue(eng.update(tr(*P_GAP), 1.5).off_track)
        s = eng.update(tr(*P_STEM), 1.6)             # back inside a polygon
        self.assertFalse(s.off_track)

    def test_dropout_mid_perch_does_not_reset_the_timer(self):
        # A brief tracking dropout while perched must not restart "how long off
        # track" - the rat has not moved. Timer holds across the invalid tick.
        eng = make_estimator(off_track_after_s=1.0)
        eng.update(tr(*P_STEM), 0.0)
        eng.update(tr(*P_GAP), 0.2)                  # gap timer starts at 0.2
        d = eng.update(tr(*P_GAP, state="COASTING"), 0.6)  # invalid: held
        self.assertFalse(d.valid)
        self.assertFalse(d.off_track)                # off_track is False while invalid
        s = eng.update(tr(*P_GAP), 1.3)              # re-acquire, still in gap
        self.assertTrue(s.off_track)                 # 1.1s since 0.2, not since 1.3

    def test_never_off_track_before_first_localization(self):
        # A gap point before any zone is committed is invalid, never off_track.
        eng = make_estimator(off_track_after_s=1.0)
        s0 = eng.update(tr(*P_GAP), 0.0)
        s1 = eng.update(tr(*P_GAP), 5.0)
        self.assertFalse(s0.valid)
        self.assertFalse(s0.off_track)
        self.assertFalse(s1.off_track)


class TestNoFalseTransition(unittest.TestCase):
    def test_invalid_then_reacquire_same_zone_no_edge(self):
        eng = make_estimator()
        a = eng.update(tr(*P_STEM), 0.0)
        self.assertEqual(a.entered, CENTRAL_STEM)
        b = eng.update(tr(*P_STEM, state="COASTING"), 0.1)
        self.assertEqual(b.zone, UNKNOWN)
        self.assertIsNone(b.entered)
        self.assertIsNone(b.exited)
        c = eng.update(tr(*P_STEM), 0.2)             # confident again, same zone
        self.assertEqual(c.zone, CENTRAL_STEM)
        self.assertTrue(c.valid)
        self.assertIsNone(c.entered)                 # no spurious re-entry


class TestSubstate(unittest.TestCase):
    def test_in_well_vs_outside_well(self):
        eng = make_estimator()
        s_out = eng.update(tr(*P_HUB_OUT), 0.0)
        self.assertEqual(s_out.zone, HUB)
        self.assertEqual(s_out.substate, OUTSIDE_WELL)
        s_in = eng.update(tr(*P_HUB), 0.1)
        self.assertEqual(s_in.zone, HUB)
        self.assertEqual(s_in.substate, IN_WELL)

    def test_substate_none_off_hub(self):
        eng = make_estimator()
        s = eng.update(tr(*P_STEM), 0.0)   # stem, not the HUB
        self.assertIsNone(s.substate)


class TestDirection(unittest.TestCase):
    def test_up_stem_when_progress_increases(self):
        eng = make_estimator()
        s = None
        # Move up the stem toward the junction (y decreasing => progress up).
        for i, cy in enumerate((780, 720, 660, 600)):
            s = eng.update(tr(500, cy), i * 0.1)
        self.assertEqual(s.zone, CENTRAL_STEM)
        self.assertEqual(s.direction, UP_STEM)
        self.assertIsNotNone(s.stem_progress)
        self.assertTrue(0.0 <= s.stem_progress <= 1.0)

    def test_down_stem_when_progress_decreases(self):
        eng = make_estimator()
        s = None
        for i, cy in enumerate((560, 640, 720, 780)):
            s = eng.update(tr(500, cy), i * 0.1)
        self.assertEqual(s.direction, DOWN_STEM)

    def test_no_axis_no_direction(self):
        zm = make_zone_map()
        zm = ZoneMap(zones=zm.zones, well=zm.well)   # drop the stem axis
        eng = MazeStateEstimator(zm, MazeStateParams(min_dwell_ticks=1))
        s = None
        for i, cy in enumerate((780, 720, 660, 600)):
            s = eng.update(tr(500, cy), i * 0.1)
        self.assertIsNone(s.direction)
        self.assertIsNone(s.stem_progress)

    def test_direction_none_off_stem(self):
        eng = make_estimator()
        eng.update(tr(*P_STEM), 0.0)
        s = eng.update(tr(*P_LCHOICE), 0.1)          # choice arm: not on stem
        self.assertIsNone(s.direction)
        self.assertIsNone(s.stem_progress)


class TestLoopDetection(unittest.TestCase):
    def _run_loop(self, choice_pt, return_pt):
        eng = make_estimator()
        seq = [(P_HUB, 0.0), (P_STEM, 0.1), (P_JUNCTION, 0.2), (choice_pt, 0.3),
               (return_pt, 0.4), (P_HUB, 0.5)]
        last = None
        for pt, now in seq:
            last = eng.update(tr(*pt), now)
        return last

    def test_left_loop(self):
        s = self._run_loop(P_LCHOICE, P_LRETURN)
        self.assertEqual(s.zone, HUB)
        self.assertTrue(s.loop_completed)
        self.assertEqual(s.loop_side, LEFT)

    def test_right_loop(self):
        s = self._run_loop(P_RCHOICE, P_RRETURN)
        self.assertTrue(s.loop_completed)
        self.assertEqual(s.loop_side, RIGHT)

    def test_no_loop_without_choice_arm(self):
        # HUB -> stem -> junction -> stem -> HUB: no choice arm visited, no loop.
        eng = make_estimator()
        for pt, now in [(P_HUB, 0.0), (P_STEM, 0.1), (P_JUNCTION, 0.2),
                        (P_STEM, 0.3), (P_HUB, 0.4)]:
            s = eng.update(tr(*pt), now)
        self.assertFalse(s.loop_completed)
        self.assertIsNone(s.loop_side)


class TestVisitedSince(unittest.TestCase):
    def test_choice_arm_since_leaving_hub(self):
        eng = make_estimator()
        eng.update(tr(*P_HUB), 0.0)
        eng.update(tr(*P_STEM), 0.1)
        eng.update(tr(*P_JUNCTION), 0.2)
        eng.update(tr(*P_LCHOICE), 0.3)
        s = eng.update(tr(*P_LRETURN), 0.4)          # now in the return arm
        self.assertTrue(s.visited_since(LEFT_CHOICE, HUB))
        self.assertFalse(s.visited_since(RIGHT_CHOICE, HUB))

    def test_anchor_resets_on_new_hub_visit(self):
        # Complete a left lap, return to the HUB, then head out again: the left
        # choice is no longer "since last leaving the HUB".
        eng = make_estimator()
        for pt, now in [(P_HUB, 0.0), (P_STEM, 0.1), (P_JUNCTION, 0.2),
                        (P_LCHOICE, 0.3), (P_LRETURN, 0.4), (P_HUB, 0.5),
                        (P_STEM, 0.6)]:
            s = eng.update(tr(*pt), now)
        # Left the HUB again at t=0.6; no choice arm visited since then.
        self.assertFalse(s.visited_since(LEFT_CHOICE, HUB))


class TestPersistence(unittest.TestCase):
    def test_zone_map_roundtrip(self):
        zm = make_zone_map()
        zm2 = ZoneMap.from_dict(zm.to_dict())
        self.assertEqual(len(zm2.zones), len(zm.zones))
        self.assertEqual(zm2.classify_point(0.5, 0.90), HUB)
        self.assertEqual(zm2.classify_point(0.5, 0.65), CENTRAL_STEM)
        self.assertEqual(zm2.stem_base, zm.stem_base)
        self.assertEqual(zm2.stem_tip, zm.stem_tip)
        self.assertTrue(zm2.in_well(0.5, 0.92))

    def test_from_dict_ignores_unknown_names(self):
        d = {"version": 1, "zones": [
            {"name": "CENTRAL_STEM", "polygon_norm": [[0.4, 0.5], [0.6, 0.5],
                                                      [0.6, 1.0], [0.4, 1.0]]},
            {"name": "BOGUS", "polygon_norm": [[0, 0], [1, 0], [1, 1]]},
            {"name": "UNKNOWN", "polygon_norm": [[0, 0], [1, 0], [1, 1]]},
        ]}
        zm = ZoneMap.from_dict(d)
        self.assertEqual(len(zm.zones), 1)
        self.assertEqual(zm.zones[0].name, CENTRAL_STEM)

    def test_empty_dict(self):
        zm = ZoneMap.from_dict(None)
        self.assertFalse(zm.valid())
        self.assertIsNone(zm.classify_point(0.5, 0.5))


class TestCsvHelpers(unittest.TestCase):
    def test_header_and_row(self):
        eng = make_estimator()
        s = eng.update(tr(*P_STEM), 1.5)
        header = state_timeline_header()
        row = state_row(s)
        self.assertEqual(len(header), len(row))
        self.assertEqual(row[header.index("zone")], CENTRAL_STEM)
        self.assertEqual(row[header.index("valid")], 1)
        self.assertEqual(row[header.index("entered")], CENTRAL_STEM)


if __name__ == "__main__":
    unittest.main()
