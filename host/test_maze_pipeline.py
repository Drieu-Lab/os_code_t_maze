"""
test_maze_pipeline.py - headless unit tests for the shared maze-state pipeline.

No Tk, no OpenCV, no camera: exercises maze_pipeline.MazeStatePipeline over
duck-typed track results (SimpleNamespace, exactly like maze_state_validate.py),
verifying it drives ONE estimator, fans a full RatState stream out to every
subscriber without dropping one-frame edges, and stays thread-safe under a
concurrent on_track vs set_zone_map / set_params / reset. See docs/decisions/0041.
Also exercises the FAST view + push seam (subscribe_fast / set_fast_params) that
the Rules actuation thread consumes for low-latency reward (ADR-0042).

Run from host/:
    python -m unittest test_maze_pipeline
"""

from __future__ import annotations

import threading
import time
import types
import unittest

from maze_pipeline import DEFAULT_QUEUE_MAX, MazeStatePipeline
from maze_state import HUB, LEFT_CHOICE, MazeStateParams
from test_maze_state import make_zone_map  # reuse the estimator test fixture

W, H = 1000, 1000


def _tr(cx, cy, state="TRACKING", conf=0.9):
    """A duck-typed TrackResult at pixel (cx, cy)."""
    return types.SimpleNamespace(state=state, confidence=conf, frame_size=(W, H),
                                 centroid=(cx, cy), smoothed=(cx, cy))


class TestSnapshotAndFanout(unittest.TestCase):
    def test_snapshot_empty_then_published(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        self.assertIsNone(pipe.snapshot())
        pipe.on_track(_tr(500, 900), block_id=1, mono_ts=1.0)
        snap = pipe.snapshot()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.block_id, 1)
        self.assertEqual(snap.mono_ts, 1.0)
        self.assertEqual(snap.state.zone, HUB)

    def test_state_now_is_mono_ts(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        pipe.on_track(_tr(500, 900), block_id=1, mono_ts=42.5)
        self.assertEqual(pipe.snapshot().state.now, 42.5)

    def test_multiple_subscribers_each_get_every_bundle(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        a = pipe.register()
        b = pipe.register()
        for i in range(5):
            pipe.on_track(_tr(500, 900), block_id=i, mono_ts=float(i))
        da = a.drain()
        db = b.drain()
        self.assertEqual([x.block_id for x in da], [0, 1, 2, 3, 4])
        self.assertEqual([x.block_id for x in db], [0, 1, 2, 3, 4])
        # Draining empties the queue.
        self.assertEqual(a.drain(), [])

    def test_late_subscriber_only_sees_subsequent(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        pipe.on_track(_tr(500, 900), block_id=0, mono_ts=0.0)
        sub = pipe.register()
        pipe.on_track(_tr(500, 900), block_id=1, mono_ts=1.0)
        self.assertEqual([x.block_id for x in sub.drain()], [1])


class TestNoMissedEdges(unittest.TestCase):
    def test_drain_captures_one_frame_choice_edge(self):
        """A choice-entry edge lives on ONE frame; draining must not lose it even
        though many non-edge frames surround it (the reason snapshot() is unsafe
        for edge consumers)."""
        params = MazeStateParams(min_dwell_ticks=3)
        pipe = MazeStatePipeline(make_zone_map(), params)
        sub = pipe.register()
        t = 0.0
        # Commit HUB, then the stem, then dwell into the LEFT choice arm so a
        # single `entered == LEFT_CHOICE` edge is produced, then more HUB frames.
        script = ([(500, 900)] * 4      # HUB
                  + [(500, 650)] * 4    # CENTRAL_STEM
                  + [(500, 420)] * 4    # JUNCTION
                  + [(200, 350)] * 4    # LEFT_CHOICE  <- the choice edge
                  + [(200, 350)] * 6)   # stay (no new edge)
        for (cx, cy) in script:
            pipe.on_track(_tr(cx, cy), block_id=int(t), mono_ts=t)
            t += 1.0
        bundles = sub.drain()
        edges = [b.state.entered for b in bundles if b.state.entered is not None]
        self.assertIn(LEFT_CHOICE, edges)
        # And the LATEST snapshot does NOT carry the edge (it's a later, no-edge
        # frame) - proving why edge consumers must drain, not sample.
        self.assertIsNone(pipe.snapshot().state.entered)


class TestBoundedQueue(unittest.TestCase):
    def test_drop_oldest_and_count(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams(), queue_max=4)
        sub = pipe.register()
        for i in range(7):   # 3 more than capacity
            pipe.on_track(_tr(500, 900), block_id=i, mono_ts=float(i))
        bundles = sub.drain()
        self.assertEqual(len(bundles), 4)
        # Oldest three (0,1,2) were dropped; newest four remain.
        self.assertEqual([x.block_id for x in bundles], [3, 4, 5, 6])
        self.assertEqual(sub.dropped(), 3)

    def test_default_queue_max_is_generous(self):
        self.assertGreaterEqual(DEFAULT_QUEUE_MAX, 64)


class TestConfigAndReset(unittest.TestCase):
    def test_set_zone_map_clears_snapshot(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        pipe.on_track(_tr(500, 900), block_id=1, mono_ts=1.0)
        self.assertIsNotNone(pipe.snapshot())
        pipe.set_zone_map(make_zone_map())
        self.assertIsNone(pipe.snapshot())

    def test_zones_valid_reflects_map(self):
        pipe = MazeStatePipeline()   # empty zone map
        self.assertFalse(pipe.zones_valid())
        pipe.set_zone_map(make_zone_map())
        self.assertTrue(pipe.zones_valid())

    def test_set_params_takes_effect(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams(min_dwell_ticks=3))
        self.assertEqual(pipe.params.min_dwell_ticks, 3)
        pipe.set_params(MazeStateParams(min_dwell_ticks=1))
        self.assertEqual(pipe.params.min_dwell_ticks, 1)

    def test_unregister_stops_delivery(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        sub = pipe.register()
        sub.close()
        pipe.on_track(_tr(500, 900), block_id=1, mono_ts=1.0)
        self.assertEqual(sub.drain(), [])


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_on_track_and_reconfig(self):
        """Hammer on_track from a worker thread while the "Tk" thread reconfigures;
        it must not raise or deadlock, and the stream stays consistent."""
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        sub = pipe.register()
        stop = threading.Event()
        errors = []

        def producer():
            i = 0
            try:
                while not stop.is_set():
                    pipe.on_track(_tr(500, 900), block_id=i, mono_ts=float(i))
                    i += 1
            except Exception as e:  # pragma: no cover - failure path
                errors.append(e)

        t = threading.Thread(target=producer)
        t.start()
        try:
            for _ in range(200):
                pipe.set_params(MazeStateParams(min_dwell_ticks=2))
                pipe.set_zone_map(make_zone_map())
                pipe.snapshot()
                sub.drain()
                pipe.zones_valid()
        finally:
            stop.set()
            t.join(timeout=2.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])


class TestFastView(unittest.TestCase):
    """The fast (low-dwell) push seam the Rules actuation thread consumes."""

    def test_default_fast_dwell_is_one(self):
        pipe = MazeStatePipeline(make_zone_map(),
                                 MazeStateParams(min_dwell_ticks=4))
        # Debounced keeps the requested dwell; fast defaults to 1 frame.
        self.assertEqual(pipe.params.min_dwell_ticks, 4)
        self.assertEqual(pipe.fast_params.min_dwell_ticks, 1)

    def test_subscribe_fast_receives_every_frame(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        got = []
        pipe.subscribe_fast(lambda b: got.append(b.block_id))
        for i in range(5):
            pipe.on_track(_tr(500, 900), block_id=i, mono_ts=float(i))
        self.assertEqual(got, [0, 1, 2, 3, 4])

    def test_fast_bundle_carries_fast_state(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        got = []
        pipe.subscribe_fast(got.append)
        pipe.on_track(_tr(500, 900), block_id=7, mono_ts=3.5)
        self.assertEqual(got[-1].block_id, 7)
        self.assertEqual(got[-1].mono_ts, 3.5)
        self.assertEqual(got[-1].state.zone, HUB)

    def test_fast_commits_zone_before_debounced(self):
        """The whole point: with fast min_dwell=1 vs debounced=4, entering a new
        zone is reflected on the fast push ~3 frames before snapshot()."""
        pipe = MazeStatePipeline(make_zone_map(),
                                 MazeStateParams(min_dwell_ticks=4))
        fast = []
        pipe.subscribe_fast(fast.append)
        t = 0.0
        for _ in range(4):                 # commit HUB in both views
            pipe.on_track(_tr(500, 900), block_id=int(t), mono_ts=t)
            t += 1.0
        self.assertEqual(pipe.snapshot().state.zone, HUB)
        self.assertEqual(fast[-1].state.zone, HUB)
        # One frame in the LEFT choice arm: fast commits it now; debounced holds.
        pipe.on_track(_tr(200, 350), block_id=int(t), mono_ts=t)
        self.assertEqual(fast[-1].state.zone, LEFT_CHOICE)   # fast view: committed
        self.assertEqual(pipe.snapshot().state.zone, HUB)    # debounced: still HUB

    def test_set_fast_params_tunes_only_fast(self):
        pipe = MazeStatePipeline(make_zone_map(),
                                 MazeStateParams(min_dwell_ticks=4))
        pipe.set_fast_params(MazeStateParams(min_dwell_ticks=3))
        self.assertEqual(pipe.fast_params.min_dwell_ticks, 3)
        self.assertEqual(pipe.params.min_dwell_ticks, 4)     # debounced untouched

    def test_set_zone_map_reconfigures_both_views(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        fast = []
        pipe.subscribe_fast(fast.append)
        pipe.on_track(_tr(500, 900), block_id=1, mono_ts=1.0)
        pipe.set_zone_map(make_zone_map())   # must not break the fast estimator
        pipe.on_track(_tr(500, 900), block_id=2, mono_ts=2.0)
        self.assertEqual(fast[-1].state.zone, HUB)

    def test_unsubscribe_fast_stops_delivery(self):
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        got = []
        cb = got.append
        pipe.subscribe_fast(cb)
        pipe.on_track(_tr(500, 900), block_id=0, mono_ts=0.0)
        pipe.unsubscribe_fast(cb)
        pipe.on_track(_tr(500, 900), block_id=1, mono_ts=1.0)
        self.assertEqual([b.block_id for b in got], [0])

    def test_fast_callback_exception_is_isolated(self):
        """A raising fast subscriber must not stop other fast subscribers, the
        debounced fan-out, or the snapshot (it runs inline on the worker)."""
        pipe = MazeStatePipeline(make_zone_map(), MazeStateParams())
        good = []
        sub = pipe.register()
        pipe.subscribe_fast(lambda _b: (_ for _ in ()).throw(RuntimeError("boom")))
        pipe.subscribe_fast(good.append)
        pipe.on_track(_tr(500, 900), block_id=5, mono_ts=5.0)
        self.assertEqual([b.block_id for b in good], [5])
        self.assertEqual([b.block_id for b in sub.drain()], [5])
        self.assertEqual(pipe.snapshot().block_id, 5)


if __name__ == "__main__":
    unittest.main()
