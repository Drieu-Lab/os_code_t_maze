"""
pipeline.py - one session, end to end: LED spans + serial spans -> bindings,
clock fit, verdicts.

    analyze(blobs, events) -> SyncResult

``blobs`` is what the vision layer hands over: per blinking LED, its lit spans
and its dark spans (spans.py's ``Span`` with camera ticks at each edge).
``events`` is the serial capture parsed by ``spans.serial_spans``. Nothing
here touches pixels or files.

Steps:

1. **Bind** every blob (matching.bind_blob) against the sensors' active states
   (``S<i>=0`` crossings, ``L<i>=0`` lick contacts, ``SOL`` pulses;
   spans.active_spans):
   which serial channel it mirrors, with which polarity, and how well. A blob
   with no spans is ``silent``; one whose best candidate is too weak is
   ``unbound``; otherwise ``matched``.
2. **Collect pairs** from the bindings whose channel *kind* is in
   ``fit_channel_kinds``. Each matched span yields two pairs (its start edge and
   its end edge), de-duplicated by (channel, firmware ms) because two LEDs on
   the same optocoupler channel see the same event.
3. **Fit** (clockfit.fit_clock) and attach an overall verdict.

Which channels feed the fit is a deliberate, named choice: beams (``S``) and
solenoids (``SOL``) by default, licks (``L``) excluded. Licks are the most
numerous signal but the noisiest (fast bursts, ~45 % camera recall, the one
source of mis-pairs seen so far); the robust fit would shrug them off, but a
line built only from unambiguous events is easier to defend, and beams +
valve already give thousands of points per session. Lick LEDs are still bound
and reported. Flip ``fit_channel_kinds`` to include ``"L"`` to change that.
(Decision taken 2026-09-08 with the design doc's section 3.7 in hand.)

Stdlib-only; exercised on synthetic data in tests/test_sync_core.py and on a
real excerpt in tests/test_sync_fixture.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.core.sync import clockfit
from tmaze.core.sync.matching import DARK, LIT, Binding, bind_blob, score_binding
from tmaze.core.sync.spans import Span, active_spans, channel_kind, visible

DEFAULT_FIT_CHANNEL_KINDS: Tuple[str, ...] = ("S", "SOL")

MATCHED, SILENT, UNBOUND = "matched", "silent", "unbound"


@dataclass(frozen=True)
class BlobSpans:
    """One blinking LED as seen in the video: its lit and dark spans."""
    blob: int
    lit: Sequence[Span]
    dark: Sequence[Span]


@dataclass
class SyncResult:
    bindings: Dict[int, Binding] = field(default_factory=dict)
    candidates: Dict[int, List[Binding]] = field(default_factory=dict)   # per blob, best first
    blob_verdicts: Dict[int, str] = field(default_factory=dict)
    pairs: List[clockfit.Pair] = field(default_factory=list)
    fit: Optional[clockfit.ClockFit] = None
    verdict: str = clockfit.VERDICT_TOO_FEW

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "blob_verdicts": {str(k): v for k, v in self.blob_verdicts.items()},
            "bindings": {str(k): vars(b) for k, b in self.bindings.items()},
            "fit": self.fit.to_dict() if self.fit else None,
        }


def pairs_from_binding(binding: Binding, blob: BlobSpans, events: Dict[str, Sequence[Span]],
                       tol_s: float, min_visible_s: float) -> List[clockfit.Pair]:
    """The (firmware ms, camera ticks) pairs a binding contributes: both edges
    of every matched span, skipping edges without a camera tick. The caller
    de-duplicates on (channel, firmware ms): two LEDs on one optocoupler channel
    see the same serial line."""
    led = blob.lit if binding.polarity == LIT else blob.dark
    _, matches = score_binding(binding.blob, binding.channel, binding.polarity, led,
                               events[binding.channel], tol_s, min_visible_s)
    vis = visible(events[binding.channel], min_visible_s)   # the list score_binding matched against
    start_edge, end_edge = (clockfit.ON, clockfit.OFF) if binding.polarity == LIT else (clockfit.OFF, clockfit.ON)
    out: List[clockfit.Pair] = []
    for m in matches:
        e, l = vis[m.event_idx], led[m.led_idx]
        if e.clk0 is not None and l.clk0 is not None:
            out.append(clockfit.Pair(e.clk0, l.clk0, binding.channel, start_edge,
                                     l.host_t0 - e.host_t0, e.host_t0, e.segment))
        if e.clk1 is not None and l.clk1 is not None:
            out.append(clockfit.Pair(e.clk1, l.clk1, binding.channel, end_edge,
                                     l.host_t1 - e.host_t1, e.host_t1, e.segment))
    return out


def analyze(blobs: Sequence[BlobSpans], events: Dict[str, Sequence[Span]],
            tol_s: float = 0.20, min_visible_s: float = 0.06,
            fit_channel_kinds: Sequence[str] = DEFAULT_FIT_CHANNEL_KINDS,
            min_score: float = 0.5, min_matched: int = 3, min_pairs: int = 30,
            seed: int = 0) -> SyncResult:
    """Bind every blob, collect fit pairs from the chosen channel kinds, fit.

    ``events`` may hold both states of each sensor (as ``serial_spans`` returns
    them); only the active states are matched (``spans.active_spans``).
    ``tol_s`` is the both-ends host-clock tolerance (why 200 ms: matching.py).
    """
    res = SyncResult()
    events = active_spans(events)
    seen: set = set()
    for blob in blobs:
        if not blob.lit and not blob.dark:
            res.blob_verdicts[blob.blob] = SILENT
            continue
        best, cands = bind_blob(blob.blob, {LIT: blob.lit, DARK: blob.dark}, events,
                                tol_s, min_visible_s, min_score, min_matched)
        res.candidates[blob.blob] = cands
        if best is None:
            res.blob_verdicts[blob.blob] = UNBOUND
            continue
        res.bindings[blob.blob] = best
        res.blob_verdicts[blob.blob] = MATCHED
        if channel_kind(best.channel) not in fit_channel_kinds:
            continue
        for p in pairs_from_binding(best, blob, events, tol_s, min_visible_s):
            key = (p.channel, p.x)
            if key in seen:
                continue
            seen.add(key)
            res.pairs.append(p)
    res.pairs.sort(key=lambda p: p.host_t)
    res.fit = clockfit.fit_clock(res.pairs, min_pairs=min_pairs, seed=seed)
    res.verdict = res.fit.verdict if res.fit is not None else clockfit.VERDICT_TOO_FEW
    return res
