"""
daily_log.py - read the resources repo's daily husbandry table (pure core).

The resources repo owns the machine-readable daily husbandry table
`records/animal/daily_log.csv` (the source of truth defined in
docs/decisions/0076): one row per `(animal_id, date)` with weight, baseline,
% baseline, water on/off, water mL, supplemental water, and free-text notes,
behind a leading `# schema_version=N` comment line.

This core is the *code-repo* reader for that file. It resolves the resources repo
via the shared sibling-path resolver (``animals.resources_root``, ADR-0075), reads
the CSV with stdlib ``csv`` (no new dependency), and answers the two lookups the
rig needs: the row for a given `(animal, date)` (WS2 prefill / WS4 exact join) and
the most recent row for an animal on or before a date (to recover a baseline when
today's row hasn't been entered yet).

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

import animals

# Column order mirrors the resources builder (records/animal/build_daily_log.py).
FIELDS = [
    "animal_id", "date", "weight_g", "baseline_g", "pct_baseline",
    "water_on", "water_off", "water_ml", "supp_water", "notes",
]


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
