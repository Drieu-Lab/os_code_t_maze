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
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.paths import host_dir

SCHEMA_VERSION = 1
LED_MAP_FILENAME = "led_map.json"
_SCHEMA_NOTE = ("tmaze led_map v1 - which board indicator LED mirrors which serial channel, "
                "and where it sits (full-frame px) in the reference session. Written by "
                "`python -m tmaze.tools.sync_fit --map`; see docs/design/cross-stream-sync-design.md "
                "sections 3.1/3.4 and tmaze/core/sync/led_map.py.")

LIT, DARK = "lit", "dark"


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
    """
    blob_thresh: float = 30.0
    min_area: int = 2
    nms_radius: int = 3
    radius: int = 2
    ring_in: int = 5
    ring_out: int = 9
    sample_every: int = 10
    margin_px: int = 24


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


@dataclass
class LedMap:
    session_id: str
    board_rect: Tuple[int, int, int, int]
    leds: List[LedEntry] = field(default_factory=list)
    detector: DetectorParams = field(default_factory=DetectorParams)
    frame_size: Optional[Tuple[int, int]] = None      # (width, height) of the reference frame
    created: str = ""

    def constellation(self) -> List[Tuple[float, float]]:
        """Every LED's reference pixel position, bound or not - the point set
        registration matches each session's blobs against."""
        return [e.ref_px for e in self.leds]

    def by_id(self) -> Dict[str, LedEntry]:
        return {e.led_id: e for e in self.leds}

    def to_dict(self) -> dict:
        return {
            "_schema": _SCHEMA_NOTE,
            "schema_version": SCHEMA_VERSION,
            "reference": {
                "session_id": self.session_id,
                "board_rect": list(self.board_rect),
                "frame_size": list(self.frame_size) if self.frame_size else None,
                "created": self.created,
            },
            "detector": asdict(self.detector),
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


def from_dict(d: dict) -> LedMap:
    """Build a LedMap from parsed JSON, checking the parts a typo would break."""
    ver = d.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise ValueError(f"led_map schema_version {ver!r} is not {SCHEMA_VERSION}")
    ref = d.get("reference") or {}
    det = DetectorParams(**{k: v for k, v in (d.get("detector") or {}).items()
                            if k in DetectorParams.__dataclass_fields__})
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
                  None if not fs else (int(fs[0]), int(fs[1])), str(ref.get("created") or ""))


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
