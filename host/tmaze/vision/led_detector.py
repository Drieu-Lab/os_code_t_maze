"""led_detector.py - read the board's indicator LEDs out of a recorded video.

The pixel side of the cross-stream sync (design doc section 3.2 / 3.4; FW-0045).
It finds the LEDs that blink, turns each into a per-frame brightness trace, and
registers the LED pattern (the *constellation*) against the one stored in
``led_map.json`` so a board or camera that moved a little still yields the
right ROIs. Everything downstream - traces to spans, matching, the clock fit -
is stdlib and lives in ``tmaze.core.sync``; this module never touches serial
data and knows nothing about channels.

Three stages, each a pure NumPy function so it is testable on synthetic frames;
the only OpenCV I/O is ``iter_frames`` (and ``cv2.blur``/``dilate`` for speed):

1. **Extremes** (pass 1 over the frames): a running max and min plus a sampled
   median of the board crop. An LED that lights up shows in ``max - median``;
   one that goes dark shows in ``median - min``; everything static cancels.
2. **Blobs**: LEDs are LOCAL MAXIMA of those difference images (``find_blobs``),
   not connected components - the modules' channel LEDs sit 8-12 px apart and
   bloom into each other. Peaks closer than ``nms_radius`` are one LED. The
   ``noise_floor`` (the difference images away from the blobs) is reported so
   a future change of video compression that lifts it is caught.
3. **Traces** (pass 2): per frame, per LED, the mean of a small disk minus the
   mean of a background ring around it (``TraceSampler``), in grey and in
   red-minus-green. The ring cancels room-light and exposure wobble.

**Registration** (``register``): the observed blob centroids vs the stored
constellation, as a similarity transform (rotation + translation + a little
scale - the board is flat and the camera fixed). With ~11 points, brute force
over every pair of correspondences is cheap; the best hypothesis is refined by
least squares on its inliers. Ties between distinct transforms (a row of four
identical LEDs shifted by one pitch looks like no shift when one LED is silent)
are reported as ``ambiguous`` rather than guessed - time labels from the
bindings break them (section 3.4 step 4).

Depends on ``numpy`` and ``cv2`` (the vision layer, like ``tracker.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - the pure functions below still work
    cv2 = None  # type: ignore

from tmaze.core.sync import pipeline, spans
from tmaze.core.sync.led_map import DetectorParams, GREEN, RED

ON, OFF, BOTH = "on", "off", "both"


# ------------------------------------------------------------------ extremes

class Extremes:
    """Running max / min and a sampled median of frames (BGR uint8, same shape)."""

    def __init__(self, sample_every: int = 10) -> None:
        self.sample_every = max(1, int(sample_every))
        self.max: Optional[np.ndarray] = None
        self.min: Optional[np.ndarray] = None
        self._samples: List[np.ndarray] = []
        self.n = 0

    def add(self, frame: np.ndarray) -> None:
        if self.max is None:
            self.max, self.min = frame.copy(), frame.copy()
        else:
            np.maximum(self.max, frame, out=self.max)
            np.minimum(self.min, frame, out=self.min)
        if self.n % self.sample_every == 0:
            self._samples.append(frame.copy())
        self.n += 1

    def median(self) -> np.ndarray:
        if not self._samples:
            raise ValueError("no frames added")
        return np.median(np.stack(self._samples), axis=0).astype(np.uint8)


def _grey(im: np.ndarray) -> np.ndarray:
    return im.astype(np.float32).mean(axis=2) if im.ndim == 3 else im.astype(np.float32)


def difference_images(ext: Extremes) -> Tuple[np.ndarray, np.ndarray]:
    """(bright, dark): how far above the median a pixel ever went, and how far
    below. Grey levels (0-255), float32."""
    med = _grey(ext.median())
    return _grey(ext.max) - med, med - _grey(ext.min)


# --------------------------------------------------------------------- blobs

@dataclass
class Blob:
    """One blinking LED in the analysed image (crop coordinates)."""
    id: int
    x: float
    y: float
    area: int          # pixels above threshold in the peak's patch (bloom size)
    peak: float        # largest brightness change seen
    direction: str     # ON (lights up), OFF (goes dark) or BOTH


def find_blobs(diff: np.ndarray, thresh: float, min_area: int = 2, nms_radius: int = 3,
               direction: str = ON, start_id: int = 0) -> List[Blob]:
    """Blinking LEDs as local maxima of a difference image.

    Light 3x3 blur; keep pixels that are the maximum within a
    ``(2*nms_radius+1)`` window and at least ``thresh``; refine each to the
    intensity-weighted centroid of its patch; drop duplicates on a plateau.
    """
    diff = np.asarray(diff, np.float32)
    if cv2 is not None:
        sm = cv2.blur(diff, (3, 3))
        k = 2 * nms_radius + 1
        local_max = cv2.dilate(sm, np.ones((k, k), np.uint8))
    else:  # pragma: no cover - slow NumPy fallback
        sm = _box3(diff)
        local_max = _dilate(sm, nms_radius)
    peaks = np.argwhere((sm >= local_max - 1e-6) & (sm >= thresh))
    h, w = diff.shape
    out: List[Blob] = []
    for py, px in peaks:
        y0, y1 = max(0, py - nms_radius), min(h, py + nms_radius + 1)
        x0, x1 = max(0, px - nms_radius), min(w, px + nms_radius + 1)
        patch = diff[y0:y1, x0:x1]
        wts = np.clip(patch - thresh * 0.5, 0, None)
        area = int((patch >= thresh).sum())
        if area < min_area or wts.sum() <= 0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx = float((wts * xx).sum() / wts.sum())
        cy = float((wts * yy).sum() / wts.sum())
        if any((b.x - cx) ** 2 + (b.y - cy) ** 2 < nms_radius ** 2 for b in out):
            continue
        out.append(Blob(start_id + len(out), cx, cy, area, float(patch.max()), direction))
    return out


def merge_blobs(on: Sequence[Blob], off: Sequence[Blob], radius: float) -> List[Blob]:
    """One LED can show in both difference images; merge those within ``radius``.
    Ids are renumbered 0..n-1 in order of (y, x) so they are stable run to run."""
    merged: List[Blob] = []
    for b in list(on) + list(off):
        dup = next((m for m in merged if (m.x - b.x) ** 2 + (m.y - b.y) ** 2 <= radius ** 2), None)
        if dup is None:
            merged.append(Blob(0, b.x, b.y, b.area, b.peak, b.direction))
        else:
            dup.direction = BOTH if dup.direction != b.direction else dup.direction
            dup.peak = max(dup.peak, b.peak)
            dup.area = max(dup.area, b.area)
    merged.sort(key=lambda b: (round(b.y), b.x))
    for i, b in enumerate(merged):
        b.id = i
    return merged


def detect_blobs(ext: Extremes, p: DetectorParams) -> Tuple[List[Blob], float]:
    """Blobs from the accumulated extremes, plus the noise floor: the 99.9th
    percentile of the difference images with the blobs' patches masked out.
    A floor approaching ``p.blob_thresh`` means the threshold no longer
    separates LEDs from compression noise."""
    bright, dark = difference_images(ext)
    on = find_blobs(bright, p.blob_thresh, p.min_area, p.nms_radius, ON)
    off = find_blobs(dark, p.blob_thresh, p.min_area, p.nms_radius, OFF)
    blobs = merge_blobs(on, off, float(p.radius))
    return blobs, noise_floor(bright, dark, blobs, p.nms_radius + 2)


def noise_floor(bright: np.ndarray, dark: np.ndarray, blobs: Sequence[Blob], radius: int) -> float:
    h, w = bright.shape
    yy, xx = np.mgrid[0:h, 0:w]
    keep = np.ones((h, w), bool)
    for b in blobs:
        keep &= (xx - b.x) ** 2 + (yy - b.y) ** 2 > radius * radius
    vals = np.concatenate([bright[keep], dark[keep]])
    return float(np.percentile(vals, 99.9)) if vals.size else 0.0


# ----------------------------------------------------------------- fiducials

@dataclass
class Fiducial:
    """One persistently-lit colour LED (crop coordinates). Unlike a blinking Blob
    it never changes, so it is invisible to the difference images; it is found in
    the *persistence* images instead (ADR-0153)."""
    id: int
    x: float
    y: float
    color: str          # RED or GREEN
    strength: float     # the persistent colour contrast at its peak


def persistence_images(ext: Extremes, min_frac: float) -> Tuple[np.ndarray, np.ndarray]:
    """(red, green): the persistent red-ness and green-ness of every pixel - the
    ``100*(1-min_frac)``-th percentile over the sampled frames of ``R-max(G,B)``
    and ``G-max(R,B)`` (BGR input). A steady red/green LED scores high in every
    frame so even its low percentile is high; a dot lit only briefly (a hand or
    the rat passing through) is dark most of the time, so its low percentile is
    low and it is rejected. A neutral/white retroreflective marker has R~=G~=B, so
    both scores are ~0 and it is ignored (markers stay a physical QA reference)."""
    if not ext._samples:
        raise ValueError("no sampled frames")
    stack = np.stack(ext._samples).astype(np.float32)   # (n, h, w, 3) BGR
    if stack.ndim != 4 or stack.shape[-1] != 3:
        raise ValueError("persistence needs colour (BGR) frames")
    b, g, r = stack[..., 0], stack[..., 1], stack[..., 2]
    red = r - np.maximum(g, b)
    green = g - np.maximum(r, b)
    q = 100.0 * (1.0 - float(min_frac))
    return np.percentile(red, q, axis=0), np.percentile(green, q, axis=0)


def _merge_fiducials(fids: Sequence[Fiducial], radius: float) -> List[Fiducial]:
    """Drop the weaker of any two fiducials within ``radius`` (a red and a green
    peak at one spot is one LED); keep the stronger colour."""
    out: List[Fiducial] = []
    for f in sorted(fids, key=lambda f: -f.strength):
        if any((o.x - f.x) ** 2 + (o.y - f.y) ** 2 <= radius * radius for o in out):
            continue
        out.append(f)
    return out


def detect_fiducials(ext: Extremes, p: DetectorParams,
                     exclude: Sequence[Blob] = ()) -> List[Fiducial]:
    """The persistently-lit red/green LEDs, as local maxima of the persistence
    images. A true fiducial never changes, so any persistence peak that coincides
    (within ``nms_radius``) with a blinking blob in ``exclude`` is dropped - that
    is a *channel* LED that merely rests lit (a beam/lick LED is on ~95% of the
    time), not a fiducial. Ids are renumbered top-to-bottom (round(y), x) so they
    are stable run to run."""
    red, green = persistence_images(ext, p.fid_min_frac)
    found: List[Fiducial] = []
    for color, img in ((RED, red), (GREEN, green)):
        for b in find_blobs(img, p.fid_thresh, p.min_area, p.nms_radius, ON):
            found.append(Fiducial(0, b.x, b.y, color, b.peak))
    r2 = float(p.nms_radius) ** 2
    if exclude:
        found = [f for f in found
                 if not any((f.x - e.x) ** 2 + (f.y - e.y) ** 2 <= r2 for e in exclude)]
    merged = _merge_fiducials(found, float(p.radius))
    merged.sort(key=lambda f: (round(f.y), f.x))
    for i, f in enumerate(merged):
        f.id = i
    return merged


# -------------------------------------------------------------------- traces

class TraceSampler:
    """Per-frame disk-minus-ring brightness for a fixed set of blobs.

    ``sample(frame)`` returns two arrays of length n_blobs: grey and
    red-minus-green (the latter separates a red LED from a white reflection on
    a colour camera). Disks of other blobs are excluded from every ring so a
    neighbour's flash does not leak into the background estimate.
    """

    def __init__(self, shape: Tuple[int, int], blobs: Sequence[Blob], p: DetectorParams) -> None:
        h, w = shape
        yy, xx = np.mgrid[0:h, 0:w]
        disks = [(xx - b.x) ** 2 + (yy - b.y) ** 2 <= p.radius ** 2 for b in blobs]
        all_disks = np.zeros((h, w), bool)
        for d in disks:
            all_disks |= d
        self._disk_idx = [np.flatnonzero(d) for d in disks]
        self._ring_idx = []
        for b in blobs:
            rr = (xx - b.x) ** 2 + (yy - b.y) ** 2
            ring = (rr > p.ring_in ** 2) & (rr <= p.ring_out ** 2) & ~all_disks
            self._ring_idx.append(np.flatnonzero(ring))
        self.n_blobs = len(blobs)

    def sample(self, frame: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        c = frame.astype(np.float32)
        g = c.mean(axis=2).ravel() if c.ndim == 3 else c.ravel()
        rg = (c[:, :, 2] - c[:, :, 1]).ravel() if c.ndim == 3 else np.zeros_like(g)
        grey = np.empty(self.n_blobs, np.float32)
        rmg = np.empty(self.n_blobs, np.float32)
        for j, (d, r) in enumerate(zip(self._disk_idx, self._ring_idx)):
            bg_g = g[r].mean() if r.size else 0.0
            bg_r = rg[r].mean() if r.size else 0.0
            grey[j] = g[d].mean() - bg_g
            rmg[j] = rg[d].mean() - bg_r
        return grey, rmg


def blob_spans(blob_id: int, trace: Sequence[float], frames: Sequence[spans.Frame],
               clk_unit_s: Optional[float]) -> pipeline.BlobSpans:
    """One LED's trace -> its lit and dark spans (the core's hysteresis)."""
    rising, falling, _ = spans.hysteresis_edges([float(v) for v in trace])
    ch = f"blob{blob_id}"
    return pipeline.BlobSpans(
        blob_id,
        spans.spans_from_edges(rising, falling, frames, ch, lit=True, clk_unit_s=clk_unit_s),
        spans.spans_from_edges(rising, falling, frames, ch, lit=False, clk_unit_s=clk_unit_s))


# ---------------------------------------------------------------- video I/O

def iter_frames(path: str, start: int = 0, n: Optional[int] = None,
                crop: Optional[Tuple[int, int, int, int]] = None) -> Iterator[Tuple[int, np.ndarray]]:
    """Yield ``(k, frame)`` for frames ``start .. start+n-1`` of a video, cropped
    to ``crop = (x0, y0, x1, y1)`` when given. ``k`` counts from 0."""
    if cv2 is None:
        raise RuntimeError("OpenCV (cv2) is required to read video")
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {path}")
    try:
        if start > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        k = 0
        while n is None or k < n:
            ok, frame = cap.read()
            if not ok:
                break
            if crop is not None:
                x0, y0, x1, y1 = crop
                frame = frame[y0:y1, x0:x1]
            yield k, frame
            k += 1
    finally:
        cap.release()


def polygon_mask(h: int, w: int, poly: Sequence[Tuple[float, float]]) -> np.ndarray:
    """Boolean ``(h, w)`` mask, True for pixels INSIDE ``poly`` (crop coords).

    Vectorised ray-casting so it needs no OpenCV (the crop is small and the
    polygon has a handful of vertices). Mirrors ``led_map.point_in_poly`` per
    pixel; used to blank everything outside the operator-drawn board search
    region before detection (see docs/decisions/0195)."""
    ys, xs = np.mgrid[0:h, 0:w]
    inside = np.zeros((h, w), bool)
    n = len(poly)
    if n < 3:
        return inside
    j = n - 1
    for i in range(n):
        xi, yi = float(poly[i][0]), float(poly[i][1])
        xj, yj = float(poly[j][0]), float(poly[j][1])
        denom = (yj - yi) or 1e-9
        cond = ((yi > ys) != (yj > ys)) & (xs < (xj - xi) * (ys - yi) / denom + xi)
        inside ^= cond
        j = i
    return inside


def mask_extremes(ext: Extremes, poly: Optional[Sequence[Tuple[float, float]]]) -> None:
    """Blank every pixel OUTSIDE ``poly`` (crop coords) in the accumulated
    max/min and the sampled frames, so the difference + persistence images AND
    the noise floor see only what is inside the board search region. In-place;
    a no-op when ``poly`` has fewer than 3 vertices. See docs/decisions/0195."""
    if ext.max is None or poly is None or len(poly) < 3:
        return
    h, w = ext.max.shape[:2]
    outside = ~polygon_mask(h, w, poly)
    ext.max[outside] = 0
    ext.min[outside] = 0
    for s in ext._samples:
        s[outside] = 0


def accumulate_extremes(path: str, crop: Optional[Tuple[int, int, int, int]],
                        start: int, n: Optional[int], p: DetectorParams,
                        poly: Optional[Sequence[Tuple[float, float]]] = None,
                        progress: Optional[Callable[[int], None]] = None) -> Extremes:
    """Pass 1: read the window and accumulate the running max/min/median samples.
    Shared by the blink detector, the fiducial detector, and the trace sampler so
    one video read serves all three. ``poly`` (crop-relative vertices), when
    given, blanks everything outside it so a rat/hand crossing a corner of the
    crop rectangle can't inflate the noise floor or spawn blobs (ADR-0195).
    ``progress(k)``, when given, is called after each frame."""
    ext = Extremes(p.sample_every)
    for k, fr in iter_frames(path, start, n, crop):
        ext.add(fr)
        if progress:
            progress(k)
    if ext.n == 0:
        raise RuntimeError("no frames read")
    mask_extremes(ext, poly)
    return ext


def sample_traces(path: str, crop: Optional[Tuple[int, int, int, int]], start: int,
                  n: int, blobs: Sequence[Blob], shape: Tuple[int, int],
                  p: DetectorParams,
                  progress: Optional[Callable[[int], None]] = None
                  ) -> Tuple[np.ndarray, np.ndarray]:
    """Pass 2: per-frame disk-minus-ring traces for a given blob list (which may
    mix detected blobs with synthetic points at pose-transformed map positions -
    the hybrid path). Returns (grey, rmg), each (n, n_blobs)."""
    grey = np.full((n, len(blobs)), np.nan, np.float32)
    rmg = np.full((n, len(blobs)), np.nan, np.float32)
    if blobs:
        sampler = TraceSampler(shape, blobs, p)
        for k, fr in iter_frames(path, start, n, crop):
            grey[k], rmg[k] = sampler.sample(fr)
            if progress:
                progress(k)
    return grey, rmg


def scan_video(path: str, crop: Optional[Tuple[int, int, int, int]], start: int, n: Optional[int],
               p: DetectorParams) -> Tuple[List[Blob], float, np.ndarray, np.ndarray, int]:
    """Pass 1 + pass 2 over a video window. Returns (blobs, noise_floor,
    grey_traces, rmg_traces, n_frames_read); traces are (n_frames, n_blobs) and
    blob positions are in crop coordinates (add the crop origin for full-frame)."""
    ext = accumulate_extremes(path, crop, start, n, p)
    blobs, floor = detect_blobs(ext, p)
    grey, rmg = sample_traces(path, crop, start, ext.n, blobs, ext.max.shape[:2], p)
    return blobs, floor, grey, rmg, ext.n


# -------------------------------------------------------------- registration

@dataclass(frozen=True)
class Similarity:
    """x' = s * R(theta) * x + t, the 2-D rotation + uniform scale + shift."""
    scale: float = 1.0
    theta: float = 0.0          # radians, counter-clockwise in image coordinates
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, xy: np.ndarray) -> np.ndarray:
        c, s = math.cos(self.theta) * self.scale, math.sin(self.theta) * self.scale
        xy = np.asarray(xy, float).reshape(-1, 2)
        return np.column_stack([c * xy[:, 0] - s * xy[:, 1] + self.tx,
                                s * xy[:, 0] + c * xy[:, 1] + self.ty])

    def displacement(self, xy: np.ndarray) -> float:
        """Mean distance the transform moves the given points - 'how far from
        identity', used to prefer the smallest board shift among tied fits."""
        xy = np.asarray(xy, float).reshape(-1, 2)
        return float(np.linalg.norm(self.apply(xy) - xy, axis=1).mean()) if len(xy) else 0.0

    def to_dict(self) -> dict:
        return {"scale": self.scale, "rotation_deg": math.degrees(self.theta),
                "tx": self.tx, "ty": self.ty}

    @staticmethod
    def from_two_pairs(p0, p1, q0, q1) -> Optional["Similarity"]:
        """The similarity taking p0->q0 and p1->q1 exactly."""
        dp = np.subtract(p1, p0)
        dq = np.subtract(q1, q0)
        lp = float(np.hypot(*dp))
        if lp < 1e-9:
            return None
        scale = float(np.hypot(*dq)) / lp
        theta = math.atan2(dq[1], dq[0]) - math.atan2(dp[1], dp[0])
        c, s = math.cos(theta) * scale, math.sin(theta) * scale
        tx = q0[0] - (c * p0[0] - s * p0[1])
        ty = q0[1] - (s * p0[0] + c * p0[1])
        return Similarity(scale, theta, float(tx), float(ty))

    @staticmethod
    def fit(src: np.ndarray, dst: np.ndarray) -> "Similarity":
        """Least-squares similarity src -> dst (Umeyama / Procrustes, 2-D)."""
        src = np.asarray(src, float).reshape(-1, 2)
        dst = np.asarray(dst, float).reshape(-1, 2)
        if len(src) < 2:
            raise ValueError("need at least two point pairs")
        ms, md = src.mean(axis=0), dst.mean(axis=0)
        a, b = src - ms, dst - md
        # complex-number form: find z = s*e^{i theta} minimising |z*a - b|^2
        za = a[:, 0] + 1j * a[:, 1]
        zb = b[:, 0] + 1j * b[:, 1]
        denom = float((np.abs(za) ** 2).sum())
        if denom < 1e-12:
            raise ValueError("degenerate source points")
        z = (np.conj(za) * zb).sum() / denom
        scale, theta = abs(z), math.atan2(z.imag, z.real)
        c, s = math.cos(theta) * scale, math.sin(theta) * scale
        tx = md[0] - (c * ms[0] - s * ms[1])
        ty = md[1] - (s * ms[0] + c * ms[1])
        return Similarity(float(scale), float(theta), float(tx), float(ty))


REG_OK, REG_AMBIGUOUS, REG_FAILED = "ok", "ambiguous", "registration_failed"


@dataclass
class Registration:
    """Where the stored constellation sits in this session's image.

    pairs         - (reference index, observed index) inliers.
    rms_px        - root-mean-square distance between transformed reference
                    points and their observed blobs.
    unmatched_reference - stored LEDs with no blob near them (silent, or moved).
    unmatched_observed  - blobs that are not in the map (new, or the map moved).
    alternatives  - other transforms that explained as many points (ambiguity).
    """
    transform: Similarity
    pairs: List[Tuple[int, int]]
    rms_px: float
    n_reference: int
    n_observed: int
    verdict: str
    unmatched_reference: List[int] = field(default_factory=list)
    unmatched_observed: List[int] = field(default_factory=list)
    alternatives: List[Similarity] = field(default_factory=list)

    @property
    def n_inliers(self) -> int:
        return len(self.pairs)

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "transform": self.transform.to_dict(),
                "n_inliers": self.n_inliers, "n_reference": self.n_reference,
                "n_observed": self.n_observed, "rms_px": self.rms_px,
                "pairs": [list(p) for p in self.pairs],
                "unmatched_reference": self.unmatched_reference,
                "unmatched_observed": self.unmatched_observed,
                "n_alternatives": len(self.alternatives)}


def _assign(pred: np.ndarray, obs: np.ndarray, tol: float,
            ref_labels: Optional[Sequence] = None,
            obs_labels: Optional[Sequence] = None) -> List[Tuple[int, int]]:
    """Greedy one-to-one nearest pairing of predicted reference points to
    observed blobs, closest pairs first, within ``tol`` px. When labels are given
    (e.g. fiducial colour), only same-label points may pair."""
    if len(pred) == 0 or len(obs) == 0:
        return []
    d = np.linalg.norm(pred[:, None, :] - obs[None, :, :], axis=2)
    order = np.argsort(d, axis=None)
    used_r: set = set()
    used_o: set = set()
    pairs: List[Tuple[int, int]] = []
    for flat in order:
        r, o = divmod(int(flat), d.shape[1])
        if d[r, o] > tol:
            break
        if r in used_r or o in used_o:
            continue
        if ref_labels is not None and obs_labels is not None and ref_labels[r] != obs_labels[o]:
            continue
        used_r.add(r)
        used_o.add(o)
        pairs.append((r, o))
    return sorted(pairs)


def _rms(t: Similarity, ref: np.ndarray, obs: np.ndarray, pairs: Sequence[Tuple[int, int]]) -> float:
    if not pairs:
        return float("inf")
    r = np.array([p[0] for p in pairs])
    o = np.array([p[1] for p in pairs])
    return float(np.sqrt(((t.apply(ref[r]) - obs[o]) ** 2).sum(axis=1).mean()))


def register(observed: Sequence[Tuple[float, float]], reference: Sequence[Tuple[float, float]],
             tol_px: float = 2.0, scale_range: Tuple[float, float] = (0.8, 1.25),
             max_rotation_deg: float = 20.0, min_inliers: int = 4,
             obs_labels: Optional[Sequence] = None,
             ref_labels: Optional[Sequence] = None) -> Registration:
    """Fit the stored constellation (``reference``) onto this session's blob
    centroids (``observed``) with a similarity transform.

    Brute force: every pair of reference points against every ordered pair of
    observed points (11 LEDs: ~6,000 hypotheses) proposes a transform; the one
    that puts the most reference points within ``tol_px`` of an observed blob
    wins and is refined by least squares on its inliers. Hypotheses outside
    ``scale_range`` / ``max_rotation_deg`` are skipped - the board does not
    turn upside down between sessions. If several *different* transforms tie
    on inlier count the result is ``ambiguous`` (the one closest to identity
    is returned, the others listed); fewer than ``min_inliers`` or a large
    residual is ``registration_failed``.

    ``ref_labels``/``obs_labels`` (e.g. fiducial colour) restrict correspondences
    to same-label points - this both speeds the search and breaks the symmetry of
    an otherwise identical row (a red/green pattern that a plain shift would map
    onto the wrong colour is rejected).
    """
    obs = np.asarray(observed, float).reshape(-1, 2)
    ref = np.asarray(reference, float).reshape(-1, 2)
    n_o, n_r = len(obs), len(ref)
    fail = Registration(Similarity(), [], float("inf"), n_r, n_o, REG_FAILED,
                        list(range(n_r)), list(range(n_o)))
    if n_o < 2 or n_r < 2:
        return fail
    labelled = ref_labels is not None and obs_labels is not None
    max_rot = math.radians(max_rotation_deg)
    best: List[Tuple[int, float, Similarity, List[Tuple[int, int]]]] = []
    best_n = 0
    for a in range(n_r):
        for b in range(a + 1, n_r):
            d_ref = float(np.hypot(*(ref[b] - ref[a])))
            if d_ref < 1e-6:
                continue
            for i in range(n_o):
                for j in range(n_o):
                    if i == j:
                        continue
                    if labelled and (ref_labels[a] != obs_labels[i]
                                     or ref_labels[b] != obs_labels[j]):
                        continue
                    d_obs = float(np.hypot(*(obs[j] - obs[i])))
                    s = d_obs / d_ref
                    if not (scale_range[0] <= s <= scale_range[1]):
                        continue
                    t = Similarity.from_two_pairs(ref[a], ref[b], obs[i], obs[j])
                    if t is None or abs(math.remainder(t.theta, 2 * math.pi)) > max_rot:
                        continue
                    pairs = _assign(t.apply(ref), obs, tol_px, ref_labels, obs_labels)
                    if len(pairs) < best_n or len(pairs) < 2:
                        continue
                    if len(pairs) > best_n:
                        best, best_n = [], len(pairs)
                    best.append((len(pairs), _rms(t, ref, obs, pairs), t, pairs))
    if not best:
        return fail
    # refine each tied hypothesis on its inliers, then group distinct transforms
    refined: List[Tuple[Similarity, List[Tuple[int, int]], float]] = []
    for _, _, t, pairs in best:
        r = np.array([p[0] for p in pairs])
        o = np.array([p[1] for p in pairs])
        t2 = Similarity.fit(ref[r], obs[o]) if len(pairs) >= 2 else t
        pairs2 = _assign(t2.apply(ref), obs, tol_px, ref_labels, obs_labels)
        if len(pairs2) < len(pairs):
            t2, pairs2 = t, pairs
        refined.append((t2, pairs2, _rms(t2, ref, obs, pairs2)))
    top_n = max(len(p) for _, p, _ in refined)
    refined = [x for x in refined if len(x[1]) == top_n]
    distinct: List[Tuple[Similarity, List[Tuple[int, int]], float]] = []
    for t, pairs, rms in refined:
        if not any(np.linalg.norm(t.apply(ref) - u.apply(ref), axis=1).max() <= tol_px
                   for u, _, _ in distinct):
            distinct.append((t, pairs, rms))
    distinct.sort(key=lambda x: (x[0].displacement(ref), x[2]))
    t, pairs, rms = distinct[0]
    matched_r = {p[0] for p in pairs}
    matched_o = {p[1] for p in pairs}
    verdict = REG_OK
    if len(distinct) > 1:
        verdict = REG_AMBIGUOUS
    if len(pairs) < min_inliers or rms > tol_px:
        verdict = REG_FAILED
    return Registration(t, pairs, rms, n_r, n_o, verdict,
                        [i for i in range(n_r) if i not in matched_r],
                        [i for i in range(n_o) if i not in matched_o],
                        [u for u, _, _ in distinct[1:]])


# -------------------------------------------------------- hybrid + overlay

def hybrid_blobs(expected: Sequence[Tuple[float, float]], detected: Sequence[Blob],
                 tol_px: float) -> List[Tuple[Blob, Optional[int], bool]]:
    """Combine detected blinking blobs with pose-transformed map positions.

    ``expected`` are the stored LED positions transformed into this session's crop
    coordinates (parallel to the map's LED order); ``detected`` are the blinking
    blobs actually found. Expected LEDs and detected blobs within ``tol_px`` are
    paired closest pair first, each at most once, and a paired expected LED uses
    its blob (``is_detected`` True); otherwise a synthetic blob is placed at the
    expected position so it is still traced (the hybrid fallback, ADR-0153).
    Closest-first matters when channel LEDs sit about ``tol_px`` apart (a
    neighbouring row's LED, ~4 px): pairing in map order let an LED take its
    neighbour's blob (docs/decisions/0196). Detected blobs matching no expected
    LED are appended (``exp_index`` None) for reporting. Returns
    ``(blob, exp_index, is_detected)`` with ids renumbered."""
    det = list(detected)
    cands = sorted(((b.x - ex) ** 2 + (b.y - ey) ** 2, ei, j)
                   for ei, (ex, ey) in enumerate(expected) for j, b in enumerate(det))
    pair: dict = {}
    used: set = set()
    for dd, ei, j in cands:
        if dd > tol_px * tol_px:
            break
        if ei in pair or j in used:
            continue
        pair[ei] = j
        used.add(j)
    out: List[Tuple[Blob, Optional[int], bool]] = []
    for ei, (ex, ey) in enumerate(expected):
        if ei in pair:
            out.append((det[pair[ei]], ei, True))
        else:
            out.append((Blob(0, float(ex), float(ey), 0, 0.0, BOTH), ei, False))
    for j, b in enumerate(det):
        if j not in used:
            out.append((b, None, False))
    for i, (b, _ei, _is) in enumerate(out):
        b.id = i
    return out


def draw_overlay(canvas: np.ndarray, board_poly: Optional[Sequence[Tuple[float, float]]] = None,
                 fiducials: Sequence[Fiducial] = (), blobs: Sequence[Blob] = (),
                 labels: Optional[dict] = None) -> np.ndarray:
    """Annotate a copy of ``canvas`` (BGR or grey uint8) for a manual QA check: the
    ROI polygon (yellow), fiducials (red/green rings), blinking LEDs (white crosses
    + optional label). All coordinates must be in the canvas's own pixel space, so
    the caller subtracts the crop origin first. You eyeball whether the ROI lands
    inside the physical retroreflective marker corners (ADR-0153)."""
    if cv2 is None:
        raise RuntimeError("cv2 is required to draw the overlay")
    img = np.ascontiguousarray(canvas)
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img.copy()
    if board_poly:
        pts = np.array([[int(round(x)), int(round(y))] for x, y in board_poly], np.int32)
        cv2.polylines(img, [pts], True, (0, 255, 255), 1)
    for f in fiducials:
        colour = (0, 0, 255) if f.color == RED else (0, 255, 0)
        cv2.circle(img, (int(round(f.x)), int(round(f.y))), 4, colour, 1)
    for b in blobs:
        px, py = int(round(b.x)), int(round(b.y))
        cv2.drawMarker(img, (px, py), (255, 255, 255), cv2.MARKER_CROSS, 6, 1)
        lab = labels.get(b.id) if labels else None
        if lab:
            cv2.putText(img, str(lab), (px + 4, py - 4),
                        cv2.FONT_HERSHEY_PLAIN, 0.7, (255, 255, 255), 1)
    return img


# ------------------------------------------------------------- NumPy fallbacks

def _box3(a: np.ndarray) -> np.ndarray:  # pragma: no cover
    p = np.pad(a, 1, mode="edge")
    return sum(p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)) / 9.0


def _dilate(a: np.ndarray, r: int) -> np.ndarray:  # pragma: no cover
    p = np.pad(a, r, mode="edge")
    out = a.copy()
    for i in range(2 * r + 1):
        for j in range(2 * r + 1):
            np.maximum(out, p[i:i + a.shape[0], j:j + a.shape[1]], out=out)
    return out
