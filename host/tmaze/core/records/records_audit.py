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
from typing import Any, List, Optional, Sequence, Tuple

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
