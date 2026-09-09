"""
clockfit.py - the straight line between two hardware clocks.

Given matched pairs (x = firmware ms of a serial edge, y = camera ticks of the
video frame that first shows the LED change), fit

    y = offset[segment] + rate * x

and report how well it holds. Choices, each measured on real footage (design
doc section 3.7):

- **Robust line (Theil-Sen).** The median of the slopes between pairs of
  points, then the median intercept. A dozen mis-paired points among thousands
  do not move it, unlike least squares. With thousands of points the number of
  point-pairs is millions, so the slopes are taken over a seeded random sample
  of at most ``max_pairs`` pairs: deterministic, fast in pure Python, and
  statistically indistinguishable from the full set.
- **Shared rate, one offset per segment.** The firmware clock restarts on a MODE
  switch (ADR-0064) but does not change speed, so slopes are taken only between
  points of the same segment and each segment gets its own offset.
- **One offset per edge polarity.** An LED that turns ON part-way through an
  exposure still clears the threshold in that frame; one that turns OFF still
  leaves enough light to stay above it, so OFF edges land one frame later
  (+32 ms vs +62-69 ms measured). The fit reports the median residual per edge
  polarity so a consumer can correct for it, and uses ON edges for the
  intercept when both are present.
- **Residuals in milliseconds of firmware time**, i.e. (y - prediction) / rate,
  so the numbers are comparable across cameras with different tick rates.
- **Host-clock step check.** The pairs also carry the *host* lag (frame stamp
  minus serial-line stamp). It should sit at a roughly constant ~60 ms; a jump
  between consecutive blocks of pairs means the PC's wall clock stepped (e.g. a
  Windows time resync) and the host-based join is untrustworthy from then on.
  Reported, never silently absorbed.

Stdlib-only; tests in tests/test_sync_core.py.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.core.sync.matching import mad, median

ON, OFF = "on", "off"

VERDICT_OK = "ok"
VERDICT_TOO_FEW = "too_few_events"
VERDICT_HOST_STEP = "host_clock_step"


@dataclass(frozen=True)
class Pair:
    """One matched edge: the same electrical event seen by two clocks."""
    x: float               # firmware ms (serial side)
    y: float               # camera ticks (video side)
    channel: str
    edge: str              # ON: the LED lit up at this edge; OFF: it went dark
    host_lag_s: float      # frame host stamp minus serial host stamp
    host_t: float          # serial host stamp, for ordering the step check
    segment: int = 0


@dataclass(frozen=True)
class ResidualStats:
    n: int
    median_ms: float
    mad_ms: float
    p5_ms: float
    p95_ms: float


@dataclass(frozen=True)
class HostStep:
    """A jump in the host lag between two consecutive blocks of pairs."""
    host_t: float
    delta_s: float


@dataclass(frozen=True)
class ClockFit:
    rate: float                                  # y units per x unit
    offsets: Dict[int, float]                    # per segment
    n_pairs: int
    residuals: ResidualStats
    per_channel: Dict[str, ResidualStats] = field(default_factory=dict)
    per_edge_offset_ms: Dict[str, float] = field(default_factory=dict)   # median residual, ON / OFF
    host_lag_median_s: float = float("nan")
    host_lag_mad_s: float = float("nan")
    host_steps: List[HostStep] = field(default_factory=list)
    verdict: str = VERDICT_OK

    def predict(self, x: float, segment: int = 0) -> float:
        return self.offsets[segment] + self.rate * x

    def to_dict(self) -> dict:
        return {
            "rate": self.rate,
            "offsets": {str(k): v for k, v in self.offsets.items()},
            "n_pairs": self.n_pairs,
            "residual_ms": vars(self.residuals),
            "per_channel_ms": {k: vars(v) for k, v in self.per_channel.items()},
            "per_edge_offset_ms": dict(self.per_edge_offset_ms),
            "host_lag_median_s": self.host_lag_median_s,
            "host_lag_mad_s": self.host_lag_mad_s,
            "host_steps": [vars(s) for s in self.host_steps],
            "verdict": self.verdict,
        }


# ---------------------------------------------------------------- Theil-Sen

def _sample_pairs(n: int, max_pairs: int, seed: int) -> List[Tuple[int, int]]:
    total = n * (n - 1) // 2
    if total <= max_pairs:
        return [(i, j) for i in range(n) for j in range(i + 1, n)]
    rng = random.Random(seed)
    out: List[Tuple[int, int]] = []
    while len(out) < max_pairs:
        i, j = rng.randrange(n), rng.randrange(n)
        if i != j:
            out.append((i, j))
    return out


def theil_sen(xs: Sequence[float], ys: Sequence[float], max_pairs: int = 20000, seed: int = 0
              ) -> Tuple[float, float]:
    """Robust line y = a + b x -> (a, b). Pairs with equal x are skipped."""
    if len(xs) < 2:
        raise ValueError("theil_sen needs at least two points")
    slopes = [(ys[j] - ys[i]) / (xs[j] - xs[i])
              for i, j in _sample_pairs(len(xs), max_pairs, seed) if xs[j] != xs[i]]
    if not slopes:
        raise ValueError("theil_sen: all x values identical")
    b = median(slopes)
    a = median([y - b * x for x, y in zip(xs, ys)])
    return a, b


def _percentile(sorted_xs: Sequence[float], p: float) -> float:
    if not sorted_xs:
        return float("nan")
    k = (len(sorted_xs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def residual_stats(resid_ms: Sequence[float]) -> ResidualStats:
    s = sorted(resid_ms)
    return ResidualStats(len(s), median(s), mad(s), _percentile(s, 5), _percentile(s, 95))


# --------------------------------------------------------------- the fit

def host_lag_steps(pairs: Sequence[Pair], block: int = 50, threshold_s: float = 0.1
                   ) -> List[HostStep]:
    """Jumps of the host lag between consecutive blocks of ``block`` pairs.

    Pairs are taken in host-time order, cut into blocks, and the median host
    lag of each block compared with the previous one; a difference beyond
    ``threshold_s`` is a step. Blocks, not single pairs, so one mis-pair cannot
    raise a false alarm.
    """
    ordered = sorted(pairs, key=lambda p: p.host_t)
    if len(ordered) < 2 * block:
        return []
    medians: List[Tuple[float, float]] = []
    for start in range(0, len(ordered) - block + 1, block):
        chunk = ordered[start:start + block]
        medians.append((chunk[0].host_t, median([p.host_lag_s for p in chunk])))
    steps: List[HostStep] = []
    for (_, m0), (t1, m1) in zip(medians, medians[1:]):
        if abs(m1 - m0) > threshold_s:
            steps.append(HostStep(t1, m1 - m0))
    return steps


def fit_clock(pairs: Sequence[Pair], min_pairs: int = 30, max_pairs: int = 20000, seed: int = 0,
              step_block: int = 50, step_threshold_s: float = 0.1) -> Optional[ClockFit]:
    """Fit y = offset[segment] + rate * x over the pairs. None if too few.

    Slopes are computed within segments only (shared rate); each segment's
    offset is the median of ``y - rate * x`` over its ON edges when it has at
    least ``min_pairs // 2`` of them, else over all its pairs.
    """
    if len(pairs) < min_pairs:
        return None
    by_seg: Dict[int, List[Pair]] = {}
    for p in pairs:
        by_seg.setdefault(p.segment, []).append(p)
    # Shared rate: sample slopes per segment, in proportion to its size.
    slopes: List[float] = []
    for seg_pairs in by_seg.values():
        if len(seg_pairs) < 2:
            continue
        share = max(2, int(max_pairs * len(seg_pairs) / len(pairs)))
        xs = [p.x for p in seg_pairs]
        ys = [p.y for p in seg_pairs]
        slopes.extend((ys[j] - ys[i]) / (xs[j] - xs[i])
                      for i, j in _sample_pairs(len(xs), share, seed) if xs[j] != xs[i])
    if not slopes:
        return None
    rate = median(slopes)
    offsets: Dict[int, float] = {}
    for seg, seg_pairs in by_seg.items():
        on = [p for p in seg_pairs if p.edge == ON]
        basis = on if len(on) >= max(2, min_pairs // 2) else seg_pairs
        offsets[seg] = median([p.y - rate * p.x for p in basis])
    resid = [(p.y - (offsets[p.segment] + rate * p.x)) / rate for p in pairs]
    per_channel: Dict[str, List[float]] = {}
    per_edge: Dict[str, List[float]] = {}
    for p, r in zip(pairs, resid):
        per_channel.setdefault(p.channel, []).append(r)
        per_edge.setdefault(p.edge, []).append(r)
    host_lags = [p.host_lag_s for p in pairs]
    steps = host_lag_steps(pairs, step_block, step_threshold_s)
    return ClockFit(
        rate=rate, offsets=offsets, n_pairs=len(pairs),
        residuals=residual_stats(resid),
        per_channel={k: residual_stats(v) for k, v in sorted(per_channel.items())},
        per_edge_offset_ms={k: median(v) for k, v in sorted(per_edge.items())},
        host_lag_median_s=median(host_lags), host_lag_mad_s=mad(host_lags),
        host_steps=steps,
        verdict=VERDICT_HOST_STEP if steps else VERDICT_OK,
    )
