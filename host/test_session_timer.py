"""
test_session_timer.py - unit tests for the wall-clock session-timer core.

Headless (stdlib unittest), like the other pure-core suites. ``now`` is injected
so the wall clock is never touched. Run from host/:
    python -m unittest discover -p "test_*.py"

Covers: elapsed grows while running (stop_ts still None), a start/stop span,
a restart keeping the original start_ts while the latest stop updates stop_ts,
reset clearing everything, and the pre-start empty state. See docs/decisions/0036.
"""

import unittest

from session_timer import SessionTimer


class TestSessionTimer(unittest.TestCase):
    def test_before_start_is_empty(self):
        t = SessionTimer()
        snap = t.snapshot(now=100.0)
        self.assertIsNone(snap.start_ts)
        self.assertIsNone(snap.stop_ts)
        self.assertFalse(snap.running)
        self.assertIsNone(snap.elapsed_s)

    def test_running_elapsed_grows_stop_none(self):
        t = SessionTimer()
        t.start(now=10.0)
        snap = t.snapshot(now=25.0)
        self.assertEqual(snap.start_ts, 10.0)
        self.assertIsNone(snap.stop_ts)
        self.assertTrue(snap.running)
        self.assertAlmostEqual(snap.elapsed_s, 15.0)

    def test_start_stop_span(self):
        t = SessionTimer()
        t.start(now=10.0)
        t.stop(now=40.0)
        snap = t.snapshot(now=999.0)   # now is ignored once stopped
        self.assertEqual(snap.start_ts, 10.0)
        self.assertEqual(snap.stop_ts, 40.0)
        self.assertFalse(snap.running)
        self.assertAlmostEqual(snap.elapsed_s, 30.0)

    def test_restart_keeps_original_start_updates_stop(self):
        t = SessionTimer()
        t.start(now=10.0)
        t.stop(now=20.0)
        t.start(now=30.0)              # restart: original start_ts kept
        snap = t.snapshot(now=35.0)
        self.assertEqual(snap.start_ts, 10.0)
        self.assertTrue(snap.running)
        self.assertAlmostEqual(snap.elapsed_s, 25.0)   # 35 - 10, span includes the pause
        t.stop(now=50.0)              # latest stop updates stop_ts
        snap = t.snapshot(now=999.0)
        self.assertEqual(snap.stop_ts, 50.0)
        self.assertAlmostEqual(snap.elapsed_s, 40.0)   # 50 - 10

    def test_double_start_is_noop(self):
        t = SessionTimer()
        t.start(now=10.0)
        t.start(now=15.0)             # ignored while already running
        self.assertEqual(t.snapshot(now=15.0).start_ts, 10.0)

    def test_stop_without_start_is_noop(self):
        t = SessionTimer()
        t.stop(now=10.0)
        snap = t.snapshot(now=10.0)
        self.assertIsNone(snap.start_ts)
        self.assertIsNone(snap.stop_ts)
        self.assertFalse(snap.running)

    def test_reset_clears_everything(self):
        t = SessionTimer()
        t.start(now=10.0)
        t.stop(now=40.0)
        t.reset()
        snap = t.snapshot(now=100.0)
        self.assertIsNone(snap.start_ts)
        self.assertIsNone(snap.stop_ts)
        self.assertFalse(snap.running)
        self.assertIsNone(snap.elapsed_s)


if __name__ == "__main__":
    unittest.main()
