"""
dashboard.py - pure welfare-dashboard signals over the daily table (+ registry).

The Records Manager GUI shows a read-only welfare dashboard so the operator can
see, at a glance, whether any rat needs attention before running. This core owns
the four signals it displays (no Tk, no I/O - it takes already-loaded rows so it
stays headlessly testable):

1. **% baseline today** - each rat's weight as a fraction of its baseline, the
   ``< 80%`` red flag (the protocol's water-restriction floor, RA26A102), and the
   *grams-to-80%* make-up deficit (how much weight is missing to clear the flag).
2. **Not-logged-today alarm** - which rats have no daily row yet ("not logged") or
   a row with no weight ("not weighed"), so a forgotten entry is obvious.
3. **Daily water intake by source** - task-earned + operator-manual reward (summed
   from that day's ``session_registry`` rows) plus supplemental and bottle water
   (from the daily row), as a labelled breakdown + total. Totals only; there is no
   target/adequacy flag (that's a human judgement). The four sources are always
   added: ``bottle_water_ml`` is water given outside the task only, never the rig
   reward typed in again (docs/decisions/0208, superseding ADR-0106).
4. **Weight / % baseline trend** - the last ~7-14 days of weight and % baseline
   per rat, oldest-first, for a sparkline/trend view.

Fractions stay 0..1 (a fraction, e.g. 0.8 == 80%); the GUI renders them as %.

Pure stdlib, no Tk/serial. Headless tests: python -m unittest test_dashboard.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from tmaze.core.records import daily_log

# The protocol's water-restriction floor: RA26A102 s.14d maintains rats at
# 80-85% of free-water/feeding weight, so below 80% is a flag. A fraction, not a
# percent. See docs/decisions/0207.
BELOW_THRESHOLD = 0.80
THRESHOLD_LABEL = f"{BELOW_THRESHOLD * 100:.0f}%"

# The provenance-labelled water channels, in display order (mirrors the four
# water channels the session registry surfaces; ADR-0084).
WATER_SOURCES: Tuple[str, ...] = ("task", "manual", "supplemental", "bottle")


def _num(value: Any) -> Optional[float]:
    """Parse a cell to float, or None if blank/non-numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> Optional[dt.date]:
    """Parse a ``YYYY-MM-DD`` string (or pass a date through); None if it doesn't."""
    if isinstance(value, dt.date):
        return value
    try:
        return dt.datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Signal 1: % baseline + below-threshold flag + grams-to-threshold deficit
# --------------------------------------------------------------------------- #
def pct_baseline_of(row: Optional[Dict[str, str]]) -> Optional[float]:
    """A row's % baseline as a fraction, computed from weight/baseline.

    Weight and baseline are the source of truth; ``pct_baseline`` is a derived
    mirror that can drift (e.g. a weight edited on one surface without the mirror
    being recomputed). So we recompute from weight/baseline FIRST and only fall
    back to the stored cell when the row can't be computed (weight or baseline
    missing). :func:`daily_log.write_rows` keeps the stored mirror in step, so in
    practice the two agree - this precedence just makes the reader authoritative
    if they ever don't. Returns None when neither path yields a value.
    """
    if not row:
        return None
    computed = daily_log.compute_pct_baseline(row.get("weight_g"),
                                              row.get("baseline_g"))
    if computed is not None:
        return computed
    return _num(row.get("pct_baseline"))


def below_threshold(pct: Optional[float],
                    threshold: float = BELOW_THRESHOLD) -> bool:
    """Whether a % baseline fraction is under the flag line (None -> False)."""
    return pct is not None and pct < threshold


def grams_to_threshold(row: Optional[Dict[str, str]],
                       threshold: float = BELOW_THRESHOLD) -> Optional[float]:
    """Grams the animal is *below* the threshold weight (baseline * threshold).

    Positive = the make-up deficit needed to clear the flag; 0.0 when at/above
    the line; None when weight or baseline is missing.
    """
    w = _num((row or {}).get("weight_g"))
    b = _num((row or {}).get("baseline_g"))
    if w is None or b is None or b == 0:
        return None
    deficit = b * threshold - w
    return round(deficit, 1) if deficit > 0 else 0.0


def weight_status(log: daily_log.DailyLog, animal_id: str,
                  date: str, threshold: float = BELOW_THRESHOLD
                  ) -> Dict[str, Any]:
    """Signal 1 for one rat on one date: pct, flag, deficit, and the raw scalars.

    ``logged`` is False when there's no row that day (the caller can merge this
    with signal 2); the numeric fields are then None.
    """
    row = log.for_date(animal_id, date)
    pct = pct_baseline_of(row)
    return {
        "animal_id": str(animal_id).strip(),
        "date": str(date).strip(),
        "logged": row is not None,
        "ad_lib": daily_log.is_ad_lib(row),
        "weight_g": _num((row or {}).get("weight_g")),
        "baseline_g": _num((row or {}).get("baseline_g")),
        "pct_baseline": pct,
        "below_threshold": below_threshold(pct, threshold),
        "grams_to_threshold": grams_to_threshold(row, threshold),
    }


# --------------------------------------------------------------------------- #
# Signal 2: not-logged / not-weighed today
# --------------------------------------------------------------------------- #
def not_logged_today(log: daily_log.DailyLog, animal_ids: List[str],
                     date: str) -> Dict[str, str]:
    """`{animal_id: reason}` for rats needing attention today.

    ``"not logged"`` when there's no row for the date; ``"not weighed"`` when a
    row exists but its weight is blank. Rats fully logged + weighed are omitted,
    so a truthy result means "something's missing".

    An ad-lib (off-restriction) day is expected to have no restriction data, so a
    row marked ``water_regime=ad_lib`` never raises the not-weighed alarm
    (ADR-0100). A day with *no row at all* still reads as "not logged" - the
    operator marks the weekend (e.g. via the Records Manager span helper) so the
    ad-lib rows exist to suppress the alarm.
    """
    out: Dict[str, str] = {}
    for aid in animal_ids:
        row = log.for_date(aid, date)
        if row is None:
            out[str(aid).strip()] = "not logged"
        elif daily_log.is_ad_lib(row):
            continue
        elif _num(row.get("weight_g")) is None:
            out[str(aid).strip()] = "not weighed"
    return out


# --------------------------------------------------------------------------- #
# Signal 3: daily water intake by source (totals only, no target flag)
# --------------------------------------------------------------------------- #
def registry_rows_for(registry_rows: List[Dict[str, Any]], animal_id: str,
                      date: str) -> List[Dict[str, Any]]:
    """The ``session_registry`` rows for one `(animal, date)` (a day may hold
    several sessions)."""
    aid = str(animal_id).strip()
    d = str(date).strip()
    return [r for r in registry_rows
            if str(r.get("animal", "")).strip() == aid
            and str(r.get("date", "")).strip() == d]


def collect_water(daily_row: Optional[Dict[str, str]],
                  registry_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Milliliters per source for one `(animal, date)`.

    ``task``/``manual`` are summed from the day's already-filtered
    ``registry_rows`` (see :func:`registry_rows_for`); ``supplemental`` and
    ``bottle`` come from the daily row's ``supp_water_ml`` / ``bottle_water_ml``
    cells, read as mL when numeric and 0 otherwise. The LEGACY free-text
    ``supp_water`` cell is never parsed ("2 min" is not 2 mL; ADR-0160) - an
    unsplit legacy cell simply contributes nothing until a human splits it.

    All four are separate water and are always counted, whatever the day's
    ``water_method``: task water lives only in the registry, so a bottle cell
    holding it is a records error for the audit to flag
    (``records_audit.bottle_water_on_task_day``), not something to guess around
    here (docs/decisions/0208).
    """
    task = sum(_num(r.get("task_reward_ml")) or 0.0 for r in registry_rows)
    manual = sum(_num(r.get("manual_reward_ml")) or 0.0 for r in registry_rows)
    supp = daily_log.supp_water_ml(daily_row) or 0.0
    bottle = _num((daily_row or {}).get("bottle_water_ml")) or 0.0
    return {"task": task, "manual": manual, "supplemental": supp,
            "bottle": bottle}


def water_breakdown(by_source: Dict[str, float]) -> Dict[str, Any]:
    """Labelled non-zero water sources (in WATER_SOURCES order) + the total mL.

    Totals only - there is deliberately no target/adequacy flag (a human call).
    """
    sources: List[Tuple[str, float]] = []
    total = 0.0
    for name in WATER_SOURCES:
        ml = _num(by_source.get(name)) or 0.0
        if ml:
            sources.append((name, round(ml, 3)))
        total += ml
    return {"sources": sources, "total_ml": round(total, 3)}


# --------------------------------------------------------------------------- #
# Signal 4: weight / % baseline trend over the last N days
# --------------------------------------------------------------------------- #
def weight_trend(log: daily_log.DailyLog, animal_id: str, end_date: str,
                 days: int = 14,
                 start_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Oldest-first ``[{date, weight_g, pct_baseline}]`` over a date window.

    The window is ``[start, end]``: ``end`` is ``end_date``; ``start`` is
    ``start_date`` when given (the dashboard's explicit From date), else
    ``end - (days - 1)``. Only rows with a parseable date and a weight are
    included. If a bound doesn't parse it is treated as open, so a bad
    ``end_date`` with no ``start_date`` returns all dated+weighed rows.
    """
    end = _iso(end_date)
    start = (_iso(start_date) if start_date
             else (end - dt.timedelta(days=max(days, 1) - 1) if end else None))
    out: List[Dict[str, Any]] = []
    for row in log.rows_for(animal_id):
        d = _iso(row.get("date"))
        w = _num(row.get("weight_g"))
        if d is None or w is None:
            continue
        if (start is not None and d < start) or (end is not None and d > end):
            continue
        out.append({
            "date": row.get("date", "").strip(),
            "weight_g": w,
            "pct_baseline": pct_baseline_of(row),
            "ad_lib": daily_log.is_ad_lib(row),
        })
    out.sort(key=lambda r: r["date"])
    return out


def water_ml_trend(log: daily_log.DailyLog, registry_rows: List[Dict[str, Any]],
                   animal_id: str, end_date: str,
                   days: int = 14,
                   start_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Oldest-first ``[{date, total_ml}]`` of each day's total water (all four
    channels summed by :func:`collect_water`) over the window ``[start, end]``:
    ``end`` is ``end_date``; ``start`` is ``start_date`` when given, else
    ``end - (days - 1)``.

    A date is included when it has a daily row *or* any registry row for the
    animal within the window; ``total_ml`` is 0.0 when nothing numeric is
    recorded that day. If ``end_date`` doesn't parse, the window is unbounded.

    Ad-lib (off-restriction) days are EXCLUDED: this trace is a read of the
    *maintenance* water level (the amount holding the animal at target during
    restriction), and a weekend of free water is a different regime that would
    distort it (ADR-0100 / FW-0030). The % baseline trend still plots those days.

    This is the optional water overlay behind the dashboard's Show-water toggle;
    it stays sparse until the numeric ``bottle_water_ml``/``supp_water_ml`` cells (or the
    registry's reward channels) are populated.
    """
    end = _iso(end_date)
    start = (_iso(start_date) if start_date
             else (end - dt.timedelta(days=max(days, 1) - 1) if end else None))
    aid = str(animal_id).strip()

    def _in_window(d: Optional[dt.date]) -> bool:
        return (d is not None
                and (start is None or d >= start)
                and (end is None or d <= end))

    dates: set = set()
    for row in log.rows_for(animal_id):
        # Skip ad-lib days entirely (they aren't maintenance-level intake).
        if _in_window(_iso(row.get("date"))) and not daily_log.is_ad_lib(row):
            dates.add(row.get("date", "").strip())
    for r in registry_rows:
        if str(r.get("animal", "")).strip() == aid and _in_window(_iso(r.get("date"))):
            dates.add(str(r.get("date", "")).strip())

    out: List[Dict[str, Any]] = []
    for ds in dates:
        drow = log.for_date(aid, ds)
        if daily_log.is_ad_lib(drow):
            continue
        day_reg = registry_rows_for(registry_rows, aid, ds)
        total = water_breakdown(collect_water(drow, day_reg))["total_ml"]
        out.append({"date": ds, "total_ml": total})
    out.sort(key=lambda r: r["date"])
    return out


# --------------------------------------------------------------------------- #
# Signal 5: weekly-structure spans + Daily-table helpers
# --------------------------------------------------------------------------- #
# These support the weekly-rhythm view: the Dashboard trend shades the weekend
# ad-lib window (and a faint calendar-weekend reference) over a true date axis,
# and the Daily table gets a weekday column, a day-over-day weight delta, and a
# missing-day (gap) indicator. All are pure date math over the daily table, so
# they stay headlessly testable. Windows are an explicit inclusive
# ``[start_date, end_date]`` (the dashboard drives it from From/To dates).
def _date_range(start: Optional[dt.date],
                end: Optional[dt.date]) -> List[dt.date]:
    """Every ``dt.date`` from ``start`` to ``end`` inclusive; ``[]`` if either is
    None or the range is inverted."""
    if start is None or end is None or end < start:
        return []
    out: List[dt.date] = []
    day = start
    while day <= end:
        out.append(day)
        day += dt.timedelta(days=1)
    return out


def _contiguous_spans(dates: Any) -> List[Tuple[dt.date, dt.date]]:
    """Collapse an iterable of ``dt.date`` into sorted inclusive ``(start, end)``
    runs of consecutive calendar days."""
    spans: List[Tuple[dt.date, dt.date]] = []
    for d in sorted(set(dates)):
        if spans and d == spans[-1][1] + dt.timedelta(days=1):
            spans[-1] = (spans[-1][0], d)
        else:
            spans.append((d, d))
    return spans


def ad_lib_spans(log: daily_log.DailyLog, animal_ids: List[str],
                 start_date: str,
                 end_date: str) -> List[Tuple[dt.date, dt.date]]:
    """Inclusive ``(start, end)`` runs of contiguous days in
    ``[start_date, end_date]`` where ANY of ``animal_ids`` is marked ``ad_lib``.

    Union across rats: the weekend free-water window is shared, so one band
    reads for the whole chart, while a lone per-rat deviation still shows
    (ADR-0100). Empty when the range is invalid."""
    start, end = _iso(start_date), _iso(end_date)
    ids = [str(a).strip() for a in (animal_ids or [])]
    marked: List[dt.date] = []
    for day in _date_range(start, end):
        ds = day.isoformat()
        if any(daily_log.is_ad_lib(log.for_date(aid, ds)) for aid in ids):
            marked.append(day)
    return _contiguous_spans(marked)


def weekend_spans(start_date: str,
                  end_date: str) -> List[Tuple[dt.date, dt.date]]:
    """Inclusive ``(Sat, Sun)`` date runs within ``[start_date, end_date]`` - the
    calendar 'norm' reference, independent of the data (Mon=0 .. Sun=6)."""
    start, end = _iso(start_date), _iso(end_date)
    weekend = [d for d in _date_range(start, end) if d.weekday() >= 5]
    return _contiguous_spans(weekend)


def weekday_label(date_str: Any) -> str:
    """A date's weekday abbreviation (``Mon`` .. ``Sun``); ``""`` if it doesn't
    parse. Display-only helper for the Daily table's Day column."""
    d = _iso(date_str)
    return d.strftime("%a") if d else ""


def weight_delta(log: daily_log.DailyLog, animal_id: str,
                 date: str) -> Optional[float]:
    """This day's weight minus the most recent EARLIER weighed day's weight for
    the animal (day-over-day change, rounded to 0.1 g).

    None when this day has no weight or there's no earlier weighed day;
    intervening unweighed days are skipped (compares to the last real weight)."""
    d = _iso(date)
    this = _num((log.for_date(animal_id, str(date).strip()) or {}).get("weight_g"))
    if d is None or this is None:
        return None
    prior: Optional[float] = None
    prior_d: Optional[dt.date] = None
    for row in log.rows_for(animal_id):
        rd, w = _iso(row.get("date")), _num(row.get("weight_g"))
        if rd is None or w is None or rd >= d:
            continue
        if prior_d is None or rd > prior_d:
            prior_d, prior = rd, w
    return None if prior is None else round(this - prior, 1)


def missing_days(log: daily_log.DailyLog, animal_id: str, start_date: str,
                 end_date: str) -> List[str]:
    """ISO dates in ``[start_date, end_date]`` with no daily row for the animal -
    the weekly-adherence gaps. Empty when the range is invalid."""
    start, end = _iso(start_date), _iso(end_date)
    aid = str(animal_id).strip()
    return [d.isoformat() for d in _date_range(start, end)
            if log.for_date(aid, d.isoformat()) is None]
