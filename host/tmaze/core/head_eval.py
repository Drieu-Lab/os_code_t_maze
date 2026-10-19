"""
head_eval.py - pure helpers for scoring a tracked point against DeepLabCut labels.

Used by ``tmaze.tools.head_point_eval`` to judge candidate "head point"
estimators (the blob centre of mass, the skeleton rules in
``tmaze.vision.head_point``, learned mappings) on the hand-labelled DeepLabCut
frames. Stdlib only, so it is headless-testable.

DeepLabCut's ``CollectedData_<scorer>.csv`` has four header rows (scorer,
individuals, bodyparts, coords) and one row per labelled image whose first three
cells are ``labeled-data, <video name>, <image name>``. The T-maze project is set
up as multi-animal with three individuals (600/601/602) but each video holds one
rat, so for each body part the first individual with a value is used.

The scoring target is the HEAD CENTRE: the mean of whichever of nose / right
ear / left ear are labelled. A point is on the WRONG END when it is closer to
``tail_base`` than to the neck (or ``mid_spine_neck`` when the neck is unlabelled).
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

Point = Tuple[float, float]

HEAD_PARTS = ("nose", "right_ear", "left_ear")
NECK_PARTS = ("neck", "mid_spine_neck")


def frame_index_of(image_name: str) -> Optional[int]:
    """``img01902.png`` -> 1902 (DeepLabCut names extracted frames by index)."""
    m = re.search(r"(\d+)", image_name)
    return int(m.group(1)) if m else None


def read_collected_data(path: str) -> Dict[str, Dict[str, Point]]:
    """``{image_name: {bodypart: (x, y)}}`` from a DeepLabCut label CSV."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 4:
        return {}
    bodyparts, coords = rows[2], rows[3]
    out: Dict[str, Dict[str, Point]] = {}
    for row in rows[4:]:
        if len(row) < 3:
            continue
        parts: Dict[str, Point] = {}
        xs: Dict[str, float] = {}
        for j in range(3, min(len(row), len(bodyparts))):
            cell = row[j].strip()
            if not cell:
                continue
            part, axis = bodyparts[j], coords[j]
            if part in parts:
                continue  # an earlier individual already gave this part
            if axis == "x":
                xs[part] = float(cell)
            elif axis == "y" and part in xs:
                parts[part] = (xs[part], float(cell))
        out[row[2]] = parts
    return out


def mean_point(points: Iterable[Optional[Point]]) -> Optional[Point]:
    pts = [p for p in points if p is not None]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def head_centre(parts: Mapping[str, Point]) -> Optional[Point]:
    return mean_point(parts.get(p) for p in HEAD_PARTS)


def neck_point(parts: Mapping[str, Point]) -> Optional[Point]:
    for p in NECK_PARTS:
        if p in parts:
            return parts[p]
    return None


def dist(a: Optional[Point], b: Optional[Point]) -> Optional[float]:
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


def wrong_end(pt: Optional[Point], parts: Mapping[str, Point]) -> Optional[bool]:
    """True if ``pt`` is nearer the tail base than the neck (None if unknown)."""
    d_tail = dist(pt, parts.get("tail_base"))
    d_neck = dist(pt, neck_point(parts))
    if d_tail is None or d_neck is None:
        return None
    return d_tail < d_neck


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile, q in [0, 1]."""
    v = sorted(values)
    if not v:
        return None
    pos = q * (len(v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


@dataclass
class Score:
    """Per-frame outcome of one candidate on one labelled image."""
    candidate: str
    session: str
    frame: int
    point: Optional[Point]
    valid: bool
    d_head: Optional[float]
    d_neck: Optional[float]
    wrong_end: Optional[bool]


def score_point(candidate: str, session: str, frame: int, pt: Optional[Point],
                valid: bool, parts: Mapping[str, Point]) -> Score:
    return Score(candidate, session, frame, pt, valid,
                 dist(pt, head_centre(parts)), dist(pt, neck_point(parts)),
                 wrong_end(pt, parts))


@dataclass
class Summary:
    candidate: str
    n: int = 0
    n_point: int = 0
    valid_frac: float = 0.0
    head_median: Optional[float] = None
    head_p90: Optional[float] = None
    neck_median: Optional[float] = None
    wrong_end_frac: Optional[float] = None
    per_group: Dict[str, Optional[float]] = field(default_factory=dict)


def summarise(scores: Sequence[Score], group_of=lambda s: s.session) -> List[Summary]:
    """One Summary per candidate (in first-seen order); ``per_group`` holds the
    median head distance per group (session by default)."""
    order: List[str] = []
    by: Dict[str, List[Score]] = {}
    for s in scores:
        if s.candidate not in by:
            order.append(s.candidate)
            by[s.candidate] = []
        by[s.candidate].append(s)
    out: List[Summary] = []
    for name in order:
        ss = by[name]
        heads = [s.d_head for s in ss if s.d_head is not None]
        necks = [s.d_neck for s in ss if s.d_neck is not None]
        ends = [s.wrong_end for s in ss if s.wrong_end is not None]
        groups: Dict[str, List[float]] = {}
        for s in ss:
            if s.d_head is not None:
                groups.setdefault(group_of(s), []).append(s.d_head)
        out.append(Summary(
            candidate=name, n=len(ss),
            n_point=sum(1 for s in ss if s.point is not None),
            valid_frac=sum(1 for s in ss if s.valid) / len(ss) if ss else 0.0,
            head_median=percentile(heads, 0.5), head_p90=percentile(heads, 0.9),
            neck_median=percentile(necks, 0.5),
            wrong_end_frac=(sum(ends) / len(ends)) if ends else None,
            per_group={g: percentile(v, 0.5) for g, v in sorted(groups.items())}))
    return out


def format_table(summaries: Sequence[Summary], px_to_cm: Optional[float] = None) -> str:
    """Plain-text table of summaries (distances in px, plus cm when a scale is given)."""
    def f(v, nd=1):
        return "-" if v is None else f"{v:.{nd}f}"

    head = (f"{'candidate':34s} {'n':>4s} {'valid':>6s} {'head med':>9s} "
            f"{'head p90':>9s} {'neck med':>9s} {'wrong end':>10s}")
    if px_to_cm:
        head += f" {'head med cm':>12s}"
    lines = [head, "-" * len(head)]
    for s in summaries:
        line = (f"{s.candidate:34s} {s.n:4d} {100 * s.valid_frac:5.0f}% "
                f"{f(s.head_median):>9s} {f(s.head_p90):>9s} {f(s.neck_median):>9s} "
                f"{'-' if s.wrong_end_frac is None else f'{100 * s.wrong_end_frac:.0f}%':>10s}")
        if px_to_cm:
            line += f" {f(None if s.head_median is None else s.head_median * px_to_cm):>12s}"
        lines.append(line)
    return "\n".join(lines)
