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
from typing import Dict, List

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


def _print_section(section: records_audit.AuditSection) -> int:
    """Print one report block; return its flagged-item count (0 -> 'none' or,
    when the check was skipped, the skip note)."""
    print(f"== {section.title} ==")
    if section.skipped:
        print(f"  ({section.note})")
        return 0
    if not section.items:
        print("  none")
        return 0
    for line in section.items:
        print(f"  {line}")
    if section.fix:
        print(f"  Fix: {section.fix}")
    return section.count


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

    triples = records_audit.session_triples(registry_rows, annot_rows, ignore)
    kept_registry = [r for r in registry_rows
                     if (r.get("session_id") or "").strip() not in ignore]

    print(f"Records integrity check ({len(triples)} sessions, "
          f"registry={reg_path})")
    if not registry_rows:
        print("  (no session_registry.csv - run "
              "`python -m tmaze.tools.build_session_registry` first for the "
              "duration/animal columns; annotation-only checks still run)")

    sections = records_audit.build_report(
        triples, daily_rows, kept_registry, daily, registry)
    problems = sum(_print_section(s) for s in sections)

    print(f"\n{problems} item(s) flagged. Nothing was changed "
          "(this is a read-only report).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
