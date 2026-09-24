"""exclusive.py - at most one channel of a group is active on any frame.

A single rat cannot break two beams, or lick two ports, at the same instant, but
on the LED board the light of one channel's LEDs spills into its neighbours'
(the ``bleed`` table in ``led_map.json``; up to ~46 % of a dim-row lick LED's
own change). Read LED by LED, that spill can look like a second channel firing.
So within each one-active group listed in ``host/led_board_layout.json``
(``exclusive_groups``), the per-session sync lets only the strongest channel be
active on a frame and returns the others to rest (docs/decisions/0201).

Scoring, per frame and per LED: how far the LED has moved from its resting
level towards its active level (``lit`` LEDs brighten, ``dark`` ones dim), as a
fraction of the TYPICAL full change of LEDs in the same row of the same group
this session (the median over them; over the LEDs of channels that have serial
events when the caller says which - ``fired``). A channel's score is the mean
over its LEDs. Measuring against the row's typical change, not the LED's own,
keeps a channel that never fired this session - whose only "change" is a
neighbour's spill - from scoring as high as the channel really firing.

When the top channel scores at least ``active_min`` the others are set to their
resting level on that frame. Two counts are reported per group: frames where a
losing channel also reached ``active_min`` (spill the rule removed) and frames
where it reached ``conflict_min`` (two channels truly lit - the rule's premise
failed; e.g. two animals in the rig). Removing a group from the layout file
turns the rule off for it.

Pure stdlib (lists of floats); the pixels live in the vision layer.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

LIT = "lit"
DARK = "dark"


@dataclass(frozen=True)
class ExclusiveParams:
    """active_min   - the top channel must reach this score for the rule to act.
    conflict_min - a second channel at or above this is really lit too (well
                   above the worst measured spill, ~0.46 on one LED).
    min_span     - an LED whose trace moves less than this (grey levels) never
                   changed; it does not score (same default as the hysteresis)."""
    active_min: float = 0.5
    conflict_min: float = 0.75
    min_span: float = 15.0


@dataclass(frozen=True)
class GroupLed:
    """One LED of a group member: its trace column, channel, row and direction."""
    column: int
    channel: str        # channel base, e.g. "L0"
    row: str            # "a" / "b" (the led_id suffix); "" when unnamed
    polarity: str       # LIT or DARK: what the LED does while its channel is active


@dataclass
class GroupReport:
    """What the rule did to one group this session."""
    name: str
    channels: List[str]
    n_leds: int
    frames_active: int = 0      # frames some channel was active (the rule acted)
    frames_suppressed: int = 0  # ... and a second channel also looked active
    frames_conflict: int = 0    # ... and that second channel was really lit
    suppressed_by_channel: Dict[str, int] = field(default_factory=dict)
    first_conflicts: List[int] = field(default_factory=list)   # frame indices (up to 10)

    def to_dict(self) -> dict:
        return {
            "channels": self.channels,
            "n_leds": self.n_leds,
            "frames_active": self.frames_active,
            "frames_suppressed": self.frames_suppressed,
            "frames_conflict": self.frames_conflict,
            "suppressed_by_channel": dict(sorted(self.suppressed_by_channel.items())),
            "first_conflicts": list(self.first_conflicts),
        }


def row_of(led_id: str) -> str:
    """``S3_a`` -> ``a``; an id without a row suffix -> ``""``."""
    head, sep, tail = led_id.rpartition("_")
    return tail if sep and head else ""


def _finite(v: Optional[float]) -> bool:
    return v is not None and not math.isnan(v)


def _levels(trace: Sequence[float], polarity: str) -> Optional[Tuple[float, float]]:
    """(rest, swing) of one LED: rest = its median (the active state is brief),
    swing = signed distance from rest to its active extreme. None if flat."""
    vals = [v for v in trace if _finite(v)]
    if len(vals) < 10:
        return None
    rest = statistics.median(vals)
    if polarity == DARK:
        swing = min(vals) - rest
    else:
        swing = max(vals) - rest
    return rest, swing


def enforce(traces: Dict[int, List[float]], groups: Dict[str, Sequence[GroupLed]],
            params: ExclusiveParams = ExclusiveParams(),
            fired: Optional[Set[str]] = None
            ) -> Tuple[Dict[int, List[float]], Dict[str, GroupReport]]:
    """Apply the one-active rule to every group.

    ``traces`` maps a trace column to its per-frame values (all the same length).
    ``fired`` - the channel bases that have serial events this session. When
    given, a row's typical change is taken over those channels' LEDs only (a
    row falls back to all its LEDs if none of them fired): in a group where
    most channels never fire - 15 reward valves, 2-3 used per session - a
    median over every LED would be a spill-sized change, and spill would then
    score as "really lit" (docs/decisions/0203).
    Returns new traces (columns not in any group are passed through unchanged)
    and one report per group.
    """
    out = {c: list(t) for c, t in traces.items()}
    reports: Dict[str, GroupReport] = {}
    for name, leds in groups.items():
        leds = [g for g in leds if g.column in traces]
        channels = sorted({g.channel for g in leds})
        rep = GroupReport(name, channels, len(leds))
        reports[name] = rep
        if len(channels) < 2:
            continue
        levels = {g.column: _levels(traces[g.column], g.polarity) for g in leds}
        by_row: Dict[str, List[float]] = {}
        by_row_fired: Dict[str, List[float]] = {}
        for g in leds:
            lv = levels[g.column]
            if lv is not None and abs(lv[1]) >= params.min_span:
                by_row.setdefault(g.row, []).append(abs(lv[1]))
                if fired is not None and g.channel in fired:
                    by_row_fired.setdefault(g.row, []).append(abs(lv[1]))
        by_row.update(by_row_fired)
        ref = {row: statistics.median(s) for row, s in by_row.items()}
        scored = [(g, levels[g.column]) for g in leds
                  if levels[g.column] is not None and g.row in ref]
        members: Dict[str, List[Tuple[GroupLed, float, float]]] = {}
        for g, (rest, _swing) in scored:
            sign = -1.0 if g.polarity == DARK else 1.0
            members.setdefault(g.channel, []).append((g, rest, sign / ref[g.row]))
        if len(members) < 2:
            continue

        n = len(next(iter(traces.values())))
        for k in range(n):
            scores: List[Tuple[float, str]] = []
            for ch, mem in members.items():
                fr = [(traces[g.column][k] - rest) * scale
                      for g, rest, scale in mem if _finite(traces[g.column][k])]
                if fr:
                    scores.append((sum(fr) / len(fr), ch))
            if not scores:
                continue
            scores.sort(reverse=True)
            top, winner = scores[0]
            if top < params.active_min:
                continue
            rep.frames_active += 1
            runner = scores[1][0] if len(scores) > 1 else float("-inf")
            if runner >= params.active_min:
                rep.frames_suppressed += 1
                for s, ch in scores[1:]:
                    if s >= params.active_min:
                        rep.suppressed_by_channel[ch] = rep.suppressed_by_channel.get(ch, 0) + 1
            if runner >= params.conflict_min:
                rep.frames_conflict += 1
                if len(rep.first_conflicts) < 10:
                    rep.first_conflicts.append(k)
            for ch, mem in members.items():
                if ch == winner:
                    continue
                for g, rest, _scale in mem:
                    out[g.column][k] = rest
    return out, reports


def groups_from(exclusive_groups: Dict[str, Sequence[str]],
                leds: Sequence[Tuple[int, str, Optional[str], Optional[str]]]
                ) -> Dict[str, List[GroupLed]]:
    """Build the per-group LED lists from the layout's ``exclusive_groups``
    (group name -> channel bases) and the session's located map LEDs, given as
    ``(column, led_id, channel, polarity)`` (``channel`` like ``S3=1``). LEDs
    without a channel or a lit/dark polarity are left out."""
    home: Dict[str, str] = {}
    for name, chans in exclusive_groups.items():
        for ch in chans:
            home[ch] = name
    out: Dict[str, List[GroupLed]] = {name: [] for name in exclusive_groups}
    for column, led_id, channel, polarity in leds:
        if not channel or polarity not in (LIT, DARK):
            continue
        base = channel.split("=")[0]
        name = home.get(base)
        if name is not None:
            out[name].append(GroupLed(column, base, row_of(led_id), polarity))
    return out
