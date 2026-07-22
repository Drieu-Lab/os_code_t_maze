"""
maze_pipeline.py - one shared maze-state pipeline over the tracking worker.

A thin, thread-safe wrapper around a SINGLE pure ``MazeStateEstimator``
(maze_state.py) that runs on the camera's tracking worker (ADR-0040) instead of
once per tab. Before this, the Maze state / Rules / Behavior tabs each built their
own estimator and re-reduced ``tracker.snapshot_result()`` on their own Tk poll,
so the same reduction ran three times, the three could disagree (per-tab params),
and each committed a zone only as fast as its ~150 ms poll (min_dwell_ticks x
poll). This module makes the reduction happen ONCE, at the camera frame rate, and
fans the result out to every tab. See docs/decisions/0041 (extends ADR-0025).

Design (headless, stdlib-only - no Tk/OpenCV/numpy, like maze_state.py):

  - ``on_track(track_result, block_id, mono_ts)`` is subscribed to the camera's
    TrackPublisher, so it is called on the WORKER thread for every published
    result. Under a lock it folds the result through the shared estimator into a
    ``RatState`` and packages a ``TrackBundle`` (the source result + its
    BlockID / monotonic + wall stamps + the RatState). The bundle becomes the
    latest snapshot AND is appended to every registered subscriber's queue.

  - ``snapshot()`` serves display-only reads (readout labels, preview) a
    freshest, known-age bundle. ``register()`` hands a consumer a per-subscriber
    bounded FIFO of bundles it drains on its own Tk poll: the maze-state edges
    (``entered`` / ``exited`` / ``loop_completed``) are one-frame flags, so an
    edge-consuming tab (behavior, rules) must fold EVERY bundle since its last
    poll, not just sample the latest - otherwise a choice/loop landing between
    two polls is silently lost. The queue is drop-oldest + counts drops so a
    stalled GUI is visible, never unbounded.

  - ``set_zone_map`` / ``set_params`` / ``reset`` are the Tk-thread editing seam
    (owned by the Maze state tab); they take the same lock as ``on_track`` so an
    edit can never tear a concurrent fold. The estimator is GLOBAL: a consumer's
    session boundary (rules gate edge, behavior MODE switch) resets only that
    consumer's own downstream ``TaskEvents`` / stats, never this shared estimator
    (``RatState.history`` is global physical position).

Clock. The bundle timestamps the state with ``mono_ts`` (the worker's
``time.monotonic()``), the same clock the rules engine ticks on, so a consumer
can pass ``bundle.mono_ts`` straight to ``engine.tick``. ``wall_ts`` is kept
alongside so the state-timeline log can still record wall-clock time.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional

from maze_state import MazeStateEstimator, MazeStateParams, RatState, ZoneMap

# A subscriber queue holds up to ~this many bundles before dropping the oldest.
# At a ~30-60 fps camera and a ~100-150 ms consumer poll a poll drains only a
# handful, so this is many seconds of slack; a persistently full queue means a
# stalled GUI (surfaced via ``Subscriber.dropped``), not silent unbounded growth.
DEFAULT_QUEUE_MAX = 256


@dataclass(frozen=True)
class TrackBundle:
    """One published maze-state result + the metadata to align/age it.

    ``state`` is the RatState the shared estimator produced from ``track_result``;
    ``block_id`` is the camera BlockID the result was computed on (staleness key);
    ``mono_ts`` is the worker ``time.monotonic()`` used as the estimator/engine
    clock; ``wall_ts`` is ``time.time()`` at publish for wall-clock logging.
    """
    track_result: object
    block_id: Optional[int]
    mono_ts: float
    wall_ts: float
    state: RatState


class Subscriber:
    """A per-consumer, thread-safe, bounded FIFO of ``TrackBundle``s.

    The worker ``append``s newest bundles; the consumer ``drain``s them all on
    its Tk poll and folds each in order. Bounded drop-oldest (a full queue evicts
    the head and increments ``dropped``) so a slow consumer degrades gracefully.
    """

    def __init__(self, pipeline: "MazeStatePipeline", maxlen: int) -> None:
        self._pipeline = pipeline
        self._lock = threading.Lock()
        self._q: Deque[TrackBundle] = deque(maxlen=maxlen)
        self._dropped = 0

    def _append(self, bundle: TrackBundle) -> None:
        """Enqueue newest bundle (called on the worker); count a drop-oldest."""
        with self._lock:
            if self._q.maxlen is not None and len(self._q) >= self._q.maxlen:
                self._dropped += 1  # the append below evicts the oldest bundle
            self._q.append(bundle)

    def drain(self) -> List[TrackBundle]:
        """Return every queued bundle in arrival order and clear the queue."""
        with self._lock:
            out = list(self._q)
            self._q.clear()
        return out

    def dropped(self) -> int:
        """Bundles dropped because this consumer fell behind (a lag indicator)."""
        with self._lock:
            return self._dropped

    def close(self) -> None:
        """Unregister from the pipeline (idempotent)."""
        self._pipeline._unregister(self)


class MazeStatePipeline:
    """One shared estimator on the worker; snapshot + drained fan-out to tabs."""

    def __init__(self, zone_map: Optional[ZoneMap] = None,
                 params: Optional[MazeStateParams] = None,
                 queue_max: int = DEFAULT_QUEUE_MAX) -> None:
        self._lock = threading.Lock()
        self._estimator = MazeStateEstimator(zone_map or ZoneMap(),
                                             params or MazeStateParams())
        self._latest: Optional[TrackBundle] = None
        self._subscribers: List[Subscriber] = []
        self._queue_max = max(1, int(queue_max))

    # ------------------------------------------------------ worker (producer)
    def on_track(self, track_result, block_id: Optional[int],
                 mono_ts: float) -> None:
        """Fold one published track result into the shared state (on the worker).

        Subscribe this to ``CameraView.subscribe_track``. It runs under the
        pipeline lock so a concurrent ``set_zone_map`` / ``set_params`` / ``reset``
        can't tear the fold; subscribers are notified OUTSIDE the lock.
        """
        wall_ts = time.time()
        with self._lock:
            state = self._estimator.update(track_result, mono_ts)
            bundle = TrackBundle(track_result, block_id, mono_ts, wall_ts, state)
            self._latest = bundle
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub._append(bundle)

    # ------------------------------------------------------- Tk editing seam
    def set_zone_map(self, zone_map: ZoneMap) -> None:
        """Swap the zone map (resets the estimator) and clear the snapshot."""
        with self._lock:
            self._estimator.set_zone_map(zone_map)
            self._latest = None

    def set_params(self, params: MazeStateParams) -> None:
        with self._lock:
            self._estimator.set_params(params)

    def reset(self) -> None:
        """Drop the shared estimator's internal state (rarely needed globally)."""
        with self._lock:
            self._estimator.reset()
            self._latest = None

    # --------------------------------------------------------- reads / config
    def snapshot(self) -> Optional[TrackBundle]:
        """The freshest bundle for display-only reads, or None."""
        with self._lock:
            return self._latest

    @property
    def zone_map(self) -> ZoneMap:
        """The current (immutable) zone map (for the editor / preview / status)."""
        with self._lock:
            return self._estimator.zone_map

    @property
    def params(self) -> MazeStateParams:
        with self._lock:
            return self._estimator.params

    def zones_valid(self) -> bool:
        """True once the shared zone map has a usable polygon (>= 3 verts)."""
        with self._lock:
            return self._estimator.zone_map.valid()

    # -------------------------------------------------------- subscription
    def register(self) -> Subscriber:
        """Create + register a per-consumer drained queue."""
        sub = Subscriber(self, self._queue_max)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def _unregister(self, sub: Subscriber) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)
