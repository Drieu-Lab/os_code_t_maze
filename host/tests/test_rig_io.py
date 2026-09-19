"""
test_rig_io.py - the RigClient reader -> Tk fan-out seam.

Covers the receipt-stamp callback (docs/decisions/0175, S3): a stamped consumer
receives ``(recv_wall, recv_mono, line)`` in order, the stamped callback takes
precedence over the plain one, and the plain ``on_line`` path still works for the
other clients (rig_logger, lick_tuner, ...) that never register a stamp.

These exercise the ``_dispatch_line`` hop with a fake Tk root, so no socket,
broker or real thread is involved.

Run from host/:  python -m unittest tests.test_rig_io
"""

from __future__ import annotations

import unittest

from tmaze.io.rig_io import RigClient


class _SyncRoot:
    """Fake Tk root whose ``after`` runs the callback immediately (records count)."""

    def __init__(self):
        self.after_calls = 0

    def after(self, _delay, func, *args):
        self.after_calls += 1
        func(*args)
        return "after-id"


class TestStampedDispatch(unittest.TestCase):
    def test_stamped_callback_receives_stamps_and_line(self):
        c = RigClient(_SyncRoot())
        got = []
        c.on_line_stamped(lambda w, m, ln: got.append((w, m, ln)))
        c._dispatch_line(111.5, 222.5, "S,0,1")
        self.assertEqual(got, [(111.5, 222.5, "S,0,1")])

    def test_stamped_takes_precedence_over_plain(self):
        c = RigClient(_SyncRoot())
        plain, stamped = [], []
        c.on_line(plain.append)
        c.on_line_stamped(lambda w, m, ln: stamped.append(ln))
        c._dispatch_line(1.0, 2.0, "L,0,1")
        self.assertEqual(stamped, ["L,0,1"])
        self.assertEqual(plain, [])

    def test_plain_callback_still_works_without_a_stamp(self):
        c = RigClient(_SyncRoot())
        plain = []
        c.on_line(plain.append)
        c._dispatch_line(1.0, 2.0, "R,0,1")
        self.assertEqual(plain, ["R,0,1"])


if __name__ == "__main__":
    unittest.main()
