"""event_frames.py - which video frames show each channel's LED changing.

The second pass of the LED-map builder (docs/decisions/0196). Pass 1 fitted the
camera clock from the LEDs that are easy to find; with that fit every serial
event (a beam break, a lick, a reward pulse) can be placed on the video's frame
axis. For each channel this module picks, per event, a few frames DURING the
event and a few frames just BEFORE it, and turns them into per-frame weights:
``+1/n`` on the during frames and ``-1/n`` on the before frames, averaged over
the events. Summing ``weight x frame`` over the video (done in
``tmaze.vision.led_event_map``) then gives the channel's average
"during minus before" image, in which its own LED(s) are the bright spots.

The before frames are a **quiet baseline**: a frame counts only if no channel
of the same function (another beam for a beam, another lick for a lick) and no
reward is active within ``quiet_margin`` frames, so a neighbour's glow does not
leak into the baseline. The during frames drop any frame where another channel
of the same function is active, so two channels never share an image.

Pure stdlib (frame indices and weights only; the pixels live in the vision
layer).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from tmaze.core.sync.clockfit import ClockFit
from tmaze.core.sync.spans import Span, channel_kind

REWARD_KIND = "SOL"


@dataclass(frozen=True)
class EventMapParams:
    """Knobs of the event-triggered pass (frames are video frames).

    guard_frames    - frames skipped after an event's predicted onset (the frame
                      that straddles the change, plus fit jitter).
    during_frames   - at most this many frames of each event are averaged.
    before_frames   - baseline frames taken before the onset ...
    before_guard    - ... skipping this many right before it.
    quiet_margin    - a frame within this many frames of an active span of the
                      same function, or of a reward, is not quiet.
    min_events      - fewest usable events before a channel is localised at all.
    snr_min         - a peak must be this many robust-noise units above zero.
    rel_min         - a channel's 2nd, 3rd ... LED must reach this fraction of
                      its brightest (the dim row is ~1/3 of the bright one).
    min_sep_px      - two LEDs of one channel are at least this far apart (the
                      along-row pitch is ~4 px).
    claim_px        - once a channel claims an LED, later channels ignore pixels
                      this close to it ...
    reward_claim_px - ... or this close for a reward LED, whose flash blooms wider.
    same_led_px     - a pass-1 LED this close to a pass-2 LED of the same channel
                      is the same LED (pass 1's centroid is pulled by bloom; the
                      next LED along a row is ~4 px away).
    fid_exclude_px  - a persistently-lit spot this close to a channel LED is that
                      channel LED resting lit, not a fiducial (the ENTRY/EXIT beam
                      LEDs sat ~4 px off; the nearest real fiducial is ~7 px).
    """
    guard_frames: int = 1
    during_frames: int = 6
    before_frames: int = 8
    before_guard: int = 2
    quiet_margin: int = 1
    min_events: int = 3
    snr_min: float = 6.0
    rel_min: float = 0.15
    min_sep_px: float = 4.0
    claim_px: float = 2.0
    reward_claim_px: float = 4.0
    same_led_px: float = 3.0
    fid_exclude_px: float = 5.0


@dataclass
class ChannelWeights:
    """One channel's frame weights: ``weights[frame] = w`` (during > 0, before < 0).
    ``n_spans`` = its active spans; ``n_used`` = those that yielded both a during
    and a quiet before window."""
    channel: str
    n_spans: int
    n_used: int
    weights: Dict[int, float]


def function_of(channel: str) -> str:
    """The channel's function: ``S`` (beam), ``L`` (lick) or ``SOL`` (reward)."""
    return channel_kind(channel)


def span_frames(spans: Sequence[Span], fit: ClockFit,
                cam_ts: Sequence[Optional[float]]) -> List[Tuple[int, int]]:
    """Each span as ``(first, last)`` frame index on the video: the first frame
    stamped at or after the predicted onset, the last at or before the predicted
    end (``last < first`` when the span falls between two frames). Spans the fit
    cannot place (no firmware clock, or an unfitted segment) are skipped, as are
    spans outside the video."""
    stamped = sorted((float(t), k) for k, t in enumerate(cam_ts) if t is not None)
    ts = [t for t, _ in stamped]
    ks = [k for _, k in stamped]
    out: List[Tuple[int, int]] = []
    for s in spans:
        if s.clk0 is None or s.clk1 is None or s.segment not in fit.offsets:
            continue
        a, b = sorted((fit.predict(s.clk0, s.segment), fit.predict(s.clk1, s.segment)))
        i = bisect.bisect_left(ts, a)
        j = bisect.bisect_right(ts, b) - 1
        if i >= len(ts) or j < 0:
            continue
        out.append((ks[i], ks[j] if j >= i else ks[i] - 1))
    return out


def _mark(frames: Sequence[Tuple[int, int]], margin: int, n: int) -> Set[int]:
    out: Set[int] = set()
    for first, last in frames:
        for k in range(max(0, first - margin), min(n, max(first, last) + margin + 1)):
            out.add(k)
    return out


def event_weights(active: Dict[str, List[Span]], fit: ClockFit,
                  cam_ts: Sequence[Optional[float]],
                  p: EventMapParams = EventMapParams()) -> Dict[str, ChannelWeights]:
    """Per active channel, the during/quiet-before frame weights (see module doc).

    ``active`` holds only the ACTIVE-state spans (``spans.active_spans``), so a
    beam's spans are its breaks, never its resting state. ``cam_ts[k]`` is frame
    k's camera stamp (the fit's y axis)."""
    n = len(cam_ts)
    placed = {ch: span_frames(sp, fit, cam_ts) for ch, sp in active.items()}
    busy: Dict[str, Set[int]] = {}
    for ch, fr in placed.items():
        f = function_of(ch)
        busy.setdefault(f, set()).update(_mark(fr, p.quiet_margin, n))
    by_channel_exact = {ch: _mark(fr, 0, n) for ch, fr in placed.items()}
    out: Dict[str, ChannelWeights] = {}
    for ch, fr in placed.items():
        f = function_of(ch)
        noisy = set(busy.get(f, ())) | set(busy.get(REWARD_KIND, ()))
        others = set()
        for other, frames in by_channel_exact.items():
            if other != ch and function_of(other) == f:
                others |= frames
        weights: Dict[int, float] = {}
        used = 0
        for first, last in fr:
            d0 = first + p.guard_frames
            d1 = min(last, d0 + p.during_frames - 1)
            during = [k for k in range(d0, d1 + 1) if 0 <= k < n and k not in others]
            b1 = first - p.before_guard - 1
            before = [k for k in range(b1 - p.before_frames + 1, b1 + 1)
                      if 0 <= k < n and k not in noisy]
            if not during or not before:
                continue
            used += 1
            for k in during:
                weights[k] = weights.get(k, 0.0) + 1.0 / len(during)
            for k in before:
                weights[k] = weights.get(k, 0.0) - 1.0 / len(before)
        if used:
            weights = {k: w / used for k, w in weights.items() if w != 0.0}
        out[ch] = ChannelWeights(ch, len(fr), used, weights)
    return out


def frame_roles(weights: Dict[str, ChannelWeights]) -> Dict[int, List[Tuple[str, float]]]:
    """Invert the per-channel weights to ``frame -> [(channel, w), ...]`` so a
    single pass over the video can feed every channel's sum."""
    out: Dict[int, List[Tuple[str, float]]] = {}
    for ch, cw in weights.items():
        for k, w in cw.weights.items():
            out.setdefault(k, []).append((ch, w))
    return out
