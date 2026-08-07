"""
session_summary.py - the per-session behavioral summary (pure core).

At the end of a recording session the operator wants a short, plain answer to
"what did the animal actually do this session?" - the accuracy it reached, how
side-biased it was, how much of the time it ran up the stem vs took a denied
non-stem shortcut, how far it ran, and so on. The raw per-trial Phase 3 CSV
(docs/decisions/0047) already holds everything needed to recompute that offline,
but nothing writes the ready-made summary; a human has to reprocess the CSV.

This core builds that summary. It FOLDS two already-computed readouts - the Phase
3 cued-task stats (``phase3_stats.Phase3Stats`` over the published table,
docs/decisions/0052) and the live locomotor/compliance stats
(``behavior_stats.BehaviorSnapshot``, docs/decisions/0032) - plus a little session
metadata into one ``SessionSummary``, which renders to (a) machine-readable JSON
for reanalysis and (b) a compact human-readable text block. It does NOT recompute
the stats cores' measures: those cores stay the single source of truth, this only
selects, labels, and serialises their fields (see docs/decisions/0071).

Additions widen the summary to be self-contained:
  - it carries the operator's ``notes`` for the session - now an ordered list of
    TIMESTAMPED entries (``session_notes.NoteEntry`` dicts: iso/mono/trial_index/
    text/tag/count/edited_iso), not the single free-text string of ADR-0073, so
    each note is an event on the shared timeline (docs/decisions/0074), and
  - it folds a copy of the Phase 3 task params that shape interpretation
    (``params``: sequence/cue/reward knobs, saved per session in
    ``config/phase3.json``) and derives an ESTIMATED task-reward water volume
    from the rewarded + top-up pulse counts at the configured pulse width via the
    shared ``reward_volume`` calibration (docs/decisions/0019). Like the existing
    ``duration_s``, the water figure is a session-level scalar this formatter
    derives from its inputs; it is not one of the two stats cores' measures.

The thin ``session_controller.py`` shell calls :func:`build_summary` on Session
Stop and writes the two files into the session's ``summary/`` folder; this module
does no file I/O, imports no Tk/serial, and is headlessly testable like the other
pure cores (``session_log.py`` / ``phase3_stats.py``).

Scope note. Both inputs are "since Phase 3 was armed", not "since Session Start":
``published_state()`` reflects the whole Phase 3 table since arm, and the
locomotor accumulator resets on the Phase 3 arm edge (docs/decisions/0052). In the
normal arm-then-Start-then-Stop workflow that equals the session, but not if the
operator ran trials before Start or spanned several Start/Stop cycles on one arm.
The summary records this via its ``scope`` field / header line; exact Start/Stop-
window scoping is deferred (docs/future-work.md FW-0028).

Headless tests: python -m unittest test_session_summary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from tmaze.core.reward import reward_volume

if TYPE_CHECKING:  # type-only imports - no runtime coupling to the stats cores
    from tmaze.core.behavior_stats import BehaviorSnapshot
    from phase3_stats import Phase3Stats

# Bump when the summary JSON layout changes so an offline reader can branch on it.
# v2 adds the operator ``notes``, the folded ``task_params``, and the estimated
# water block (docs/decisions/0073). v3 changes ``notes`` from a single string to
# an ordered list of timestamped entry objects (docs/decisions/0074). v4 adds the
# ``manual_water`` figure - a sibling to ``estimated_water`` totalling the rare
# in-task MANUAL reward pulses that the task estimate deliberately excludes
# (docs/decisions/0078).
SCHEMA_VERSION = 4

# Both inputs are measured from the Phase 3 arm edge, not Session Start (see the
# module docstring + docs/future-work.md FW-0028); recorded so a reader knows what
# window the numbers cover.
SCOPE = "since Phase 3 arm"


@dataclass
class SessionSummary:
    """A session's behavioral summary: metadata + the two stat readouts.

    ``cued`` / ``locomotor`` are the frozen readouts from the two stats cores (or
    None when that half wasn't available). ``duration_s`` is derived from the
    wall-clock anchors when both are present. Renders to JSON (reanalysis) or a
    human-readable text block; it holds the readouts by reference and only
    formats them, so the stats cores remain the single source of truth.
    """
    session_id: str
    animal: str
    t0: Dict[str, Any]
    t_end: Optional[Dict[str, Any]]
    cued: Optional["Phase3Stats"] = None
    locomotor: Optional["BehaviorSnapshot"] = None
    seed: Optional[int] = None
    tones: Dict[str, Any] = field(default_factory=dict)
    # The Phase 3 task params that shape interpretation (sequence/cue/reward
    # knobs), folded as a copy from the session's config/phase3.json so the
    # summary is self-contained (docs/decisions/0073); {} when unavailable.
    params: Dict[str, Any] = field(default_factory=dict)
    # Operator notes / interventions for the session as an ordered list of
    # timestamped entries (session_notes.NoteEntry dicts; docs/decisions/0074);
    # [] when none were recorded.
    notes: List[Dict[str, Any]] = field(default_factory=list)
    # Pulse widths (ms) of the rare in-task MANUAL reward dispenses the session
    # saw on the wire (REWARD,<port>,MANUAL,<dur_ms>; docs/decisions/0019). The
    # shell collects these during the session; this core totals them into the
    # ``manual_water`` figure (docs/decisions/0078). [] when none occurred.
    manual_pulses_ms: List[float] = field(default_factory=list)
    generated_iso: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    scope: str = SCOPE

    # ------------------------------------------------------------- derived
    @property
    def duration_s(self) -> Optional[float]:
        """Session span in seconds from the wall-clock anchors, or None if the
        end anchor is missing/unparseable."""
        try:
            start = float(self.t0.get("wall"))
            end = float(self.t_end.get("wall")) if self.t_end else None
        except (TypeError, ValueError, AttributeError):
            return None
        if end is None:
            return None
        return max(0.0, end - start)

    @property
    def estimated_water(self) -> Optional[Dict[str, Any]]:
        """Estimated water delivered by TASK rewards this session, or None if the
        cued stats or the reward pulse width aren't available.

        Counts the correct-choice prime pulses (``cued.rewarded``) plus the lick
        top-up pulses (``cued.topups``) - both from the Phase 3 stats core, not
        recomputed here - each at the configured reward pulse width, converted to
        microliters via the shared ``reward_volume`` bench calibration
        (docs/decisions/0019). This is an ESTIMATE of task-delivered reward only:
        operator/manual pulses are not counted (docs/decisions/0073), so it can
        undercount the day's true intake."""
        if self.cued is None:
            return None
        pulse_raw = self.params.get("reward_pulse_ms")
        try:
            pulse_ms = float(pulse_raw)
        except (TypeError, ValueError):
            return None
        if pulse_ms <= 0:
            return None
        rewarded = int(getattr(self.cued, "rewarded", 0) or 0)
        topups = int(getattr(self.cued, "topups", 0) or 0)
        pulses = rewarded + topups
        per_pulse = reward_volume.reward_volume_ul(pulse_ms)
        return {
            "ul": round(per_pulse * pulses, 1),
            "pulses": pulses,
            "rewarded": rewarded,
            "topups": topups,
            "pulse_ms": pulse_raw,
            "per_pulse_ul": round(per_pulse, 1),
        }

    @property
    def manual_water(self) -> Dict[str, Any]:
        """Water delivered by rare in-task MANUAL reward pulses this session.

        Totals the operator-fired ``REWARD,<port>,MANUAL,<dur_ms>`` pulses the
        shell collected off the wire (docs/decisions/0019), each converted to
        microliters at its own pulse width via the shared ``reward_volume``
        calibration. This is the channel the task estimate (``estimated_water``)
        deliberately excludes, surfaced separately so the two never conflate and
        the day's true task+manual intake can be reconciled (docs/decisions/0078).
        Always returns a definite figure (``0`` pulses / ``0.0`` uL when no manual
        dispenses occurred), unlike ``estimated_water`` which is None when it
        can't be computed."""
        total = 0.0
        for ms in self.manual_pulses_ms:
            total += reward_volume.reward_volume_ul(ms)
        return {"ul": round(total, 1), "pulses": len(self.manual_pulses_ms)}

    # -------------------------------------------------------------- JSON
    def as_dict(self) -> Dict[str, Any]:
        """The machine-readable summary as a plain dict (JSON-ready).

        Stat readouts are emitted verbatim (their own field names) so an offline
        reader gets every measure; fractions stay 0..1 (the text form does the
        percentage conversion for humans)."""
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "animal": self.animal,
            "seed": self.seed,
            "tones": self.tones,
            "task_params": self.params,
            "scope": self.scope,
            "t0": self.t0,
            "t_end": self.t_end,
            "duration_s": self.duration_s,
            "generated_iso": self.generated_iso,
            "cued_task": _stats_dict(self.cued),
            "estimated_water": self.estimated_water,
            "manual_water": self.manual_water,
            "locomotion": _stats_dict(self.locomotor),
            "notes": self.notes,
        }

    def render_json(self) -> str:
        """Render the summary as pretty JSON text (the shell writes it)."""
        return json.dumps(self.as_dict(), indent=2) + "\n"

    # -------------------------------------------------------------- text
    def render_text(self) -> str:
        """Render a compact, human-readable summary block (the shell writes it).

        Rates become percentages / per-minute figures; an empty measure shows as
        ``--``. Two sections mirror the two stats groups shown live in the
        behavior panel (docs/decisions/0052)."""
        lines = ["Session behavioral summary", "=" * 40]
        lines.append(f"Session:  {self.session_id}")
        lines.append(f"Animal:   {self.animal}")
        if self.seed is not None:
            lines.append(f"Seed:     {self.seed}")
        tone_line = _tone_line(self.tones)
        if tone_line:
            lines.append(f"Tones:    {tone_line}")
        lines.append(f"Start:    {_iso_of(self.t0)}")
        lines.append(f"End:      {_iso_of(self.t_end)}")
        lines.append(f"Duration: {_duration(self.duration_s)}")
        lines.append(f"Scope:    {self.scope}")

        lines.append("")
        lines.append("Task params")
        lines.append("-" * 40)
        lines.extend(_params_lines(self.params))

        lines.append("")
        lines.append("Cued task")
        lines.append("-" * 40)
        lines.extend(_cued_lines(self.cued))
        lines.append(f"  Water (est): {_water_str(self.estimated_water)}")
        manual = self.manual_water
        if manual["pulses"]:
            lines.append(f"  Water (man): {_manual_water_str(manual)}")

        lines.append("")
        lines.append("Excursions / locomotion")
        lines.append("-" * 40)
        lines.extend(_locomotor_lines(self.locomotor))

        lines.append("")
        lines.append("Notes")
        lines.append("-" * 40)
        lines.extend(_notes_lines(self.notes))

        return "\n".join(lines) + "\n"


def build_summary(*, session_id: str, animal: str,
                  t0: Dict[str, Any], t_end: Optional[Dict[str, Any]] = None,
                  cued: Optional["Phase3Stats"] = None,
                  locomotor: Optional["BehaviorSnapshot"] = None,
                  seed: Optional[int] = None,
                  tones: Optional[Dict[str, Any]] = None,
                  params: Optional[Dict[str, Any]] = None,
                  notes: Optional[List[Dict[str, Any]]] = None,
                  manual_pulses_ms: Optional[List[float]] = None,
                  generated_iso: Optional[str] = None) -> SessionSummary:
    """Build a :class:`SessionSummary` from the session metadata + the two frozen
    stat readouts, plus the folded task ``params``, the operator ``notes``
    (an ordered list of timestamped entry dicts; docs/decisions/0074), and the
    ``manual_pulses_ms`` widths of any in-task manual reward dispenses the shell
    collected (docs/decisions/0078). Pure: it stores and formats the inputs
    (deriving only the session-level scalars ``duration_s`` / ``estimated_water``
    / ``manual_water``); it recomputes none of the stats cores' measures.
    ``generated_iso`` defaults to now (the moment the summary was built)."""
    return SessionSummary(
        session_id=session_id, animal=animal, t0=t0, t_end=t_end,
        cued=cued, locomotor=locomotor, seed=seed,
        tones=dict(tones) if tones else {},
        params=dict(params) if params else {},
        notes=list(notes) if notes else [],
        manual_pulses_ms=list(manual_pulses_ms) if manual_pulses_ms else [],
        generated_iso=generated_iso or datetime.now().isoformat(
            timespec="seconds"))


# ------------------------------------------------------------------ helpers
def _stats_dict(stats: Any) -> Optional[Dict[str, Any]]:
    """A frozen stats readout as a plain dict (JSON-ready), or None if absent.

    Uses ``vars`` on the dataclass instance so every field is emitted with its
    own name; tuples (e.g. ``lr_bias``) become JSON arrays."""
    if stats is None:
        return None
    return {k: v for k, v in vars(stats).items()}


def _params_lines(params: Dict[str, Any]) -> list:
    """Human-readable Phase 3 task-param lines (the copy folded from the session's
    config/phase3.json; docs/decisions/0073)."""
    if not params:
        return ["  (no task params)"]

    def g(key: str) -> Any:
        return params.get(key)

    return [
        f"  Sequence:    block {g('block_size')}   max-run {g('max_same_run')}",
        f"  Cue:         require up-stem {g('require_up_stem')}   "
        f"min-conf {_conf(g('min_confidence'))}",
        f"  Reward:      pulse {g('reward_pulse_ms')} ms   "
        f"max top-ups {g('max_topups')}   "
        f"reward-incorrect {g('reward_incorrect')}",
    ]


def _water_str(water: Optional[Dict[str, Any]]) -> str:
    """The estimated task-reward water as a one-line string, or ``--`` when it
    couldn't be estimated (no cued stats / no pulse width)."""
    if not water:
        return "--"
    return (f"{reward_volume.format_volume(water['ul'])}   "
            f"[{water['pulses']} pulses @ {water['pulse_ms']} ms; excl. manual]")


def _manual_water_str(water: Dict[str, Any]) -> str:
    """The manual in-task water as a one-line string (docs/decisions/0078)."""
    return (f"{reward_volume.format_volume(water['ul'])}   "
            f"[{water['pulses']} manual pulses]")


def _notes_lines(notes: List[Dict[str, Any]]) -> list:
    """The operator notes as a timestamped list (docs/decisions/0074), or a
    ``(none)`` placeholder when empty.

    Each entry renders as ``HH:MM:SS  [trial N]  #tag  text (edited)``; a leading
    tally line summarises tag usage (event tags as ``xN``, counter tags as their
    ``+N`` total) - an aggregation of the given entries, not a stats-core
    measure."""
    if not notes:
        return ["  (none)"]
    lines: list = []
    tally = _tag_tally(notes)
    if tally:
        lines.append(f"  Tags: {tally}")
    for e in notes:
        lines.append("  " + _note_entry_line(e))
    return lines


def _note_entry_line(e: Dict[str, Any]) -> str:
    """One timestamped note-entry line for the text summary."""
    parts = [_hms(e.get("iso"))]
    trial = e.get("trial_index")
    if trial is not None:
        parts.append(f"trial {trial}")
    tag = e.get("tag")
    if tag:
        parts.append(f"#{tag}")
    head = "  ".join(parts)
    text = (e.get("text") or "").strip()
    body = text if text else "(no text)"
    if e.get("edited_iso"):
        body += " (edited)"
    return f"{head}  {body}"


def _tag_tally(notes: List[Dict[str, Any]]) -> str:
    """A compact per-tag usage tally (``non-stem x3, lift-backs x5``), or "" when
    no entry carries a tag. Sums each tagged entry's ``count`` so a counter tag
    reads as its running total; first-seen order is preserved."""
    totals: Dict[str, int] = {}
    order: List[str] = []
    for e in notes:
        tag = e.get("tag")
        if not tag:
            continue
        if tag not in totals:
            totals[tag] = 0
            order.append(tag)
        totals[tag] += int(e.get("count", 1) or 1)
    if not order:
        return ""
    return ", ".join(f"{t} x{totals[t]}" for t in order)


def _hms(iso: Optional[str]) -> str:
    """The time-of-day (``HH:MM:SS``) from an ISO stamp, or the raw value if it
    can't be parsed / is absent."""
    if not iso:
        return "--:--:--"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(iso)


def _conf(x: Any) -> str:
    """A min-confidence value as a 2-decimal string, or ``--`` if unparseable."""
    try:
        return f"{float(x):.2f}"
    except (TypeError, ValueError):
        return "--"


def _pct(x: Optional[float]) -> str:
    return "--" if x is None else f"{x * 100:.0f}%"


def _lr(t) -> str:
    return "--" if t is None else f"{t[0] * 100:.0f}/{t[1] * 100:.0f}"


def _rate(x: Optional[float]) -> str:
    return "--" if x is None else f"{x:.1f}"


def _rt(x: Optional[float]) -> str:
    return "--" if x is None else f"{x:.2f} s"


def _side(side: Optional[str]) -> str:
    return {"left": "L", "right": "R"}.get(side, "")


def _iso_of(anchor: Optional[Dict[str, Any]]) -> str:
    if not anchor:
        return "--"
    return str(anchor.get("iso") or "--")


def _duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--"
    total = int(max(0.0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _tone_line(tones: Dict[str, Any]) -> str:
    """A one-line cue->side mapping, or "" when the tone info is absent."""
    if not tones:
        return ""
    high_hz = tones.get("high_tone_hz")
    low_hz = tones.get("low_tone_hz")
    high_is_left = tones.get("high_tone_is_left")
    if high_is_left is None or high_hz is None or low_hz is None:
        return ""
    high_side = "LEFT" if high_is_left else "RIGHT"
    low_side = "RIGHT" if high_is_left else "LEFT"
    return f"HIGH {high_hz} Hz -> {high_side}   LOW {low_hz} Hz -> {low_side}"


def _cued_lines(st: Optional["Phase3Stats"]) -> list:
    """Human-readable Phase 3 cued-task lines (docs/decisions/0052 measures)."""
    if st is None:
        return ["  (no cued-task data)"]
    n = st.window_n
    return [
        f"  Completed:   {st.completed}/{st.total}   "
        f"correct {st.correct}  incorrect {st.incorrect}",
        f"  Accuracy:    {_pct(st.accuracy_cum)}  "
        f"(last {n}: {_pct(st.accuracy_win)})",
        f"  By cue:      HIGH {_pct(st.high_accuracy)} [{st.high_completed}]   "
        f"LOW {_pct(st.low_accuracy)} [{st.low_completed}]",
        f"  Side bias:   {_lr(st.lr_bias)}   "
        f"[{st.left_choices}L/{st.right_choices}R]",
        f"  Reward rate: {_pct(st.reward_rate)}   "
        f"[{st.rewarded}/{st.completed}]",
        f"  Win-stay:    {_pct(st.win_stay)}   "
        f"Lose-shift: {_pct(st.lose_shift)}",
        f"  Choice RT:   {_rt(st.mean_rt_s)}   [n={st.rt_n}]",
    ]


def _locomotor_lines(sn: Optional["BehaviorSnapshot"]) -> list:
    """Human-readable locomotor/compliance lines (docs/decisions/0032 measures)."""
    if sn is None:
        return ["  (no locomotor data)"]
    n = sn.window_n
    dist_m = sn.distance_cm / 100.0
    return [
        f"  Stem-gated:  {_pct(sn.compliance_cum)}  "
        f"(last {n}: {_pct(sn.compliance_win)})   "
        f"[{sn.stem_choices}/{sn.total_choices}]",
        f"  Non-stem:    {sn.nonstem_choices} (denied)",
        f"  L/R choice:  {_lr(sn.lr_cum)}   "
        f"[{sn.left_choices}L/{sn.right_choices}R]",
        f"  Same-side run: max {sn.same_side_run_max}"
        f"{_side(sn.same_side_run_max_side)}",
        f"  Throughput:  {sn.stem_choices} trials   "
        f"{_rate(sn.trials_per_min)}/min   "
        f"(active {_duration(sn.active_s)})",
        f"  Distance:    {dist_m:.1f} m   {_rate(sn.distance_m_per_min)} m/min",
    ]
