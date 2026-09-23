"""led_map.py - the one-time table of which board LED mirrors which serial channel.

``host/led_map.json`` is written once from a mapping (or ordinary) session and
read by every later sync run (design doc sections 3.1, 3.3, 3.4; FW-0045). It
holds three things:

- **reference** - which session the map was made from and the ``board_rect``
  (full-frame pixels ``[x0, y0, x1, y1]``) that the detector searches every
  session. The rectangle is seeded from the LEDs the mapping run found, widened
  by a margin, so it needs no hand drawing; registration tracks small shifts
  inside it.
- **detector** - the detection knobs the map was made with (blob threshold,
  disk/ring radii, ...) so a later run reproduces the same blobs.
- **leds** - one entry per blinking LED: a stable ``led_id``, its ``ref_px``
  (full-frame pixel centroid in the reference session - together these form
  the *constellation* that registration matches against), the serial
  ``channel`` it mirrors (``S3=0``, ``L2=0``, ``SOL2.1``; None when unbound),
  the LED ``polarity`` during that channel's active state (``lit``/``dark``)
  and the measured start lag. Positions are reference-image pixels by decision
  (2026-09-09): no ruler on the rig; every later session is expressed as a
  similarity transform relative to this reference.

Stdlib-only (json + dataclasses) so the pure core can read it; the pixels
themselves are only ever touched in ``tmaze.vision.led_detector``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.paths import host_dir

SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = (1, 2, 3)
LED_MAP_FILENAME = "led_map.json"
LED_BOARD_SEARCH_FILENAME = "led_board_search.json"
_SCHEMA_NOTE = ("tmaze led_map v3 - which board indicator LED mirrors which serial channel, "
                "and where it sits (full-frame px) in the reference session, plus the "
                "persistently-lit red/green fiducial LEDs whose hull is the board ROI and "
                "whose pattern gives the board pose, plus the optional operator-drawn "
                "board_search_poly the detector is confined to (v3; excludes maze corners a "
                "rat crosses). Written by "
                "`python -m tmaze.tools.sync_fit --map`; see docs/design/cross-stream-sync-design.md "
                "sections 3.1/3.4 and tmaze/core/sync/led_map.py.")


def led_board_search_path() -> str:
    """Default location of the operator-drawn board search polygon:
    ``host/led_board_search.json`` (same normalized ``polygon_norm`` schema as
    ``roi_polygon.json``); an INPUT to ``sync_fit --map`` (ADR-0195)."""
    return os.path.join(str(host_dir()), LED_BOARD_SEARCH_FILENAME)

LIT, DARK = "lit", "dark"
# The persistent fiducial colours (see docs/glossary.md 'fiducial'). Neutral/white
# retroreflective markers are NOT detected - they stay a physical QA reference the
# operator eyeballs against the debug overlay (ADR-0153).
RED, GREEN = "red", "green"
FIDUCIAL_COLORS = (RED, GREEN)


def led_map_path() -> str:
    """Default location: ``host/led_map.json`` beside the other rig config files."""
    return os.path.join(str(host_dir()), LED_MAP_FILENAME)


@dataclass(frozen=True)
class DetectorParams:
    """Knobs of the blob detector and trace sampler (tmaze.vision.led_detector).

    blob_thresh  - min brightness change (grey levels, 0-255) between the extreme
                   and median images for a pixel to count as blinking. The spike
                   measured a compression-noise floor of ~25 against LED contrasts
                   of 90-185, so 30 separates them; 15 finds a thousand false blobs.
    min_area     - min pixels above threshold in a peak's patch.
    nms_radius   - two peaks closer than this (px) are one LED. Neighbouring
                   channel LEDs sit ~8-12 px apart, so 3.
    radius       - LED disk radius (px) the brightness is averaged over.
    ring_in/out  - background ring radii (px) subtracted from the disk mean.
    sample_every - frame stride for the median image (every 10th frame).
    margin_px    - how far beyond the LEDs the board_rect is widened.
    fid_thresh   - min persistent colour contrast (red-minus-green or the reverse,
                   grey levels) for a pixel to count as a steady red/green fiducial.
    fid_min_frac - a fiducial must be lit in at least this FRACTION of the sampled
                   frames; the persistence test that rejects a hand/rat that is
                   only briefly bright (ADR-0153). The detector reads the
                   100*(1-frac)-th percentile of each pixel's colour contrast.
    hull_margin_px - how far the fiducial convex hull is grown outward to form the
                   board ROI polygon (must clear the ring radius for edge LEDs).
    """
    blob_thresh: float = 30.0
    min_area: int = 2
    nms_radius: int = 3
    radius: int = 2
    ring_in: int = 5
    ring_out: int = 9
    sample_every: int = 10
    margin_px: int = 24
    fid_thresh: float = 25.0
    fid_min_frac: float = 0.9
    hull_margin_px: int = 30


@dataclass(frozen=True)
class LedEntry:
    led_id: str
    ref_px: Tuple[float, float]
    channel: Optional[str] = None
    polarity: Optional[str] = None
    lag_s: Optional[float] = None
    note: str = ""

    @property
    def bound(self) -> bool:
        return self.channel is not None


@dataclass(frozen=True)
class Fiducial:
    """One persistently-lit red/green board LED (schema v2). Its ``ref_px`` in the
    reference session; ``color`` is 'red' or 'green'. Together the fiducials form
    the point set whose convex hull is the board ROI and whose pattern registration
    matches to recover the board pose (ADR-0153)."""
    fid_id: str
    ref_px: Tuple[float, float]
    color: str


@dataclass
class LedMap:
    session_id: str
    board_rect: Tuple[int, int, int, int]
    leds: List[LedEntry] = field(default_factory=list)
    detector: DetectorParams = field(default_factory=DetectorParams)
    frame_size: Optional[Tuple[int, int]] = None      # (width, height) of the reference frame
    created: str = ""
    fiducials: List[Fiducial] = field(default_factory=list)     # persistent red/green LEDs (v2)
    board_poly: Optional[List[Tuple[float, float]]] = None      # ROI outline = fiducial hull + margin (v2)
    board_search_poly: Optional[List[Tuple[float, float]]] = None  # operator-drawn search region, full-frame px (v3)

    def constellation(self) -> List[Tuple[float, float]]:
        """Every LED's reference pixel position, bound or not - the point set
        registration matches each session's blobs against."""
        return [e.ref_px for e in self.leds]

    def fiducial_constellation(self) -> List[Tuple[float, float]]:
        """Every fiducial's reference pixel position - the persistent-LED point set
        that registration matches to recover the board pose (v2)."""
        return [f.ref_px for f in self.fiducials]

    def fiducial_colors(self) -> List[str]:
        """The colour label of each fiducial, parallel to :meth:`fiducial_constellation`
        (a same-colour matching constraint for registration)."""
        return [f.color for f in self.fiducials]

    def by_id(self) -> Dict[str, LedEntry]:
        return {e.led_id: e for e in self.leds}

    def to_dict(self) -> dict:
        return {
            "_schema": _SCHEMA_NOTE,
            "schema_version": SCHEMA_VERSION,
            "reference": {
                "session_id": self.session_id,
                "board_rect": list(self.board_rect),
                "board_poly": None if self.board_poly is None
                else [[round(x, 2), round(y, 2)] for x, y in self.board_poly],
                "board_search_poly": None if self.board_search_poly is None
                else [[round(x, 2), round(y, 2)] for x, y in self.board_search_poly],
                "frame_size": list(self.frame_size) if self.frame_size else None,
                "created": self.created,
            },
            "detector": asdict(self.detector),
            "fiducials": [{
                "fid_id": f.fid_id,
                "ref_px": [round(f.ref_px[0], 2), round(f.ref_px[1], 2)],
                "color": f.color,
            } for f in self.fiducials],
            "leds": [{
                "led_id": e.led_id,
                "ref_px": [round(e.ref_px[0], 2), round(e.ref_px[1], 2)],
                "channel": e.channel,
                "polarity": e.polarity,
                "lag_s": None if e.lag_s is None else round(e.lag_s, 4),
                "note": e.note,
            } for e in self.leds],
        }


def _rect(v: Sequence) -> Tuple[int, int, int, int]:
    if len(v) != 4:
        raise ValueError(f"board_rect must be [x0, y0, x1, y1], got {v!r}")
    x0, y0, x1, y1 = (int(x) for x in v)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"board_rect must have x1 > x0 and y1 > y0, got {v!r}")
    return x0, y0, x1, y1


def _poly(v: Optional[Sequence]) -> Optional[List[Tuple[float, float]]]:
    """Validate an optional board_poly: absent/None, or >=3 [x, y] points."""
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        raise ValueError(f"board_poly must be null or >=3 [x, y] points, got {v!r}")
    out: List[Tuple[float, float]] = []
    for i, p in enumerate(v):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError(f"board_poly[{i}] must be [x, y], got {p!r}")
        out.append((float(p[0]), float(p[1])))
    return out


def from_dict(d: dict) -> LedMap:
    """Build a LedMap from parsed JSON, checking the parts a typo would break.

    Both schema v1 (no fiducials/board_poly) and v2 load, so a map written before
    the fiducial upgrade still registers via the blinking-LED constellation."""
    ver = d.get("schema_version")
    if ver not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"led_map schema_version {ver!r} is not one of {SUPPORTED_SCHEMA_VERSIONS}")
    ref = d.get("reference") or {}
    det = DetectorParams(**{k: v for k, v in (d.get("detector") or {}).items()
                            if k in DetectorParams.__dataclass_fields__})
    fiducials: List[Fiducial] = []
    seen_fid: set = set()
    for i, e in enumerate(d.get("fiducials") or []):
        fid_id = str(e.get("fid_id") or "")
        if not fid_id or fid_id in seen_fid:
            raise ValueError(f"fiducials[{i}]: fid_id missing or duplicated ({fid_id!r})")
        seen_fid.add(fid_id)
        px = e.get("ref_px")
        if not isinstance(px, (list, tuple)) or len(px) != 2:
            raise ValueError(f"fiducials[{i}] ({fid_id}): ref_px must be [x, y]")
        color = e.get("color")
        if color not in FIDUCIAL_COLORS:
            raise ValueError(f"fiducials[{i}] ({fid_id}): color must be one of "
                             f"{FIDUCIAL_COLORS}, got {color!r}")
        fiducials.append(Fiducial(fid_id, (float(px[0]), float(px[1])), color))
    leds: List[LedEntry] = []
    seen: set = set()
    for i, e in enumerate(d.get("leds") or []):
        led_id = str(e.get("led_id") or "")
        if not led_id or led_id in seen:
            raise ValueError(f"leds[{i}]: led_id missing or duplicated ({led_id!r})")
        seen.add(led_id)
        px = e.get("ref_px")
        if not isinstance(px, (list, tuple)) or len(px) != 2:
            raise ValueError(f"leds[{i}] ({led_id}): ref_px must be [x, y]")
        pol = e.get("polarity")
        if pol not in (None, LIT, DARK):
            raise ValueError(f"leds[{i}] ({led_id}): polarity must be lit/dark/null, got {pol!r}")
        ch = e.get("channel")
        if (ch is None) != (pol is None):
            raise ValueError(f"leds[{i}] ({led_id}): channel and polarity go together")
        leds.append(LedEntry(led_id, (float(px[0]), float(px[1])), ch, pol,
                             None if e.get("lag_s") is None else float(e["lag_s"]),
                             str(e.get("note") or "")))
    fs = ref.get("frame_size")
    return LedMap(str(ref.get("session_id") or ""), _rect(ref.get("board_rect") or []), leds, det,
                  None if not fs else (int(fs[0]), int(fs[1])), str(ref.get("created") or ""),
                  fiducials, _poly(ref.get("board_poly")),
                  _poly(ref.get("board_search_poly")))


def load(path: Optional[str] = None) -> LedMap:
    with open(path or led_map_path(), encoding="utf-8") as f:
        return from_dict(json.load(f))


def save(m: LedMap, path: Optional[str] = None) -> str:
    p = path or led_map_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m.to_dict(), f, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


def board_rect_around(points: Sequence[Tuple[float, float]], margin_px: int,
                      frame_size: Optional[Tuple[int, int]] = None) -> Tuple[int, int, int, int]:
    """The search rectangle for a set of LED positions: their bounding box
    widened by ``margin_px`` on every side, clipped to the frame."""
    if not points:
        raise ValueError("no points to build a board_rect from")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = int(min(xs)) - margin_px, int(min(ys)) - margin_px
    x1, y1 = int(max(xs)) + 1 + margin_px, int(max(ys)) + 1 + margin_px
    if frame_size:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(frame_size[0], x1), min(frame_size[1], y1)
    return x0, y0, x1, y1


# ------------------------------------------------------------ ROI geometry (v2)
# Pure Python (no numpy) so the core stays shell-free (ADR-0083 / test_architecture);
# the vision layer calls these to build/apply the board ROI from the fiducials.

def convex_hull(points: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """The convex hull of a point set (Andrew's monotone chain), as its vertices.
    Returns the input (de-duplicated, sorted) when there are fewer than 3 points."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def poly_bbox(poly: Sequence[Tuple[float, float]]) -> Tuple[int, int, int, int]:
    """The integer bounding box ``(x0, y0, x1, y1)`` (half-open) of a polygon -
    the fast crop the vision layer searches before masking to the polygon."""
    if not poly:
        raise ValueError("empty polygon")
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return int(min(xs)), int(min(ys)), int(max(xs)) + 1, int(max(ys)) + 1


def point_in_poly(pt: Tuple[float, float], poly: Sequence[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test (True when ``pt`` is inside ``poly``)."""
    x, y = pt
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def board_poly_around(points: Sequence[Tuple[float, float]], margin_px: int,
                      frame_size: Optional[Tuple[int, int]] = None
                      ) -> List[Tuple[float, float]]:
    """The board ROI outline for a set of fiducials: their convex hull, grown
    outward by ``margin_px`` (each vertex pushed away from the hull centroid),
    clipped to the frame. Falls back to the rectangle when the hull is degenerate
    (fewer than 3 non-collinear points)."""
    if not points:
        raise ValueError("no points to build a board_poly from")
    hull = convex_hull(points)
    if len(hull) < 3:
        x0, y0, x1, y1 = board_rect_around(points, margin_px, frame_size)
        return [(float(x0), float(y0)), (float(x1), float(y0)),
                (float(x1), float(y1)), (float(x0), float(y1))]
    cx = sum(p[0] for p in hull) / len(hull)
    cy = sum(p[1] for p in hull) / len(hull)
    out: List[Tuple[float, float]] = []
    for x, y in hull:
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy) or 1.0
        nx, ny = x + dx / d * margin_px, y + dy / d * margin_px
        if frame_size:
            nx = min(max(0.0, nx), float(frame_size[0]))
            ny = min(max(0.0, ny), float(frame_size[1]))
        out.append((round(nx, 2), round(ny, 2)))
    return out


def led_id_for(channel: Optional[str], polarity: Optional[str], taken: Sequence[str],
               blob: int) -> str:
    """A readable, stable id: the channel's line plus a letter for the second
    LED on the same channel (``S3_a``, ``S3_b``, ``SOL2.1_a``); unbound LEDs
    keep their blob number (``blob_11``)."""
    if channel is None:
        return f"blob_{blob}"
    base = channel.split("=")[0]
    for suffix in "abcdefgh":
        cand = f"{base}_{suffix}"
        if cand not in taken:
            return cand
    return f"{base}_{blob}"
