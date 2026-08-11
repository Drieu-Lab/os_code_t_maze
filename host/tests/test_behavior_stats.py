"""
test_behavior_stats.py - unit tests for the Phase 2a behavior-readout core.

Headless (stdlib unittest), like the other pure-core suites. Run from host/:
    python -m unittest discover -p "test_*.py"

Covers the core-5 measures (stem-compliance, L/R balance, non-stem count,
same-side run, throughput) plus total distance - including per-EXCURSION choice
counting (a figure-8 that crosses between the two arms is one trip, not two),
the confidence-gap re-seed (a re-acquisition jump is NOT added), the jump cap,
the homography path, and the active-time pause behavior. See docs/decisions/0032.
"""

import unittest

from tmaze.core.behavior_stats import BehaviorStats, LEFT, RIGHT
from tmaze.core.maze_state import HUB, UNKNOWN, RatState
from tmaze.core.task_events import TaskEventFrame
from tmaze.core.rules_engine import Scale


_UNSET = object()


def frame(now, *, valid=True, choice=None, stem=False, zone=UNKNOWN,
          committed=_UNSET):
    """Build a TaskEventFrame with just the fields BehaviorStats reads.

    ``choice`` sets choice_entered; ``committed`` sets the shared once-per-trip
    choice_committed edge (docs/decisions/0055) BehaviorStats now counts off, and
    defaults to ``choice`` so a choice frame commits by default. Pass
    ``committed=None`` to model a same-trip re-entry that does NOT re-commit (the
    shared edge already fired this trip). ``stem`` sets both stem predicates -
    BehaviorStats reads only ``outbound_used_stem``, but a frame where the trip
    used the stem is one where it departed up it, so keeping them consistent
    avoids a nonsensical synthetic frame."""
    committed_side = choice if committed is _UNSET else committed
    return TaskEventFrame(
        now=now, valid=valid,
        at_well=False, outbound_used_stem=stem,
        left_hub_via_stem=stem,
        well_arrived=False, departed_hub_via_stem=False, choice_entered=choice,
        choice_committed=committed_side,
        loop_closed=None, loop_clean=False,
        same_side_run=0, same_side_choice_run=0,
        left_loops=0, right_loops=0,
        trip_id=0, rat_state=RatState(zone=zone, valid=valid, now=now))


def hub(now):
    """A tick with the animal back at the hub (re-arms the next excursion)."""
    return frame(now, zone=HUB)


class TestComplianceAndChoices(unittest.TestCase):
    def test_compliance_cumulative(self):
        s = BehaviorStats()
        # 3 stem-gated, 1 non-stem => 3/4 compliance (one choice per excursion).
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))
        s.update(hub(0.2))
        s.update(frame(0.3, choice=RIGHT, stem=True))
        s.update(hub(0.4))
        s.update(frame(0.5, choice=LEFT, stem=False))      # non-stem
        s.update(hub(0.6))
        s.update(frame(0.7, choice=RIGHT, stem=True))
        snap = s.snapshot()
        self.assertEqual(snap.total_choices, 4)
        self.assertEqual(snap.stem_choices, 3)
        self.assertEqual(snap.nonstem_choices, 1)
        self.assertAlmostEqual(snap.compliance_cum, 0.75)

    def test_no_choices_yet_is_none(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        snap = s.snapshot()
        self.assertIsNone(snap.compliance_cum)
        self.assertIsNone(snap.compliance_win)
        self.assertIsNone(snap.lr_cum)

    def test_lr_balance_only_counts_stem_gated(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))
        s.update(hub(0.2))
        s.update(frame(0.3, choice=LEFT, stem=True))
        s.update(hub(0.4))
        s.update(frame(0.5, choice=RIGHT, stem=True))
        s.update(hub(0.6))
        s.update(frame(0.7, choice=RIGHT, stem=False))     # non-stem: ignored for L/R
        snap = s.snapshot()
        self.assertEqual((snap.left_choices, snap.right_choices), (2, 1))
        self.assertIsNotNone(snap.lr_cum)
        self.assertAlmostEqual(snap.lr_cum[0], 2 / 3)
        self.assertAlmostEqual(snap.lr_cum[1], 1 / 3)

    def test_nonstem_left_right_split(self):
        # F8 (docs/decisions/0095): denied (non-stem) trips are also split by the
        # committed side - a directional pull on the shortcut trips, kept separate
        # from the stem-gated L/R balance. Here: 1 stem-LEFT trial, then three
        # denied trips (LEFT, RIGHT, LEFT) => nonstem 2L/1R, stem L/R unchanged.
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))       # stem-gated
        s.update(hub(0.2))
        s.update(frame(0.3, choice=LEFT, stem=False))      # denied L
        s.update(hub(0.4))
        s.update(frame(0.5, choice=RIGHT, stem=False))     # denied R
        s.update(hub(0.6))
        s.update(frame(0.7, choice=LEFT, stem=False))      # denied L
        snap = s.snapshot()
        self.assertEqual(snap.nonstem_choices, 3)
        self.assertEqual((snap.nonstem_left, snap.nonstem_right), (2, 1))
        # Split sums to the total denied count, by construction.
        self.assertEqual(snap.nonstem_left + snap.nonstem_right,
                         snap.nonstem_choices)
        self.assertAlmostEqual(snap.nonstem_lr[0], 2 / 3)
        self.assertAlmostEqual(snap.nonstem_lr[1], 1 / 3)
        # Stem-gated L/R is untouched by the denied trips.
        self.assertEqual((snap.left_choices, snap.right_choices), (1, 0))

    def test_nonstem_lr_none_before_any_denied_trip(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))       # only a stem trial
        snap = s.snapshot()
        self.assertEqual((snap.nonstem_left, snap.nonstem_right), (0, 0))
        self.assertIsNone(snap.nonstem_lr)


class TestPerExcursionCounting(unittest.TestCase):
    def test_figure8_nonstem_cross_counts_one(self):
        # Out one arm and back the other WITHOUT the stem: two choice-zone entries
        # in one trip must count as a single non-stem choice, not two.
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=False))      # enters L (from return): commits
        s.update(frame(0.2, choice=RIGHT, stem=False, committed=None))  # cross to R, same trip
        s.update(hub(0.3))                                 # back home
        snap = s.snapshot()
        self.assertEqual(snap.total_choices, 1)
        self.assertEqual(snap.nonstem_choices, 1)
        self.assertEqual(snap.stem_choices, 0)

    def test_return_via_stem_counts_one(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))       # out via stem to L
        s.update(hub(0.2))                                 # back via stem
        snap = s.snapshot()
        self.assertEqual(snap.total_choices, 1)
        self.assertEqual(snap.stem_choices, 1)

    def test_within_trip_arm_cross_after_stem_counts_one(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))       # stem-gated L (commits, counted)
        s.update(frame(0.2, choice=RIGHT, stem=True, committed=None))  # cross to R: ignored
        s.update(hub(0.3))
        snap = s.snapshot()
        self.assertEqual(snap.total_choices, 1)
        self.assertEqual((snap.left_choices, snap.right_choices), (1, 0))

    def test_two_separate_excursions_count_two(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))
        s.update(hub(0.2))                                 # returned home => re-armed
        s.update(frame(0.3, choice=RIGHT, stem=True))
        s.update(hub(0.4))
        self.assertEqual(s.snapshot().total_choices, 2)


class TestRollingWindow(unittest.TestCase):
    def test_window_reflects_recent_only(self):
        s = BehaviorStats(window_n=3)
        # First two excursions non-stem, then three stem-gated; window is last 3.
        for i, (choice, stem) in enumerate([
                (LEFT, False), (RIGHT, False), (LEFT, True), (RIGHT, True), (LEFT, True)]):
            s.update(hub(2 * i))
            s.update(frame(2 * i + 1, choice=choice, stem=stem))
        snap = s.snapshot()
        self.assertAlmostEqual(snap.compliance_cum, 3 / 5)
        self.assertAlmostEqual(snap.compliance_win, 1.0)   # last 3 all stem-gated

    def test_resize_preserves_recent(self):
        s = BehaviorStats(window_n=5)
        for i in range(5):
            s.update(hub(2 * i))
            s.update(frame(2 * i + 1, choice=LEFT, stem=True))
        s.set_window(2)
        snap = s.snapshot()
        self.assertEqual(snap.window_n, 2)
        self.assertAlmostEqual(snap.compliance_win, 1.0)


class TestSameSideRun(unittest.TestCase):
    def test_tracks_current_and_max(self):
        s = BehaviorStats()
        for i, side in enumerate([LEFT, LEFT, LEFT, RIGHT]):
            s.update(hub(2 * i))
            s.update(frame(2 * i + 1, choice=side, stem=True))
        snap = s.snapshot()
        self.assertEqual(snap.same_side_run_cur, 1)        # switched to right last
        self.assertEqual(snap.same_side_run_max, 3)        # three lefts in a row

    def test_tracks_side_of_current_and_max_run(self):
        s = BehaviorStats()
        for i, side in enumerate([LEFT, LEFT, LEFT, RIGHT, RIGHT]):
            s.update(hub(2 * i))
            s.update(frame(2 * i + 1, choice=side, stem=True))
        snap = s.snapshot()
        self.assertEqual((snap.same_side_run_cur, snap.same_side_run_side), (2, RIGHT))
        self.assertEqual((snap.same_side_run_max, snap.same_side_run_max_side), (3, LEFT))

    def test_nonstem_excursion_does_not_reset_run(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True))       # run L = 1
        s.update(hub(0.2))
        s.update(frame(0.3, choice=RIGHT, stem=False))     # non-stem: not a trial
        s.update(hub(0.4))
        s.update(frame(0.5, choice=LEFT, stem=True))       # run L = 2 (not reset)
        snap = s.snapshot()
        self.assertEqual((snap.same_side_run_cur, snap.same_side_run_side), (2, LEFT))

    def test_run_side_none_before_any_stem_choice(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=False))      # non-stem: not a trial
        snap = s.snapshot()
        self.assertIsNone(snap.same_side_run_side)
        self.assertIsNone(snap.same_side_run_max_side)


class TestThroughputActiveTime(unittest.TestCase):
    def test_active_time_sums_normal_ticks(self):
        s = BehaviorStats(max_dt_s=0.5)
        for i in range(11):                                # 11 ticks at dt=0.15 => 1.5 s
            s.update(frame(i * 0.15))
        self.assertAlmostEqual(s.snapshot().active_s, 1.5, places=6)

    def test_large_gap_not_counted(self):
        s = BehaviorStats(max_dt_s=0.5)
        s.update(frame(0.0))
        s.update(frame(0.15))          # +0.15
        s.update(frame(60.0))          # 59.85 s jump > cap => dropped
        s.update(frame(60.15))         # +0.15
        self.assertAlmostEqual(s.snapshot().active_s, 0.30, places=6)

    def test_mark_gap_breaks_timing(self):
        s = BehaviorStats(max_dt_s=0.5)
        s.update(frame(0.0))
        s.update(frame(0.15))          # +0.15
        s.mark_gap()                   # pause
        s.update(frame(0.30))          # first tick after resume: no dt added
        s.update(frame(0.45))          # +0.15
        self.assertAlmostEqual(s.snapshot().active_s, 0.30, places=6)

    def test_trials_per_min(self):
        s = BehaviorStats(max_dt_s=1.0)
        # 2 stem-gated trials over 2 s active => 60 trials/min.
        s.update(hub(0.0))
        s.update(frame(1.0, choice=LEFT, stem=True))
        s.update(hub(1.5))
        s.update(frame(2.0, choice=RIGHT, stem=True))
        snap = s.snapshot()
        self.assertAlmostEqual(snap.active_s, 2.0, places=6)
        self.assertAlmostEqual(snap.trials_per_min, 60.0, places=6)

    def test_trials_per_min_none_below_one_second(self):
        s = BehaviorStats()
        s.update(hub(0.0))
        s.update(frame(0.15, choice=LEFT, stem=True))
        self.assertIsNone(s.snapshot().trials_per_min)


class TestDistance(unittest.TestCase):
    def test_linear_scale_sums_segments(self):
        s = BehaviorStats()
        scale = Scale(cm_per_px=0.1)
        s.update(frame(0.0), pos_px=(0.0, 0.0), scale=scale)     # seed
        s.update(frame(0.1), pos_px=(30.0, 40.0), scale=scale)   # 50 px => 5 cm
        s.update(frame(0.2), pos_px=(30.0, 40.0), scale=scale)   # no move
        snap = s.snapshot()
        self.assertTrue(snap.distance_contributed)
        self.assertAlmostEqual(snap.distance_cm, 5.0)

    def test_homography_path(self):
        s = BehaviorStats()
        # Pure 0.2 world-cm-per-px scaling expressed as a homography.
        scale = Scale(homography=[[0.2, 0.0, 0.0], [0.0, 0.2, 0.0], [0.0, 0.0, 1.0]])
        s.update(frame(0.0), pos_px=(0.0, 0.0), scale=scale)
        s.update(frame(0.1), pos_px=(0.0, 100.0), scale=scale)   # 100 px * 0.2 = 20 cm
        self.assertAlmostEqual(s.snapshot().distance_cm, 20.0)

    def test_jump_cap_rejects_spurious_hop(self):
        s = BehaviorStats(max_jump_px=50.0)
        scale = Scale(cm_per_px=1.0)
        s.update(frame(0.0), pos_px=(0.0, 0.0), scale=scale)
        s.update(frame(0.1), pos_px=(1000.0, 0.0), scale=scale)  # 1000 px > cap => skipped
        s.update(frame(0.2), pos_px=(1010.0, 0.0), scale=scale)  # 10 px from new anchor => 10 cm
        self.assertAlmostEqual(s.snapshot().distance_cm, 10.0)

    def test_confidence_gap_reseeds_without_adding_jump(self):
        s = BehaviorStats(max_jump_px=1e9)   # large cap so only the gap logic matters
        scale = Scale(cm_per_px=1.0)
        s.update(frame(0.0, valid=True), pos_px=(0.0, 0.0), scale=scale)
        s.update(frame(0.1, valid=True), pos_px=(10.0, 0.0), scale=scale)   # +10 cm
        s.update(frame(0.2, valid=False), pos_px=(500.0, 0.0), scale=scale)  # gap: drop anchor
        s.update(frame(0.3, valid=True), pos_px=(900.0, 0.0), scale=scale)   # re-seed, NOT added
        s.update(frame(0.4, valid=True), pos_px=(905.0, 0.0), scale=scale)   # +5 cm from re-seed
        snap = s.snapshot()
        self.assertAlmostEqual(snap.distance_cm, 15.0)   # 10 + 5, never the ~400 jump

    def test_no_scale_means_no_distance(self):
        s = BehaviorStats()
        s.update(frame(0.0), pos_px=(0.0, 0.0), scale=None)
        s.update(frame(0.1), pos_px=(100.0, 0.0), scale=None)
        snap = s.snapshot()
        self.assertFalse(snap.distance_contributed)
        self.assertEqual(snap.distance_cm, 0.0)

    def test_invalid_scale_means_no_distance(self):
        s = BehaviorStats()
        empty = Scale()   # neither cm_per_px nor homography => not valid()
        s.update(frame(0.0), pos_px=(0.0, 0.0), scale=empty)
        s.update(frame(0.1), pos_px=(100.0, 0.0), scale=empty)
        self.assertFalse(s.snapshot().distance_contributed)


class TestReset(unittest.TestCase):
    def test_reset_clears_everything(self):
        s = BehaviorStats()
        scale = Scale(cm_per_px=1.0)
        s.update(hub(0.0))
        s.update(frame(0.1, choice=LEFT, stem=True), pos_px=(0.0, 0.0), scale=scale)
        s.update(hub(0.2))
        s.update(frame(0.3, choice=LEFT, stem=True), pos_px=(10.0, 0.0), scale=scale)
        s.reset()
        snap = s.snapshot()
        self.assertEqual(snap.total_choices, 0)
        self.assertEqual(snap.same_side_run_max, 0)
        self.assertEqual(snap.distance_cm, 0.0)
        self.assertEqual(snap.active_s, 0.0)


if __name__ == "__main__":
    unittest.main()
