"""board_layout.py - how many indicator LEDs each serial channel drives.

``host/led_board_layout.json`` is a hand-written description of the LED board
(docs/decisions/0196): each beam (``S0``-``S5``) and lick (``L0``-``L2``) sensor
lights TWO LEDs, one in each of two rows (one row is dimmer than the other), and
each reward valve (``SOL..``) lights one. The map builder uses it to know how
many LEDs to look for per channel and warns when it finds fewer.

Naming of the two LEDs of a channel: ``_a`` is the one in the BRIGHT row,
``_b`` the one in the DIM row (``S3_a``, ``S3_b``). Every bright/dim pair on the
board is offset by about the same vector (the row spacing), so
:func:`row_offset_outliers` flags a channel whose pair does not match the rest -
usually a neighbour's LED picked by mistake.

Stdlib-only (json); no pixels are touched here.
"""

from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.core.sync.spans import channel_kind
from tmaze.paths import host_dir

SCHEMA_VERSION = 1
LAYOUT_FILENAME = "led_board_layout.json"
SOURCE_DEFAULT = "default (no layout file: 1 LED per channel)"

Point = Tuple[float, float]


def layout_path() -> str:
    """Default location: ``host/led_board_layout.json``."""
    return os.path.join(str(host_dir()), LAYOUT_FILENAME)


@dataclass(frozen=True)
class BoardLayout:
    """LEDs per channel: ``per_channel`` (``"S0": 2``) wins over ``per_kind``
    (``"S": 2``); anything unlisted has one LED. ``exclusive_groups`` (group name
    -> channel bases) lists the one-active groups the per-session sync enforces
    (docs/decisions/0201); none listed = the rule is off."""
    per_kind: Dict[str, int] = field(default_factory=dict)
    per_channel: Dict[str, int] = field(default_factory=dict)
    source: str = SOURCE_DEFAULT
    exclusive_groups: Dict[str, List[str]] = field(default_factory=dict)

    def expected(self, channel: str) -> int:
        base = channel_base(channel)
        if base in self.per_channel:
            return self.per_channel[base]
        return self.per_kind.get(channel_kind(base), 1)


def channel_base(channel: str) -> str:
    """The channel without its wire state: ``S3=1`` -> ``S3``; ``SOL2.1`` unchanged."""
    return channel.split("=")[0]


def _counts(d: object, what: str) -> Dict[str, int]:
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError(f"{what} must be an object of name -> LED count, got {d!r}")
    out: Dict[str, int] = {}
    for k, v in d.items():
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"{what}[{k!r}] must be a whole number >= 0, got {v!r}")
        out[str(k)] = v
    return out


def _groups(d: object) -> Dict[str, List[str]]:
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError(f"exclusive_groups must be an object of name -> channel list, got {d!r}")
    out: Dict[str, List[str]] = {}
    seen: Dict[str, str] = {}
    for name, chans in d.items():
        if not isinstance(chans, list) or not all(isinstance(c, str) and c for c in chans):
            raise ValueError(f"exclusive_groups[{name!r}] must be a list of channel names, "
                             f"got {chans!r}")
        for c in chans:
            if c in seen:
                raise ValueError(f"channel {c!r} is in both exclusive_groups "
                                 f"{seen[c]!r} and {name!r}")
            seen[c] = str(name)
        out[str(name)] = list(chans)
    return out


def from_dict(d: dict, source: str = "") -> BoardLayout:
    if d.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"led_board_layout schema_version must be {SCHEMA_VERSION}, "
                         f"got {d.get('schema_version')!r}")
    return BoardLayout(_counts(d.get("leds_per_kind"), "leds_per_kind"),
                       _counts(d.get("leds_per_channel"), "leds_per_channel"),
                       source or "led_board_layout.json",
                       _groups(d.get("exclusive_groups")))


def load(path: Optional[str] = None) -> BoardLayout:
    """The layout at ``path`` (default ``host/led_board_layout.json``); the
    one-LED-per-channel default when that file does not exist."""
    p = path or layout_path()
    if not os.path.isfile(p):
        return BoardLayout()
    with open(p, encoding="utf-8") as f:
        return from_dict(json.load(f), p)


def row_offset(pairs: Dict[str, Tuple[Point, Point]]) -> Optional[Point]:
    """The board's bright-row -> dim-row vector: the per-axis median of
    ``b - a`` over the channels' ``(a, b)`` pairs; None without any pair."""
    if not pairs:
        return None
    dx = [b[0] - a[0] for a, b in pairs.values()]
    dy = [b[1] - a[1] for a, b in pairs.values()]
    return statistics.median(dx), statistics.median(dy)


def row_offset_outliers(pairs: Dict[str, Tuple[Point, Point]], offset: Point,
                        tol_px: float = 3.0) -> List[str]:
    """Channels whose ``b - a`` is more than ``tol_px`` from ``offset``, sorted."""
    out = []
    for ch, (a, b) in pairs.items():
        if math.hypot(b[0] - a[0] - offset[0], b[1] - a[1] - offset[1]) > tol_px:
            out.append(ch)
    return sorted(out)


def near(p: Point, points: Sequence[Point], radius: float) -> bool:
    """Is ``p`` within ``radius`` px of any of ``points``?"""
    return any(math.hypot(p[0] - q[0], p[1] - q[1]) <= radius for q in points)
