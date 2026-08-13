"""
records_audit.py - read-only integrity checks over the coordinated records (pure core).

Bug ``#260810_RecordsIntegrity``: the coordinated records (``animals.json``,
``daily_log.csv``, ``session_annotations.csv``, ``session_registry.csv``, the
generated Excel views) drift out of step in ways no single surface catches -
a mistyped animal id that isn't in ``animals.json``, a session whose
``(animal, date)`` has no daily husbandry row, a daily row with a water VOLUME
typed into the water-on/off TIME column, or a burst of near-zero-duration
start/stop "sessions". This core DETECTS those problems and returns them for a
caller to print; it never edits anything - the data fixes are the operator's, by
the audit decision (ADR-0097). The thin ``tools/records_check.py`` shell loads
the files and prints what these functions return.

Pure stdlib (``re``), headlessly testable.

Headless tests: python -m unittest test_records_audit.
"""

from __future__ import annotations

import re
from typing import Any, List, NamedTuple, Optional, Sequence, Set, Tuple

# A session shorter than this (seconds) is almost certainly start/stop churn - a
# mis-fire or aborted run - not a real session. Bug #260810 saw bursts of ~12 s
# bundles (5 in ~5 min). Reporting only; nothing is dropped on this basis (real
# short sessions do happen). See docs/decisions/0097.
SHORT_SESSION_S = 60.0

# A clock-time cell looks like "4:30pm", "16:04", "3:15PM", "5:30", or a bare
# "6pm"/"1 PM" (hour with minutes, or hour with am/pm - a lone number isn't a
# time). Anything else non-blank in a time column (e.g. "8.46ml rig; No suppl",
# "Till Monday evening") is a water VOLUME or a free-text note misfiled into the
# on/off TIME column - finding C of bug #260810.
_TIME_RE = re.compile(
    r"^\s*\d{1,2}(?::\d{2}\s*(?:[ap]m)?|\s*[ap]m)\s*$", re.IGNORECASE)

# The two daily-log columns that are meant to hold clock times.
WATER_TIME_FIELDS = ("water_on", "water_off")


def looks_like_time(value: Any) -> bool:
    """Whether ``value`` reads as a clock time (see ``_TIME_RE``)."""
    return bool(_TIME_RE.match(str(value if value is not None else "")))


def suspect_water_cells(
        daily_rows: Sequence[dict]) -> List[Tuple[str, str, str, str]]:
    """``(animal_id, date, field, value)`` for every non-blank ``water_on`` /
    ``water_off`` cell that doesn't read as a clock time.

    A blank cell is fine (nothing was entered); a non-time value means a volume
    or free-text note was filed into a time column, so the machine-readable
    ``water_ml`` never got it (finding C of #260810)."""
    out: List[Tuple[str, str, str, str]] = []
    for row in daily_rows or []:
        for field in WATER_TIME_FIELDS:
            val = str(row.get(field, "") or "").strip()
            if val and not looks_like_time(val):
                out.append((str(row.get("animal_id", "") or "").strip(),
                            str(row.get("date", "") or "").strip(), field, val))
    return out


def sessions_missing_daily(
        session_triples: Sequence[Tuple[str, str, str]],
        daily: Optional[Any]) -> List[Tuple[str, str, str]]:
    """``(session_id, animal, date)`` for each session whose ``(animal, date)``
    has no ``daily_log.csv`` row.

    Sessions with a blank animal or date are skipped (there's no key to join on).
    ``daily`` is a ``daily_log.DailyLog`` (or None -> everything counts as
    missing, so the caller can note the table was absent)."""
    out: List[Tuple[str, str, str]] = []
    for sid, animal, date in session_triples:
        a, d = str(animal or "").strip(), str(date or "").strip()
        if not a or not d:
            continue
        if daily is None or daily.for_date(a, d) is None:
            out.append((sid, a, d))
    return out


def noncanonical_animals(animal_ids: Sequence[str],
                         registry: Optional[Any]) -> List[str]:
    """Distinct, sorted animal ids that the canonical registry doesn't recognise.

    Empty when the registry is unavailable (can't validate -> don't warn), so a
    missing ``animals.json`` never produces false positives. ``registry`` is an
    ``animals.AnimalRegistry``."""
    if registry is None or not getattr(registry, "available", False):
        return []
    unknown = set()
    for raw in animal_ids:
        a = str(raw or "").strip()
        if a and not registry.is_known(a):
            unknown.add(a)
    return sorted(unknown)


def short_sessions(rows: Sequence[dict],
                   threshold_s: float = SHORT_SESSION_S
                   ) -> List[Tuple[str, float]]:
    """``(session_id, duration_s)`` for sessions whose duration is a number in
    ``[0, threshold_s)`` (likely start/stop churn).

    A blank/absent/non-numeric duration is skipped (unknown, not necessarily
    short)."""
    out: List[Tuple[str, float]] = []
    for row in rows or []:
        try:
            dur = float(row.get("duration_s"))
        except (TypeError, ValueError):
            continue
        if 0 <= dur < threshold_s:
            out.append((str(row.get("session_id", "") or "").strip(), dur))
    return out


# --------------------------------------------------------------- aggregation
# The detectors above each find one KIND of drift. The pieces below assemble
# them into a single structured report so every caller - the CLI shell
# (tools/records_check.py) and the Records Manager's integrity panel - renders
# the SAME findings without re-implementing the aggregation. Still pure: it takes
# already-loaded records and returns data (no I/O, no printing, no edits).

class AuditSection(NamedTuple):
    """One block of the integrity report: a ``title``, the flagged ``items``
    (formatted, human-readable strings), a one-line ``fix`` hint, and a ``note``
    that is non-empty only when the whole check was SKIPPED (and says why, e.g.
    the underlying table was unavailable). ``count`` is the number of items."""
    title: str
    items: List[str]
    fix: str = ""
    note: str = ""

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def skipped(self) -> bool:
        return bool(self.note)


def session_triples(registry_rows: Sequence[dict],
                    annot_rows: Sequence[dict],
                    ignore: Optional[Set[str]] = None
                    ) -> List[Tuple[str, str, str]]:
    """``(session_id, animal, date)`` for every known session - registry rows
    first, then annotation-only sessions (in the CSV but not the registry) so old
    notes are still checked - skipping ignored ids and duplicates."""
    skip = ignore or set()
    triples: List[Tuple[str, str, str]] = []
    seen: Set[str] = set()
    for src in (registry_rows or [], annot_rows or []):
        for r in src:
            sid = str(r.get("session_id", "") or "").strip()
            if not sid or sid in skip or sid in seen:
                continue
            seen.add(sid)
            triples.append((sid, str(r.get("animal", "") or "").strip(),
                            str(r.get("date", "") or "").strip()))
    return triples


def build_report(triples: Sequence[Tuple[str, str, str]],
                 daily_rows: Sequence[dict],
                 kept_registry: Sequence[dict],
                 daily: Optional[Any],
                 registry: Optional[Any]) -> List[AuditSection]:
    """Assemble the full integrity report from already-loaded records.

    ``daily`` is a ``daily_log.DailyLog`` (or None) and ``registry`` an
    ``animals.AnimalRegistry`` (or None); when either is unavailable its
    section(s) are emitted SKIPPED rather than as false positives. ``triples``
    are the joined sessions (see ``session_triples``), ``kept_registry`` the
    registry rows with ignored ids already removed."""
    sections: List[AuditSection] = []

    if registry is not None and getattr(registry, "available", False):
        unknown = noncanonical_animals([a for _sid, a, _d in triples], registry)
        sections.append(AuditSection(
            "Animal ids not in animals.json", list(unknown),
            "add them to animals.json, or list those sessions in "
            "host/session_ignore.json."))
    else:
        sections.append(AuditSection(
            "Animal ids not in animals.json", [],
            note="animals.json unavailable - skipped"))

    if daily is not None and getattr(daily, "available", False):
        missing = [f"{sid}  ({a} {d})"
                   for sid, a, d in sessions_missing_daily(triples, daily)]
        sections.append(AuditSection(
            "Sessions with no daily_log.csv (animal, date) row", missing,
            "add the (animal, date) husbandry rows to daily_log.csv."))
        suspects = [f"{a} {d}  {field}={val!r}"
                    for a, d, field, val in suspect_water_cells(daily_rows)]
        sections.append(AuditSection(
            "Daily-log water cells that aren't clock times", suspects,
            "move the volume into water_ml (and any note into supp_water/notes); "
            "leave water_on/water_off for on/off times."))
    else:
        sections.append(AuditSection(
            "Sessions with no daily_log.csv (animal, date) row", [],
            note="daily_log.csv unavailable - skipped"))

    short = [f"{sid}  ({dur:.0f} s)"
             for sid, dur in short_sessions(kept_registry)]
    sections.append(AuditSection(
        f"Near-zero-duration sessions (< {SHORT_SESSION_S:.0f} s, "
        "likely start/stop churn)", short,
        "if a listed session isn't real, add it to host/session_ignore.json."))

    return sections


def total_flagged(sections: Sequence[AuditSection]) -> int:
    """Total number of flagged items across all (non-skipped) sections."""
    return sum(s.count for s in sections)
