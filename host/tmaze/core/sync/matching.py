"""
matching.py - which LED span is which serial event.

Two sorted lists of spans (spans.py) - one LED's lit or dark spans, and one
serial channel's spans - are paired so that every pair agrees at BOTH ends
within a tolerance measured on the shared host clock. Requiring both ends is
what separates beams a rat crosses within a few hundred ms of each other: a
nearest-edge match confused ENTRY and EXIT (design doc section 3.7).

The matcher is **ordered and one-to-one**: pairs may not cross (if event 7 is
paired with span 12, event 8 cannot take span 11) and no span is used twice.
This is the small piece of sequence alignment (Needleman-Wunsch's ordering
constraint) that a burst of fast licks needs; a greedy nearest match there
occasionally grabbed a neighbour and produced -200 ms outliers. We do NOT need
the full algorithm because the host clock already bounds where each pair can
be (60 +/- 10 ms apart, against a 150 ms tolerance), so the search is a narrow
band around the diagonal, not the whole grid.

Implementation: every (event, span) pair that agrees at both ends is an
*edge* with cost |start lag| + |end lag|. Among chains of edges with strictly
increasing event AND span index, we want the most edges, and among those the
lowest total cost. That is a longest-chain problem solved in one pass over the
events with a Fenwick tree (a small array structure that answers "best chain
ending at any span index below j" in log time) - see ``match_spans``.

A ``Binding`` is the verdict for one LED: which channel, which polarity (the
LED is *lit* or *dark* while the channel is active), the lag and its spread,
and a score. The score is the **precision** - the fraction of the LED's spans
that a serial event of that channel explains - because that is what answers
"which wire is this LED on?". Recall over the *visible* serial spans is
reported alongside as camera coverage, not folded into the score: the camera
catches only ~30-45 % of licks (fast bursts), and an F1 score would have
rejected a lick LED whose every blink is a real lick. ``bind_blob`` tries every
channel x polarity and keeps the best. Channels are the sensors' active states only (a beam's
crossing, e.g. ``S4=0`` on a pre-v8 bundle / ``S4=1`` from v8 on, a lick contact, a valve
pulse ``SOL2.1``; see ``spans.active_spans``): the resting state is the same wire phrased the other
way round, and letting both compete gave two labels for one LED.

Tolerance: the pipeline's default is 200 ms. The nominal worst case is ~140 ms
(60 ms host lag + one 40 ms frame of quantisation + one more frame on OFF
edges); sensor chatter merged into a crossing can shift its serial start a
further 50-70 ms before the LED visibly reacts, and 150 ms rejected those
(design doc section 3.7). 200 ms stays under the ~170 ms lick period, so a
burst's neighbours cannot pair at both ends.

Stdlib-only; tests in tests/test_sync_core.py.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.core.sync.spans import Span, visible


# --------------------------------------------------------------- the matcher

@dataclass(frozen=True)
class Match:
    """One accepted (event, LED span) pair. Lags are LED minus event, seconds."""
    event_idx: int
    led_idx: int
    start_lag: float
    end_lag: float

    @property
    def cost(self) -> float:
        return abs(self.start_lag) + abs(self.end_lag)


class _PrefixMax:
    """Fenwick tree for prefix MAXIMUM over positions 1..n of comparable values.

    ``update(i, v)`` raises the value at position i; ``query(i)`` returns the
    largest value at any position <= i (or None). Both O(log n).
    """

    def __init__(self, n: int):
        self.n = n
        self.tree: List[Optional[tuple]] = [None] * (n + 1)

    def update(self, i: int, v: tuple) -> None:
        i += 1
        while i <= self.n:
            if self.tree[i] is None or v > self.tree[i]:
                self.tree[i] = v
            i += i & -i

    def query(self, i: int) -> Optional[tuple]:
        i += 1
        best: Optional[tuple] = None
        while i > 0:
            v = self.tree[i]
            if v is not None and (best is None or v > best):
                best = v
            i -= i & -i
        return best


def match_spans(led: Sequence[Span], events: Sequence[Span], tol_s: float) -> List[Match]:
    """Ordered one-to-one matching of LED spans to event spans.

    Both inputs must be sorted by ``host_t0``. A pair is admissible when
    |led.host_t0 - event.host_t0| <= tol_s AND |led.host_t1 - event.host_t1| <=
    tol_s. Returns the chain with the most pairs, then the lowest total cost,
    in event order.
    """
    if not led or not events:
        return []
    starts = [s.host_t0 for s in led]
    # Admissible edges, grouped by event (events ascending, spans ascending).
    edges: List[Tuple[int, int, float, float]] = []          # (event_idx, led_idx, ds, de)
    for ei, e in enumerate(events):
        lo = bisect_left(starts, e.host_t0 - tol_s)
        hi = bisect_right(starts, e.host_t0 + tol_s)
        for lj in range(lo, hi):
            ds = led[lj].host_t0 - e.host_t0
            de = led[lj].host_t1 - e.host_t1
            if abs(de) <= tol_s:
                edges.append((ei, lj, ds, de))
    if not edges:
        return []
    # Longest chain with strictly increasing event AND span index; value =
    # (count, -total_cost, edge_id) so tuple comparison picks more pairs first,
    # then cheaper. All queries for one event run before its updates so two
    # edges of the same event can never chain.
    tree = _PrefixMax(len(led))
    best: List[tuple] = [()] * len(edges)
    prev: List[int] = [-1] * len(edges)
    k = 0
    while k < len(edges):
        ei = edges[k][0]
        group_end = k
        while group_end < len(edges) and edges[group_end][0] == ei:
            group_end += 1
        for g in range(k, group_end):
            _, lj, ds, de = edges[g]
            q = tree.query(lj - 1) if lj > 0 else None
            cnt, negcost, pid = q if q is not None else (0, 0.0, -1)
            best[g] = (cnt + 1, negcost - (abs(ds) + abs(de)), g)
            prev[g] = pid
        for g in range(k, group_end):
            tree.update(edges[g][1], best[g])
        k = group_end
    # Backtrack from the best chain end.
    end = max(range(len(edges)), key=lambda g: best[g])
    chain: List[Match] = []
    g = end
    while g >= 0:
        ei, lj, ds, de = edges[g]
        chain.append(Match(ei, lj, ds, de))
        g = prev[g]
    chain.reverse()
    return chain


# ---------------------------------------------------------------- statistics

def median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty sequence")
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def mad(xs: Sequence[float]) -> float:
    """Median absolute deviation - a spread measure that ignores outliers."""
    m = median(xs)
    return median([abs(x - m) for x in xs])


# ------------------------------------------------------------------ bindings

LIT, DARK = "lit", "dark"


@dataclass(frozen=True)
class Binding:
    """What one LED mirrors, and how well.

    channel / polarity - the statement "the LED is <polarity> while <channel>
                    is active": LIT for reward and lick LEDs (lit during the
                    pulse / contact), DARK for beam LEDs (dark during the
                    crossing). Channels are the sensors' ACTIVE states only
                    (``S4=0``, ``L2=0``, ``SOL2.1`` on a pre-v8 bundle;
                    spans.active_spans), so every count below refers to the same
                    spans and the score can be recomputed from them.
    n_events      - visible serial spans of the channel (recall denominator).
    n_led         - the LED's spans of that polarity (precision denominator).
    lag_s / lag_mad_s      - LED start minus event start, median and spread.
    end_lag_s     - LED end minus event end, median.
    score         - precision = n_matched / n_led (see module docstring);
                    ``recall`` = n_matched / n_events is the camera coverage.
    """
    blob: int
    channel: str
    polarity: str
    n_events: int
    n_matched: int
    n_led: int
    lag_s: float
    lag_mad_s: float
    end_lag_s: float
    score: float

    @property
    def recall(self) -> float:
        return self.n_matched / self.n_events if self.n_events else 0.0

    @property
    def precision(self) -> float:
        return self.n_matched / self.n_led if self.n_led else 0.0


def score_binding(blob: int, channel: str, polarity: str, led: Sequence[Span],
                  events: Sequence[Span], tol_s: float, min_visible_s: float
                  ) -> Tuple[Optional[Binding], List[Match]]:
    """Match one LED polarity against one channel and summarize it."""
    vis = visible(events, min_visible_s)
    if not vis or not led:
        return None, []
    matches = match_spans(led, vis, tol_s)
    if not matches:
        return None, []
    precision = len(matches) / len(led)
    starts = [m.start_lag for m in matches]
    ends = [m.end_lag for m in matches]
    return Binding(blob, channel, polarity, len(vis), len(matches), len(led),
                   median(starts), mad(starts), median(ends), precision), matches


MIN_LAG_S = -0.02


def lag_plausible(b: Binding, min_lag_s: float = MIN_LAG_S) -> bool:
    """An LED driven by a wire reacts AFTER the wire, at both edges.

    The frame that first shows a change is stamped at grab (after exposure),
    the serial line a few ms after the event, so a real binding's median start
    and end lags are both positive (+30 ms ON, +60-90 ms OFF, measured). A
    negative median lag at either edge means the LED changed before the serial
    line did - impossible for that wire, and the signature of a behavioural
    coincidence: the centre reward fires as the rat crosses the centre beam, so
    the beam LED's dark spans overlap 7 of 8 reward pulses in one excerpt, but
    the LED relights ~150 ms before the pulse ends. ``min_lag_s`` leaves room
    for serial-stamp jitter.
    """
    return b.lag_s >= min_lag_s and b.end_lag_s >= min_lag_s


def bind_blob(blob: int, led_by_polarity: Dict[str, Sequence[Span]],
              events_by_channel: Dict[str, Sequence[Span]], tol_s: float,
              min_visible_s: float, min_score: float = 0.5, min_matched: int = 3,
              min_lag_s: float = MIN_LAG_S) -> Tuple[Optional[Binding], List[Binding]]:
    """Try every (channel, polarity) for one LED; return (accepted, all candidates).

    ``events_by_channel`` should hold the sensors' active states only
    (spans.active_spans) - otherwise "dark while S4 broken" and "lit while S4 clear"
    would compete as two candidates for the same wire. Accepted = the
    best-scoring candidate whose lags are plausible (``lag_plausible``) that
    clears ``min_score`` (precision) and ``min_matched`` (so three coincidences
    cannot bind a LED); otherwise None (the LED is *unbound* - it blinks, but
    not in step with anything in the log). Candidates are sorted plausible
    first, then by score and matched count, so a report can show what an
    unbound LED nearly matched.
    """
    candidates: List[Binding] = []
    for polarity in (LIT, DARK):
        led = led_by_polarity.get(polarity) or []
        if not led:
            continue
        for channel, ev in events_by_channel.items():
            b, _ = score_binding(blob, channel, polarity, led, ev, tol_s, min_visible_s)
            if b is not None:
                candidates.append(b)
    candidates.sort(key=lambda b: (not lag_plausible(b, min_lag_s), -b.score, -b.n_matched))
    best = candidates[0] if candidates else None
    if best is not None and lag_plausible(best, min_lag_s) \
            and best.score >= min_score and best.n_matched >= min_matched:
        return best, candidates
    return None, candidates
