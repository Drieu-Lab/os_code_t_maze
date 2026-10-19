"""
head_point.py - estimate the head end of a tracked rat blob from its shape.

The blob tracker (tracker.py) reports the blob's centre of mass (CoM). Because
the dark head and front body of a hooded rat on the black rig floor are often
missing from the foreground mask, the CoM sits on the hindquarters - ~75 px
(~17 cm) from the nose on the hand-labelled DeepLabCut frames. This module
finds a point at the HEAD end of the blob instead.

Method (a *medial axis* - the blob's centre line, with the radius of the largest
circle that fits inside the blob at each point; see docs/glossary.md):

  1. Distance transform of the blob mask -> circle radius at every pixel.
  2. Thin the largest connected piece of the blob to a 1-px skeleton and keep
     only its LONGEST path (the "spine"), which drops the side branches legs,
     ears and ragged edges create.
  3. Decide which spine end is the head with one of two rules:
       * ``half_mean`` - split the spine at the point nearest the CoM; the side
         with the larger average radius is the head side (the tail is thin).
       * ``thin_end``  - compare the average radius over the last
         ``end_len_px`` of each end; the thinner end is the tail.
  4. Head point, two variants:
       * ``head``     - the head-side spine end, pushed outwards along the spine
         direction to the blob edge (the skeleton stops short of the outline);
       * ``head_far`` - the farthest blob pixel (any fragment) along the
         CoM -> head-end direction, within a corridor around the body axis, so
         a detached head fragment can still be reached.

Shape alone picks the wrong end on roughly one labelled frame in five (the dark
hood can be missing and only a stub of tail visible), so HeadTracker also votes
with the direction of travel and frame-to-frame continuity.

estimate_head is a pure function of a binary mask + a point: no tracker state, no Tk. Coordinates
are in the mask's own pixel grid (the tracker's proc resolution); the caller
maps them to full-frame pixels. Needs OpenCV with the contrib ``ximgproc``
module (``cv2.ximgproc.thinning``), which the drieu_t_maze env has.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - depends on host environment
    cv2 = None  # type: ignore
    np = None  # type: ignore

RULE_HALF_MEAN = "half_mean"
RULE_THIN_END = "thin_end"
RULES = (RULE_HALF_MEAN, RULE_THIN_END)

# 8-connected neighbour offsets for walking the skeleton.
_NEIGHBOURS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


@dataclass
class HeadPointParams:
    """Tuning for estimate_head. Pixel values are in the mask's grid (the
    tracker's proc resolution, 720 px wide by default)."""
    end_len_px: int = 15        # spine length compared at each end (thin_end)
    min_spine_px: int = 12      # shorter spine -> blob too round/small to orient
    min_margin: float = 0.15    # relative radius difference needed to call it valid
    tangent_px: int = 6         # spine length used for the end direction
    corridor_px: float = 20.0   # half-width of the body-axis corridor (head_far)


@dataclass
class HeadEstimate:
    """Result of estimate_head. Points are (x, y) in the mask's pixel grid."""
    valid: bool
    rule: str
    head: Optional[Tuple[float, float]] = None      # spine end extended to the edge
    head_far: Optional[Tuple[float, float]] = None  # farthest pixel along the axis
    tail: Optional[Tuple[float, float]] = None      # tail-side spine end
    margin: float = 0.0          # relative radius difference behind the decision
    spine_len: int = 0           # spine length in skeleton pixels
    spine: Optional["np.ndarray"] = None   # (N, 2) x, y, ordered tail -> head
    radii: Optional["np.ndarray"] = None   # (N,) circle radius along the spine
    confidence: float = 0.0      # 0..1 strength of the head/tail decision
    votes: Optional[dict] = None # HeadTracker only: the signed shape/motion/continuity votes


def blob_mask_from_contours(contours: Sequence["np.ndarray"],
                            shape: Tuple[int, int]) -> "np.ndarray":
    """Filled binary mask (uint8 0/255) of one blob's contours at ``shape`` (h, w)."""
    m = np.zeros(shape, np.uint8)
    cs = [c for c in contours if c is not None]
    if cs:
        cv2.drawContours(m, cs, -1, 255, thickness=cv2.FILLED)
    return m


def spine_mask(light_mask: "np.ndarray", contours: Sequence["np.ndarray"],
               grow_px: int = 6) -> "np.ndarray":
    """The blob as seen in a more lightly cleaned foreground mask.

    The tracker's own mask is cleaned with a morphological OPEN, which erodes the
    thin tail and the patchy front body. This keeps the tracker's chosen blob
    (its filled contours) plus any ``light_mask`` pixels within ``grow_px`` of
    it, so the spine can use parts the open step removed without picking up
    unrelated specks elsewhere in the frame."""
    blob = blob_mask_from_contours(contours, light_mask.shape[:2])
    if grow_px > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow_px + 1, 2 * grow_px + 1))
        near = cv2.dilate(blob, k)
    else:
        near = blob
    return cv2.bitwise_or(blob, cv2.bitwise_and(light_mask, near))


BRIDGE_GAP_PX = 15.0   # pieces farther than this from the main piece are left out
BRIDGE_MIN_AREA = 15   # smaller pieces are specks, not body parts
BRIDGE_WIDTH_PX = 3    # thickness of the line joining a piece to the main one


def _bridged_body(mask: "np.ndarray") -> "np.ndarray":
    """The largest connected piece of ``mask`` with the nearby pieces joined on.

    The hooded rat's blob often splits in two across its dark middle (rear
    body + tail in one piece, front body in another). A spine on the largest
    piece alone stops at the split, whose tapered edge then looks like a tail.
    Each other piece of at least BRIDGE_MIN_AREA px whose nearest point lies
    within BRIDGE_GAP_PX of the growing body is joined to it by a thin line
    between their closest points, nearest piece first."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 2:
        return mask
    areas = stats[1:, cv2.CC_STAT_AREA]
    biggest = 1 + int(np.argmax(areas))
    body = np.where(labels == biggest, 255, 0).astype(np.uint8)
    others = [i for i in range(1, n) if i != biggest and stats[i, cv2.CC_STAT_AREA]
              >= BRIDGE_MIN_AREA]

    def outline(m):
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        return np.vstack([c.reshape(-1, 2) for c in cs]).astype(np.float64)

    pieces = {i: outline(np.where(labels == i, 255, 0).astype(np.uint8)) for i in others}
    while pieces:
        body_pts = outline(body)
        best = None
        for i, pts in pieces.items():
            d2 = ((pts[:, None, :] - body_pts[None, :, :]) ** 2).sum(-1)
            k = int(np.argmin(d2))
            gap = float(np.sqrt(d2.flat[k]))
            if best is None or gap < best[0]:
                a, b = divmod(k, d2.shape[1])
                best = (gap, i, pts[a], body_pts[b])
        gap, i, p, q = best
        if gap > BRIDGE_GAP_PX:
            break
        body[labels == i] = 255
        cv2.line(body, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])), 255, BRIDGE_WIDTH_PX)
        del pieces[i]
    return body


def _bfs(start: Tuple[int, int], pixels: set):
    """Breadth-first walk over skeleton pixels from ``start``.
    Returns (farthest pixel, parent map)."""
    parent = {start: None}
    queue = deque([start])
    last = start
    while queue:
        p = queue.popleft()
        last = p
        for dx, dy in _NEIGHBOURS:
            q = (p[0] + dx, p[1] + dy)
            if q in pixels and q not in parent:
                parent[q] = p
                queue.append(q)
    return last, parent


def longest_skeleton_path(body: "np.ndarray") -> List[Tuple[int, int]]:
    """Longest path (list of (x, y)) through the skeleton of a binary mask.

    Two breadth-first walks: from any pixel to the farthest one (an end of the
    longest path), then from there to the farthest again. Exact for a tree,
    which a thinned blob nearly always is; a skeleton loop (blob with a hole)
    still yields a long path, just not necessarily the longest."""
    skel = cv2.ximgproc.thinning(body, thinningType=cv2.ximgproc.THINNING_ZHANGSUEN)
    ys, xs = np.nonzero(skel)
    if len(xs) == 0:
        return []
    pixels = set(zip(xs.tolist(), ys.tolist()))
    end_a, _ = _bfs((int(xs[0]), int(ys[0])), pixels)
    end_b, parent = _bfs(end_a, pixels)
    path = []
    p = end_b
    while p is not None:
        path.append(p)
        p = parent[p]
    return path  # end_b ... end_a


def _decide_half_mean(path_xy: "np.ndarray", radii: "np.ndarray", com) -> Tuple[bool, float, bool]:
    """(head_is_last_end, margin, usable). Split at the spine point nearest the CoM."""
    d2 = (path_xy[:, 0] - com[0]) ** 2 + (path_xy[:, 1] - com[1]) ** 2
    k = int(np.argmin(d2))
    first, last = radii[:k], radii[k + 1:]
    if len(first) < 2 or len(last) < 2:
        return True, 0.0, False  # CoM at a spine end: no two sides to compare
    m_first, m_last = float(first.mean()), float(last.mean())
    big = max(m_first, m_last)
    margin = abs(m_first - m_last) / big if big > 0 else 0.0
    return m_last > m_first, margin, True


def _decide_thin_end(radii: "np.ndarray", end_len: int) -> Tuple[bool, float, bool]:
    """(head_is_last_end, margin, usable). Compare the two end segments."""
    n = min(end_len, len(radii) // 3)
    if n < 2:
        return True, 0.0, False
    m_first, m_last = float(radii[:n].mean()), float(radii[-n:].mean())
    big = max(m_first, m_last)
    margin = abs(m_first - m_last) / big if big > 0 else 0.0
    return m_last > m_first, margin, True


def _extend_to_edge(mask: "np.ndarray", start, direction, max_px: float):
    """Walk from ``start`` along unit ``direction`` while inside ``mask``;
    return the last inside point (sub-pixel steps of 0.5 px)."""
    h, w = mask.shape[:2]
    x, y = float(start[0]), float(start[1])
    best = (x, y)
    steps = int(max_px * 2)
    for i in range(1, steps + 1):
        px = start[0] + direction[0] * i * 0.5
        py = start[1] + direction[1] * i * 0.5
        xi, yi = int(round(px)), int(round(py))
        if not (0 <= xi < w and 0 <= yi < h) or mask[yi, xi] == 0:
            break
        best = (px, py)
    return best


def _farthest_along(mask: "np.ndarray", origin, direction, corridor: float):
    """Mean position of the mask pixels within 2 px of the largest projection on
    ``direction`` from ``origin``, among pixels within ``corridor`` of the axis."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    dx = xs - origin[0]
    dy = ys - origin[1]
    along = dx * direction[0] + dy * direction[1]
    across = np.abs(-dx * direction[1] + dy * direction[0])
    keep = (across <= corridor) & (along > 0)
    if not np.any(keep):
        return None
    a = along[keep]
    top = a >= a.max() - 2.0
    return (float(xs[keep][top].mean()), float(ys[keep][top].mean()))


@dataclass
class Spine:
    """A blob's spine, in a padded crop of the input mask (``offset`` = the crop's
    top-left corner in the input grid). ``path[0]`` and ``path[-1]`` are its two
    ends, in no particular head/tail order."""
    crop: "np.ndarray"
    offset: Tuple[int, int]
    com: Tuple[float, float]     # CoM in crop coordinates
    path: "np.ndarray"           # (N, 2) x, y in crop coordinates
    radii: "np.ndarray"          # (N,)


def extract_spine(mask: "np.ndarray", com: Tuple[float, float]) -> Optional[Spine]:
    """Spine of a blob mask (None if empty or the skeleton is a single pixel).

    Works in a padded crop around the blob because thinning cost scales with
    area; the spine comes from the largest connected piece with nearby pieces
    joined on (_bridged_body)."""
    if mask is None or not np.any(mask):
        return None
    x0, y0, bw, bh = cv2.boundingRect(cv2.findNonZero(mask))
    pad = 2
    h, w = mask.shape[:2]
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(w, x0 + bw + pad), min(h, y0 + bh + pad)
    crop = np.where(mask[cy0:cy1, cx0:cx1] > 0, 255, 0).astype(np.uint8)
    body = _bridged_body(crop)
    path = longest_skeleton_path(body)
    if len(path) < 2:
        return None
    dist = cv2.distanceTransform(body, cv2.DIST_L2, 5)
    radii = np.array([dist[y, x] for x, y in path], np.float64)
    return Spine(crop, (cx0, cy0), (com[0] - cx0, com[1] - cy0),
                 np.array(path, np.float64), radii)


def shape_vote(spine: Spine, rule: str, params: HeadPointParams) -> Tuple[bool, float, bool]:
    """(head is ``path[-1]``, relative radius margin, usable) from the blob shape."""
    if rule == RULE_HALF_MEAN:
        return _decide_half_mean(spine.path, spine.radii, spine.com)
    return _decide_thin_end(spine.radii, params.end_len_px)


def place_head(spine: Spine, head_last: bool, params: HeadPointParams,
               out: HeadEstimate) -> HeadEstimate:
    """Fill ``out``'s head / head_far / tail / spine for the chosen orientation."""
    path_xy, radii = spine.path, spine.radii
    if not head_last:
        path_xy, radii = path_xy[::-1], radii[::-1]
    crop, com_c = spine.crop, spine.com
    cx0, cy0 = spine.offset

    # Head end + its outward direction from the last tangent_px of the spine.
    head_end = path_xy[-1]
    back = path_xy[max(0, len(path_xy) - 1 - params.tangent_px)]
    v = head_end - back
    norm = math.hypot(v[0], v[1])
    if norm > 0:
        direction = (v[0] / norm, v[1] / norm)
        max_r = float(radii.max()) if len(radii) else 0.0
        head = _extend_to_edge(crop, head_end, direction, max_px=2.0 * max_r + 2.0)
    else:
        head = (float(head_end[0]), float(head_end[1]))

    axis = (head[0] - com_c[0], head[1] - com_c[1])
    anorm = math.hypot(axis[0], axis[1])
    head_far = None
    if anorm > 0:
        head_far = _farthest_along(crop, com_c, (axis[0] / anorm, axis[1] / anorm),
                                   params.corridor_px)

    def shift(pt):
        return None if pt is None else (pt[0] + cx0, pt[1] + cy0)

    out.head = shift(head)
    out.head_far = shift(head_far) if head_far is not None else out.head
    out.tail = shift((float(path_xy[0][0]), float(path_xy[0][1])))
    out.spine = path_xy + np.array([cx0, cy0], np.float64)
    out.radii = radii
    out.spine_len = len(path_xy)
    return out


def estimate_head(mask: "np.ndarray", com: Tuple[float, float],
                  rule: str = RULE_THIN_END,
                  params: Optional[HeadPointParams] = None) -> HeadEstimate:
    """Estimate the head end of one blob from its shape alone (one frame).

    ``mask`` is a binary image (uint8, non-zero = blob) holding only this
    animal's blob - all its fragments; the spine is taken from the largest one.
    ``com`` is the tracker's centre of mass in the same pixel grid. See
    HeadTracker for the version that also uses motion and frame-to-frame
    continuity."""
    if rule not in RULES:
        raise ValueError(f"unknown head rule {rule!r}; expected one of {RULES}")
    p = params or HeadPointParams()
    out = HeadEstimate(valid=False, rule=rule)
    spine = extract_spine(mask, com)
    if spine is None:
        return out
    head_last, margin, usable = shape_vote(spine, rule, p)
    place_head(spine, head_last, p, out)
    out.margin = margin
    out.confidence = margin
    out.valid = bool(usable and out.spine_len >= p.min_spine_px and margin >= p.min_margin)
    return out


@dataclass
class OrientationWeights:
    """How HeadTracker combines its head/tail cues (see HeadTracker)."""
    shape: float = 1.0
    motion: float = 1.5
    shape_full_margin: float = 0.5   # radius margin that counts as a full shape vote
    motion_ref_px: float = 1.5       # CoM speed (px/frame) that counts as a full motion vote
    motion_dead_px: float = 0.5      # CoM speed below this is blob flicker, not walking
    velocity_frames: int = 10        # CoM history used for the speed estimate
    flip_evidence: float = 1.0       # summed opposing evidence needed to flip the held end
    flip_decay: float = 0.95         # per-frame carry-over of that sum (older frames count less)
    min_axis_cos: float = 0.3        # |cos| between body axis and held heading to keep holding
    min_confidence: float = 0.25     # evidence (0..1) needed to call a fresh decision valid


class HeadTracker:
    """Per-frame head estimate that remembers the previous frames.

    Each frame two signed votes ("is ``path[-1]`` the head?", each -1..+1) are
    averaged with OrientationWeights into the frame's EVIDENCE:

      * shape  - the chosen rule's radius margin (the tail end is thin);
      * motion - rats mostly move head-first, so the end the CoM is moving
                 towards, scaled down when the rat is nearly still and zero
                 below ``motion_dead_px`` (a tail tip dropping in and out of
                 the blob shifts the CoM a few px without the rat moving).

    Continuity is hysteresis, not a third vote. The tracker remembers the
    HEADING (unit vector from the CoM towards the head); this frame's spine end
    on that side stays the head while opposing evidence, summed over frames
    with ``flip_decay`` carry-over, stays below ``flip_evidence`` - so a flip
    needs a few frames of agreement, not one noisy frame, while weak but
    steady evidence still gets there (the sum levels off at
    evidence / (1 - flip_decay), so the decay must stay close to 1). The heading, not the
    old end's position, is what is matched because the spine's end point jumps
    around (tail tip, a leg) while its direction does not. When the body axis is
    nearly perpendicular to the heading (|cos| < ``min_axis_cos``, e.g. curled
    up) the frame is decided fresh. (As a vote, continuity would let one early
    wrong choice outvote the motion cue forever.) A held orientation keeps the
    previous frame's validity; a fresh decision is valid when its evidence
    clears ``min_confidence``. One instance per tracked animal; call
    ``reset()`` when the track is lost."""

    def __init__(self, rule: str = RULE_THIN_END, params: Optional[HeadPointParams] = None,
                 weights: Optional[OrientationWeights] = None):
        if rule not in RULES:
            raise ValueError(f"unknown head rule {rule!r}; expected one of {RULES}")
        self.rule = rule
        self.params = params or HeadPointParams()
        self.weights = weights or OrientationWeights()
        self._coms: deque = deque(maxlen=max(2, self.weights.velocity_frames + 1))
        self._heading: Optional[Tuple[float, float]] = None
        self._against = 0.0
        self._prev_valid = False

    def reset(self) -> None:
        self._coms.clear()
        self._heading = None
        self._against = 0.0
        self._prev_valid = False

    def update(self, mask: "np.ndarray", com: Optional[Tuple[float, float]]) -> HeadEstimate:
        out = HeadEstimate(valid=False, rule=self.rule)
        if com is None:
            self.reset()
            return out
        self._coms.append((float(com[0]), float(com[1])))
        spine = extract_spine(mask, com)
        if spine is None:
            return out
        p, w = self.params, self.weights
        ends = (spine.path[0] + spine.offset, spine.path[-1] + spine.offset)

        shape_last, margin, usable = shape_vote(spine, self.rule, p)
        s_shape = 0.0
        if usable and w.shape_full_margin > 0:
            s_shape = (1.0 if shape_last else -1.0) * min(1.0, margin / w.shape_full_margin)

        s_motion = 0.0
        if len(self._coms) >= 2:
            first, last = self._coms[0], self._coms[-1]
            n = len(self._coms) - 1
            vx, vy = (last[0] - first[0]) / n, (last[1] - first[1]) / n
            speed = math.hypot(vx, vy)
            ax, ay = ends[1][0] - ends[0][0], ends[1][1] - ends[0][1]
            alen = math.hypot(ax, ay)
            span = w.motion_ref_px - w.motion_dead_px
            if speed > w.motion_dead_px and alen > 0 and span > 0:
                cos = (vx * ax + vy * ay) / (speed * alen)
                s_motion = cos * min(1.0, (speed - w.motion_dead_px) / span)

        # Continuity: +1 = the held heading points at path[-1], -1 = path[0],
        # 0 = no heading yet, or the body axis is across it.
        s_cont = 0.0
        ax, ay = ends[1][0] - ends[0][0], ends[1][1] - ends[0][1]
        alen = math.hypot(ax, ay)
        if self._heading is not None and alen > 0:
            cos = (ax * self._heading[0] + ay * self._heading[1]) / alen
            if abs(cos) >= w.min_axis_cos:
                s_cont = 1.0 if cos > 0 else -1.0

        wsum = w.shape + w.motion
        evidence = (w.shape * s_shape + w.motion * s_motion) / wsum if wsum > 0 else 0.0
        if s_cont != 0.0:
            self._against = max(0.0, w.flip_decay * self._against - evidence * s_cont)
        else:
            self._against = 0.0
        if s_cont != 0.0 and self._against < w.flip_evidence:
            head_last = s_cont > 0          # hold the remembered heading
            valid = self._prev_valid or abs(evidence) >= w.min_confidence
        else:
            head_last = evidence > 0 if evidence != 0 else shape_last
            valid = abs(evidence) >= w.min_confidence
            self._against = 0.0
        place_head(spine, head_last, p, out)
        out.margin = margin
        out.votes = {"shape": s_shape, "motion": s_motion, "continuity": s_cont}
        out.confidence = abs(evidence)
        out.valid = bool(out.spine_len >= p.min_spine_px and valid)
        if alen > 0:
            sign = 1.0 if head_last else -1.0
            self._heading = (sign * ax / alen, sign * ay / alen)
        self._prev_valid = out.valid
        return out
