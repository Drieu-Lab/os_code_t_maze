"""
session_timer.py - wall-clock session timer for the behavior readout (pure core).

Records when the experimenter starts and stops a session (via the behavior
panel's Start/Stop button) and reports the wall-clock span between them. Like the
other pure cores (behavior_stats.py, task_events.py), the logic is stdlib-only
and headless-testable; the Tk shell (behavior_view.py) drives it and formats the
display.

Why separate from behavior_stats.active_s. This "duration" is deliberately a
different quantity: a wall-clock SPAN from first Start to Stop/now that INCLUDES
paused stretches, whereas behavior_stats.active_s is ACTIVE time (pauses removed)
that denominates the per-minute rates. The session timer answers "how long has
this session been open?"; active_s answers "over how much running time were these
rates measured?". See docs/decisions/0036.

Semantics. The first Start since a reset sets start_ts; Stop sets stop_ts. A
restart after a stop keeps the original start_ts (so the span still runs from the
first Start), and the next Stop updates stop_ts. ``now`` is injected (epoch
seconds) so the core is fully testable without touching the wall clock.

Thread-safety: driven single-threaded (the Tk poll loop / button callbacks), like
the other cores. It takes no locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SessionTimerSnapshot:
    """Immutable readout of the session timer (the view formats display).

    ``start_ts`` / ``stop_ts`` are epoch seconds (or None before the first
    start / stop). ``elapsed_s`` is the wall-clock span: ``now - start_ts`` while
    running, else ``stop_ts - start_ts``; None before the first start.
    """
    start_ts: Optional[float]
    stop_ts: Optional[float]
    running: bool
    elapsed_s: Optional[float]


class SessionTimer:
    """Tracks the wall-clock span of a manually started/stopped session.

    Pure and single-threaded. Just the two anchors plus a running flag.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._start_ts: Optional[float] = None
        self._stop_ts: Optional[float] = None
        self._running = False

    def start(self, now: float) -> None:
        """Begin (or resume) the session. Keeps the original start on a restart
        so the span still runs from the first Start."""
        if self._running:
            return
        if self._start_ts is None:
            self._start_ts = float(now)
        self._running = True

    def stop(self, now: float) -> None:
        """End the current running stretch, recording the stop time."""
        if not self._running:
            return
        self._stop_ts = float(now)
        self._running = False

    def snapshot(self, now: float) -> SessionTimerSnapshot:
        if self._start_ts is None:
            elapsed: Optional[float] = None
        elif self._running:
            elapsed = float(now) - self._start_ts
        else:
            # Not running: span up to the last stop. stop_ts is set whenever the
            # timer has run and stopped.
            elapsed = (self._stop_ts - self._start_ts
                       if self._stop_ts is not None else 0.0)
        return SessionTimerSnapshot(
            start_ts=self._start_ts,
            stop_ts=self._stop_ts,
            running=self._running,
            elapsed_s=elapsed)
