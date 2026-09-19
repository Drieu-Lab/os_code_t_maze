"""
test_rig_io.py - the RigClient reader -> Tk fan-out seam.

Covers the receipt-stamp callback (docs/decisions/0175, S3): a stamped consumer
receives ``(recv_wall, recv_mono, line)`` in order, the stamped callback takes
precedence over the plain one, and the plain ``on_line`` path still works for the
other clients (rig_logger, lick_tuner, ...) that never register a stamp.

These exercise the ``_dispatch_line`` hop with a fake Tk root, so no socket,
broker or real thread is involved. The batching seam (docs/decisions/0176, R3)
is covered too: many lines in one burst schedule ONE drain, replayed in order.

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


class _RecordRoot:
    """Fake Tk root that only RECORDS scheduled callbacks (does not run them),
    so a test can count how many ``after`` hops a burst produced and then run
    them by hand."""

    def __init__(self):
        self.scheduled = []  # list of (func, args)

    def after(self, _delay, func, *args):
        self.scheduled.append((func, args))
        return "after-id"

    def run_pending(self):
        """Run every currently-scheduled callback once (a later drain may
        schedule more, which stay queued for the next call)."""
        pending, self.scheduled = self.scheduled, []
        for func, args in pending:
            func(*args)


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


class TestBurstBatching(unittest.TestCase):
    def test_a_burst_schedules_exactly_one_drain(self):
        root = _RecordRoot()
        c = RigClient(root)
        got = []
        c.on_line_stamped(lambda w, m, ln: got.append(ln))
        for i in range(5):                         # one recv burst -> 5 lines
            c._dispatch_line(float(i), float(i), f"L,{i},1")
        self.assertEqual(len(root.scheduled), 1, "one drain per burst, not per line")
        self.assertEqual(got, [], "nothing delivered until the drain runs")

        root.run_pending()                         # the Tk drain fires
        self.assertEqual(got, [f"L,{i},1" for i in range(5)], "order preserved")

    def test_next_burst_after_a_drain_schedules_again(self):
        root = _RecordRoot()
        c = RigClient(root)
        got = []
        c.on_line_stamped(lambda w, m, ln: got.append(ln))

        c._dispatch_line(1.0, 1.0, "A")
        c._dispatch_line(1.0, 1.0, "B")
        root.run_pending()                         # drains A, B; clears the flag
        c._dispatch_line(2.0, 2.0, "C")            # a new burst
        self.assertEqual(len(root.scheduled), 1, "the new burst schedules a drain")
        root.run_pending()
        self.assertEqual(got, ["A", "B", "C"])

    def test_line_arriving_mid_drain_is_delivered_in_the_same_pass(self):
        root = _RecordRoot()
        c = RigClient(root)
        got = []

        def cb(_w, _m, ln):
            got.append(ln)
            if ln == "first":                      # re-enter: queue one more
                c._dispatch_line(9.0, 9.0, "reentered")

        c.on_line_stamped(cb)
        c._dispatch_line(1.0, 1.0, "first")
        root.run_pending()                         # one drain handles both
        self.assertEqual(got, ["first", "reentered"])
        self.assertEqual(root.scheduled, [], "no extra drain scheduled mid-pass")

    def test_plain_callback_is_also_batched(self):
        root = _RecordRoot()
        c = RigClient(root)
        plain = []
        c.on_line(plain.append)
        c._dispatch_line(1.0, 1.0, "X")
        c._dispatch_line(1.0, 1.0, "Y")
        self.assertEqual(len(root.scheduled), 1)
        root.run_pending()
        self.assertEqual(plain, ["X", "Y"])


if __name__ == "__main__":
    unittest.main()
