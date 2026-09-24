"""led_event_map.py - find each channel's LED(s) from its serial events.

The pixel side of the LED-map builder's second pass (docs/decisions/0196). The
frame weights come from ``tmaze.core.sync.event_frames``; here they are applied:

1. **Composites** (one video read): per channel, ``sum(weight x grey frame)`` =
   its average "during the event minus just before" image. Anything steady at
   that moment cancels, so the channel's own LED(s) are the spots that remain.
2. **Localisation** (``localise_all``): per channel, the strongest local maxima
   of the smoothed absolute composite that clear the robust noise floor by
   ``snr_min``, up to the number of LEDs the board layout expects. Channels are
   handled STRONGEST FIRST and each claims its LEDs, so a later (weaker) channel
   whose events co-fire a bright reward LED cannot take that reward LED's pixel.
3. **Bleed** (``bleed_matrix``): how much each LED's disk-minus-ring brightness
   (the same measure the per-session trace sampler uses) moves when ANOTHER
   channel fires, as a fraction of how much it moves for its own channel.

Depends on ``numpy`` and ``cv2`` (the vision layer).
"""

from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

from tmaze.core.sync import event_frames as EF
from tmaze.core.sync.led_map import DetectorParams
from tmaze.vision import led_detector as D

ProgressFn = Callable[[int], None]


def progress_printer(label: str, total: int, stream=None, step_pct: int = 5) -> ProgressFn:
    """A per-frame callback that prints ``label: NN% (k/total, Ts)`` to stderr
    every ``step_pct`` percent - the map build reads a multi-minute video three
    times, so it says how far along it is."""
    out = stream or sys.stderr
    t0 = time.time()
    state = {"next": 0}
    total = max(1, int(total))

    def tick(k: int) -> None:
        pct = int(100 * (k + 1) / total)
        if pct >= state["next"] or k + 1 == total:
            out.write(f"\r  {label}: {pct:3d}% ({k + 1}/{total}, {time.time() - t0:.0f}s)")
            if k + 1 >= total:
                out.write("\n")
            out.flush()
            state["next"] = pct + step_pct
    return tick


def _grey(frame: np.ndarray) -> np.ndarray:
    return frame.astype(np.float32).mean(axis=2) if frame.ndim == 3 else frame.astype(np.float32)


def accumulate(frames, roles: Dict[int, List[Tuple[str, float]]], shape: Tuple[int, int],
               progress: Optional[ProgressFn] = None) -> Dict[str, np.ndarray]:
    """``frames`` yields ``(k, frame)``; returns channel -> composite (grey levels)."""
    comp: Dict[str, np.ndarray] = {}
    for k, fr in frames:
        for ch, w in roles.get(k, ()):
            acc = comp.get(ch)
            if acc is None:
                acc = comp[ch] = np.zeros(shape, np.float64)
            acc += w * _grey(fr)
        if progress:
            progress(k)
    return {ch: a.astype(np.float32) for ch, a in comp.items()}


def composites_from_video(path: str, crop: Optional[Tuple[int, int, int, int]], start: int,
                          n: int, roles: Dict[int, List[Tuple[str, float]]],
                          shape: Tuple[int, int],
                          progress: Optional[ProgressFn] = None) -> Dict[str, np.ndarray]:
    return accumulate(D.iter_frames(path, start, n, crop), roles, shape, progress)


@dataclass
class Peak:
    """One localised LED of a channel (crop coordinates)."""
    channel: str
    x: float
    y: float
    magnitude: float    # smoothed |during - before| at the peak (grey levels)
    sign: float         # +1: the LED gets brighter during the event, -1: darker
    snr: float          # magnitude / the composite's robust noise floor


def _smooth(a: np.ndarray) -> np.ndarray:
    if cv2 is not None:
        return cv2.blur(a.astype(np.float32), (3, 3))
    return D._box3(a)  # pragma: no cover


def _disk(shape: Tuple[int, int], pts: Sequence[Tuple[float, float]], r: float) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    out = np.zeros(shape, bool)
    for x, y in pts:
        out |= (xx - x) ** 2 + (yy - y) ** 2 <= r * r
    return out


def _robust_noise(vals: np.ndarray) -> float:
    if vals.size == 0:
        return 1e-6
    return float(1.4826 * np.median(np.abs(vals - np.median(vals)))) + 1e-6


def _centroid(mag: np.ndarray, x: int, y: int) -> Tuple[float, float]:
    h, w = mag.shape
    y0, y1, x0, x1 = max(0, y - 1), min(h, y + 2), max(0, x - 1), min(w, x + 2)
    patch = mag[y0:y1, x0:x1]
    s = float(patch.sum())
    if s <= 0:
        return float(x), float(y)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    return float((patch * xx).sum() / s), float((patch * yy).sum() / s)


def find_peaks(comp: np.ndarray, n_max: int, inside: np.ndarray, blocked: np.ndarray,
               p: EF.EventMapParams, channel: str = "") -> Tuple[List[Peak], float]:
    """Up to ``n_max`` LEDs of one composite, brightest first, plus its noise floor.

    Candidates are local maxima (7x7) of the 3x3-smoothed ``|comp|`` inside
    ``inside`` and outside ``blocked``, at least ``snr_min`` noise units high;
    after the first, each must reach ``rel_min`` of the first and sit
    ``min_sep_px`` from the ones already taken."""
    sm = _smooth(comp)
    mag = np.abs(sm)
    usable = inside & ~blocked
    noise = _robust_noise(mag[usable])
    mag = np.where(usable, mag, 0.0).astype(np.float32)
    if cv2 is not None:
        local_max = cv2.dilate(mag, np.ones((7, 7), np.uint8))
    else:  # pragma: no cover
        local_max = D._dilate(mag, 3)
    ys, xs = np.nonzero((mag >= local_max - 1e-6) & (mag >= p.snr_min * noise) & usable)
    order = np.argsort(-mag[ys, xs], kind="stable")
    out: List[Peak] = []
    for j in order:
        if len(out) >= n_max:
            break
        x, y = int(xs[j]), int(ys[j])
        m = float(mag[y, x])
        if out and m < p.rel_min * out[0].magnitude:
            break
        cx, cy = _centroid(mag, x, y)
        if any(math.hypot(cx - q.x, cy - q.y) < p.min_sep_px for q in out):
            continue
        out.append(Peak(channel, cx, cy, m, 1.0 if sm[y, x] >= 0 else -1.0, m / noise))
    return out, noise


def localise_all(comps: Dict[str, np.ndarray], expected: Dict[str, int], inside: np.ndarray,
                 p: EF.EventMapParams) -> Dict[str, List[Peak]]:
    """Every channel's LEDs, strongest channel first; each claimed LED blocks
    ``claim_px`` (``reward_claim_px`` for a reward LED) around it for the
    channels that come after. ``inside`` is the board search region mask."""
    shape = inside.shape
    none = np.zeros(shape, bool)
    first = {ch: find_peaks(c, 1, inside, none, p, ch)[0] for ch, c in comps.items()}
    order = sorted(comps, key=lambda ch: -(first[ch][0].snr if first[ch] else 0.0))
    blocked = np.zeros(shape, bool)
    out: Dict[str, List[Peak]] = {}
    for ch in order:
        peaks, _ = find_peaks(comps[ch], expected.get(ch, 1), inside, blocked, p, ch)
        out[ch] = peaks
        r = p.reward_claim_px if EF.function_of(ch) == EF.REWARD_KIND else p.claim_px
        blocked |= _disk(shape, [(q.x, q.y) for q in peaks], r)
    return out


def disk_ring_values(image: np.ndarray, points: Sequence[Tuple[float, float]],
                     p: DetectorParams) -> np.ndarray:
    """The trace sampler's disk-minus-ring value of a (composite) image at each
    point - linear, so on a during-minus-before composite it is exactly the
    average change of that LED's trace."""
    blobs = [D.Blob(i, float(x), float(y), 0, 0.0, D.ON) for i, (x, y) in enumerate(points)]
    grey, _ = D.TraceSampler(image.shape[:2], blobs, p).sample(image.astype(np.float32))
    return grey


def bleed_matrix(comps: Dict[str, np.ndarray], leds: Sequence[Tuple[str, str, Tuple[float, float]]],
                 p: DetectorParams, min_frac: float = 0.05) -> Dict[str, Dict[str, float]]:
    """``bleed[source][led_id]``: the change of that LED's trace when ``source``
    fires, as a fraction of its change for its OWN channel. ``leds`` is
    ``[(led_id, own_channel, (x, y)), ...]`` in crop coordinates. Only fractions
    with ``|f| >= min_frac`` are kept; a source's own LEDs are left out."""
    if not leds:
        return {}
    pts = [xy for _, _, xy in leds]
    vals = {ch: disk_ring_values(c, pts, p) for ch, c in comps.items()}
    out: Dict[str, Dict[str, float]] = {}
    for src, v in vals.items():
        row: Dict[str, float] = {}
        for i, (led_id, own, _) in enumerate(leds):
            if own == src or own not in vals:
                continue
            base = float(vals[own][i])
            if abs(base) < 1e-6:
                continue
            f = float(v[i]) / base
            if abs(f) >= min_frac:
                row[led_id] = f
        if row:
            out[src] = row
    return out
