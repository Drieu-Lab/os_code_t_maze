"""
test_phase3_traces.py - headless tests for the Phase 3 live-trace core.

Covers ``phase3_traces.build_traces`` (docs/decisions/0114) over deterministic
snapshots (build a Phase3Log, fill trials via its public methods incl.
``trial_started`` / ``trial_ended`` for the dead-time ITI, ``.snapshot()``): the
wall-clock time axis, the rolling median inter-trial interval and its threshold
crossing, the out-of-block strategy rates (side bias, behavioural alternation),
min-denominator gating, intervention-block spans, and the undefined-without-
errors directional bias. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_phase3_traces
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.phase3.phase3_traces import (
    DEFAULT_DISENGAGED_GAP_S, DEFAULT_MIN_DEN, BlockSpan, TraceResult,
    build_traces)
from tmaze.core.phase3.trial_sequence import (
    HIGH, LEFT, LOW, RIGHT, TrialConfig, TrialSequence)

_BASE = datetime(2026, 7, 23, 12, 0, 0)


def _iso(sec: float) -> str:
    """An ISO stamp ``sec`` seconds after the fixed session base."""
    return (_BASE + timedelta(seconds=sec)).isoformat()


def _seq(n=30, block=30, seed=123, high_tone_is_left=True):
    return TrialSequence(
        TrialConfig(max_trials=n, block_size=block, max_same_run=3,
                    high_tone_is_left=high_tone_is_left),
        seed=seed)


def _log(**kw):
    return Phase3Log(_seq(**kw), animal="RAT600",
                     session_start_iso=_BASE.isoformat())


def _fill(log, index, chosen, outcome, *, start_s, end_s, rewarded=None):
    """Fill trial ``index`` end to end with explicit start/end stamps so the
    dead-time ITI (start[N] - end[N-1]) is deterministic. ``chosen`` is the
    task-events side string ("left"/"right")."""
    log.trial_started(index, _iso(start_s))
    # A choice midway between start and end (its exact time is irrelevant here).
    log.choice_scored(index, chosen, outcome, _iso((start_s + end_s) / 2.0))
    if rewarded is None:
        rewarded = outcome == CORRECT
    if rewarded:
        log.reward_fired(index, _iso((start_s + end_s) / 2.0))
    log.trial_ended(index, _iso(end_s))


class TestEmpty(unittest.TestCase):
    def test_none_snapshot_is_empty(self):
        r = build_traces(None)
        self.assertIsInstance(r, TraceResult)
        self.assertEqual(len(r), 0)
        self.assertEqual(r.blocks, [])
        self.assertIsNone(r.t0_iso)

    def test_freshly_armed_has_no_points(self):
        r = build_traces(_log(n=10).snapshot(0, armed=True))
        self.assertEqual(len(r), 0)


class TestTimeAxis(unittest.TestCase):
    def test_time_is_monotonic_and_zero_based(self):
        log = _log(n=10)
        # Trials every ~30 s (10 s dead time + 20 s in-trial).
        for i in range(4):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        r = build_traces(log.snapshot(4, armed=True))
        self.assertEqual(len(r), 4)
        self.assertEqual(r.trial, [1, 2, 3, 4])
        self.assertEqual(r.t[0], 0.0)                 # first start == t0
        self.assertEqual(r.t, sorted(r.t))            # monotonic
        self.assertAlmostEqual(r.t[1], 30.0)


class TestITI(unittest.TestCase):
    def test_dead_time_median_and_threshold_crossing(self):
        # Three short (~5 s) dead times, then one long (~60 s) gap. With a small
        # window the rolling median tracks the recent long gaps and crosses the
        # default disengaged line; cumulatively it stays below it.
        log = _log(n=10)
        _fill(log, 0, "left", CORRECT, start_s=0, end_s=20)
        _fill(log, 1, "left", CORRECT, start_s=25, end_s=45)     # ITI 5
        _fill(log, 2, "left", CORRECT, start_s=50, end_s=70)     # ITI 5
        _fill(log, 3, "left", CORRECT, start_s=130, end_s=150)   # ITI 60
        _fill(log, 4, "left", CORRECT, start_s=210, end_s=230)   # ITI 60
        r = build_traces(log.snapshot(5, armed=True), window_n=2)
        # trial 1 has no predecessor -> no ITI sample -> None.
        self.assertIsNone(r.iti_med_s[0])
        # trials 2,3 -> gaps [5,5] -> median 5.
        self.assertAlmostEqual(r.iti_med_s[2], 5.0)
        # trials 4,5 -> gaps [60,60] -> median 60, above the disengaged line.
        self.assertAlmostEqual(r.iti_med_s[4], 60.0)
        self.assertGreater(r.iti_med_s[4], DEFAULT_DISENGAGED_GAP_S)

    def test_negative_gap_dropped(self):
        # A trial that "starts" before the previous one ended (clock anomaly) is
        # not folded into the ITI - here trial 2 has no other sample, so None.
        log = _log(n=10)
        _fill(log, 0, "left", CORRECT, start_s=0, end_s=50)
        _fill(log, 1, "left", CORRECT, start_s=40, end_s=90)   # start < prev end
        r = build_traces(log.snapshot(2, armed=True))
        self.assertIsNone(r.iti_med_s[1])


class TestStrategy(unittest.TestCase):
    def test_side_bias_all_left(self):
        log = _log(n=10)
        for i in range(4):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        r = build_traces(log.snapshot(4, armed=True))
        self.assertAlmostEqual(r.side_bias_left[-1], 1.0)

    def test_switch_rate_alternating(self):
        # Strict L,R,L,R,... over consecutive out-of-block trials -> the animal
        # switched on every adjacent own-pair -> switch rate 1.0.
        log = _log(n=10)
        sides = ["left", "right", "left", "right", "left"]
        for i, s in enumerate(sides):
            _fill(log, i, s, CORRECT, start_s=30 * i, end_s=30 * i + 20)
        r = build_traces(log.snapshot(5, armed=True))
        self.assertAlmostEqual(r.switch_rate[-1], 1.0)

    def test_win_stay_all_stay_after_correct(self):
        # Always LEFT, always CORRECT -> every post-correct pair stayed -> 1.0;
        # no incorrect trials -> lose_shift undefined (None).
        log = _log(n=10)
        for i in range(5):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        r = build_traces(log.snapshot(5, armed=True))
        self.assertAlmostEqual(r.win_stay[-1], 1.0)
        self.assertIsNone(r.lose_shift[-1])


class TestGating(unittest.TestCase):
    def test_min_den_gates_early_window(self):
        # With min_den=3, the first two completed trials have too small a
        # denominator to report accuracy; the third fills it.
        log = _log(n=10)
        for i in range(3):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        r = build_traces(log.snapshot(3, armed=True), min_den=3)
        self.assertIsNone(r.accuracy[0])
        self.assertIsNone(r.accuracy[1])
        self.assertAlmostEqual(r.accuracy[2], 1.0)
        self.assertEqual(DEFAULT_MIN_DEN, 3)


class TestDirBias(unittest.TestCase):
    def test_none_without_errors(self):
        # All correct -> no directional errors -> signed bias undefined (None).
        log = _log(n=10)
        for i in range(4):
            side = "left" if log._rows[i].correct_arm == "LEFT" else "right"
            _fill(log, i, side, CORRECT, start_s=30 * i, end_s=30 * i + 20)
        r = build_traces(log.snapshot(4, armed=True))
        self.assertIsNone(r.dir_bias[-1])

    def test_left_pull_is_positive(self):
        # Force every trial to be a LEFT pull (cued/correct RIGHT, chose LEFT):
        # signed bias = +1. Drive off the plan's correct_arm, picking trials the
        # plan marks RIGHT-correct.
        log = _log(n=40)
        filled = 0
        i = 0
        while filled < 4 and i < len(log):
            if log._rows[i].correct_arm == "RIGHT":
                _fill(log, i, "left", INCORRECT,
                      start_s=30 * filled, end_s=30 * filled + 20)
                filled += 1
            i += 1
        r = build_traces(log.snapshot(len(log), armed=True))
        self.assertEqual(filled, 4)
        self.assertAlmostEqual(r.dir_bias[-1], 1.0)


class TestBlockSpans(unittest.TestCase):
    def test_intervention_run_becomes_a_span(self):
        # Mark trials 3-4 (0-based 2-3) as an INTERVENTION block by setting the
        # sched_state directly (a full closed-loop sim isn't needed to exercise
        # the span reduction). They should collapse into one BlockSpan.
        log = _log(n=10)
        for i in range(5):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        for i in (2, 3):
            log._rows[i].sched_state = "INTERVENTION"
            log._rows[i].block_kind = "side_bias"
        r = build_traces(log.snapshot(5, armed=True))
        self.assertEqual(len(r.blocks), 1)
        span = r.blocks[0]
        self.assertIsInstance(span, BlockSpan)
        self.assertEqual(span.kind, "side_bias")
        self.assertAlmostEqual(span.start_t, 60.0)    # trial 3 start (index 2)
        self.assertAlmostEqual(span.end_t, 90.0)      # trial 4 start (index 3)

    def test_intervention_excluded_from_free_strategy(self):
        # An in-block trial must not count toward the out-of-block side bias.
        log = _log(n=10)
        for i in range(5):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        # Make one trial an in-block RIGHT choice; side_bias_left should stay 1.0
        # because the in-block trial is excluded from the free denominator.
        log._rows[4].sched_state = "INTERVENTION"
        log.choice_scored(4, "right", CORRECT, _iso(125))
        r = build_traces(log.snapshot(5, armed=True))
        self.assertAlmostEqual(r.side_bias_left[-1], 1.0)


if __name__ == "__main__":
    unittest.main()
