"""
test_maze_pipeline.py - headless unit tests for the shared maze-state pipeline.

No Tk, no OpenCV, no camera: exercises maze_pipeline.MazeStatePipeline over
duck-typed track results (SimpleNamespace, exactly like maze_state_validate.py),
verifying it drives ONE estimator, fans a full RatState stream out to every
subscriber without dropping one-frame edges, and stays thread-safe under a
concurrent on_track vs set_zone_map / set_params / reset. See docs/decisions/0041.

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


if __name__ == "__main__":
    unittest.main()
