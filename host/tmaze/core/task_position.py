"""
task_position.py - which point the task uses as the rat's position (docs/decisions/0230).

By default the task's position is the tracker's centre of mass (CoM). With the
Tracking tab's "Use head for task" switch on, it is DeepLabCut-Live's
``head_center`` instead (``tmaze.io.dlc_head_client``), with the CoM as the
fallback. ``TaskPositionSelector.select`` makes that choice once per camera
frame, on the track worker, and the answer rides on the tracker result as
``task_point`` to every maze-state consumer and to the video sidecar.

Sources (the sidecar's ``task_src`` column):

  * ``com`` - switch off: the CoM, exactly as before ADR-0230.
  * ``dlc`` - a trusted DLC head centre (likelihood >= pcutoff) from at most
    ``max_lag_frames`` camera frames ago. DLC answers ~20 ms after its frame,
    so this point is usually one frame old.
  * ``dlc_hold`` - no fresh trusted head centre: the last one, kept for at most
    ``hold_s`` so a short DLC dropout doesn't swap head and body back and forth.
  * ``com_fallback`` - switch on, but no trusted head centre for longer than
    ``hold_s`` (or the helper is not running): the CoM.

Only the two DLC sources replace the tracker's point (``dlc_override``); for
``com`` and ``com_fallback`` the consumers read the tracker result as they
always have, so the switch off changes nothing. Stdlib only (pure core).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from tmaze.core.dlc_head import HEAD_PART
from tmaze.core.recent_tally import RecentTally, TallySnapshot

SRC_COM = "com"
SRC_DLC = "dlc"
SRC_DLC_HOLD = "dlc_hold"
SRC_COM_FALLBACK = "com_fallback"
SOURCES = (SRC_COM, SRC_DLC, SRC_DLC_HOLD, SRC_COM_FALLBACK)
DLC_SOURCES = (SRC_DLC, SRC_DLC_HOLD)

# Header values for the trial CSV's ``position_source`` and the sidecar's
# ``task_position`` line: the mode the session ran in.
MODE_COM = "com"
MODE_DLC_HEAD = "dlc_head"

# ~80 ms at the camera's 25 fps: one frame is DLC's normal delay, the second
# covers a frame the helper skipped while busy.
DEFAULT_MAX_LAG_FRAMES = 2
# Long enough to bridge a few `low` / `skipped` frames, short enough that the
# rat (~1 px/ms at a run) hasn't left a zone on a stale point.
DEFAULT_HOLD_S = 0.15

TASK_COLUMNS = ("task_src", "task_x", "task_y")
_BLANKS = ("",) * len(TASK_COLUMNS)

Point = Tuple[float, float]


@dataclass(frozen=True)
class TaskPoint:
    """The point the task uses on one frame, in full-frame px (None = no point).
    ``likelihood`` is DLC's for the two DLC sources, else None."""
    x: Optional[float]
    y: Optional[float]
    source: str
    likelihood: Optional[float] = None

    @property
    def xy(self) -> Optional[Point]:
        return None if self.x is None or self.y is None else (self.x, self.y)


def com_of(track_result) -> Optional[Point]:
    """The tracker's point as the maze-state estimator reads it: the Kalman
    estimate when there is one, else the raw detection."""
    if track_result is None:
        return None
    smoothed = getattr(track_result, "smoothed", None)
    return smoothed if smoothed is not None else getattr(track_result, "centroid", None)


def dlc_override(track_result) -> Optional[TaskPoint]:
    """The result's ``task_point`` when it is a DLC point, else None (then the
    consumer reads the tracker's own point and state, as before)."""
    tp = getattr(track_result, "task_point", None)
    if tp is None or tp.source not in DLC_SOURCES or tp.xy is None:
        return None
    return tp


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


class TaskPositionSelector:
    """Chooses each frame's task point. ``select`` runs on the camera's track
    worker; ``set_enabled`` / ``configure`` / ``counts`` are called from Tk, so
    the shared state is locked. ``now`` is passed in (monotonic s) to keep this
    clock-free and testable."""

    def __init__(self, max_lag_frames: int = DEFAULT_MAX_LAG_FRAMES,
                 hold_s: float = DEFAULT_HOLD_S):
        self._lock = threading.Lock()
        self._enabled = False
        self.max_lag_frames = int(max_lag_frames)
        self.hold_s = float(hold_s)
        self._held: Optional[Tuple[float, float, float]] = None   # x, y, likelihood
        self._held_t = 0.0
        # Frames per source, over the last few seconds and since reset_counts.
        self._tally = RecentTally()

    # ------------------------------------------------------------ settings
    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def mode(self) -> str:
        return MODE_DLC_HEAD if self.enabled else MODE_COM

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self._enabled = bool(on)
            self._held = None

    def configure(self, max_lag_frames: int, hold_s: float) -> None:
        with self._lock:
            self.max_lag_frames = max(0, int(max_lag_frames))
            self.hold_s = max(0.0, float(hold_s))

    def counts(self) -> Dict[str, int]:
        """Frames per source since the last ``reset_counts``."""
        total = self._tally.total()
        return {s: total.get(s, 0) for s in SOURCES}

    def tally(self, now: float) -> TallySnapshot:
        """Frames per source over the last ``window_s`` and since the reset."""
        return self._tally.snapshot(now)

    def reset_counts(self) -> None:
        self._tally.reset()

    # ------------------------------------------------------------ per frame
    def select(self, block_id: Optional[int], now: float, com: Optional[Point],
               sample=None) -> TaskPoint:
        """This frame's point. ``block_id`` is the camera frame the tracker ran
        on; ``com`` the tracker's point (``com_of``); ``sample`` the newest DLC
        answer (a ``dlc_head.DlcHeadSample``) or None. A sample counts as fresh
        when its ``block_id`` is 0..max_lag_frames behind this frame's; without
        block ids it never does (the task then stays on the CoM)."""
        with self._lock:
            if not self._enabled:
                point = _com_point(com, SRC_COM)
            else:
                head = self._fresh_head(block_id, sample)
                if head is not None:
                    self._held, self._held_t = head, now
                    point = TaskPoint(head[0], head[1], SRC_DLC, head[2])
                elif self._held is not None and now - self._held_t <= self.hold_s:
                    h = self._held
                    point = TaskPoint(h[0], h[1], SRC_DLC_HOLD, h[2])
                else:
                    self._held = None
                    point = _com_point(com, SRC_COM_FALLBACK)
        self._tally.add(now, point.source)
        return point

    def _fresh_head(self, block_id, sample) -> Optional[Tuple[float, float, float]]:
        if sample is None or block_id is None or sample.block_id is None:
            return None
        if not sample.valid:
            return None
        lag = int(block_id) - int(sample.block_id)
        if lag < 0 or lag > self.max_lag_frames:
            return None
        x, y, lik = sample.points[HEAD_PART]
        if not (_finite(x) and _finite(y)):
            return None
        return float(x), float(y), float(lik)


def _com_point(com: Optional[Point], source: str) -> TaskPoint:
    if com is None:
        return TaskPoint(None, None, source)
    return TaskPoint(float(com[0]), float(com[1]), source)


# ------------------------------------------------------------ video sidecar
def task_row(point: Optional[TaskPoint]) -> tuple:
    """TASK_COLUMNS values for one frame; all blank with no tracker result."""
    if point is None:
        return _BLANKS
    if point.xy is None:
        return (point.source, "", "")
    return (point.source, f"{point.x:.1f}", f"{point.y:.1f}")


def column_doc() -> Dict[str, str]:
    """Ordered {column: one-line meaning}, for the bundle's format.json."""
    return {
        "task_src": "which point the task used: com (switch off) / dlc (DLC-Live "
                    "head_center) / dlc_hold (last head_center, <= hold_s old) / "
                    "com_fallback (switch on, no trusted head); blank = no track result",
        "task_x": "x of the point the task used (full-frame px); blank = none",
        "task_y": "y of the point the task used (full-frame px); blank = none",
    }


def header_fields(selector: Optional[TaskPositionSelector], pcutoff=None) -> Dict[str, str]:
    """Sidecar ``#`` header lines: the mode at record start and its settings."""
    if selector is None or not selector.enabled:
        return {"task_position": MODE_COM}
    return {"task_position": MODE_DLC_HEAD,
            "task_position_part": HEAD_PART,
            "task_position_max_lag_frames": str(selector.max_lag_frames),
            "task_position_hold_s": f"{selector.hold_s:g}",
            "task_position_pcutoff": "" if pcutoff is None else f"{float(pcutoff):g}"}
