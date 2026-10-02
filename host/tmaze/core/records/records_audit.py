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

Each flagged item carries a stable ``Finding.key`` so the operator can
acknowledge (dismiss) a finding that can't be "fixed" - a genuinely short real
session, a legacy animal id - and it stays dismissed across runs;
``build_report`` partitions findings into active vs acknowledged given the key
set from ``records_audit_ack`` (ADR-0105). It also flags a ``water_method`` cell
that isn't a known method, reusing ``daily_log.normalize_method`` so the
vocabulary can't drift.

The 2026-09-11 records review (docs/decisions/0159) added four more checks:
annotations for sessions that no longer exist on any recordings root ("ghost"
annotations), sessions whose bundle has no behavioural summary, same-animal
starts within a few minutes of each other (restart churn the duration check
misses), and a bundle's frozen pre-session weight disagreeing with the daily
table's weight for that day.

Pure stdlib (``re``, ``datetime``) plus the sibling ``daily_log`` core (method
vocabulary); headlessly testable.

Headless tests: python -m unittest test_records_audit.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import (Any, Dict, Iterable, List, NamedTuple, Optional, Sequence,
                    Set, Tuple)

from tmaze.core.records import daily_log

# A session shorter than this (seconds) is almost certainly start/stop churn - a
# mis-fire or aborted run - not a real session. Bug #260810 saw bursts of ~12 s
# bundles (5 in ~5 min). Reporting only; nothing is dropped on this basis (real
# short sessions do happen). See docs/decisions/0097.
SHORT_SESSION_S = 60.0

# Two sessions of the SAME animal starting within this many seconds of each
# other are almost certainly one session restarted (a mis-click, a crash, a
# re-plug) - the earlier one is the fragment. Catches restarts whose fragment
# ran longer than SHORT_SESSION_S. 5 min: the review's bursts were 1-3 min
# apart; two genuine sessions of one rat are never that close (the rat has to
# be taken out and put back). See docs/decisions/0159.
CHURN_WINDOW_S = 300.0

# The bundle's frozen pre-session weight (animal_state.json, ADR-0077) and the
# daily table's weight for the same (animal, date) should be the SAME number -
# one was copied from the other at Start. A difference beyond this many grams
# means one of them was edited afterwards or typed twice; the audit says which
# session so the operator can decide which is right. 1 g absorbs a rounding
# (scales read whole grams). See docs/decisions/0159.
WEIGHT_MISMATCH_G = 1.0

# How long a session's clock fit may stay FAILED before the audit flags it - a
# grace period so a just-recorded session the operator hasn't fit yet isn't
# nagged. 14 days: a fit that hasn't taken in two weeks needs attention (re-run
# sync_fit, or acknowledge if it's genuinely unfittable). Only `failed` sessions
# are gated this way; `host-t0` (no LED fiducials in frame / never attempted) is
# never flagged, so the check stays noise-free while LED-in-FOV coverage is
# incomplete. See docs/decisions/0188.
UNFITTED_AGE_DAYS = 14.0

# A clock-time cell looks like "4:30pm", "16:04", "3:15PM", "5:30", or a bare
# "6pm"/"1 PM" (hour with minutes, or hour with am/pm - a lone number isn't a
# time). Anything else non-blank in a time column (e.g. "8.46ml rig; No suppl",
# "Till Monday evening") is a water VOLUME or a free-text note misfiled into the
# on/off TIME column - finding C of bug #260810.
_TIME_RE = re.compile(
    r"^\s*\d{1,2}(?::\d{2}\s*(?:[ap]m)?|\s*[ap]m)\s*$", re.IGNORECASE)

# The two daily-log columns that are meant to hold clock times.
WATER_TIME_FIELDS = ("water_on", "water_off")

# A bottle-water cell within this many mL of the day's rig reward (task +
# manual) is taken to be the rig reward typed in again, not separate bottle
# water. 0.05 mL is below any real bottle measurement but covers the 2-dp
# rounding of a hand copy (August's 602 rows matched to 0.00). See
# docs/decisions/0208.
BOTTLE_MATCHES_RIG_ML = 0.05


def looks_like_time(value: Any) -> bool:
    """Whether ``value`` reads as a clock time (see ``_TIME_RE``)."""
    return bool(_TIME_RE.match(str(value if value is not None else "")))


def suspect_water_cells(
        daily_rows: Sequence[dict]) -> List[Tuple[str, str, str, str]]:
    """``(animal_id, date, field, value)`` for every non-blank ``water_on`` /
    ``water_off`` cell that doesn't read as a clock time.

    A blank cell is fine (nothing was entered); a non-time value means a volume
    or free-text note was filed into a time column, so the machine-readable
    ``bottle_water_ml`` never got it (finding C of #260810)."""
    out: List[Tuple[str, str, str, str]] = []
    for row in daily_rows or []:
        for field in WATER_TIME_FIELDS:
            val = str(row.get(field, "") or "").strip()
            if val and not looks_like_time(val):
                out.append((str(row.get("animal_id", "") or "").strip(),
                            str(row.get("date", "") or "").strip(), field, val))
    return out


def suspect_water_methods(
        daily_rows: Sequence[dict]) -> List[Tuple[str, str, str]]:
    """``(animal_id, date, value)`` for each non-blank ``water_method`` cell that
    isn't a recognised method (``daily_log.normalize_method`` folds it to "").

    Both write surfaces normalise the method (the GUI Save and the Excel
    ``--export``), so this only fires on a hand-edited CSV holding a typo/unknown
    token - making that drift visible before it silently means "unspecified"
    (ADR-0104)."""
    out: List[Tuple[str, str, str]] = []
    for row in daily_rows or []:
        val = str(row.get("water_method", "") or "").strip()
        if val and not daily_log.normalize_method(val):
            out.append((str(row.get("animal_id", "") or "").strip(),
                        str(row.get("date", "") or "").strip(), val))
    return out


def unsplit_supp_water(
        daily_rows: Sequence[dict]) -> List[Tuple[str, str, str]]:
    """``(animal_id, date, legacy_text)`` for each row whose legacy free-text
    ``supp_water`` cell is filled while BOTH v4 split cells (``supp_water_ml``,
    ``supp_access_min``) are blank (``daily_log.supp_unsplit``; ADR-0160).

    The legacy cell mixed volumes ("2.5 ml") and access durations ("2 min"),
    so no reader parses it; until a human moves each value into the right
    numeric cell that day's supplemental water counts as zero everywhere. This
    is the to-do list for that one-time split - the list shrinks as cells are
    split and the legacy column can be dropped once it is empty."""
    out: List[Tuple[str, str, str]] = []
    for row in daily_rows or []:
        if daily_log.supp_unsplit(row):
            out.append((str(row.get("animal_id", "") or "").strip(),
                        str(row.get("date", "") or "").strip(),
                        str(row.get(daily_log.SUPP_LEGACY_FIELD, "")).strip()))
    return out


def bottle_water_on_task_day(
        daily_rows: Sequence[dict],
        registry_rows: Sequence[dict]) -> List[Tuple[str, str, float, str]]:
    """``(animal_id, date, bottle_ml, reason)`` for each row whose numeric
    ``bottle_water_ml`` looks like the rig's task water typed in by hand.

    Two signs, either is enough: the day's ``water_method`` is ``task_earned``
    (the water came from the task, so there was no bottle), or the number is
    within :data:`BOTTLE_MATCHES_RIG_ML` of that day's rig reward (task +
    manual from ``registry_rows``). The daily total adds bottle water to the
    rig reward, so such a cell double-counts until it is cleared
    (docs/decisions/0208)."""
    out: List[Tuple[str, str, float, str]] = []
    for row in daily_rows or []:
        bottle = _num(row.get("bottle_water_ml"))
        if bottle is None or bottle <= 0:
            continue
        aid = str(row.get("animal_id", "") or "").strip()
        date = str(row.get("date", "") or "").strip()
        if (daily_log.normalize_method(row.get("water_method", ""))
                == daily_log.METHOD_TASK_EARNED):
            out.append((aid, date, bottle, "method is task_earned"))
            continue
        rig = sum((_num(r.get("task_reward_ml")) or 0.0)
                  + (_num(r.get("manual_reward_ml")) or 0.0)
                  for r in registry_rows or []
                  if str(r.get("animal", "")).strip() == aid
                  and str(r.get("date", "")).strip() == date)
        if rig > 0 and abs(bottle - rig) <= BOTTLE_MATCHES_RIG_ML:
            out.append((aid, date, bottle, f"equals the rig reward {rig:g} mL"))
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


def animals_missing_dob(registry: Optional[Any]) -> List[str]:
    """Canonical ids in ``animals.json`` with no ``date_of_birth`` recorded
    (schema v2, ADR-0161). Empty when the registry is unavailable. Age is
    derived from this one field for every session and welfare row, so a blank
    means every age downstream is unknown for that rat."""
    if registry is None or not getattr(registry, "available", False):
        return []
    return [aid for aid in registry.ids() if not registry.date_of_birth(aid)]


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


def ghost_annotations(annot_rows: Sequence[dict],
                      on_disk_ids: Iterable[str],
                      ignore: Optional[Set[str]] = None
                      ) -> List[Tuple[str, str, str]]:
    """``(session_id, animal, date)`` for each annotation whose session has NO
    bundle under any recordings root and is not on the ignore list.

    An annotation is keyed by session id (ADR-0082); if the bundle was deleted,
    renamed, or lives on a drive that isn't mounted, the note is orphaned and
    the View sheets show a session the registry can't. ``on_disk_ids`` are the
    bundle folder names found by scanning the configured roots."""
    have = {str(s or "").strip() for s in on_disk_ids}
    skip = ignore or set()
    out: List[Tuple[str, str, str]] = []
    for r in annot_rows or []:
        sid = str(r.get("session_id", "") or "").strip()
        if not sid or sid in skip or sid in have:
            continue
        out.append((sid, str(r.get("animal", "") or "").strip(),
                    str(r.get("date", "") or "").strip()))
    return out


# Registry columns that are ALL blank when the bundle has no summary/ folder
# (they come only from session_summary_*.json; ADR-0081).
_SUMMARY_COLUMNS = ("start_iso", "duration_s", "trials_total", "task_reward_ml")


def sessions_without_summary(rows: Sequence[dict]) -> List[str]:
    """Session ids whose registry row has none of the summary-derived columns
    filled - the bundle was recorded without a behavioural summary (a crash
    before Stop, or a very old bundle), so behaviour is unknown for it."""
    out: List[str] = []
    for row in rows or []:
        if all(not str(row.get(c, "") or "").strip() for c in _SUMMARY_COLUMNS):
            sid = str(row.get("session_id", "") or "").strip()
            if sid:
                out.append(sid)
    return out


def _start_time(row: dict) -> Optional[_dt.datetime]:
    """A session's start as a datetime: the summary's ``start_iso`` when it
    parses, else the ``<animal>_<YYYYMMDD>_<HHMMSS>`` stamp in the session id
    (so a bundle without a summary still takes part)."""
    iso = str(row.get("start_iso", "") or "").strip()
    if iso:
        try:
            return _dt.datetime.fromisoformat(iso).replace(tzinfo=None)
        except ValueError:
            pass
    sid = str(row.get("session_id", "") or "").strip()
    parts = sid.rsplit("_", 2)
    if len(parts) == 3 and len(parts[1]) == 8 and len(parts[2]) == 6 \
            and parts[1].isdigit() and parts[2].isdigit():
        try:
            return _dt.datetime.strptime(parts[1] + parts[2], "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def churn_pairs(rows: Sequence[dict],
                window_s: float = CHURN_WINDOW_S
                ) -> List[Tuple[str, str, float]]:
    """``(later_session_id, earlier_session_id, gap_s)`` for every pair of
    CONSECUTIVE same-animal sessions whose starts are less than ``window_s``
    apart - i.e. a restart. The later session carries the finding (the earlier
    one is usually the fragment, but the operator decides). Rows without an
    animal or a parseable start are skipped."""
    by_animal: Dict[str, List[Tuple[_dt.datetime, str]]] = {}
    for row in rows or []:
        animal = str(row.get("animal", "") or "").strip()
        sid = str(row.get("session_id", "") or "").strip()
        t = _start_time(row)
        if not animal or not sid or t is None:
            continue
        by_animal.setdefault(animal, []).append((t, sid))
    out: List[Tuple[str, str, float]] = []
    for animal in sorted(by_animal):
        starts = sorted(by_animal[animal])
        for (t0, s0), (t1, s1) in zip(starts, starts[1:]):
            gap = (t1 - t0).total_seconds()
            if gap < window_s:
                out.append((s1, s0, gap))
    return out


def _num(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def weight_mismatches(rows: Sequence[dict], daily: Optional[Any],
                      tolerance_g: float = WEIGHT_MISMATCH_G
                      ) -> List[Tuple[str, str, str, float, float]]:
    """``(session_id, animal, date, bundle_weight_g, daily_weight_g)`` for each
    session whose registry weight came from the BUNDLE (``husbandry_source`` is
    not the daily-table join) and differs from the daily table's weight for the
    same (animal, date) by more than ``tolerance_g``.

    Both numbers describe the same weighing, so a disagreement means one was
    edited or re-typed afterwards; the audit surfaces it rather than picking a
    winner. Rows with either weight blank/non-numeric are skipped."""
    if daily is None:
        return []
    out: List[Tuple[str, str, str, float, float]] = []
    for row in rows or []:
        src = str(row.get("husbandry_source", "") or "").strip()
        if src in ("", "none", "daily_table_join"):
            continue
        bundle_w = _num(row.get("weight_g"))
        animal = str(row.get("animal", "") or "").strip()
        date = str(row.get("date", "") or "").strip()
        if bundle_w is None or not animal or not date:
            continue
        try:
            drow = daily.for_date(animal, date)
        except Exception:
            drow = None
        daily_w = _num((drow or {}).get("weight_g"))
        if daily_w is None:
            continue
        if abs(bundle_w - daily_w) > tolerance_g:
            out.append((str(row.get("session_id", "") or "").strip(),
                        animal, date, bundle_w, daily_w))
    return out


def unfitted_sessions(rows: Sequence[dict],
                      now: Optional[_dt.datetime] = None,
                      age_days: float = UNFITTED_AGE_DAYS
                      ) -> List[Tuple[str, str, str]]:
    """``(session_id, animal, date)`` for sessions whose video<->firmware clock
    fit was ATTEMPTED but did not take (registry ``sync_method == 'failed'``) and
    whose start is more than ``age_days`` old.

    GATED on evidence a fit is possible (docs/decisions/0188): a bundle is only
    ``failed`` when LED fiducials were detected in-frame but no usable fit came
    out - genuinely actionable. ``host-t0`` (no fiducials seen, or never run
    through ``sync_fit``) is NOT flagged, so the check produces no noise while
    LED-in-FOV coverage across the rig history is still incomplete. The age gate
    is a grace period; a session with no parseable start is included (can't
    grace-gate it). Empty when no row carries a ``sync_method`` (a pre-v4
    registry) - nothing to judge."""
    now = now or _dt.datetime.now()
    cutoff = now - _dt.timedelta(days=age_days)
    out: List[Tuple[str, str, str]] = []
    for row in rows or []:
        if str(row.get("sync_method", "") or "").strip() != "failed":
            continue
        t = _start_time(row)
        if t is not None and t > cutoff:
            continue                       # too recent - within the grace period
        sid = str(row.get("session_id", "") or "").strip()
        if sid:
            out.append((sid, str(row.get("animal", "") or "").strip(),
                        str(row.get("date", "") or "").strip()))
    return out


# --------------------------------------------------------------- aggregation
# The detectors above each find one KIND of drift. The pieces below assemble
# them into a single structured report so every caller - the CLI shell
# (tools/records_check.py) and the Records Manager's integrity panel - renders
# the SAME findings without re-implementing the aggregation. Still pure: it takes
# already-loaded records and returns data (no I/O, no printing, no edits).

class Finding(NamedTuple):
    """One flagged item: a stable ``key`` (its cross-run identity, so the operator
    can acknowledge/dismiss it and it stays dismissed) plus the human-readable
    ``text`` shown in the report. The key is ``"<section id>:<natural key>"`` -
    stable across runs and reorderings, and it changes if the underlying problem
    moves to a different row/session (so a fix genuinely clears it)."""
    key: str
    text: str


class AuditSection(NamedTuple):
    """One block of the integrity report: a stable ``id``, a ``title``, the ACTIVE
    ``findings`` (unacknowledged), a one-line ``fix`` hint, a ``note`` that is
    non-empty only when the whole check was SKIPPED (says why, e.g. the table was
    unavailable), and the ``acknowledged`` findings dismissed via the ack store.

    ``items``/``count`` reflect the ACTIVE findings only (acknowledged ones are
    dismissed from the count on purpose), so existing callers that read
    ``.items``/``.count`` see just the actionable list."""
    id: str
    title: str
    findings: List[Finding]
    fix: str = ""
    note: str = ""
    acknowledged: Sequence[Finding] = ()

    @property
    def items(self) -> List[str]:
        return [f.text for f in self.findings]

    @property
    def count(self) -> int:
        return len(self.findings)

    @property
    def acknowledged_count(self) -> int:
        return len(self.acknowledged)

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


def _section(section_id: str, title: str, findings: Sequence[Finding],
             fix: str, acknowledged: Set[str]) -> AuditSection:
    """Build a section, splitting its findings into active vs acknowledged by
    stable key (an acknowledged finding is dismissed from the active list/count
    but retained under ``acknowledged`` so the operator can review/restore it)."""
    active = [f for f in findings if f.key not in acknowledged]
    acked = [f for f in findings if f.key in acknowledged]
    return AuditSection(section_id, title, active, fix, "", acked)


def build_report(triples: Sequence[Tuple[str, str, str]],
                 daily_rows: Sequence[dict],
                 kept_registry: Sequence[dict],
                 daily: Optional[Any],
                 registry: Optional[Any],
                 acknowledged: Optional[Set[str]] = None,
                 annot_rows: Optional[Sequence[dict]] = None,
                 on_disk_ids: Optional[Iterable[str]] = None,
                 ignore: Optional[Set[str]] = None) -> List[AuditSection]:
    """Assemble the full integrity report from already-loaded records.

    ``daily`` is a ``daily_log.DailyLog`` (or None) and ``registry`` an
    ``animals.AnimalRegistry`` (or None); when either is unavailable its
    section(s) are emitted SKIPPED rather than as false positives. ``triples``
    are the joined sessions (see ``session_triples``), ``kept_registry`` the
    registry rows with ignored ids already removed. ``acknowledged`` is the set
    of finding keys the operator has dismissed (``records_audit_ack``); those are
    partitioned out of each section's active findings.

    ``annot_rows`` + ``on_disk_ids`` (the bundle folder names found under the
    recordings roots) feed the ghost-annotation check; pass ``on_disk_ids=None``
    when the roots could not be scanned and that section is SKIPPED instead of
    calling every annotation a ghost. ``ignore`` is the ignore-list."""
    acked = acknowledged or set()
    sections: List[AuditSection] = []

    sid = "noncanonical_animals"
    if registry is not None and getattr(registry, "available", False):
        findings = [Finding(f"{sid}:{a}", a)
                    for a in noncanonical_animals(
                        [a for _s, a, _d in triples], registry)]
        sections.append(_section(
            sid, "Animal ids not in animals.json", findings,
            "add them to animals.json, or list those sessions in "
            "host/session_ignore.json.", acked))
        sid = "missing_date_of_birth"
        findings = [Finding(f"{sid}:{a}", a)
                    for a in animals_missing_dob(registry)]
        sections.append(_section(
            sid, "Animals with no date_of_birth in animals.json", findings,
            "look the birth date up in the animal-facility / protocol records "
            "and enter it in the Roster tab (YYYY-MM-DD), then Save roster.",
            acked))
    else:
        sections.append(AuditSection(
            sid, "Animal ids not in animals.json", [],
            note="animals.json unavailable - skipped"))

    sid = "sessions_missing_daily"
    if daily is not None and getattr(daily, "available", False):
        findings = [Finding(f"{sid}:{s}", f"{s}  ({a} {d})")
                    for s, a, d in sessions_missing_daily(triples, daily)]
        sections.append(_section(
            sid, "Sessions with no daily_log.csv (animal, date) row", findings,
            "add the (animal, date) husbandry rows to daily_log.csv.", acked))
        sid = "suspect_water_cells"
        findings = [Finding(f"{sid}:{a}:{d}:{field}",
                            f"{a} {d}  {field}={val!r}")
                    for a, d, field, val in suspect_water_cells(daily_rows)]
        sections.append(_section(
            sid, "Daily-log water cells that aren't clock times", findings,
            "move a bottle volume into bottle_water_ml (and any note into "
            "notes; rig water is already in the session registry); "
            "leave water_on/water_off for on/off times.", acked))
        sid = "suspect_water_methods"
        findings = [Finding(f"{sid}:{a}:{d}", f"{a} {d}  water_method={val!r}")
                    for a, d, val in suspect_water_methods(daily_rows)]
        sections.append(_section(
            sid, "Daily-log water_method cells that aren't a known method",
            findings,
            "use one of: " + ", ".join(daily_log.WATER_METHODS)
            + " (or clear the cell).", acked))
        sid = "unsplit_supp_water"
        findings = [Finding(f"{sid}:{a}:{d}", f"{a} {d}  supp_water={val!r}")
                    for a, d, val in unsplit_supp_water(daily_rows)]
        sections.append(_section(
            sid, "Legacy supp_water cells not yet split into mL / minutes",
            findings,
            "in the Daily tab load the row, type the volume into "
            "'Supp. water (mL)' and/or the access time into 'Supp. access "
            "(min)', then clear the legacy cell and save. Until then that "
            "day's supplemental water counts as 0 mL.", acked))
        sid = "bottle_water_on_task_day"
        findings = [Finding(f"{sid}:{a}:{d}",
                            f"{a} {d}  bottle_water_ml={ml:g} ({why})")
                    for a, d, ml, why in bottle_water_on_task_day(
                        daily_rows, kept_registry)]
        sections.append(_section(
            sid, "Bottle mL cells that look like task water typed in by hand",
            findings,
            "the rig's task water is already shown in Task mL / Man mL and is "
            "added to the total, so this day is counted twice: in the Daily "
            "tab load the row, clear 'Bottle water (mL)' and save (keep it only "
            "if a real bottle was given as well, then acknowledge).", acked))
    else:
        sections.append(AuditSection(
            sid, "Sessions with no daily_log.csv (animal, date) row", [],
            note="daily_log.csv unavailable - skipped"))

    sid = "short_sessions"
    findings = [Finding(f"{sid}:{s}", f"{s}  ({dur:.0f} s)")
                for s, dur in short_sessions(kept_registry)]
    sections.append(_section(
        sid, f"Near-zero-duration sessions (< {SHORT_SESSION_S:.0f} s, "
        "likely start/stop churn)", findings,
        "if a listed session isn't real, add it to host/session_ignore.json.",
        acked))

    sid = "churn_window"
    findings = [Finding(f"{sid}:{later}",
                        f"{later}  starts {gap:.0f} s after {earlier}")
                for later, earlier, gap in churn_pairs(kept_registry)]
    sections.append(_section(
        sid, f"Same-animal sessions starting < {CHURN_WINDOW_S / 60:.0f} min "
        "apart (likely a restart)", findings,
        "decide which of the pair is the fragment and add it to "
        "host/session_ignore.json; acknowledge if both are real.", acked))

    sid = "no_summary"
    findings = [Finding(f"{sid}:{s}", s)
                for s in sessions_without_summary(kept_registry)]
    sections.append(_section(
        sid, "Sessions whose bundle has no behavioural summary", findings,
        "the session ended without Stop (crash/kill) or predates the summary "
        "file; behaviour is unknown for it - acknowledge, or ignore-list it if "
        "it wasn't a real session.", acked))

    sid = "unfitted_sync"
    findings = [Finding(f"{sid}:{s}", f"{s}  ({a} {d})")
                for s, a, d in unfitted_sessions(kept_registry)]
    sections.append(_section(
        sid, f"Sessions with a failed video<->firmware clock fit "
        f"(> {UNFITTED_AGE_DAYS:.0f} days old)", findings,
        "re-run 'python -m tmaze.tools.sync_fit <bundle> --refit' (or the "
        "Records Manager 'Fit pending sessions' button); acknowledge if the fit "
        "genuinely can't converge for that session.", acked))

    sid = "weight_mismatch"
    if daily is not None and getattr(daily, "available", False):
        findings = [Finding(f"{sid}:{s}",
                            f"{s}  bundle {bw:g} g vs daily_log {dw:g} g  "
                            f"({a} {d})")
                    for s, a, d, bw, dw in weight_mismatches(kept_registry, daily)]
        sections.append(_section(
            sid, f"Bundle pre-session weight differs from daily_log.csv "
            f"(> {WEIGHT_MISMATCH_G:g} g)", findings,
            "one of the two was edited or re-typed after Start; correct the "
            "wrong one (the bundle's animal_state.json is the frozen record; "
            "daily_log.csv is what the dashboard trends).", acked))
    else:
        sections.append(AuditSection(
            sid, "Bundle pre-session weight differs from daily_log.csv", [],
            note="daily_log.csv unavailable - skipped"))

    sid = "ghost_annotations"
    if on_disk_ids is not None:
        findings = [Finding(f"{sid}:{s}", f"{s}  ({a} {d})")
                    for s, a, d in ghost_annotations(
                        annot_rows or [], on_disk_ids, ignore)]
        sections.append(_section(
            sid, "Annotations for sessions with no bundle on any recordings "
            "root", findings,
            "if the bundle is on a drive that isn't mounted, mount it and "
            "re-check; if it was deleted on purpose, remove the annotation row "
            "(or add the id to host/session_ignore.json).", acked))
    else:
        sections.append(AuditSection(
            sid, "Annotations for sessions with no bundle on any recordings "
            "root", [], note="recordings roots not scanned - skipped"))

    return sections


def total_flagged(sections: Sequence[AuditSection]) -> int:
    """Total number of ACTIVE flagged items across all (non-skipped) sections
    (acknowledged findings are excluded by design)."""
    return sum(s.count for s in sections)


def total_acknowledged(sections: Sequence[AuditSection]) -> int:
    """Total number of acknowledged (dismissed) findings across all sections."""
    return sum(s.acknowledged_count for s in sections)
