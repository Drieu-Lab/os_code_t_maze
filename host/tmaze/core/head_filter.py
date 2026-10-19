"""
head_filter.py - leap guard + light smoothing for the live head points.

The live tracker runs the small head network (``tmaze.vision.head_learned``)
on every frame and gets six points (head centre, neck, tail base, nose, both
ears; full-frame px). ``HeadFilter`` turns that raw stream into what is
recorded (see docs/decisions/0219):

  * LEAP GUARD - a head centre more than ``max_step_px`` from the current one
    is not trusted straight away: the frame is reported invalid and the old
    points are held. If the next frames keep agreeing with the new place
    (``confirm_frames`` in a row, each within ``agree_px`` of the last), the
    rat really is there and the filter jumps to it. A one-frame glitch is
    therefore never recorded as valid; a real fast move costs a few frames.
  * STILL-ONLY SMOOTHING - while the raw head centre moves less than
    ``still_px`` between frames, accepted points are blended with the previous
    ones (``still_alpha`` = weight of the new frame), every point the same way
    so the posture stays consistent. A moving head is not smoothed at all:
    plain smoothing makes the point trail a running rat (on two rat-600 videos
    a fixed weight of 0.6 raised the p90 error vs DeepLabCut from 9.5 to
    13-14 px), while smoothing only the still head halves its jitter (1.1 ->
    0.5 px) at no cost in accuracy.
  * HEAD DIRECTION is computed from the SMOOTHED neck and head centre (neck ->
    head centre, degrees, image axes: 0 = image right, 90 = image down), so it
    is smoothed with the points and has no wrap-around problem at +-180.

After ``max_gap_frames`` frames with no prediction (rat lost, head tracking
paused) the filter forgets and starts fresh on the next prediction.

Stdlib only (a pure core), headless-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

Point = Tuple[float, float]

OK = "ok"                # a trusted, smoothed frame
LEAP = "leap"            # head centre jumped; held until the new place is confirmed
NO_PREDICTION = "none"   # no network output this frame


@dataclass(frozen=True)
class HeadFilterParams:
    max_step_px: float = 40.0    # ~9 cm in one 40 ms frame (2.3 m/s): faster than a rat
    confirm_frames: int = 3      # frames in a row that must agree before a leap is accepted
    agree_px: float = 15.0       # "agree" = each within this of the previous one
    still_px: float = 3.0        # raw head step below this = still, and smoothed
    still_alpha: float = 0.5     # weight of the new frame while still (1 = no smoothing)
    max_gap_frames: int = 5      # frames without a prediction before starting fresh


@dataclass
class HeadSample:
    """One frame's filtered output."""
    valid: bool
    status: str                                   # OK / LEAP / NO_PREDICTION
    points: Dict[str, Point] = field(default_factory=dict)
    direction_deg: Optional[float] = None


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def direction_deg(neck: Optional[Point], head: Optional[Point]) -> Optional[float]:
    """Neck -> head centre in degrees, image axes; None when missing or coincident."""
    if neck is None or head is None or (neck[0] == head[0] and neck[1] == head[1]):
        return None
    return math.degrees(math.atan2(head[1] - neck[1], head[0] - neck[0]))


class HeadFilter:
    """Feed one raw prediction per frame with ``update``; ``reset`` between sessions."""

    def __init__(self, params: Optional[HeadFilterParams] = None):
        self.params = params or HeadFilterParams()
        self.reset()

    def reset(self) -> None:
        self._points: Optional[Dict[str, Point]] = None
        self._last_raw: Optional[Point] = None    # last ACCEPTED raw head centre
        self._pending: List[Dict[str, Point]] = []
        self._gap = 0

    def _sample(self, valid: bool, status: str) -> HeadSample:
        pts = dict(self._points or {})
        return HeadSample(valid=valid, status=status, points=pts,
                          direction_deg=direction_deg(pts.get("neck"), pts.get("head")))

    def update(self, raw: Optional[Mapping[str, Point]]) -> HeadSample:
        p = self.params
        if raw is None or raw.get("head") is None:
            self._gap += 1
            self._pending = []
            if self._gap > p.max_gap_frames:
                self._points = None
            return HeadSample(valid=False, status=NO_PREDICTION)
        self._gap = 0
        raw = dict(raw)
        if self._points is None:
            self._points = raw
            self._last_raw = raw["head"]
            return self._sample(True, OK)
        # Judged against the last accepted RAW head, not the smoothed one: the
        # smoothed point lags a running rat, and that lag must not look like a leap.
        step = _dist(raw["head"], self._last_raw)
        if step <= p.max_step_px:
            self._pending = []
            self._last_raw = raw["head"]
            a = p.still_alpha if step < p.still_px else 1.0
            for name, pt in raw.items():
                old = self._points.get(name)
                self._points[name] = pt if old is None else (
                    a * pt[0] + (1 - a) * old[0], a * pt[1] + (1 - a) * old[1])
            return self._sample(True, OK)
        if self._pending and _dist(raw["head"], self._pending[-1]["head"]) > p.agree_px:
            self._pending = []
        self._pending.append(raw)
        if len(self._pending) >= p.confirm_frames:
            self._points = raw
            self._last_raw = raw["head"]
            self._pending = []
            return self._sample(True, OK)
        return self._sample(False, LEAP)
