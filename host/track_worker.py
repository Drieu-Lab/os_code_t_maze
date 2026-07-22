"""
track_worker.py - off-grab-thread handoff primitives for camera tracking.

Two small, UI-free, dependency-free (stdlib threading only) primitives that let
rat tracking run on its own worker instead of inline on the camera grab thread,
so a slow tracker.process() can never delay the next RetrieveResult and drop
frames at the source (the structural root of #260720; see docs/decisions/0040,
extends ADR-0011's grab/encode split):

  - LatestFrameSlot: a 1-slot, drop-oldest mailbox. The grab thread put()s the
    newest oriented frame (+ its BlockID / cam timestamp / a monotonic stamp);
    the worker take()s it, blocking until one is available. If the worker is
    behind, a new put() overwrites the pending frame (returns True so the caller
    can count the skip) - tracking therefore runs at a steady, possibly lower
    rate on the FRESHEST frame, never back-pressuring capture.

  - TrackPublisher: holds the latest (track_result, block_id, monotonic_ts) under
    a lock and fans it out. snapshot() serves the Tk pollers / preview overlay
    (a known-age freshest position); subscribe(cb) invokes callbacks synchronously
    on the worker thread the instant a result is published - the low-latency,
    low-jitter seam a future positional-cue gate consumes instead of the 100-150 ms
    Tk poll (see FW-0014). Callbacks are isolated (try/except) so one subscriber
    cannot kill the worker.

Both are headless and thread-safe, so they are unit-tested directly
(test_track_worker.py) while the camera_view shell that wires them stays a thin
Tk/pypylon layer.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional, Tuple

# One handed-off frame: (frame, block_id, cam_ts, monotonic_ts). frame is a BGR
# numpy array; block_id/cam_ts may be None when the camera can't report them.
FrameItem = Tuple[Any, Optional[int], Optional[int], float]
# One published track result: (track_result, source_block_id, monotonic_ts).
TrackItem = Tuple[Any, Optional[int], float]


class LatestFrameSlot:
    """A 1-slot, drop-oldest, thread-safe frame mailbox (newest-wins handoff)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._item: Optional[FrameItem] = None
        self._stopped = False

    def put(self, frame, block_id: Optional[int], cam_ts: Optional[int],
            mono_ts: float) -> bool:
        """Store the newest frame, overwriting any not-yet-taken one.

        Returns True when a pending (unconsumed) frame was dropped, so the caller
        can count the skip for the status readout.
        """
        with self._cond:
            dropped = self._item is not None
            self._item = (frame, block_id, cam_ts, mono_ts)
            self._cond.notify()
        return dropped

    def take(self, timeout: Optional[float] = None) -> Optional[FrameItem]:
        """Block for the newest frame; return it (and clear the slot), or None on
        timeout / after stop()."""
        with self._cond:
            if self._item is None and not self._stopped:
                self._cond.wait(timeout)
            if self._stopped:
                return None
            item = self._item
            self._item = None
            return item

    def clear(self) -> None:
        """Drop any pending frame (e.g. when tracking is disabled)."""
        with self._cond:
            self._item = None

    def stop(self) -> None:
        """Unblock any waiting take() so the worker thread can exit."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()


class TrackPublisher:
    """Holds the freshest (track_result, block_id, monotonic_ts) and fans it out.

    snapshot() is for pollers/preview (a lock-guarded read of the latest tuple);
    subscribe(cb) registers a callback invoked on the publishing (worker) thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: Optional[TrackItem] = None
        self._subscribers: list[Callable[[Any, Optional[int], float], None]] = []

    def publish(self, track_result, block_id: Optional[int], mono_ts: float) -> None:
        """Store the latest result and notify subscribers (called on the worker)."""
        with self._lock:
            self._latest = (track_result, block_id, mono_ts)
            subscribers = list(self._subscribers)
        # Notify outside the lock so a slow/blocking subscriber can't stall
        # publish(); isolate each so one bad subscriber can't kill the worker.
        for cb in subscribers:
            try:
                cb(track_result, block_id, mono_ts)
            except Exception:
                pass

    def snapshot(self) -> Optional[TrackItem]:
        """The freshest (track_result, block_id, monotonic_ts), or None."""
        with self._lock:
            return self._latest

    def subscribe(self, cb: Callable[[Any, Optional[int], float], None]) -> None:
        with self._lock:
            self._subscribers.append(cb)

    def clear(self) -> None:
        """Forget the latest result (e.g. when tracking is disabled)."""
        with self._lock:
            self._latest = None
