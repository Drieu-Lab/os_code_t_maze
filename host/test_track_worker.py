"""
test_track_worker.py - headless unit tests for the off-grab-thread handoff.

No Tk, no OpenCV, no camera: exercises the two stdlib-only primitives that let
tracking run on its own worker (see docs/decisions/0040). Run from host/:

    python -m unittest test_track_worker

Covers: LatestFrameSlot newest-wins / drop-oldest + skip reporting, take timeout,
stop() unblocking a waiting take(), and clear(); TrackPublisher snapshot,
subscribe callback fan-out, callback-exception isolation, and clear().
"""

from __future__ import annotations

import threading
import time
import unittest

from track_worker import LatestFrameSlot, TrackPublisher


class TestLatestFrameSlot(unittest.TestCase):
    def test_put_then_take_returns_item(self):
        slot = LatestFrameSlot()
        self.assertFalse(slot.put("f0", 10, 111, 1.0))
        item = slot.take(timeout=0.1)
        self.assertEqual(item, ("f0", 10, 111, 1.0))

    def test_newest_wins_and_reports_skip(self):
        slot = LatestFrameSlot()
        self.assertFalse(slot.put("f0", 10, 111, 1.0))   # nothing pending
        self.assertTrue(slot.put("f1", 11, 222, 2.0))    # overwrote unconsumed f0
        item = slot.take(timeout=0.1)
        self.assertEqual(item[0], "f1")                  # got the freshest
        # Slot is now empty.
        self.assertIsNone(slot.take(timeout=0.05))

    def test_take_timeout_returns_none(self):
        slot = LatestFrameSlot()
        t0 = time.perf_counter()
        self.assertIsNone(slot.take(timeout=0.05))
        self.assertGreaterEqual(time.perf_counter() - t0, 0.04)

    def test_stop_unblocks_waiting_take(self):
        slot = LatestFrameSlot()
        out = []

        def worker():
            out.append(slot.take())   # no timeout: blocks until stop()

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.05)              # let it block
        slot.stop()
        t.join(timeout=1.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(out, [None])

    def test_stop_makes_subsequent_take_return_none(self):
        slot = LatestFrameSlot()
        slot.put("f0", 1, 1, 1.0)
        slot.stop()
        self.assertIsNone(slot.take(timeout=0.1))

    def test_clear_drops_pending(self):
        slot = LatestFrameSlot()
        slot.put("f0", 1, 1, 1.0)
        slot.clear()
        self.assertIsNone(slot.take(timeout=0.05))


class TestTrackPublisher(unittest.TestCase):
    def test_snapshot_empty_then_published(self):
        pub = TrackPublisher()
        self.assertIsNone(pub.snapshot())
        pub.publish("result", 42, 3.0)
        self.assertEqual(pub.snapshot(), ("result", 42, 3.0))

    def test_subscribe_receives_published_result(self):
        pub = TrackPublisher()
        seen = []
        pub.subscribe(lambda r, b, t: seen.append((r, b, t)))
        pub.publish("r0", 7, 1.5)
        pub.publish("r1", 8, 2.5)
        self.assertEqual(seen, [("r0", 7, 1.5), ("r1", 8, 2.5)])

    def test_callback_exception_is_isolated(self):
        pub = TrackPublisher()
        seen = []

        def bad(r, b, t):
            raise RuntimeError("boom")

        pub.subscribe(bad)
        pub.subscribe(lambda r, b, t: seen.append(r))
        pub.publish("r0", 1, 1.0)          # must not raise
        self.assertEqual(seen, ["r0"])     # good subscriber still ran
        self.assertEqual(pub.snapshot(), ("r0", 1, 1.0))

    def test_clear_resets_snapshot(self):
        pub = TrackPublisher()
        pub.publish("r0", 1, 1.0)
        pub.clear()
        self.assertIsNone(pub.snapshot())


if __name__ == "__main__":
    unittest.main()
