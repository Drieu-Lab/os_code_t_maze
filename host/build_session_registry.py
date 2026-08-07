"""
build_session_registry.py - regenerate the cross-session index (thin shell).

Scans the Phase 3 recordings root and rebuilds the two analysis artifacts:
``session_registry.csv`` (one row per session; docs/decisions/0081) and the tidy
``session_registry_tags.csv`` (``session_id, tag, count``). The joining/formatting
lives in the pure ``session_registry`` core; this shell only resolves the paths
(recordings root, the sibling resources daily table, the note-tag vocabulary),
does the file writes, and prints a one-line summary.

It is a FULL idempotent rebuild - re-run it any time (after sessions, before
analysis) and it re-scans every bundle, so it never double-counts. The outputs
land under the recordings root, which is git-ignored (``host/data/``), matching
how the resources repo treats ``planning/outputs/`` - regenerate, don't version.

Run from host/ (conda env drieu_t_maze):
    python build_session_registry.py                 # default recordings root
    python build_session_registry.py --root PATH     # a custom root
"""

from __future__ import annotations

import argparse
import os

from tmaze.core.records import daily_log
from tmaze.core.records import note_tags
from tmaze.core.session import session_registry
from tmaze.paths import host_dir

# Default recordings root + the note-tag vocabulary location. host/ is found by
# landmark search, so the script runs from any working directory (ADR-0083).
_HERE = str(host_dir())
DEFAULT_RECORDINGS_ROOT = os.path.join(_HERE, "data", "phase3_recordings")
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
    ap.add_argument("--root", default=DEFAULT_RECORDINGS_ROOT,
                    help="Phase 3 recordings root to scan "
                         f"(default: {DEFAULT_RECORDINGS_ROOT})")
    ap.add_argument("--out-dir", default=None,
                    help="where to write the CSVs (default: the recordings root)")
    args = ap.parse_args(argv)

    # Best-effort inputs: an absent daily table just skips the fallback join; the
    # tag vocabulary shapes the wide tag_* columns (tidy file carries all tags).
    daily = daily_log.load()
    vocab = note_tags.load_tags(NOTE_TAGS_PATH)

    rows, tag_rows = session_registry.build(args.root, daily=daily)

    out_dir = args.out_dir or args.root
    reg_path = os.path.join(out_dir, REGISTRY_FILENAME)
    tags_path = os.path.join(out_dir, TAGS_FILENAME)
    _atomic_write(reg_path, session_registry.render_registry_csv(
        rows, tag_names=vocab.names()))
    _atomic_write(tags_path, session_registry.render_tags_csv(tag_rows))

    print(f"session registry: {len(rows)} sessions, "
          f"{len(tag_rows)} tag rows -> {reg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
