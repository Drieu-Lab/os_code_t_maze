"""
daily_log.py - read the resources repo's daily husbandry table (pure core).

The resources repo owns the machine-readable daily husbandry table
`records/animal/daily_log.csv` (the source of truth defined in
docs/decisions/0076): one row per `(animal_id, date)` with weight, baseline,
% baseline, water on/off, water mL, supplemental water, and free-text notes,
behind a leading `# schema_version=N` comment line.

This core is the *code-repo* reader/writer for that file. It resolves the
resources repo via the shared sibling-path resolver (``animals.resources_root``,
ADR-0075), reads the CSV with stdlib ``csv`` (no new dependency), and answers the
two lookups the rig needs: the row for a given `(animal, date)` (WS2 prefill / WS4
exact join) and the most recent row for an animal on or before a date (to recover
a baseline when today's row hasn't been entered yet).

It also *writes* the table for the Records Manager GUI: :func:`write_rows`
serialises the same on-disk shape the resources ``build_daily_log.py`` writer uses
(a ``# schema_version`` comment line, then the header + one deduped, sorted row per
``(animal_id, date)``), and :func:`upsert_rows` merges GUI edits into the existing
rows by that key so a save never drops a row another surface (the Excel Entry
sheet) added. Writes are atomic (temp file + ``os.replace``). The CSV stays the
single source of truth; the Excel workbook is a reconciled, hand-editable fallback
(the plan's dual-surface model).

Every read is best-effort: a missing/renamed resources checkout or a malformed
file yields an empty table rather than raising, so Session Start still works
without the husbandry data (it just falls back to a Start dialog - see the
controller).

Pure stdlib (``csv`` / ``os``), no Tk/serial, headlessly testable.

Headless tests: python -m unittest test_daily_log.
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

from tmaze.core.records import animals

# Column order mirrors the resources builder (records/animal/build_daily_log.py).
FIELDS = [
    "animal_id", "date", "weight_g", "baseline_g", "pct_baseline",
    "water_on", "water_off", "water_ml", "supp_water", "notes",
]

# Bumped in lockstep with the resources builder's schema (docs/decisions/0076).
SCHEMA_VERSION = 1

# The two columns that identify a row; edits/writes dedup + sort on this key.
KEY_FIELDS = ("animal_id", "date")


def default_daily_log_path() -> str:
    """Path to `records/animal/daily_log.csv` in the sibling resources repo."""
    return os.path.join(animals.resources_root(), "records", "animal",
                        "daily_log.csv")


def load_rows(path: Optional[str] = None) -> List[Dict[str, str]]:
    """Read the daily table best-effort; [] if it's missing/malformed.

    Skips the leading `# schema_version` comment line (and any `#` line), then
    parses the header + rows with ``csv``.
    """
    p = path or default_daily_log_path()
    try:
        with open(p, "r", encoding="utf-8", newline="") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    except (OSError, ValueError):
        return []
    reader = csv.DictReader(lines)
    rows = []
    for row in reader:
        rows.append({k: (row.get(k) or "").strip() for k in FIELDS})
    return rows


class DailyLog:
    """The daily husbandry rows, with the two lookups the rig uses.

    Single-threaded like the other cores. ``available`` is False for an empty
    table (missing file), so the caller can fall back rather than treat a blank
    lookup as "no data for this animal today".
    """

    def __init__(self, rows: Optional[List[Dict[str, str]]] = None) -> None:
        self._rows: List[Dict[str, str]] = list(rows or [])

    @property
    def available(self) -> bool:
        return bool(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def rows_for(self, animal_id: str) -> List[Dict[str, str]]:
        """Every row for an animal, oldest first (ISO dates sort chronologically).

        Used by the welfare dashboard's weight/% baseline trend (WS4)."""
        aid = str(animal_id).strip()
        out = [dict(r) for r in self._rows if r.get("animal_id") == aid]
        out.sort(key=lambda r: r.get("date", ""))
        return out

    def for_date(self, animal_id: str, date: str) -> Optional[Dict[str, str]]:
        """The row for an exact `(animal_id, date)`, or None."""
        aid = str(animal_id).strip()
        d = str(date).strip()
        for row in self._rows:
            if row.get("animal_id") == aid and row.get("date") == d:
                return dict(row)
        return None

    def latest_for(self, animal_id: str,
                   on_or_before: Optional[str] = None) -> Optional[Dict[str, str]]:
        """The most recent row for an animal (optionally on/before a date), or None.

        Dates are ISO ``YYYY-MM-DD`` so a lexicographic ``max`` is chronological.
        Used to recover a baseline when today's row hasn't been entered yet.
        """
        aid = str(animal_id).strip()
        cutoff = str(on_or_before).strip() if on_or_before else None
        best = None
        for row in self._rows:
            if row.get("animal_id") != aid:
                continue
            d = row.get("date", "")
            if not d or (cutoff is not None and d > cutoff):
                continue
            if best is None or d > best.get("date", ""):
                best = row
        return dict(best) if best is not None else None


def load(path: Optional[str] = None) -> DailyLog:
    """Load the daily husbandry table best-effort (default: the resources repo)."""
    return DailyLog(load_rows(path))


# --------------------------------------------------------------------------- #
# Writing (Records Manager GUI). The CSV is the source of truth; these keep it
# in the exact shape the resources builder emits so either surface can rebuild
# the Excel workbook from it.
# --------------------------------------------------------------------------- #

def _key(row: Dict[str, str]) -> tuple:
    """The `(animal_id, date)` identity of a row, whitespace-normalised."""
    return tuple(str(row.get(f, "") or "").strip() for f in KEY_FIELDS)


def _normalise(row: Dict[str, str]) -> Dict[str, str]:
    """A row projected onto FIELDS as trimmed strings (None -> "")."""
    return {f: str(row.get(f, "") or "").strip() for f in FIELDS}


def compute_pct_baseline(weight, baseline) -> Optional[float]:
    """weight / baseline as a fraction rounded to 4 dp, or None if not computable.

    Mirrors the Entry sheet's ``% baseline`` formula so the GUI can fill the
    column the same way the workbook does. Blank/zero/non-numeric -> None (the
    caller writes "" rather than a bogus number).
    """
    try:
        w = float(weight)
        b = float(baseline)
    except (TypeError, ValueError):
        return None
    if b == 0:
        return None
    return round(w / b, 4)


def upsert_rows(existing: List[Dict[str, str]],
                edits: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Merge ``edits`` into ``existing`` by `(animal_id, date)`, edits winning.

    A GUI save reads the current CSV (``existing``) and applies only the rows the
    user touched (``edits``) - a whole-row replace per key, never a blind
    overwrite of the file - so rows added by another surface (the Excel Entry
    sheet) survive. Returns a fresh list; input lists are not mutated. Ordering
    is irrelevant here because :func:`write_rows` sorts.
    """
    by_key: Dict[tuple, Dict[str, str]] = {}
    for row in existing:
        by_key[_key(row)] = _normalise(row)
    for row in edits:
        by_key[_key(row)] = _normalise(row)
    return list(by_key.values())


def write_rows(rows: List[Dict[str, str]],
               path: Optional[str] = None) -> List[Dict[str, str]]:
    """Write the daily table atomically; returns the deduped, sorted rows written.

    On-disk shape matches the resources builder: a ``# schema_version=N`` comment
    line, the FIELDS header, then one row per `(animal_id, date)` (last-wins on
    duplicates) sorted by that key. ISO dates make the string sort chronological.
    Writes to a temp file then ``os.replace`` so a crash can't leave a half-file.
    """
    p = path or default_daily_log_path()
    deduped: Dict[tuple, Dict[str, str]] = {}
    for row in rows:
        deduped[_key(row)] = _normalise(row)
    ordered = sorted(deduped.values(), key=_key)

    parent = os.path.dirname(os.path.abspath(p))
    os.makedirs(parent, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# schema_version={SCHEMA_VERSION}\n")
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in ordered:
            writer.writerow(row)
    os.replace(tmp, p)
    return ordered
