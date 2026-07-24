"""
vision_actuator.py - the shared vision-actuator skeleton (ADR-0043 pattern).

Both host-owned task controllers - the Rules engine (``rules_view.py``) and
Phase 3 (``phase3_controller.py``) - drive reward / DAC hardware off the shared
maze-state pipeline's FAST view (``maze_pipeline``, ADR-0042). They independently
grew the *same* concurrency skeleton (ADR-0043); this module factors that
skeleton into one base class both mix in (ADR-0053, refactor A).

The skeleton, in one place:

  - **Fast-view subscription.** The pipeline pushes each frame INLINE on the
    tracking worker to ``_on_fast_state``, which must stay cheap - it only
    ENQUEUES the bundle for the actuation thread (never touches Tk or a core).
  - **Bounded hand-off queue.** ``_enqueue_act`` puts items on a fixed-size queue,
    dropping (and counting, as a lag indicator) the newest on overflow rather
    than blocking the worker or the serial reader.
  - **A single dedicated actuation thread.** ``_actuation_loop`` is the *only*
    caller of the (single-threaded) engine / task cores, so they need no lock on
    the hot path: it drains the queue and hands each item to
    ``_process_act_item`` with the current snapshot, read once (atomically).
  - **An atomic Tk->thread snapshot.** The Tk poll builds an immutable snapshot
    of the gate state and assigns it to ``self._act_snapshot``; the thread reads
    the whole reference, so it never reads live Tk state or a live core config.

This base owns NO task / reward / DAC policy - the DAC-authority handshake, the
gate-edge resets, the snapshot's *contents*, and the per-item work all live in
the subclass. See ADR-0043 (skeleton) and ADR-0053 (this extraction).

Contract for the mixing view:
  - set ``self._pipeline`` (the shared ``MazeStatePipeline`` or ``None``) BEFORE
    calling ``_start_actuation`` / ``_stop_actuation``;
  - call ``_init_actuator(...)`` once (before the pipeline callbacks can fire);
  - assign an initial default ``self._act_snapshot`` so the thread never reads
    ``None`` if a frame arrives before the first Tk poll;
  - implement ``_process_act_item(item, snapshot)``.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional

# Bounded hand-off queue: several seconds of frames at 30-60 fps. The per-item
# actuation work is tiny so it never backs up in practice; overflow drops the
# newest item (counted) rather than growing unbounded or blocking the tracking
# worker / serial reader. See ADR-0043.
ACT_QUEUE_MAX = 512


class VisionActuator:
    """Fast-view subscription + bounded queue + a dedicated actuation thread +
    an atomic Tk->thread snapshot hand-off (the ADR-0043 skeleton).

    Mix into a Tk view. Subclasses build their own snapshot dataclass, assign it
    to ``self._act_snapshot`` on the Tk poll, and implement ``_process_act_item``.
    """

    def _init_actuator(self, *, queue_max: int = ACT_QUEUE_MAX,
                       thread_name: str = "vision-actuation") -> None:
        """Initialise the queue / thread / snapshot machinery. Call once during
        ``__init__`` (before the pipeline can push frames). Does not touch the
        pipeline - the subclass owns ``self._pipeline``."""
        self._act_queue: "queue.Queue" = queue.Queue(maxsize=queue_max)
        self._act_stop = threading.Event()
        self._act_thread: Optional[threading.Thread] = None
        self._act_drops = 0                 # items dropped on a full queue (lag)
        self._act_snapshot = None           # immutable Tk->thread hand-off (subclass)
        self._act_thread_name = thread_name

    def _start_actuation(self) -> None:
        """Subscribe to the pipeline's fast view and spawn the actuation thread.
        No-op without a pipeline (the subclass degrades to its own fallback)."""
        if getattr(self, "_pipeline", None) is None:
            return
        self._pipeline.subscribe_fast(self._on_fast_state)
        self._act_thread = threading.Thread(
            target=self._actuation_loop, name=self._act_thread_name, daemon=True)
        self._act_thread.start()

    def _on_fast_state(self, bundle) -> None:
        """Pipeline fast-view callback - runs INLINE on the tracking worker, so it
        must be cheap + non-blocking: just enqueue for the actuation thread
        (ADR-0042/0043). Never call a core or Tk from here."""
        self._enqueue_act(("frame", bundle))

    def _enqueue_act(self, item) -> None:
        """Put an item on the actuation queue, dropping (and counting) on overflow
        rather than blocking the caller (the tracking worker or the serial reader)."""
        try:
            self._act_queue.put_nowait(item)
        except queue.Full:
            self._act_drops += 1

    def _actuation_loop(self) -> None:
        """The single actuation thread: drain the queue in arrival order and hand
        each item to ``_process_act_item`` with the current snapshot (read once,
        atomically), so it is the sole caller of the single-threaded cores."""
        while not self._act_stop.is_set():
            try:
                item = self._act_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:                 # stop() sentinel
                continue
            try:
                self._process_act_item(item, self._act_snapshot)
            except Exception:
                pass

    def _process_act_item(self, item, snapshot) -> None:
        """Handle one queued item (the sole caller of the engine / task cores).
        Subclass responsibility - see the two callers for the frame / lick split."""
        raise NotImplementedError

    def _stop_actuation(self) -> None:
        """Stop the thread and unsubscribe from the fast view. Call BEFORE closing
        anything the thread writes to. Safe when the thread never started (no
        pipeline / unavailable tab)."""
        self._act_stop.set()
        if getattr(self, "_pipeline", None) is not None:
            try:
                self._pipeline.unsubscribe_fast(self._on_fast_state)
            except Exception:
                pass
        try:
            self._act_queue.put_nowait(None)   # wake the loop so it can exit
        except queue.Full:
            pass
        if self._act_thread is not None and self._act_thread.is_alive():
            self._act_thread.join(timeout=2.0)
