"""
test_disk_space.py - headless tests for the free-disk-space display helper.

Covers disk_space.disk_status: the human label, the low/OK threshold decision
(at/below counts as low), the low-space one-decimal formatting, and the
unmeasurable ("unknown") case, per docs/decisions/0151. Pure stdlib; no Tk/OS.

Run from host/:  python -m unittest test_disk_space
"""

from __future__ import annotations

import unittest

from tmaze.core import disk_space

_GB = 1024 ** 3


class TestDiskStatus(unittest.TestCase):
    def test_plenty_of_space_is_not_low(self):
        s = disk_space.disk_status(300 * _GB, warn_gb=40)
        self.assertFalse(s.is_low)
        self.assertEqual(s.text, "Disk: 300 GB free")
        self.assertAlmostEqual(s.free_gb, 300.0, places=3)

    def test_below_threshold_is_low(self):
        s = disk_space.disk_status(30 * _GB, warn_gb=40)
        self.assertTrue(s.is_low)
        self.assertEqual(s.text, "Disk: 30 GB free")

    def test_exactly_at_threshold_counts_as_low(self):
        s = disk_space.disk_status(40 * _GB, warn_gb=40)
        self.assertTrue(s.is_low)

    def test_low_space_shows_one_decimal(self):
        # 2.4 GB free should not collapse to a blunt "2 GB".
        s = disk_space.disk_status(int(2.4 * _GB), warn_gb=40)
        self.assertEqual(s.text, "Disk: 2.4 GB free")
        self.assertTrue(s.is_low)

    def test_configurable_threshold(self):
        # Same free space, different thresholds flip the flag.
        self.assertFalse(disk_space.disk_status(60 * _GB, warn_gb=50).is_low)
        self.assertTrue(disk_space.disk_status(60 * _GB, warn_gb=80).is_low)

    def test_unknown_when_unmeasurable(self):
        s = disk_space.disk_status(None, warn_gb=40)
        self.assertEqual(s.text, "Disk: unknown")
        self.assertTrue(s.is_low)
        self.assertIsNone(s.free_gb)


if __name__ == "__main__":
    unittest.main()
