"""
dlc_best - one rat's track from DeepLabCut's per-frame candidates
(docs/decisions/0229).

DeepLabCut's multi-animal model proposes, on each video frame, a few candidate
skeletons ("assemblies"): for each body part an x, a y and a confidence score.
Its own tracker then links them into tracks over time, and in our videos that
linking throws away about half the frames where the rat was found. With one rat
in the maze there is nothing to tell apart, so here each frame simply keeps the
candidate with the highest total score.

Pure: plain lists in, plain lists / text out (the worker converts DeepLabCut's
arrays with ``.tolist()``).
"""

from __future__ import annotations

import csv
import io
import math
from typing import Dict, List, Optional, Sequence

Point = Sequence[float]          # x, y, score[, identity]
Candidate = Sequence[Point]      # one point per body part, in config order


def _score(p: Point) -> float:
    """A body part's score, 0 when DeepLabCut marked it missing (negative / NaN)."""
    s = float(p[2])
    return s if s > 0 and not math.isnan(s) else 0.0


def candidate_score(cand: Candidate) -> float:
    return sum(_score(p) for p in cand)


def best_candidate(cands: Sequence[Candidate]) -> Optional[int]:
    """Index of the highest-scoring candidate (the first on a tie), or None
    when no candidate has any body part found."""
    best, best_score = None, 0.0
    for i, c in enumerate(cands):
        s = candidate_score(c)
        if s > best_score:
            best, best_score = i, s
    return best


def frame_row(cands: Sequence[Candidate], n_parts: int) -> List[Optional[float]]:
    """x, y, likelihood for each body part of the best candidate; None (a
    blank cell) for a part it did not find and for a frame with no candidate."""
    i = best_candidate(cands)
    row: List[Optional[float]] = [None] * (3 * n_parts)
    if i is None:
        return row
    for j, p in enumerate(cands[i][:n_parts]):
        if _score(p) > 0:
            row[3 * j:3 * j + 3] = [float(p[0]), float(p[1]), float(p[2])]
    return row


def track(assemblies: Dict[int, Sequence[Candidate]], n_frames: int, n_parts: int
          ) -> List[List[Optional[float]]]:
    """One row per video frame 0..n_frames-1 (frames absent from
    ``assemblies`` are blank)."""
    return [frame_row(assemblies.get(f, ()), n_parts) for f in range(n_frames)]


def found_frames(rows: Sequence[Sequence[Optional[float]]]) -> int:
    return sum(1 for r in rows if any(v is not None for v in r))


def dlc_csv(scorer: str, individual: str, bodyparts: Sequence[str],
            rows: Sequence[Sequence[Optional[float]]]) -> str:
    """DeepLabCut's standard multi-animal CSV: four header rows (scorer,
    individuals, bodyparts, coords), then ``frame, x, y, likelihood, ...``."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    n = len(bodyparts)
    w.writerow(["scorer"] + [scorer] * (3 * n))
    w.writerow(["individuals"] + [individual] * (3 * n))
    w.writerow(["bodyparts"] + [b for b in bodyparts for _ in range(3)])
    w.writerow(["coords"] + ["x", "y", "likelihood"] * n)
    for f, r in enumerate(rows):
        w.writerow([f] + ["" if v is None else repr(round(v, 4)) for v in r])
    return out.getvalue()
