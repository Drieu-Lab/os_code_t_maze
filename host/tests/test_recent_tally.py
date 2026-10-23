"""Headless tests for tmaze.core.recent_tally (the DLC panel's last-few-seconds
and total counts; docs/decisions/0230).

Run from host/:  python -m unittest tests.test_recent_tally
"""

from __future__ import annotations

import unittest

from tmaze.core.recent_tally import RecentTally


class TestRecentTally(unittest.TestCase):
    def test_recent_window_and_total(self):
        t = RecentTally(window_s=10.0)
        t.add(0.0, "a")
        t.add(5.0, "b", 20.0)
        t.add(12.0, "a", 30.0)
        s = t.snapshot(12.0)
        self.assertEqual(s.recent, {"b": 1, "a": 1})   # 0.0 is older than 10 s
        self.assertEqual(s.total, {"a": 2, "b": 1})
        self.assertEqual(s.recent_median, 30.0)        # upper median of [20, 30]
        self.assertEqual(s.total_mean, 25.0)

    def test_snapshot_later_drops_old_events_from_recent_only(self):
        t = RecentTally(window_s=10.0)
        t.add(0.0, "a", 5.0)
        s = t.snapshot(20.0)
        self.assertEqual((s.recent, s.total), ({}, {"a": 1}))
        self.assertIsNone(s.recent_median)
        self.assertEqual(s.total_mean, 5.0)

    def test_reset(self):
        t = RecentTally()
        t.add(0.0, "a", 1.0)
        t.reset()
        s = t.snapshot(0.0)
        self.assertEqual((s.recent, s.total, s.total_mean), ({}, {}, None))


if __name__ == "__main__":
    unittest.main()
