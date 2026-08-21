"""
test_phase3_log.py - headless tests for the Phase 3 session data table.

Covers the plan built from the seed-deterministic sequence, the live event
fills, the corrected-timeline lick attribution (a reward lick lands after the
trial has advanced, so it must attach to the trial whose reward it collects),
and the CSV rendering. Pure stdlib; no hardware/Tk. See docs/decisions/0047.

Run from host/:  python -m unittest test_phase3_log
"""

from __future__ import annotations

import unittest
from dataclasses import fields

from tmaze.core.phase3.debias import DebiasBlockConfig
from tmaze.core.phase3.phase3_log import (COLUMNS, Phase3Log, Phase3RowView, Phase3StateSnapshot,
                        _Row)
from tmaze.core.phase3.trial_sequence import (
    GEN_DEBIAS_BLOCKS, HIGH, LEFT, LOW, RIGHT, TrialConfig, TrialSequence)


def _seq(n=20, block=20, seed=123):
    return TrialSequence(TrialConfig(max_trials=n, block_size=block,
                                     max_same_run=3), seed=seed)


def _log(**kw):
    return Phase3Log(_seq(**kw), animal="RAT600",
                     session_start_iso="2026-07-23T12:00:00")


class TestColumns(unittest.TestCase):
    def test_columns_align_with_row_fields(self):
        # COLUMNS is "trial" + the _Row fields, in order; guards drift between the
        # header and the rendered cells.
        self.assertEqual(list(COLUMNS), ["trial"] + [f.name for f in fields(_Row)])


class TestPlan(unittest.TestCase):
    def test_plan_matches_sequence(self):
        s = _seq()
        log = Phase3Log(s, animal="R", session_start_iso="t")
        self.assertEqual(len(log), len(s))
        for i, r in enumerate(log._rows):
            self.assertEqual(r.correct_arm,
                             "LEFT" if s.correct_arm(i) == LEFT else "RIGHT")
            self.assertEqual(r.choice_cue, s.choice_cue(i))
            self.assertEqual(r.return_cue_left, s.return_cue(LEFT, i))
            self.assertEqual(r.return_cue_right, s.return_cue(RIGHT, i))
            self.assertIn(r.choice_cue, (HIGH, LOW))
            # outcome fields start empty
            self.assertIsNone(r.chosen_arm)
            self.assertIsNone(r.rewarded)


class TestLifecycle(unittest.TestCase):
    def test_full_rewarded_trial_fills_fields(self):
        log = _log()
        log.trial_started(0, "t_start")
        log.choice_cue_started(0, "cc_on")
        log.choice_cue_stopped(0, "cc_off")
        log.choice_scored(0, "left", "CORRECT", "t_choice")
        log.reward_fired(0, "t_reward")
        log.return_cue_started(0, "right", HIGH, "rc_on")
        log.return_cue_stopped(0, "rc_off")
        log.trial_ended(0, "t_end")
        log.lick("t_lick", 12345)
        r = log._rows[0]
        self.assertEqual(r.chosen_arm, "LEFT")            # normalized to upper
        self.assertEqual(r.outcome, "CORRECT")
        self.assertEqual(r.rewarded, "1")
        self.assertEqual(r.return_arm_used, "RIGHT")
        self.assertEqual(r.return_cue_played, HIGH)
        self.assertEqual(r.trial_start_iso, "t_start")
        self.assertEqual(r.choice_time_iso, "t_choice")
        self.assertEqual(r.reward_cmd_iso, "t_reward")
        self.assertEqual(r.trial_end_iso, "t_end")
        self.assertEqual(r.lick_time_iso, "t_lick")
        self.assertEqual(r.lick_fw_ms, "12345")

    def test_unrewarded_trial_marks_zero_and_no_lick(self):
        log = _log()
        log.choice_scored(0, "right", "INCORRECT", "t_choice")
        # no reward_fired -> a stray lick must not attach anywhere
        log.lick("t_lick", 99)
        r = log._rows[0]
        self.assertEqual(r.rewarded, "0")
        self.assertIsNone(r.lick_time_iso)

    def test_topups_count_increments(self):
        # Lick top-ups at the well after the prime accumulate on the trial's
        # running count (ADR-0069); blank until the first top-up.
        log = _log()
        log.reward_fired(0, "t_reward")
        self.assertIsNone(log._rows[0].topups)
        log.topup_fired(0)
        log.topup_fired(0)
        self.assertEqual(log._rows[0].topups, "2")

    def test_reset_trial_clears_topups(self):
        log = _log()
        log.reward_fired(0, "t_reward")
        log.topup_fired(0)
        log.reset_trial(0)               # Repeat/Back clears live fields
        self.assertIsNone(log._rows[0].topups)

    def test_reward_magnitude_records_prime_and_topup_widths(self):
        # docs/decisions/0110: reward_fired/topup_fired also record the RAW pulse
        # widths (ms), appended as prime_ms + topup_ms_total (a running sum).
        log = _log()
        log.reward_fired(0, "t_reward", 500)
        self.assertEqual(log._rows[0].prime_ms, "500")
        self.assertIsNone(log._rows[0].topup_ms_total)
        log.topup_fired(0, 300)
        log.topup_fired(0, 300)
        self.assertEqual(log._rows[0].topups, "2")
        self.assertEqual(log._rows[0].topup_ms_total, "600")   # 300 + 300

    def test_reward_magnitude_columns_blank_without_widths(self):
        # A caller that omits pulse_ms (a pre-magnitude path) leaves the two new
        # cells blank while still counting the pulse/top-ups (back-compat).
        log = _log()
        log.reward_fired(0, "t_reward")
        log.topup_fired(0)
        self.assertIsNone(log._rows[0].prime_ms)
        self.assertIsNone(log._rows[0].topup_ms_total)
        self.assertEqual(log._rows[0].topups, "1")

    def test_reset_trial_clears_reward_magnitude(self):
        log = _log()
        log.reward_fired(0, "t_reward", 500)
        log.topup_fired(0, 300)
        log.reset_trial(0)
        self.assertIsNone(log._rows[0].prime_ms)
        self.assertIsNone(log._rows[0].topup_ms_total)

    def test_trial_start_keeps_first(self):
        log = _log()
        log.trial_started(0, "first")
        log.trial_started(0, "second")     # a later poke-and-retreat
        self.assertEqual(log._rows[0].trial_start_iso, "first")

    def test_skipped_return_records_none(self):
        log = _log()
        log.return_cue_started(0, "left", None, "rc_on")  # gate skipped the tone
        self.assertEqual(log._rows[0].return_cue_played, "NONE")
        self.assertEqual(log._rows[0].return_arm_used, "LEFT")


class TestLickAttribution(unittest.TestCase):
    def test_lick_attaches_across_advancement(self):
        # Reward fires on trial 0; the trial ends (index advances); the collection
        # lick arrives during trial 1 but must attach to trial 0.
        log = _log()
        log.reward_fired(0, "t_reward0")
        log.trial_ended(0, "t_end0")
        log.trial_started(1, "t_start1")
        log.lick("t_lick0", 111)
        self.assertEqual(log._rows[0].lick_time_iso, "t_lick0")
        self.assertIsNone(log._rows[1].lick_time_iso)

    def test_only_first_lick_counts(self):
        log = _log()
        log.reward_fired(0, "t_reward")
        log.lick("first", 1)
        log.lick("second", 2)             # extra licks at the well are ignored
        self.assertEqual(log._rows[0].lick_time_iso, "first")
        self.assertEqual(log._rows[0].lick_fw_ms, "1")

    def test_lick_without_fw_ms(self):
        log = _log()
        log.reward_fired(0, "t")
        log.lick("t_lick", None)
        self.assertEqual(log._rows[0].lick_fw_ms, "")


class TestRender(unittest.TestCase):
    def test_csv_structure(self):
        log = _log(n=20)
        log.choice_scored(0, "left", "CORRECT", "t_choice")
        text = log.render_csv()
        lines = text.strip("\n").split("\n")
        comments = [ln for ln in lines if ln.startswith("#")]
        self.assertTrue(any("animal=RAT600" in ln for ln in comments))
        self.assertTrue(any("high_tone_is_left" in ln for ln in comments))
        # header row + 20 data rows follow the comments
        body = [ln for ln in lines if not ln.startswith("#")]
        self.assertEqual(body[0], ",".join(COLUMNS))
        self.assertEqual(len(body), 1 + 20)
        # each data row has exactly len(COLUMNS) cells
        for row in body[1:]:
            self.assertEqual(len(row.split(",")), len(COLUMNS))
        # trial 1's scored fields rendered
        first = body[1].split(",")
        self.assertEqual(first[0], "1")
        self.assertEqual(first[COLUMNS.index("chosen_arm")], "LEFT")
        self.assertEqual(first[COLUMNS.index("outcome")], "CORRECT")


class TestExtendAndAbandon(unittest.TestCase):
    """Append-only extension + operator-abandon rows (docs/decisions/0058/0059)."""

    def test_extend_to_appends_only_new_rows(self):
        s = _seq(n=20)
        log = Phase3Log(s, animal="R", session_start_iso="t")
        log.choice_scored(0, "left", "CORRECT", "t_choice")
        s.extend(20)
        self.assertEqual(log.extend_to(s), 20)
        self.assertEqual(len(log), 40)
        # the already-scored row is untouched
        self.assertEqual(log._rows[0].outcome, "CORRECT")
        # the new rows carry the extended plan and start unscored
        for i in range(20, 40):
            self.assertEqual(log._rows[i].correct_arm,
                             "LEFT" if s.correct_arm(i) == LEFT else "RIGHT")
            self.assertEqual(log._rows[i].choice_cue, s.choice_cue(i))
            self.assertIsNone(log._rows[i].outcome)

    def test_extend_to_is_idempotent_when_no_growth(self):
        s = _seq(n=20)
        log = Phase3Log(s, animal="R", session_start_iso="t")
        self.assertEqual(log.extend_to(s), 0)   # sequence not grown -> nothing added
        self.assertEqual(len(log), 20)

    def test_trial_abandoned_sets_outcome_and_end(self):
        log = _log()
        log.trial_abandoned(2, "t_end")
        self.assertEqual(log._rows[2].outcome, "ABANDONED")
        self.assertEqual(log._rows[2].trial_end_iso, "t_end")

    def test_trial_abandoned_never_overwrites_a_score(self):
        log = _log()
        log.choice_scored(1, "left", "CORRECT", "t_choice")
        log.trial_abandoned(1, "t_end")
        self.assertEqual(log._rows[1].outcome, "CORRECT")   # scored row preserved

    def test_planned_trials_header_reflects_extension(self):
        s = _seq(n=20)
        log = Phase3Log(s, animal="R", session_start_iso="t")
        s.extend(20)
        log.extend_to(s)
        header = [ln for ln in log.render_csv().split("\n") if ln.startswith("#")]
        self.assertTrue(any("planned_trials=40" in ln for ln in header))

    def test_header_records_reward_params(self):
        # The reward pulse width + max top-ups used this run are stamped in the
        # header for reproducibility (FW-0024/ADR-0069).
        log = Phase3Log(_seq(), animal="R", session_start_iso="t",
                        reward_pulse_ms=250, max_topups=4)
        header = [ln for ln in log.render_csv().split("\n") if ln.startswith("#")]
        self.assertTrue(any("reward_pulse_ms=250" in ln for ln in header))
        self.assertTrue(any("max_topups=4" in ln for ln in header))


class TestSnapshot(unittest.TestCase):
    def test_rowview_fields_align(self):
        # Phase3RowView is "trial" + the _Row fields, in order; guards drift
        # between the live row and the published view (like TestColumns).
        self.assertEqual([f.name for f in fields(Phase3RowView)],
                         ["trial"] + [f.name for f in fields(_Row)])

    def test_snapshot_mirrors_rows_and_plan(self):
        s = _seq(n=20)
        log = Phase3Log(s, animal="R", session_start_iso="t")
        snap = log.snapshot(0, armed=True)
        self.assertIsInstance(snap, Phase3StateSnapshot)
        self.assertEqual(snap.total, len(log))
        self.assertEqual(len(snap.rows), len(log))
        self.assertEqual(snap.rows[0].trial, 1)
        self.assertEqual(snap.rows[-1].trial, len(log))
        for view, row in zip(snap.rows, log._rows):
            self.assertEqual(view.correct_arm, row.correct_arm)
            self.assertEqual(view.choice_cue, row.choice_cue)
            self.assertEqual(view.return_cue_left, row.return_cue_left)
            self.assertEqual(view.return_cue_right, row.return_cue_right)
        # run/tone metadata comes straight off the sequence config
        self.assertEqual(snap.seed, s.seed)
        self.assertEqual(snap.high_tone_is_left, s.cfg.high_tone_is_left)
        self.assertEqual(snap.high_tone_hz, s.cfg.high_tone_hz)
        self.assertEqual(snap.low_tone_hz, s.cfg.low_tone_hz)

    def test_snapshot_is_decoupled(self):
        # A snapshot copies rows by value, so mutating the log afterwards must not
        # change the already-published snapshot; a fresh snapshot does reflect it.
        log = _log()
        before = log.snapshot(0, armed=True)
        self.assertIsNone(before.rows[0].outcome)
        log.choice_scored(0, "left", "CORRECT", "t_choice")
        self.assertIsNone(before.rows[0].outcome)          # frozen freeze
        after = log.snapshot(0, armed=True)
        self.assertEqual(after.rows[0].outcome, "CORRECT")  # new snapshot sees it
        self.assertEqual(after.rows[0].chosen_arm, "LEFT")

    def test_snapshot_current_index_and_armed(self):
        log = _log()
        armed = log.snapshot(3, armed=True)
        self.assertEqual(armed.current_index, 3)
        self.assertTrue(armed.armed)
        disarmed = log.snapshot(3, armed=False)
        self.assertFalse(disarmed.armed)


def _debias_seq(n=40, seed=3):
    cfg = TrialConfig(max_trials=n, block_size=20, max_same_run=4,
                      generator=GEN_DEBIAS_BLOCKS,
                      debias=DebiasBlockConfig(window=8, min_errors=4,
                                               trigger_bias=0.3, exit_consecutive=3,
                                               max_block_len=6))
    return TrialSequence(cfg, seed=seed)


class TestDebiasLogging(unittest.TestCase):
    def test_new_columns_exist_in_order(self):
        # The six closed-loop columns, then the two reward-magnitude columns
        # (docs/decisions/0110), then the unrewarded-probe marker (docs/decisions/
        # 0115-probe), are appended at the end of the schema, in order.
        self.assertEqual(COLUMNS[-9:],
                         ("sched_state", "block_id", "block_trigger", "bias",
                          "block_kind", "alt_rate", "prime_ms", "topup_ms_total",
                          "probe"))

    def test_header_bumped_to_proto8_with_generator(self):
        header = [ln for ln in _log().render_csv().split("\n") if ln.startswith("#")]
        self.assertTrue(any("proto=8" in ln for ln in header))
        self.assertTrue(any("generator=balanced" in ln for ln in header))
        # A balanced session records no closed-loop params.
        self.assertFalse(any("debias_blocks" in ln for ln in header))

    def test_balanced_generator_leaves_debias_columns_blank(self):
        log = _log()
        log.choice_scored(0, "left", "CORRECT", "t0")
        log.set_decision(0)                       # no-op for the balanced generator
        r = log._rows[0]
        self.assertIsNone(r.sched_state)
        self.assertIsNone(r.block_id)
        self.assertIsNone(r.block_trigger)
        self.assertIsNone(r.bias)
        self.assertEqual(log.snapshot(1, armed=True).generator, "balanced")

    def test_debias_header_and_columns_are_filled(self):
        seq = _debias_seq(n=40)
        log = Phase3Log(seq, animal="RAT600", session_start_iso="t")
        header = [ln for ln in log.render_csv().split("\n") if ln.startswith("#")]
        self.assertTrue(any("generator=debias_blocks" in ln for ln in header))
        self.assertTrue(any("debias_blocks window=" in ln for ln in header))
        self.assertTrue(any("closed-loop" in ln for ln in header))   # seed caveat

        saw_block = False
        for i in range(40):
            seq.commit(i)
            log.set_decision(i)
            state = seq.trial_meta(i)["sched_state"]
            correct = seq.correct_arm(i)
            chosen = RIGHT if state == "INTERVENTION" else LEFT
            side = "left" if chosen == LEFT else "right"
            outcome = "CORRECT" if chosen == correct else "INCORRECT"
            log.choice_scored(i, side, outcome, f"t{i}")
            seq.record_outcome(i, correct, chosen)
            log.set_decision(i)
            r = log._rows[i]
            self.assertIn(r.sched_state, ("NORMAL", "INTERVENTION"))
            self.assertIsNotNone(r.bias)                # every decided trial logs b
            if r.sched_state == "INTERVENTION":
                saw_block = True
                self.assertIsNotNone(r.block_id)
                self.assertEqual(r.block_trigger, "auto")
        self.assertTrue(saw_block)                      # a corrective block ran
        snap = log.snapshot(40, armed=True)
        self.assertEqual(snap.generator, "debias_blocks")
        self.assertGreater(snap.trigger_bias, 0.0)

    def test_refresh_provisional_realigns_future_rows_after_block(self):
        # A corrective block inserts forced trials without consuming the spine, so
        # after it the display rows for not-yet-run trials must be re-aligned to the
        # spine cursor - otherwise the ribbon shows stale/opposite cues even back in
        # NORMAL mode (docs/decisions/0096).
        cfg = TrialConfig(max_trials=40, block_size=20, max_same_run=4,
                          generator=GEN_DEBIAS_BLOCKS,
                          debias=DebiasBlockConfig(window=10, min_errors=100,
                                                   exit_consecutive=3,
                                                   max_block_len=50))
        seq = TrialSequence(cfg, seed=1)
        log = Phase3Log(seq, animal="RAT600", session_start_iso="t")
        # 3 NORMAL trials, then a manual 4-trial forced-RIGHT block (no spine used).
        for i in range(3):
            seq.commit(i)
            c = seq.correct_arm(i)
            seq.record_outcome(i, c, c)
        seq.force_block(RIGHT)
        for i in range(3, 7):
            seq.commit(i)
            seq.record_outcome(i, RIGHT, LEFT)          # never comply
        seq.end_block()
        seq.commit(7)                                   # first NORMAL after block
        # Re-align the display from the current trial (7) onward.
        log.refresh_provisional(7)
        # The current trial's row is finalized (committed side + NORMAL context).
        self.assertEqual(log._rows[7].sched_state, "NORMAL")
        # A future row now reflects the spine-cursor provisional, which equals the
        # side that trial actually commits to (the fix; pre-fix it showed seq[8]).
        row8_cue = log._rows[8].choice_cue
        self.assertEqual(seq.commit(8), seq.seq[4])     # trial 8 == next spine slot
        self.assertEqual(row8_cue, seq.choice_cue(8))   # displayed == committed cue


if __name__ == "__main__":
    unittest.main()
