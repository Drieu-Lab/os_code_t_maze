"""
build_session_registry.py - regenerate the cross-session index (thin shell).

Scans the Phase 3 recordings root and rebuilds the two analysis artifacts:
``session_registry.csv`` (one row per session; docs/decisions/0081) and the tidy
``session_registry_tags.csv`` (``session_id, tag, count``). The joining/formatting
lives in the pure ``session_registry`` core; this shell only resolves the paths
(recordings root, the sibling resources daily table, the note-tag vocabulary),
does the file writes, and prints a one-line summary.

Scan input vs. output are two DIFFERENT locations (see docs/decisions/0088):

- ``--root`` (the scan INPUT) defaults to the rig's recordings folder,
  ``<camera_output_dir>/phase3_recordings`` (``tmaze.paths.phase3_recordings_root``,
  env-overridable via ``TMAZE_RECORDINGS_DIR``) - where the rig actually writes
  bundles. Earlier this defaulted to ``host/data/phase3_recordings`` and so found
  zero sessions.
- ``--out-dir`` (where the CSVs are WRITTEN) defaults to ``host/data/phase3_recordings``
  (git-ignored), which is where the Records Manager GUI and the resources
  ``build_session_training_log.py`` read ``session_registry.csv`` from.

It is a FULL idempotent rebuild - re-run it any time (after sessions, before
analysis) and it re-scans every bundle, so it never double-counts.

Run (conda env drieu_t_maze; from anywhere - paths resolve by landmark search):
    python -m tmaze.tools.build_session_registry              # rig recordings root -> host/data
    python -m tmaze.tools.build_session_registry --root PATH  # scan a custom root
    python -m tmaze.tools.build_session_registry --out-dir PATH  # write the CSVs elsewhere
"""

from __future__ import annotations

import argparse
import os

from tmaze.core.records import animals
from tmaze.core.records import daily_log
from tmaze.core.records import note_tags
from tmaze.core.records import records_audit
from tmaze.core.records import session_ignore
from tmaze.core.session import session_registry
from tmaze.paths import host_dir, phase3_recordings_root

# The scan INPUT default: the rig's recordings folder (single-sourced from
# tmaze.paths so it matches where the rig writes; env-overridable). The registry
# OUTPUT default is host/data/phase3_recordings (git-ignored) - decoupled from
# the scan root so consumers keep reading the CSV from one stable place (ADR-0088).
# host/ is found by landmark search, so the script runs from any directory (ADR-0083).
_HERE = str(host_dir())
DEFAULT_SCAN_ROOT = str(phase3_recordings_root())
REGISTRY_OUTPUT_DIR = os.path.join(_HERE, "data", "phase3_recordings")
NOTE_TAGS_PATH = os.path.join(_HERE, "note_tags.json")

REGISTRY_FILENAME = "session_registry.csv"
TAGS_FILENAME = "session_registry_tags.csv"


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=DEFAULT_SCAN_ROOT,
                    help="Phase 3 recordings root to scan "
                         f"(default: {DEFAULT_SCAN_ROOT})")
    ap.add_argument("--out-dir", default=None,
                    help="where to write the CSVs "
                         f"(default: {REGISTRY_OUTPUT_DIR})")
    args = ap.parse_args(argv)

    # Best-effort inputs: an absent daily table just skips the fallback join; the
    # tag vocabulary shapes the wide tag_* columns (tidy file carries all tags);
    # the ignore-list drops non-real bundles (test runs / stray folders) without
    # deleting them (ADR-0097).
    daily = daily_log.load()
    vocab = note_tags.load_tags(NOTE_TAGS_PATH)
    ignore = session_ignore.load_ignored()

    rows, tag_rows = session_registry.build(args.root, daily=daily, ignore=ignore)

    out_dir = args.out_dir or REGISTRY_OUTPUT_DIR
    reg_path = os.path.join(out_dir, REGISTRY_FILENAME)
    tags_path = os.path.join(out_dir, TAGS_FILENAME)
    _atomic_write(reg_path, session_registry.render_registry_csv(
        rows, tag_names=vocab.names()))
    _atomic_write(tags_path, session_registry.render_tags_csv(tag_rows))

    print(f"session registry: {len(rows)} sessions, "
          f"{len(tag_rows)} tag rows -> {reg_path}")

    # Advisory only - never fail the build (the data fixes are the operator's;
    # bug #260810/ADR-0097). A non-canonical animal id points at a typo or an
    # un-listed test session; `python -m tmaze.tools.records_check` reports the
    # deeper cross-reference gaps (husbandry, water columns, churn).
    unknown = records_audit.noncanonical_animals(
        [r.get("animal", "") for r in rows], animals.load_registry())
    if unknown:
        print("  WARNING: animal ids not in animals.json: " + ", ".join(unknown))
        print("           add them to animals.json, or list those sessions in "
              "host/session_ignore.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
