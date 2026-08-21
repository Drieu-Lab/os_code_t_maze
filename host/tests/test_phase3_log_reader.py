"""
test_phase3_log_reader.py - headless tests for the recorded-CSV snapshot reader.

Covers ``phase3_log_reader`` (docs/decisions/0117): render a live ``Phase3Log`` to
CSV, read it back, and assert that ``build_traces`` on the RECONSTRUCTED snapshot
equals ``build_traces`` on the live one (the reference line must be computed
identically to the live line), plus the derived ``SessionConfig`` (cue mapping,
tones, generator, return-cue usage, probe presence). Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_phase3_log_reader
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from tmaze.core.phase3.phase3_log import Phase3Log
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.phase3.phase3_log_reader import (
    SessionConfig, describe_config_mismatch, discover_sessions, parse_csv_text,
    session_config_from_snapshot, snapshot_from_csv)
from tmaze.core.phase3.phase3_traces import build_traces
from tmaze.core.phase3.trial_sequence import TrialConfig, TrialSequence

_BASE = datetime(2026, 7, 23, 12, 0, 0)


def _iso(sec: float) -> str:
    return (_BASE + timedelta(seconds=sec)).isoformat()


def _log(n=20, high_tone_is_left=True):
    seq = TrialSequence(
        TrialConfig(max_trials=n, block_size=30, max_same_run=3,
                    high_tone_is_left=high_tone_is_left),
        seed=123)
    return Phase3Log(seq, animal="600", session_start_iso=_BASE.isoformat(),
                     session_id="600_20260723_120000")


def _fill(log, index, chosen, outcome, *, start_s, end_s):
    log.trial_started(index, _iso(start_s))
    log.choice_scored(index, chosen, outcome, _iso((start_s + end_s) / 2.0))
    if outcome == CORRECT:
        log.reward_fired(index, _iso((start_s + end_s) / 2.0))
    log.trial_ended(index, _iso(end_s))


def _assert_traces_equal(tc: unittest.TestCase, a, b) -> None:
    for name in ("t", "trial", "iti_med_s", "accuracy", "high_acc", "low_acc",
                 "side_bias_left", "switch_rate", "win_stay", "lose_shift",
                 "cue_follow", "dir_bias", "sched_alt_rate", "blocks",
                 "probe_blocks", "t0_iso", "window_n", "iti_window_n"):
        tc.assertEqual(getattr(a, name), getattr(b, name), f"series {name} differs")


class TestRoundTrip(unittest.TestCase):
    def test_reconstructed_traces_match_live(self):
        log = _log(n=12)
        # A mix of correct/incorrect, both arms, ~30 s trials with 10 s dead time.
        pattern = ["left", "left", "right", "left", "right", "right",
                   "left", "right", "left", "right", "left", "right"]
        for i, arm in enumerate(pattern):
            outcome = CORRECT if i % 3 else INCORRECT
            _fill(log, i, arm, outcome, start_s=30 * i, end_s=30 * i + 20)
        live = log.snapshot(12, armed=True)

        text = log.render_csv()
        recon, _cfg = parse_csv_text(text)

        for w in (5, 20):
            _assert_traces_equal(
                self,
                build_traces(live, window_n=w, iti_window_n=3),
                build_traces(recon, window_n=w, iti_window_n=3))

    def test_high_tone_is_left_false_round_trips(self):
        log = _log(n=6, high_tone_is_left=False)
        for i in range(6):
            _fill(log, i, "left" if i % 2 else "right", CORRECT,
                  start_s=30 * i, end_s=30 * i + 20)
        live = log.snapshot(6, armed=True)
        recon, cfg = parse_csv_text(log.render_csv())
        self.assertFalse(cfg.high_tone_is_left)
        _assert_traces_equal(self, build_traces(live), build_traces(recon))


class TestSessionConfig(unittest.TestCase):
    def test_config_reads_header_and_derives_flags(self):
        log = _log(n=4)
        for i in range(4):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        _snap, cfg = parse_csv_text(log.render_csv())
        self.assertEqual(cfg.generator, "balanced")
        self.assertTrue(cfg.high_tone_is_left)
        # No return cue played and no probe trials in this plain balanced fill.
        self.assertFalse(cfg.uses_return_cue)
        self.assertFalse(cfg.has_probe)

    def test_derivation_matches_between_live_and_reconstructed(self):
        log = _log(n=5)
        for i in range(5):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        # A played return cue on one trial should be picked up symmetrically.
        log.return_cue_started(2, "left", "HIGH", _iso(75))
        live = log.snapshot(5, armed=True)
        _recon, cfg = parse_csv_text(log.render_csv())
        live_cfg = session_config_from_snapshot(live)
        self.assertTrue(cfg.uses_return_cue)
        self.assertEqual(cfg.uses_return_cue, live_cfg.uses_return_cue)


class TestFileRead(unittest.TestCase):
    def test_snapshot_from_csv_reads_file(self):
        import os
        import tempfile
        log = _log(n=3)
        for i in range(3):
            _fill(log, i, "left", CORRECT, start_s=30 * i, end_s=30 * i + 20)
        fd, path = tempfile.mkstemp(suffix=".csv")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(log.render_csv())
            loaded = snapshot_from_csv(path)
        finally:
            os.remove(path)
        self.assertIsNotNone(loaded)
        snap, cfg = loaded
        self.assertEqual(snap.total, 3)
        self.assertEqual(cfg.generator, "balanced")

    def test_missing_file_returns_none(self):
        self.assertIsNone(snapshot_from_csv("does_not_exist_12345.csv"))


class TestConfigMismatch(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(high_tone_is_left=True, generator="balanced",
                    high_tone_hz=10000, low_tone_hz=4000,
                    uses_return_cue=False, has_probe=False)
        base.update(kw)
        return SessionConfig(**base)

    def test_identical_configs_have_no_diffs(self):
        self.assertEqual(describe_config_mismatch(self._cfg(), self._cfg()), [])

    def test_lists_each_differing_field(self):
        current = self._cfg(uses_return_cue=False, generator="balanced")
        reference = self._cfg(uses_return_cue=True, generator="intervention_blocks",
                              high_tone_is_left=False, has_probe=True)
        diffs = describe_config_mismatch(current, reference)
        joined = "; ".join(diffs)
        self.assertIn("return cue off→on", joined)
        self.assertIn("generator balanced→intervention_blocks", joined)
        self.assertIn("cue map HIGH=L→HIGH=R", joined)
        self.assertIn("probe absent→present", joined)


class TestDiscovery(unittest.TestCase):
    def _make_bundle(self, root, session_id):
        import os
        bundle = os.path.join(root, session_id)
        os.makedirs(os.path.join(bundle, "task"))
        # is_bundle wants a session_<name>.json manifest at the bundle root.
        with open(os.path.join(bundle, f"session_{session_id}.json"),
                  "w", encoding="utf-8") as fh:
            fh.write("{}")
        with open(os.path.join(bundle, "task", f"phase3_{session_id}.csv"),
                  "w", encoding="utf-8") as fh:
            fh.write(_log(n=1).render_csv())
        return bundle

    def test_lists_animal_sessions_newest_first_and_honors_ignore(self):
        import tempfile
        import shutil
        root = tempfile.mkdtemp()
        try:
            self._make_bundle(root, "600_20260720_090000")
            self._make_bundle(root, "600_20260723_120000")   # newer
            self._make_bundle(root, "601_20260722_100000")   # other rat
            self._make_bundle(root, "600_20260721_110000")   # will be ignored
            refs = discover_sessions(
                "600", roots=[root], ignore={"600_20260721_110000"})
            self.assertEqual([r.session_id for r in refs],
                             ["600_20260723_120000", "600_20260720_090000"])
            self.assertEqual(refs[0].label, "2026-07-23 12:00")
            self.assertTrue(refs[0].csv_path.endswith(".csv"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_blank_animal_yields_nothing(self):
        self.assertEqual(discover_sessions("", roots=["."]), [])


if __name__ == "__main__":
    unittest.main()
