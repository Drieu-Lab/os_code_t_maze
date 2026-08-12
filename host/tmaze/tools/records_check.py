"""
records_check.py - read-only integrity report for the coordinated records (thin shell).

Prints the cross-reference problems the pure ``records_audit`` core detects across
the coordinated records, without changing anything (bug ``#260810_RecordsIntegrity``;
ADR-0097). The fixes are the operator's - this just makes the drift visible:

- animal ids on real sessions that aren't in ``animals.json`` (a typo, or an
  un-listed test session that belongs in ``host/session_ignore.json``);
- sessions whose ``(animal, date)`` has no ``daily_log.csv`` husbandry row;
- ``daily_log.csv`` water-on/off cells holding a volume or free-text note instead
  of a clock time (so the machine-readable ``water_ml`` never captured it);
- near-zero-duration bundles that look like start/stop churn.

Sessions listed in ``host/session_ignore.json`` are excluded throughout, the same
as the registry build.

Run (conda env drieu_t_maze; from anywhere):
    python -m tmaze.tools.records_check
    python -m tmaze.tools.records_check --registry PATH   # a custom session_registry.csv
"""

from __future__ import annotations

import argparse
import csv
import os
from typing import Dict, List, Set, Tuple

from tmaze.core.records import (
    animals,
    daily_log,
    records_audit,
    session_annotations,
    session_ignore,
)
from tmaze.tools import build_session_registry


def _read_csv_skip_comments(path: str) -> List[Dict[str, str]]:
    """Rows of a ``# schema_version``-prefixed CSV as dicts; [] if absent."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    except OSError:
        return []
    return list(csv.DictReader(lines))


def _session_triples(registry_rows: List[Dict[str, str]],
                     annot_rows: List[Dict[str, str]],
                     ignore: Set[str]) -> List[Tuple[str, str, str]]:
    """``(session_id, animal, date)`` for every known session - registry rows
    first, then annotation-only sessions (in the CSV but not the registry) so old
    notes are still checked - skipping ignored ids and duplicates."""
    triples: List[Tuple[str, str, str]] = []
    seen: Set[str] = set()
    for src in (registry_rows, annot_rows):
        for r in src:
            sid = (r.get("session_id") or "").strip()
            if not sid or sid in ignore or sid in seen:
                continue
            seen.add(sid)
            triples.append((sid, (r.get("animal") or "").strip(),
                            (r.get("date") or "").strip()))
    return triples


def _section(title: str, lines: List[str], fix: str = "") -> int:
    """Print a titled block; return the problem count (0 -> prints 'none')."""
    print(f"== {title} ==")
    if not lines:
        print("  none")
        return 0
    for line in lines:
        print(f"  {line}")
    if fix:
        print(f"  Fix: {fix}")
    return len(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", default=None,
                    help="path to session_registry.csv (default: "
                         "host/data/phase3_recordings/session_registry.csv)")
    args = ap.parse_args(argv)

    reg_path = args.registry or os.path.join(
        build_session_registry.REGISTRY_OUTPUT_DIR,
        build_session_registry.REGISTRY_FILENAME)

    registry_rows = _read_csv_skip_comments(reg_path)
    annot_rows = session_annotations.load_rows()
    daily_rows = daily_log.load_rows()
    daily = daily_log.DailyLog(daily_rows)
    registry = animals.load_registry()
    ignore = session_ignore.load_ignored()

    triples = _session_triples(registry_rows, annot_rows, ignore)
    kept_registry = [r for r in registry_rows
                     if (r.get("session_id") or "").strip() not in ignore]

    print(f"Records integrity check ({len(triples)} sessions, "
          f"registry={reg_path})")
    if not registry_rows:
        print("  (no session_registry.csv - run "
              "`python -m tmaze.tools.build_session_registry` first for the "
              "duration/animal columns; annotation-only checks still run)")
    problems = 0

    if registry.available:
        unknown = records_audit.noncanonical_animals(
            [a for _sid, a, _d in triples], registry)
        problems += _section(
            "Animal ids not in animals.json", list(unknown),
            "add them to animals.json, or list those sessions in "
            "host/session_ignore.json.")
    else:
        print("== Animal ids not in animals.json ==")
        print("  (animals.json unavailable - skipped)")

    if daily.available:
        missing = [f"{sid}  ({a} {d})"
                   for sid, a, d in records_audit.sessions_missing_daily(
                       triples, daily)]
        problems += _section(
            "Sessions with no daily_log.csv (animal, date) row", missing,
            "add the (animal, date) husbandry rows to daily_log.csv.")
        suspects = [f"{a} {d}  {field}={val!r}"
                    for a, d, field, val in records_audit.suspect_water_cells(
                        daily_rows)]
        problems += _section(
            "Daily-log water cells that aren't clock times", suspects,
            "move the volume into water_ml (and any note into supp_water/notes); "
            "leave water_on/water_off for on/off times.")
    else:
        print("== Sessions with no daily_log.csv (animal, date) row ==")
        print("  (daily_log.csv unavailable - skipped)")

    short = [f"{sid}  ({dur:.0f} s)"
             for sid, dur in records_audit.short_sessions(kept_registry)]
    problems += _section(
        f"Near-zero-duration sessions (< {records_audit.SHORT_SESSION_S:.0f} s, "
        "likely start/stop churn)", short,
        "if a listed session isn't real, add it to host/session_ignore.json.")

    print(f"\n{problems} item(s) flagged. Nothing was changed "
          "(this is a read-only report).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
