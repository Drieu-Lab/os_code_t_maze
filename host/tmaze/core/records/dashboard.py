"""
dashboard.py - pure welfare-dashboard signals over the daily table (+ registry).

The Records Manager GUI shows a read-only welfare dashboard so the operator can
see, at a glance, whether any rat needs attention before running. This core owns
the four signals it displays (no Tk, no I/O - it takes already-loaded rows so it
stays headlessly testable):

1. **% baseline today** - each rat's weight as a fraction of its baseline, the
   ``< 90%`` red flag (the protocol's restriction floor, RA23A232), and the
   *grams-to-90%* make-up deficit (how much weight is missing to clear the flag).
2. **Not-logged-today alarm** - which rats have no daily row yet ("not logged") or
   a row with no weight ("not weighed"), so a forgotten entry is obvious.
3. **Daily water intake by source** - task-earned + operator-manual reward (summed
   from that day's ``session_registry`` rows) plus supplemental and cage water
   (from the daily row), as a labelled breakdown + total. Totals only; there is no
   target/adequacy flag (that's a human judgement).
4. **Weight / % baseline trend** - the last ~7-14 days of weight and % baseline
   per rat, oldest-first, for a sparkline/trend view.

Fractions stay 0..1 (a fraction, e.g. 0.9 == 90%); the GUI renders them as %.

Pure stdlib, no Tk/serial. Headless tests: python -m unittest test_dashboard.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from tmaze.core.records import daily_log

# The protocol's restriction floor: below 90% of free-feeding baseline is a flag
# (RA23A232). A fraction, not a percent.
BELOW_THRESHOLD = 0.90

# The provenance-labelled water channels, in display order (mirrors the four
# water channels the session registry surfaces; ADR-0084).
WATER_SOURCES: Tuple[str, ...] = ("task", "manual", "supplemental", "cage")


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
# Signal 1: % baseline + <90% flag + grams-to-threshold deficit
# --------------------------------------------------------------------------- #
def pct_baseline_of(row: Optional[Dict[str, str]]) -> Optional[float]:
    """A row's % baseline as a fraction: the stored value if numeric, else
    computed from weight/baseline, else None."""
    if not row:
        return None
    stored = _num(row.get("pct_baseline"))
    if stored is not None:
        return stored
    return daily_log.compute_pct_baseline(row.get("weight_g"),
                                          row.get("baseline_g"))


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
    ``cage`` come from the daily row's ``supp_water`` / ``water_ml`` cells, read
    as mL when numeric and 0 otherwise (those cells may hold a time or be blank).
    """
    task = sum(_num(r.get("task_reward_ml")) or 0.0 for r in registry_rows)
    manual = sum(_num(r.get("manual_reward_ml")) or 0.0 for r in registry_rows)
    supp = _num((daily_row or {}).get("supp_water")) or 0.0
    cage = _num((daily_row or {}).get("water_ml")) or 0.0
    return {"task": task, "manual": manual, "supplemental": supp, "cage": cage}


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
                 days: int = 14) -> List[Dict[str, Any]]:
    """Oldest-first ``[{date, weight_g, pct_baseline}]`` over the window ending
    (inclusive) at ``end_date``, spanning ``days`` calendar days.

    Only rows with a parseable date and a weight are included. If ``end_date``
    doesn't parse, all dated+weighed rows for the animal are returned (still
    oldest-first) so the caller still gets a trend.
    """
    end = _iso(end_date)
    start = end - dt.timedelta(days=max(days, 1) - 1) if end else None
    out: List[Dict[str, Any]] = []
    for row in log.rows_for(animal_id):
        d = _iso(row.get("date"))
        w = _num(row.get("weight_g"))
        if d is None or w is None:
            continue
        if start is not None and not (start <= d <= end):
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
                   days: int = 14) -> List[Dict[str, Any]]:
    """Oldest-first ``[{date, total_ml}]`` of each day's total water (all four
    channels summed by :func:`collect_water`) over the window ending (inclusive)
    at ``end_date``, spanning ``days`` calendar days.

    A date is included when it has a daily row *or* any registry row for the
    animal within the window; ``total_ml`` is 0.0 when nothing numeric is
    recorded that day. If ``end_date`` doesn't parse, the window is unbounded.

    Ad-lib (off-restriction) days are EXCLUDED: this trace is a read of the
    *maintenance* water level (the amount holding the animal at target during
    restriction), and a weekend of free water is a different regime that would
    distort it (ADR-0100 / FW-0030). The % baseline trend still plots those days.

    This is the optional water overlay behind the dashboard's Show-water toggle;
    it stays sparse until the numeric ``water_ml``/``supp_water`` cells (or the
    registry's reward channels) are populated.
    """
    end = _iso(end_date)
    start = end - dt.timedelta(days=max(days, 1) - 1) if end else None
    aid = str(animal_id).strip()

    def _in_window(d: Optional[dt.date]) -> bool:
        return d is not None and (start is None or start <= d <= end)

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
