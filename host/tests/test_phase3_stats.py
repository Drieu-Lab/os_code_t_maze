"""
test_phase3_stats.py - headless tests for the Phase 3 behavior-stats core.

Covers the pure ``phase3_stats.compute`` (docs/decisions/0052) over deterministic
snapshots (build a Phase3Log, fill trials via its public methods, ``.snapshot()``):
trials-completed counting, cumulative + rolling-window accuracy, per-choice-cue
accuracy split, side bias, reward rate, and outcome-based win-stay/lose-shift
(over truly-adjacent free-choice trials; docs/decisions/0093). Pure stdlib; no
hardware/Tk.

Run from host/:  python -m unittest test_phase3_stats
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from tmaze.core.phase3.debias import DebiasBlockConfig
from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.phase3_stats import (
    DEFAULT_WINDOW, Phase3Stats, _win_stay_lose_shift, compute)
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.phase3.trial_sequence import (
    GEN_DEBIAS_BLOCKS, HIGH, LEFT, LOW, RIGHT, TrialConfig, TrialSequence)


def _seq(n=20, block=20, seed=123, high_tone_is_left=True):
    return TrialSequence(
        TrialConfig(max_trials=n, block_size=block, max_same_run=3,
                    high_tone_is_left=high_tone_is_left),
        seed=seed)


def _log(**kw):
    return Phase3Log(_seq(**kw), animal="RAT600",
                     session_start_iso="2026-07-23T12:00:00")


def _resolve(log, index, chosen, outcome, *, rewarded=None,
             cue_on=None, choice=None, prime_ms=None):
    """Fill trial ``index`` the way the controller does: optionally a cue-onset
    stamp, then the scored choice, then (if rewarded) a reward fire. ``rewarded``
    defaults to True on CORRECT (mirrors the correct-only reward policy);
    ``prime_ms`` records the prime pulse width for the reward-magnitude tally."""
    if cue_on is not None:
        log.choice_cue_started(index, cue_on)
    log.choice_scored(index, chosen, outcome, choice or f"t_choice{index}")
    if rewarded is None:
        rewarded = outcome == CORRECT
    if rewarded:
        log.reward_fired(index, f"t_reward{index}", prime_ms)


def _side_for(log, index, correct):
    """The task_events side string ("left"/"right") whose choice is CORRECT (or,
    with correct=False, INCORRECT) on trial ``index`` given the plan."""
    correct_left = log._rows[index].correct_arm == "LEFT"
    want_left = correct_left if correct else not correct_left
    return "left" if want_left else "right"


class TestAbandonedExcluded(unittest.TestCase):
    def test_abandoned_trials_do_not_count_as_completed(self):
        # A skipped (ABANDONED) trial must not dilute accuracy or reward rate:
        # only genuinely scored CORRECT/INCORRECT trials count as completed.
        log = _log(n=20)
        _resolve(log, 0, _side_for(log, 0, True), CORRECT)
        log.trial_abandoned(1, "t_end1")            # operator skip
        _resolve(log, 2, _side_for(log, 2, True), CORRECT)
        st = compute(log.snapshot(3, armed=True))
        self.assertEqual(st.completed, 2)           # trial 1 excluded
        self.assertEqual(st.correct, 2)
        self.assertEqual(st.incorrect, 0)
        self.assertEqual(st.accuracy_cum, 1.0)


class TestEmpty(unittest.TestCase):
    def test_none_snapshot_is_all_empty(self):
        s = compute(None)
        self.assertIsInstance(s, Phase3Stats)
        self.assertEqual(s.total, 0)
        self.assertEqual(s.completed, 0)
        self.assertIsNone(s.accuracy_cum)
        self.assertIsNone(s.accuracy_win)
        self.assertIsNone(s.lr_bias)
        self.assertIsNone(s.reward_rate)
        self.assertIsNone(s.win_stay)
        self.assertIsNone(s.lose_shift)
        self.assertEqual(s.topups, 0)
        self.assertEqual(s.window_n, DEFAULT_WINDOW)

    def test_freshly_armed_has_total_but_no_completions(self):
        s = compute(_log(n=20).snapshot(0, armed=True))
        self.assertEqual(s.total, 20)
        self.assertEqual(s.completed, 0)
        self.assertIsNone(s.accuracy_cum)


class TestCompletedAndAccuracy(unittest.TestCase):
    def test_counts_only_scored_trials(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)
        _resolve(log, 1, "right", INCORRECT)
        s = compute(log.snapshot(2, armed=True))
        self.assertEqual(s.completed, 2)
        self.assertEqual(s.correct, 1)
        self.assertEqual(s.incorrect, 1)
        self.assertEqual(s.current_index, 2)

    def test_cumulative_accuracy(self):
        log = _log(n=20)
        # 3 correct, 1 incorrect -> 75%.
        _resolve(log, 0, "left", CORRECT)
        _resolve(log, 1, "left", CORRECT)
        _resolve(log, 2, "left", CORRECT)
        _resolve(log, 3, "right", INCORRECT)
        s = compute(log.snapshot(4, armed=True))
        self.assertAlmostEqual(s.accuracy_cum, 0.75)

    def test_rolling_window_uses_last_n(self):
        # window=2: only the last two completed trials count. Make the last two
        # correct while the earlier ones are incorrect, so cum != win.
        log = _log(n=20)
        _resolve(log, 0, "right", INCORRECT)
        _resolve(log, 1, "right", INCORRECT)
        _resolve(log, 2, "left", CORRECT)
        _resolve(log, 3, "left", CORRECT)
        s = compute(log.snapshot(4, armed=True), window_n=2)
        self.assertAlmostEqual(s.accuracy_cum, 0.5)   # 2 of 4
        self.assertAlmostEqual(s.accuracy_win, 1.0)   # last 2 both correct
        self.assertEqual(s.window_n, 2)

    def test_window_larger_than_completed_is_cumulative(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)
        _resolve(log, 1, "right", INCORRECT)
        s = compute(log.snapshot(2, armed=True), window_n=50)
        self.assertEqual(s.accuracy_win, s.accuracy_cum)


class TestPerCueAccuracy(unittest.TestCase):
    def test_split_matches_manual_count_over_plan(self):
        # Resolve the first 12 trials: make every trial CORRECT except force the
        # 3rd HIGH-cue and 2nd LOW-cue trial INCORRECT, then check the per-cue
        # accuracy against a hand count driven off the plan's choice_cue.
        log = _log(n=20)
        high_seen = low_seen = 0
        expect = {HIGH: [0, 0], LOW: [0, 0]}   # cue -> [correct, total]
        for i in range(12):
            cue = log._rows[i].choice_cue
            make_incorrect = ((cue == HIGH and high_seen == 2)
                              or (cue == LOW and low_seen == 1))
            outcome = INCORRECT if make_incorrect else CORRECT
            _resolve(log, i, _side_for(log, i, outcome == CORRECT), outcome)
            expect[cue][1] += 1
            expect[cue][0] += int(outcome == CORRECT)
            high_seen += int(cue == HIGH)
            low_seen += int(cue == LOW)
        s = compute(log.snapshot(12, armed=True))
        self.assertEqual(s.high_completed, expect[HIGH][1])
        self.assertEqual(s.low_completed, expect[LOW][1])
        self.assertAlmostEqual(s.high_accuracy, expect[HIGH][0] / expect[HIGH][1])
        self.assertAlmostEqual(s.low_accuracy, expect[LOW][0] / expect[LOW][1])

    def test_cue_accuracy_none_until_that_cue_seen(self):
        # Resolve only trials whose cue is HIGH, so LOW accuracy stays None.
        log = _log(n=20)
        done_high = 0
        for i in range(len(log)):
            if log._rows[i].choice_cue == HIGH:
                _resolve(log, i, _side_for(log, i, True), CORRECT)
                done_high += 1
            if done_high == 3:
                break
        s = compute(log.snapshot(0, armed=True))
        self.assertEqual(s.high_completed, 3)
        self.assertEqual(s.high_accuracy, 1.0)
        self.assertEqual(s.low_completed, 0)
        self.assertIsNone(s.low_accuracy)


class TestSideBias(unittest.TestCase):
    def test_bias_fraction_of_chosen_arm(self):
        log = _log(n=20)
        # 3 LEFT choices, 1 RIGHT choice (correctness irrelevant to bias).
        _resolve(log, 0, "left", CORRECT)
        _resolve(log, 1, "left", INCORRECT, rewarded=False)
        _resolve(log, 2, "left", CORRECT)
        _resolve(log, 3, "right", CORRECT)
        s = compute(log.snapshot(4, armed=True))
        self.assertEqual(s.left_choices, 3)
        self.assertEqual(s.right_choices, 1)
        self.assertAlmostEqual(s.lr_bias[0], 0.75)
        self.assertAlmostEqual(s.lr_bias[1], 0.25)

    def test_bias_none_before_any_choice(self):
        self.assertIsNone(compute(_log(n=20).snapshot(0, armed=True)).lr_bias)


class TestRewardRate(unittest.TestCase):
    def test_reward_rate_counts_rewarded_flag(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)                       # rewarded
        _resolve(log, 1, "right", INCORRECT, rewarded=False)    # not
        _resolve(log, 2, "left", INCORRECT, rewarded=True)      # shaping reward
        s = compute(log.snapshot(3, armed=True))
        self.assertEqual(s.rewarded, 2)
        self.assertAlmostEqual(s.reward_rate, 2 / 3)


class TestTopups(unittest.TestCase):
    def test_topups_sum_over_completed_trials(self):
        # The prime is the ``rewarded`` 1; top-ups are extra lick-triggered pulses
        # counted per row (docs/decisions/0069). compute() totals them so the water
        # estimate has the full pulse count (docs/decisions/0073).
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)          # rewarded, no top-ups
        log.topup_fired(0)
        log.topup_fired(0)                          # 2 top-ups on trial 0
        _resolve(log, 1, "left", CORRECT)          # rewarded
        log.topup_fired(1)                          # 1 top-up on trial 1
        _resolve(log, 2, "right", INCORRECT, rewarded=False)
        s = compute(log.snapshot(3, armed=True))
        self.assertEqual(s.rewarded, 2)
        self.assertEqual(s.topups, 3)

    def test_topups_zero_without_any(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)
        s = compute(log.snapshot(1, armed=True))
        self.assertEqual(s.topups, 0)


class TestWinStayLoseShift(unittest.TestCase):
    def test_win_stay(self):
        # Trials: L-correct, then L (stay), then R (shift) -> after 2 wins, 1 stay
        # and 1 shift => win_stay = 0.5. No incorrect trials => lose_shift None.
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)
        _resolve(log, 1, "left", CORRECT)
        _resolve(log, 2, "right", CORRECT)
        s = compute(log.snapshot(3, armed=True))
        self.assertAlmostEqual(s.win_stay, 0.5)
        self.assertIsNone(s.lose_shift)

    def test_lose_shift(self):
        # L-incorrect then R (shift), R-incorrect then R (stay) -> 2 losses, 1
        # shift => lose_shift 0.5. The single win (last trial) has no successor.
        log = _log(n=20)
        _resolve(log, 0, "left", INCORRECT, rewarded=False)
        _resolve(log, 1, "right", INCORRECT, rewarded=False)
        _resolve(log, 2, "right", CORRECT)
        s = compute(log.snapshot(3, armed=True))
        self.assertAlmostEqual(s.lose_shift, 0.5)
        # trial 0 win? no; wins are trial 2 which has no successor -> None
        self.assertIsNone(s.win_stay)

    def test_none_with_fewer_than_two_completed(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)
        s = compute(log.snapshot(1, armed=True))
        self.assertIsNone(s.win_stay)
        self.assertIsNone(s.lose_shift)


def _wsls_row(trial, arm, outcome, state="NORMAL"):
    """A minimal duck-typed row carrying only the fields _win_stay_lose_shift
    reads: the 1-based trial index, chosen arm, outcome, and scheduler state."""
    return SimpleNamespace(trial=trial, chosen_arm=arm, outcome=outcome,
                           sched_state=state)


class TestWinStayAdjacency(unittest.TestCase):
    """F3 (docs/decisions/0093): pairs must be genuine trial-to-next-trial
    transitions of the rat's OWN choices - a forced in-block trial or any gap in
    the trial numbering breaks the pair instead of being skipped over."""

    def test_plain_adjacent_pair_counts(self):
        rows = [_wsls_row(1, "LEFT", CORRECT), _wsls_row(2, "LEFT", CORRECT)]
        win_stay, lose_shift = _win_stay_lose_shift(rows)
        self.assertEqual(win_stay, 1.0)                 # stayed after a win
        self.assertIsNone(lose_shift)

    def test_in_block_trial_breaks_the_pair(self):
        # A forced INTERVENTION trial sits between two out-of-block trials: neither
        # (oob, forced) nor (forced, oob) pair may count, so there is no eligible
        # win transition and win_stay is None (not a spurious 100%).
        rows = [
            _wsls_row(1, "LEFT", CORRECT),
            _wsls_row(2, "RIGHT", CORRECT, state="INTERVENTION"),
            _wsls_row(3, "LEFT", CORRECT),
        ]
        win_stay, lose_shift = _win_stay_lose_shift(rows)
        self.assertIsNone(win_stay)
        self.assertIsNone(lose_shift)

    def test_trial_number_gap_breaks_the_pair(self):
        # An intervening trial that isn't in this list (e.g. abandoned) shows up as
        # a gap in the trial numbers, which also breaks adjacency.
        rows = [_wsls_row(1, "LEFT", CORRECT), _wsls_row(3, "LEFT", CORRECT)]
        self.assertEqual(_win_stay_lose_shift(rows), (None, None))

    def test_only_out_of_block_adjacent_pairs_count(self):
        # Two clean oob transitions around a forced block: L-correct -> L (stay),
        # then later R-incorrect -> L (shift). The forced trials in between never
        # pair. => win_stay 1.0 (one stay), lose_shift 1.0 (one shift).
        rows = [
            _wsls_row(1, "LEFT", CORRECT),
            _wsls_row(2, "LEFT", CORRECT),                       # stay after win
            _wsls_row(3, "RIGHT", CORRECT, state="INTERVENTION"),
            _wsls_row(4, "RIGHT", CORRECT, state="INTERVENTION"),
            _wsls_row(5, "RIGHT", INCORRECT),
            _wsls_row(6, "LEFT", CORRECT),                       # shift after loss
        ]
        win_stay, lose_shift = _win_stay_lose_shift(rows)
        self.assertAlmostEqual(win_stay, 1.0)
        self.assertAlmostEqual(lose_shift, 1.0)


class TestCueDwell(unittest.TestCase):
    """Cue-zone dwell (docs/decisions/0108): the RELATIVE per-trial interval
    ``choice_time_iso - choice_cue_on_iso`` reduced to mean/median/n over the
    completed trials that carry both stamps."""

    def test_mean_median_and_n_over_stamped_trials(self):
        log = _log(n=20)
        # Three trials with cue-on -> choice dwells of 1.0, 2.0, 6.0 s.
        _resolve(log, 0, "left", CORRECT,
                 cue_on="2026-07-23T12:00:00", choice="2026-07-23T12:00:01")
        _resolve(log, 1, "left", CORRECT,
                 cue_on="2026-07-23T12:00:10", choice="2026-07-23T12:00:12")
        _resolve(log, 2, "right", CORRECT,
                 cue_on="2026-07-23T12:00:20", choice="2026-07-23T12:00:26")
        s = compute(log.snapshot(3, armed=True))
        self.assertEqual(s.cue_dwell_n, 3)
        self.assertAlmostEqual(s.cue_dwell_mean_s, 3.0)   # (1+2+6)/3
        self.assertAlmostEqual(s.cue_dwell_median_s, 2.0)  # middle of [1,2,6]

    def test_median_even_count_averages_two_middle(self):
        log = _log(n=20)
        for i, (a, b) in enumerate([("12:00:00", "12:00:01"),   # 1 s
                                    ("12:01:00", "12:01:03"),   # 3 s
                                    ("12:02:00", "12:02:05"),   # 5 s
                                    ("12:03:00", "12:03:11")]):  # 11 s
            _resolve(log, i, "left", CORRECT,
                     cue_on=f"2026-07-23T{a}", choice=f"2026-07-23T{b}")
        s = compute(log.snapshot(4, armed=True))
        self.assertEqual(s.cue_dwell_n, 4)
        self.assertAlmostEqual(s.cue_dwell_median_s, 4.0)  # (3+5)/2

    def test_trials_without_both_stamps_are_excluded(self):
        # A trial scored without a cue-on stamp (no cue-on iso) contributes no
        # dwell; only the fully-stamped trial counts.
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT,
                 cue_on="2026-07-23T12:00:00", choice="2026-07-23T12:00:02")
        _resolve(log, 1, "left", CORRECT)   # default non-ISO choice, no cue_on
        s = compute(log.snapshot(2, armed=True))
        self.assertEqual(s.cue_dwell_n, 1)
        self.assertAlmostEqual(s.cue_dwell_mean_s, 2.0)

    def test_negative_interval_is_dropped(self):
        # A choice stamp before its own cue-on is a clock anomaly; it must not fold
        # into the mean/median (the one good trial is all that remains).
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT,
                 cue_on="2026-07-23T12:00:05", choice="2026-07-23T12:00:00")  # -5 s
        _resolve(log, 1, "left", CORRECT,
                 cue_on="2026-07-23T12:01:00", choice="2026-07-23T12:01:04")  # 4 s
        s = compute(log.snapshot(2, armed=True))
        self.assertEqual(s.cue_dwell_n, 1)
        self.assertAlmostEqual(s.cue_dwell_mean_s, 4.0)

    def test_empty_when_no_stamped_trials(self):
        s = compute(_log(n=20).snapshot(0, armed=True))
        self.assertIsNone(s.cue_dwell_mean_s)
        self.assertIsNone(s.cue_dwell_median_s)
        self.assertEqual(s.cue_dwell_n, 0)
        # A None snapshot is likewise empty.
        self.assertEqual(compute(None).cue_dwell_n, 0)
        self.assertIsNone(compute(None).cue_dwell_mean_s)


class TestRewardPulsesByMs(unittest.TestCase):
    """Per-width reward pulse tally (docs/decisions/0110): primes + reconstructed
    top-up widths over completed trials, keyed by width in ms."""

    def test_prime_tally_over_rewarded_trials(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT, prime_ms=500)
        _resolve(log, 1, "left", CORRECT, prime_ms=500)
        _resolve(log, 2, "right", INCORRECT, rewarded=False)   # no pulse
        s = compute(log.snapshot(3, armed=True))
        self.assertEqual(s.reward_pulses_by_ms, {"500": 2})

    def test_topups_reconstruct_per_pulse_width(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT, prime_ms=500)
        log.topup_fired(0, 300)
        log.topup_fired(0, 300)          # 2 top-ups of 300 ms -> total 600
        s = compute(log.snapshot(1, armed=True))
        # One 500 ms prime + two 300 ms top-ups (600/2 = 300 per pulse).
        self.assertEqual(s.reward_pulses_by_ms, {"500": 1, "300": 2})

    def test_mid_run_width_change_is_representable(self):
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT, prime_ms=500)
        _resolve(log, 1, "left", CORRECT, prime_ms=400)   # operator changed width
        s = compute(log.snapshot(2, armed=True))
        self.assertEqual(s.reward_pulses_by_ms, {"500": 1, "400": 1})

    def test_empty_without_recorded_widths(self):
        # A pre-magnitude session (reward_fired without a width) tallies nothing, so
        # downstream water math falls back to the single-width path.
        log = _log(n=20)
        _resolve(log, 0, "left", CORRECT)      # no prime_ms
        s = compute(log.snapshot(1, armed=True))
        self.assertEqual(s.reward_pulses_by_ms, {})
        self.assertEqual(compute(None).reward_pulses_by_ms, {})


class TestDisarmedSnapshotStillReads(unittest.TestCase):
    def test_completed_session_snapshot(self):
        # A disarmed (gate closed) snapshot still carries the table; compute reads
        # it fine (the view decides whether to SHOW it based on armed).
        log = _log(n=4, block=2)
        for i in range(4):
            _resolve(log, i, _side_for(log, i, True), CORRECT)
        s = compute(log.snapshot(4, armed=False))
        self.assertEqual(s.completed, 4)
        self.assertEqual(s.accuracy_cum, 1.0)


# --------------------------------------------------------------------------- #
# Closed-loop debias partitioning: learning measures over out-of-block trials, #
# corrective (in-block) trials summarized separately (see phase3_stats + ADR).  #
# --------------------------------------------------------------------------- #
def _debias_log(n=60, seed=3):
    cfg = TrialConfig(max_trials=n, block_size=20, max_same_run=4,
                      generator=GEN_DEBIAS_BLOCKS,
                      debias=DebiasBlockConfig(window=8, min_errors=4,
                                               trigger_bias=0.3, exit_consecutive=3,
                                               max_block_len=6))
    seq = TrialSequence(cfg, seed=seed)
    log = Phase3Log(seq, animal="RAT600",
                    session_start_iso="2026-07-23T12:00:00")
    return log, seq


def _drive_debias(log, seq, n):
    """Fill n trials like the controller: pull LEFT while NORMAL (to build a left
    bias that triggers blocks), comply (choose the forced RIGHT) while in a block
    (so blocks succeed via the consecutive-correct exit)."""
    for i in range(n):
        seq.commit(i)
        log.set_decision(i)
        state = seq.trial_meta(i)["sched_state"]
        correct = seq.correct_arm(i)
        chosen = RIGHT if state == "INTERVENTION" else LEFT
        side = "left" if chosen == LEFT else "right"
        outcome = CORRECT if chosen == correct else INCORRECT
        log.choice_scored(i, side, outcome, f"t{i}")
        if outcome == CORRECT:
            log.reward_fired(i, f"r{i}")
        seq.record_outcome(i, correct, chosen)
        log.set_decision(i)


class TestDebiasPartition(unittest.TestCase):
    def _stats(self, n=40):
        log, seq = _debias_log(n=60)
        _drive_debias(log, seq, n)
        return compute(log.snapshot(n, armed=True)), log, seq

    def test_reports_debias_generator_and_blocks_occur(self):
        st, _log, _seq = self._stats()
        self.assertEqual(st.generator, GEN_DEBIAS_BLOCKS)
        self.assertGreater(st.inblock_completed, 0)     # a block ran
        self.assertGreater(st.oob_completed, 0)         # and normal trials too
        self.assertEqual(st.oob_completed + st.inblock_completed, st.completed)
        self.assertGreaterEqual(st.n_blocks, 1)

    def test_learning_measures_use_out_of_block_only(self):
        st, log, _seq = self._stats()
        rows = log.snapshot(40, armed=True).rows
        oob = [r for r in rows
               if r.outcome in (CORRECT, INCORRECT)
               and r.sched_state != "INTERVENTION"]
        correct_oob = sum(1 for r in oob if r.outcome == CORRECT)
        self.assertEqual(st.oob_completed, len(oob))
        self.assertAlmostEqual(st.accuracy_cum, correct_oob / len(oob))

    def test_oob_count_fields_match_the_displayed_percentages(self):
        # F1 (docs/decisions/0092): the [n/N] bracket the panel/summary show next
        # to accuracy / reward rate must be the OUT-OF-BLOCK numerator+denominator
        # so the count agrees with the percentage. In this drive the in-block
        # trials are all correct+rewarded, so the all-completed counts are strictly
        # larger than the out-of-block ones - the exact mismatch F1 fixes.
        st, _log, _seq = self._stats()
        self.assertEqual(st.oob_correct, round(st.accuracy_cum * st.oob_completed))
        self.assertEqual(st.oob_rewarded, round(st.reward_rate * st.oob_completed))
        self.assertGreater(st.correct, st.oob_correct)
        self.assertGreater(st.rewarded, st.oob_rewarded)

    def test_directional_bias_is_left_and_blocks_succeed(self):
        st, _log, _seq = self._stats()
        # The out-of-block animal always pulled LEFT, so directional bias b > 0.
        self.assertIsNotNone(st.dir_bias)
        self.assertGreater(st.dir_bias, 0.0)
        # In-block it always chose the forced (RIGHT == correct) side, so those
        # trials are all correct and every completed block exits on success.
        self.assertEqual(st.inblock_accuracy, 1.0)
        self.assertGreaterEqual(st.blocks_success, 1)
        self.assertEqual(st.blocks_capped, 0)

    def test_balanced_generator_has_neutral_debias_fields(self):
        # The default generator: no partitioning, no blocks, dir_bias still defined.
        log = _log(n=10, block=10)
        for i in range(6):
            _resolve(log, i, _side_for(log, i, True), CORRECT)
        st = compute(log.snapshot(6, armed=True))
        self.assertEqual(st.generator, "balanced")
        self.assertEqual(st.inblock_completed, 0)
        self.assertEqual(st.oob_completed, st.completed)
        self.assertEqual(st.n_blocks, 0)
        self.assertEqual(st.dir_bias, 0.0)              # all correct -> no pulls
        # With no in-block trials the oob counts equal the all-completed ones, so
        # the F1 brackets read identically to the pre-debias behavior.
        self.assertEqual(st.oob_correct, st.correct)
        self.assertEqual(st.oob_rewarded, st.rewarded)

    def test_gate_progress_fields_surface_for_debias(self):
        # The new gate-progress readout: the side-bias detector is enabled, its
        # min_errors threshold is carried, and the live window error count is a
        # non-negative int <= that threshold+window (for "errors N/min" display).
        st, _log, _seq = self._stats()
        self.assertTrue(st.debias_enabled)
        self.assertEqual(st.debias_min_errors, 4)       # from _debias_log's config
        self.assertGreaterEqual(st.debias_window_errors, 0)
        # No alternation child under the debias_blocks generator.
        self.assertFalse(st.alt_enabled)
        self.assertEqual(st.alt_min_samples, 0)

    def test_gate_progress_fields_neutral_for_balanced(self):
        log = _log(n=10, block=10)
        for i in range(4):
            _resolve(log, i, _side_for(log, i, True), CORRECT)
        st = compute(log.snapshot(4, armed=True))
        self.assertFalse(st.debias_enabled)
        self.assertFalse(st.alt_enabled)
        self.assertEqual(st.debias_window_errors, 0)
        self.assertEqual(st.alt_window_samples, 0)


if __name__ == "__main__":
    unittest.main()
