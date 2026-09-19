"""
excursion_log.py - a timestamped per-excursion event log for Phase 3 (pure core).

The Phase 3 data table (:mod:`tmaze.core.phase3.phase3_log`) has one row per
*trial* - a scored stem trip. But the animal also makes OUTBOUND trips that never
become trials: it darts up a side arm without going up the stem (a "non-stem" /
denied excursion). Those trips carry real behavioral signal (impulsive shortcut
pulls, a warming-up or fatiguing animal) yet are invisible in the trial table.

This core records EVERY outbound excursion - stem and non-stem alike - as its own
timestamped row, so an offline reader can reconstruct a stem-vs-non-stem
compliance-over-time curve (bin by ``iso``) and see each denied trip as an
individual event rather than only an end-of-session tally. It mirrors
``phase3_log`` in style: an immutable per-row view, an append-only builder, and a
``render_csv`` the thin controller writes to disk (see docs/decisions/0109).

One row is appended per ``task_events.choice_committed`` edge (fires once per
outbound trip, any route). ``type`` = whether the trip went up the stem; a stem
trip is ``scored`` and carries its 1-based ``linked_trial`` (join back to the
trial CSV); a non-stem trip is ``denied`` with a blank ``linked_trial``.

Reconciliation caveat (docs/decisions/0109): these events come from
``phase3_controller``'s own ``TaskEvents`` (the fast actuation-thread view). The
``behavior_stats`` aggregates come from a SEPARATE ``TaskEvents`` on the Tk poll,
so the two are close but not bit-exact - this per-event log is the authoritative
per-excursion source.

Pure and single-threaded (the Phase 3 actuation thread is the sole caller): no
Tk/OpenCV/serial, no file I/O (the controller writes the rendered text), no locks.
Headless tests: python -m unittest test_excursion_log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# Excursion type + outcome vocabularies (kept as named constants so callers and
# tests agree on the exact tokens written to the CSV).
TYPE_UP_STEM = "up-stem"
TYPE_NON_STEM = "non-stem"
OUTCOME_SCORED = "scored"
OUTCOME_DENIED = "denied"

# task_events reports the committed side lowercase ("left"/"right"); normalize to
# the uppercase arm labels the trial CSV uses so the two files join cleanly.
_SIDE_LABEL = {"left": "LEFT", "right": "RIGHT", "LEFT": "LEFT", "RIGHT": "RIGHT"}

# CSV column order (mirrors phase3_log's COLUMNS convention). All appended-once,
# fixed order; ``event_index`` is 1-based.
COLUMNS = (
    "event_index", "iso", "mono", "type", "side", "outcome", "linked_trial",
    "sched_state", "bias", "cue_dwell_s",
)


@dataclass(frozen=True)
class ExcursionRow:
    """One outbound excursion, captured by value.

    ``linked_trial`` is the 1-based trial index for a scored stem trip and None
    for a denied non-stem trip. ``bias`` / ``cue_dwell_s`` are the closed-loop
    directional bias at the moment of commit and the relative cue-zone dwell of
    the linked trial (docs/decisions/0108); both blank when unavailable.
    """
    event_index: int
    iso: str
    mono: Optional[float]
    type: str            # TYPE_UP_STEM | TYPE_NON_STEM
    side: str            # LEFT | RIGHT
    outcome: str         # OUTCOME_SCORED | OUTCOME_DENIED
    linked_trial: Optional[int]
    sched_state: Optional[str]
    bias: Optional[float]
    cue_dwell_s: Optional[float]


def _fmt(value: Optional[object], *, decimals: Optional[int] = None) -> str:
    """Render one cell: None -> empty, floats to ``decimals`` places when asked."""
    if value is None:
        return ""
    if decimals is not None and isinstance(value, (int, float)):
        return f"{float(value):.{decimals}f}"
    return str(value)


class ExcursionLog:
    """Append-only log of Phase 3 outbound excursions (stem + non-stem)."""

    def __init__(self, *, animal: str, session_id: str,
                 session_start_iso: str,
                 session_t0: Optional[Dict[str, Any]] = None) -> None:
        self.animal = animal
        self.session_id = session_id
        self.session_start_iso = session_start_iso
        # Canonical session anchor (manifest t0: host wall+mono+iso at Start), so
        # this file self-anchors to the one instant every stream shares, matching
        # the trial CSV's header block (L9, docs/decisions/0124/0182). None on a
        # manual arm / before Start Session.
        self.session_t0 = session_t0
        self._rows: List[ExcursionRow] = []

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def rows(self) -> Tuple[ExcursionRow, ...]:
        return tuple(self._rows)

    def append(self, *, iso: str, type: str, side: str, outcome: str,
               mono: Optional[float] = None, linked_trial: Optional[int] = None,
               sched_state: Optional[str] = None, bias: Optional[float] = None,
               cue_dwell_s: Optional[float] = None) -> ExcursionRow:
        """Record one outbound excursion and return its row. ``event_index`` is
        assigned 1-based in append order; ``side`` is normalized to LEFT/RIGHT."""
        row = ExcursionRow(
            event_index=len(self._rows) + 1, iso=iso, mono=mono, type=type,
            side=_SIDE_LABEL.get(side, side), outcome=outcome,
            linked_trial=linked_trial, sched_state=sched_state, bias=bias,
            cue_dwell_s=cue_dwell_s)
        self._rows.append(row)
        return row

    # ------------------------------------------------------------- render
    def _header_comment(self) -> List[str]:
        lines = [
            f"# phase3_excursions animal={self.animal} "
            f"session_id={self.session_id} proto=1",
        ]
        # Canonical session anchor, matching the trial CSV's block (L9,
        # docs/decisions/0124/0182), so the excursions file self-anchors too.
        if self.session_t0:
            lines.append(
                f"# session_t0_iso={self.session_t0.get('iso', '')} "
                f"session_t0_wall={self.session_t0.get('wall', '')} "
                f"session_t0_mono={self.session_t0.get('mono', '')}")
        lines += [
            f"# session_start_iso={self.session_start_iso}",
            "# note: iso is a host actuation-decision time (serial latency ~ms); "
            "mono is the host monotonic clock (seconds). see docs/decisions/0109",
            "# note: one row per outbound excursion (task_events.choice_committed); "
            "type=up-stem is a scored trial (linked_trial set), type=non-stem is a "
            "denied trip (linked_trial blank). cue_dwell_s is a relative host-clock "
            "dwell, not a reaction time (docs/decisions/0108)",
        ]
        return lines

    def render_csv(self) -> str:
        """Render the whole log (header comment + column row + events) as CSV."""
        lines = list(self._header_comment())
        lines.append(",".join(COLUMNS))
        for r in self._rows:
            lines.append(",".join((
                str(r.event_index),
                _fmt(r.iso),
                _fmt(r.mono, decimals=3),
                _fmt(r.type),
                _fmt(r.side),
                _fmt(r.outcome),
                _fmt(r.linked_trial),
                _fmt(r.sched_state),
                _fmt(r.bias, decimals=4),
                _fmt(r.cue_dwell_s, decimals=3),
            )))
        return "\n".join(lines) + "\n"
