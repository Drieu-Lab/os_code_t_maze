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

For whole videos the target comes from DeepLabCut's PREDICTIONS instead
(``read_dlc_predictions``: its standard CSV output, parts below a likelihood
threshold dropped), scored frame by frame with ``VideoScorer``, which also
measures how steady a point is (jumps, jitter) and how long wrong-end
stretches last. ``DirectionScorer`` does the same for a head DIRECTION
(neck -> head centre, degrees): error against the predictions, sudden turns,
and jitter.
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


def read_dlc_predictions(path: str, min_likelihood: float = 0.6
                         ) -> Dict[int, Dict[str, Point]]:
    """``{frame: {bodypart: (x, y)}}`` from a DeepLabCut prediction CSV.

    Accepts single-animal (scorer / bodyparts / coords header rows) and
    multi-animal (+ individuals) files; the first column is the frame index.
    Parts with likelihood below ``min_likelihood`` (or blank) are dropped, and
    for each part the first individual that passes is used. Frames with no
    surviving part are left out."""
    out: Dict[int, Dict[str, Point]] = {}
    header: Dict[str, List[str]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            try:
                frame = int(row[0])
            except ValueError:
                header[row[0].strip()] = row
                continue
            bodyparts, coords = header.get("bodyparts"), header.get("coords")
            if bodyparts is None or coords is None:
                raise ValueError(f"{path}: no bodyparts/coords header rows")
            individuals = header.get("individuals", [""] * len(row))
            cells: Dict[Tuple[str, str], Dict[str, float]] = {}
            order: List[Tuple[str, str]] = []
            for j in range(1, min(len(row), len(bodyparts), len(coords))):
                cell = row[j].strip()
                if not cell:
                    continue
                key = (individuals[j] if j < len(individuals) else "", bodyparts[j])
                if key not in cells:
                    cells[key] = {}
                    order.append(key)
                cells[key][coords[j]] = float(cell)
            parts: Dict[str, Point] = {}
            for key in order:
                c = cells[key]
                if key[1] in parts or "x" not in c or "y" not in c:
                    continue
                if c.get("likelihood", 1.0) >= min_likelihood:
                    parts[key[1]] = (c["x"], c["y"])
            if parts:
                out[frame] = parts
    return out


@dataclass
class VideoSummary:
    """One candidate scored over a whole video against DeepLabCut predictions."""
    candidate: str
    frames: int = 0                 # frames seen
    n: int = 0                      # frames with a head-centre target
    n_point: int = 0                # ... where the candidate gave a point
    valid_frac: float = 0.0         # share of target frames flagged valid
    head_median: Optional[float] = None
    head_p90: Optional[float] = None
    wrong_end_frac: Optional[float] = None
    wrong_long_frac: Optional[float] = None   # share of judged frames in long wrong-end runs
    jumps_per_min: Optional[float] = None     # frame-to-frame moves > jump_px
    jitter_still: Optional[float] = None      # median frame-to-frame move while the head is still


class VideoScorer:
    """Streams one candidate's per-frame points and scores them.

    ``add`` must be called in frame order. A *jump* is a move of more than
    ``jump_px`` between consecutive frames (a flip between ends, or a glitch).
    *Jitter* is the median move between consecutive frames on which the target
    head centre itself moved less than ``still_px``. A wrong-end *run* is a
    stretch of consecutive judged frames on the wrong end; runs of at least
    ``long_run_s`` count toward ``wrong_long_frac`` (frames with no verdict
    neither extend nor break a run)."""

    def __init__(self, candidate: str, fps: float, jump_px: float = 40.0,
                 still_px: float = 1.0, long_run_s: float = 1.0):
        self.candidate = candidate
        self.fps = fps
        self.jump_px = jump_px
        self.still_px = still_px
        self.long_run = max(1, int(round(long_run_s * fps)))
        self._frames = 0
        self._dists: List[float] = []
        self._n = 0
        self._n_point = 0
        self._n_valid = 0
        self._judged = 0
        self._wrong = 0
        self._wrong_long = 0
        self._run = 0
        self._jumps = 0
        self._still_steps: List[float] = []
        self._prev: Optional[Tuple[int, Optional[Point], Optional[Point]]] = None

    def _close_run(self) -> None:
        if self._run >= self.long_run:
            self._wrong_long += self._run
        self._run = 0

    def add(self, frame: int, pt: Optional[Point], valid: bool,
            parts: Optional[Mapping[str, Point]]) -> None:
        self._frames += 1
        parts = parts or {}
        target = head_centre(parts)
        if target is not None:
            self._n += 1
            if pt is not None:
                self._n_point += 1
                self._dists.append(dist(pt, target))
            if valid:
                self._n_valid += 1
        w = wrong_end(pt, parts)
        if w is not None:
            self._judged += 1
            if w:
                self._wrong += 1
                self._run += 1
            else:
                self._close_run()
        if self._prev is not None and self._prev[0] == frame - 1:
            step = dist(self._prev[1], pt)
            if step is not None:
                if step > self.jump_px:
                    self._jumps += 1
                moved = dist(self._prev[2], target)
                if moved is not None and moved < self.still_px:
                    self._still_steps.append(step)
        self._prev = (frame, pt, target)

    def summary(self) -> VideoSummary:
        self._close_run()
        minutes = self._frames / self.fps / 60.0 if self.fps > 0 else 0.0
        return VideoSummary(
            candidate=self.candidate, frames=self._frames, n=self._n,
            n_point=self._n_point,
            valid_frac=self._n_valid / self._n if self._n else 0.0,
            head_median=percentile(self._dists, 0.5),
            head_p90=percentile(self._dists, 0.9),
            wrong_end_frac=self._wrong / self._judged if self._judged else None,
            wrong_long_frac=self._wrong_long / self._judged if self._judged else None,
            jumps_per_min=self._jumps / minutes if minutes > 0 else None,
            jitter_still=percentile(self._still_steps, 0.5))


def format_video_table(summaries: Sequence[VideoSummary],
                       px_to_cm: Optional[float] = None) -> str:
    """Plain-text table of whole-video summaries."""
    def f(v, nd=1):
        return "-" if v is None else f"{v:.{nd}f}"

    def pct(v):
        return "-" if v is None else f"{100 * v:.1f}%"

    head = (f"{'candidate':34s} {'target':>7s} {'valid':>6s} {'head med':>9s} "
            f"{'head p90':>9s} {'wrong':>7s} {'wrong>1s':>9s} {'jumps/min':>10s} "
            f"{'jitter':>7s}")
    if px_to_cm:
        head += f" {'head med cm':>12s}"
    lines = [head, "-" * len(head)]
    for s in summaries:
        line = (f"{s.candidate:34s} {s.n:7d} {100 * s.valid_frac:5.0f}% "
                f"{f(s.head_median):>9s} {f(s.head_p90):>9s} {pct(s.wrong_end_frac):>7s} "
                f"{pct(s.wrong_long_frac):>9s} {f(s.jumps_per_min):>10s} "
                f"{f(s.jitter_still, 2):>7s}")
        if px_to_cm:
            line += f" {f(None if s.head_median is None else s.head_median * px_to_cm):>12s}"
        lines.append(line)
    return "\n".join(lines)


def direction_deg(frm: Optional[Point], to: Optional[Point]) -> Optional[float]:
    """Direction from ``frm`` to ``to`` in degrees, image axes (0 = image right,
    90 = image down); None when a point is missing or they coincide."""
    if frm is None or to is None or (frm[0] == to[0] and frm[1] == to[1]):
        return None
    return math.degrees(math.atan2(to[1] - frm[1], to[0] - frm[0]))


def angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two directions, degrees (0..180)."""
    d = (a - b) % 360.0
    return min(d, 360.0 - d)


def head_direction(parts: Mapping[str, Point]) -> Optional[float]:
    """Neck -> head centre direction of a labelled / predicted pose (the rig's
    head direction, see ``tmaze.vision.head_learned.head_direction_deg``)."""
    return direction_deg(parts.get("neck"), head_centre(parts))


@dataclass
class DirectionSummary:
    """One head-direction candidate scored over a whole video."""
    candidate: str
    frames: int = 0
    n: int = 0                          # frames with a target direction and a prediction
    err_median: Optional[float] = None
    err_p90: Optional[float] = None
    over_45: Optional[float] = None     # share of those frames off by more than 45 deg
    turns_per_min: Optional[float] = None   # frame-to-frame changes > turn_deg
    jitter_still: Optional[float] = None    # median frame-to-frame change while the target is steady


class DirectionScorer:
    """Streams one candidate's per-frame head direction (degrees or None) against
    the target direction. ``add`` in frame order. A *turn* is a change of more
    than ``turn_deg`` between consecutive frames - far faster than a rat turns
    its head in 40 ms, so a glitch. *Jitter* is the median change between
    consecutive frames on which the target itself changed less than ``still_deg``."""

    def __init__(self, candidate: str, fps: float, turn_deg: float = 90.0,
                 still_deg: float = 2.0):
        self.candidate = candidate
        self.fps = fps
        self.turn_deg = turn_deg
        self.still_deg = still_deg
        self._frames = 0
        self._errs: List[float] = []
        self._turns = 0
        self._still_steps: List[float] = []
        self._prev: Optional[Tuple[int, Optional[float], Optional[float]]] = None

    def add(self, frame: int, pred: Optional[float], target: Optional[float]) -> None:
        self._frames += 1
        if pred is not None and target is not None:
            self._errs.append(angle_diff(pred, target))
        if self._prev is not None and self._prev[0] == frame - 1 and pred is not None \
                and self._prev[1] is not None:
            step = angle_diff(pred, self._prev[1])
            if step > self.turn_deg:
                self._turns += 1
            if target is not None and self._prev[2] is not None \
                    and angle_diff(target, self._prev[2]) < self.still_deg:
                self._still_steps.append(step)
        self._prev = (frame, pred, target)

    def summary(self) -> DirectionSummary:
        minutes = self._frames / self.fps / 60.0 if self.fps > 0 else 0.0
        n = len(self._errs)
        return DirectionSummary(
            candidate=self.candidate, frames=self._frames, n=n,
            err_median=percentile(self._errs, 0.5), err_p90=percentile(self._errs, 0.9),
            over_45=sum(e > 45.0 for e in self._errs) / n if n else None,
            turns_per_min=self._turns / minutes if minutes > 0 else None,
            jitter_still=percentile(self._still_steps, 0.5))


def format_direction_table(summaries: Sequence[DirectionSummary]) -> str:
    """Plain-text table of whole-video head-direction summaries (degrees)."""
    def f(v, nd=1):
        return "-" if v is None else f"{v:.{nd}f}"

    head = (f"{'direction candidate':34s} {'frames':>7s} {'err med':>8s} {'err p90':>8s} "
            f"{'>45 deg':>8s} {'turns/min':>10s} {'jitter':>7s}")
    lines = [head, "-" * len(head)]
    for s in summaries:
        over = "-" if s.over_45 is None else f"{100 * s.over_45:.1f}%"
        lines.append(f"{s.candidate:34s} {s.n:7d} {f(s.err_median):>8s} {f(s.err_p90):>8s} "
                     f"{over:>8s} {f(s.turns_per_min):>10s} {f(s.jitter_still, 2):>7s}")
    return "\n".join(lines)
