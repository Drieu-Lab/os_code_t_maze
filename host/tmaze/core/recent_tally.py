"""
recent_tally.py - count events over the last few seconds AND since a reset.

The Tracking tab's DLC status shows each number twice: how DeepLabCut-Live and
the task's position are doing right now (the last ``window_s``) and in total
(since recording started, or since the helper / switch started). ``RecentTally``
keeps both: a running total per key, plus the recent events (time, key, value)
for the window. Used by ``dlc_head_client`` (answers + their delay, skipped
frames) and ``task_position`` (frames per source). Times are passed in
(monotonic seconds) so this stays clock-free and testable. Locked: events come
from worker threads, reads from Tk. Stdlib only (pure core).
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

DEFAULT_WINDOW_S = 10.0


@dataclass(frozen=True)
class TallySnapshot:
    """``recent`` / ``total``: events per key. ``recent_values`` / the total
    sum + count: the numbers attached to events (e.g. DLC answer times)."""
    recent: Dict[str, int]
    total: Dict[str, int]
    recent_values: List[float] = field(default_factory=list)
    total_value_sum: float = 0.0
    total_value_n: int = 0

    @property
    def recent_median(self) -> Optional[float]:
        v = sorted(self.recent_values)
        return v[len(v) // 2] if v else None

    @property
    def total_mean(self) -> Optional[float]:
        return self.total_value_sum / self.total_value_n if self.total_value_n else None


class RecentTally:
    def __init__(self, window_s: float = DEFAULT_WINDOW_S):
        self.window_s = float(window_s)
        self._lock = threading.Lock()
        self._events: Deque[Tuple[float, str, Optional[float]]] = deque()
        self._total: Dict[str, int] = {}
        self._sum = 0.0
        self._n = 0

    def add(self, t: float, key: str, value: Optional[float] = None) -> None:
        with self._lock:
            self._events.append((t, key, value))
            self._total[key] = self._total.get(key, 0) + 1
            if value is not None:
                self._sum += float(value)
                self._n += 1
            cutoff = t - self.window_s
            while self._events and self._events[0][0] < cutoff:
                self._events.popleft()

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._total = {}
            self._sum, self._n = 0.0, 0

    def total(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._total)

    def snapshot(self, now: float) -> TallySnapshot:
        cutoff = now - self.window_s
        with self._lock:
            recent: Dict[str, int] = {}
            values: List[float] = []
            for t, key, value in self._events:
                if t < cutoff:
                    continue
                recent[key] = recent.get(key, 0) + 1
                if value is not None:
                    values.append(float(value))
            return TallySnapshot(recent, dict(self._total), values, self._sum, self._n)
