"""
build_session_registry.py - regenerate the cross-session index (thin shell).

Scans the Phase 3 recordings root and rebuilds the two analysis artifacts:
``session_registry.csv`` (one row per session; docs/decisions/0081) and the tidy
``session_registry_tags.csv`` (``session_id, tag, count``). The joining/formatting
lives in the pure ``session_registry`` core; this shell only resolves the paths
(recordings root, the sibling resources daily table, the note-tag vocabulary),
does the file writes, and prints a one-line summary.

Scan input vs. output are two DIFFERENT locations (see docs/decisions/0088):

- ``--root`` (the scan INPUT) defaults to the full recordings search path from
  ``tmaze.paths.phase3_recordings_roots`` - the tracked project config
  ``host/recordings_roots.json`` (docs/decisions/0156): its ``primary`` (null =
  the rig's write folder ``<camera_output_dir>/phase3_recordings``,
  env-overridable via ``TMAZE_RECORDINGS_DIR``) plus its read-only ARCHIVE roots
  where older sessions were moved when the rig disk filled (docs/decisions/0113;
  ``TMAZE_RECORDINGS_ARCHIVES`` overrides the list per process). The Records
  Manager's Generate resolves the same config, so GUI and CLI scan identical
  folders. Pass ``--root`` one or more times to scan a custom set instead;
  multiple roots are merged and de-duplicated by session_id.
- ``--out-dir`` (where the CSVs are WRITTEN) defaults to ``host/data/phase3_recordings``
  (git-ignored), which is where the Records Manager GUI and the resources
  ``build_session_training_log.py`` read ``session_registry.csv`` from.

It is a FULL idempotent rebuild - re-run it any time (after sessions, before
analysis) and it re-scans every bundle, so it never double-counts.

It REFUSES to shrink (docs/decisions/0158): before writing, the sessions the new
scan found are compared with the ones in the existing ``session_registry.csv``.
If any would vanish - an archive drive not mounted, a mistyped root - nothing is
written and the tool exits 2 listing them. Pass ``--allow-shrink`` once you have
checked the loss is intended (a session really deleted / moved to the ignore
list). The Records Manager shows the same list and asks.

Run (conda env drieu_t_maze; from anywhere - paths resolve by landmark search):
    python -m tmaze.tools.build_session_registry                     # default roots -> host/data
    python -m tmaze.tools.build_session_registry --root PATH         # scan a custom root
    python -m tmaze.tools.build_session_registry --root A --root B   # scan several roots
    python -m tmaze.tools.build_session_registry --out-dir PATH      # write the CSVs elsewhere
    python -m tmaze.tools.build_session_registry --allow-shrink      # accept a build that drops sessions
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from tmaze.core.records import animals
from tmaze.core.records import daily_log
from tmaze.core.records import note_tags
from tmaze.core.records import records_audit
from tmaze.core.records import session_ignore
from tmaze.core.session import session_registry
from tmaze.paths import host_dir, phase3_recordings_root, phase3_recordings_roots

# The scan INPUT default: the full recordings search path (primary rig folder +
# any archive roots), single-sourced from tmaze.paths so it matches where the rig
# writes and where old sessions were archived (env-overridable; ADR-0088/0113).
# DEFAULT_SCAN_ROOT stays the primary root alone (backward-compatible + the label
# the tests assert on); DEFAULT_SCAN_ROOTS is the list actually scanned by default.
# The registry OUTPUT default is host/data/phase3_recordings (git-ignored) -
# decoupled from the scan roots so consumers keep reading the CSV from one stable
# place (ADR-0088). host/ is found by landmark search, so the script runs from any
# directory (ADR-0083).
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


# ------------------------------------------------------------- provenance
def _file_sha(path: Optional[str]) -> str:
    """Short content hash of an input file, or ``absent`` when it isn't there
    (so the header says which inputs shaped the build; ADR-0157)."""
    if not path:
        return "absent"
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except OSError:
        return "absent"


def _code_commit() -> str:
    """The code repo's short commit sha (``+dirty`` when the tree has
    uncommitted changes), or ``unknown`` when git isn't available."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=_HERE, capture_output=True, text=True,
                              timeout=10)
        if head.returncode != 0:
            return "unknown"
        sha = head.stdout.strip() or "unknown"
        dirty = subprocess.run(["git", "status", "--porcelain",
                                "--untracked-files=no"], cwd=_HERE,
                               capture_output=True, text=True, timeout=10)
        if dirty.returncode == 0 and dirty.stdout.strip():
            sha += "+dirty"
        return sha
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build_meta(roots: List[str]) -> session_registry.BuildMeta:
    """Gather what this build is made from, for the CSV's header lines."""
    return session_registry.BuildMeta(
        built_at=_dt.datetime.now().isoformat(timespec="seconds"),
        code_commit=_code_commit(),
        roots=[os.path.abspath(r) for r in roots],
        inputs={
            "daily_log": _file_sha(daily_log.default_daily_log_path()),
            "note_tags": _file_sha(NOTE_TAGS_PATH),
            "session_ignore": _file_sha(session_ignore.default_ignore_path()),
        })


# ------------------------------------------------------------------ build
@dataclass
class BuildOutcome:
    """What one registry build did, as data (the Records Manager reads this
    directly instead of sniffing printed text; docs/decisions/0158).

    ``ok`` is False when nothing was written: ``refused_shrink`` (the new scan
    lost sessions and ``allow_shrink`` was off - ``shrink.vanished`` lists them)
    or ``error`` (an unexpected failure). When ``ok``, ``shrink`` is still set
    if sessions vanished but the loss was allowed, so the caller can say so.
    ``unknown_animals`` is advisory (non-canonical ids; never fails a build).
    """
    ok: bool
    sessions: int = 0
    tag_rows: int = 0
    roots: List[str] = field(default_factory=list)
    registry_path: str = ""
    previous_sessions: Optional[int] = None    # None = no previous registry
    shrink: Optional[session_registry.ShrinkReport] = None
    refused_shrink: bool = False
    unknown_animals: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def vanished(self) -> List[str]:
        return list(self.shrink.vanished) if self.shrink else []

    def summary(self) -> str:
        """One-line, human-readable result (the CLI prints it; the GUI shows it
        in its result dialog)."""
        roots_note = (f"{len(self.roots)} roots" if len(self.roots) != 1
                      else (self.roots[0] if self.roots else "no roots"))
        if self.refused_shrink:
            n = len(self.vanished)
            return (f"REFUSED: {n} session{'' if n == 1 else 's'} in the "
                    f"current registry {'is' if n == 1 else 'are'} missing "
                    f"from the scan of {roots_note}; nothing written")
        if not self.ok:
            return f"FAILED: {self.error or 'unknown error'}"
        s = (f"{self.sessions} sessions, {self.tag_rows} tag rows "
             f"(scanned {roots_note}) -> {self.registry_path}")
        if self.vanished:
            s += f"; dropped {len(self.vanished)} previously indexed (allowed)"
        return s


def _previous_ids(reg_path: str) -> Optional[List[str]]:
    """Session ids in the registry already on disk; None when there is none
    (first build) or it cannot be read (nothing to compare against)."""
    try:
        with open(reg_path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
    except OSError:
        return None
    return session_registry.registry_ids(text)


def run_build(roots: Optional[List[str]] = None,
              out_dir: Optional[str] = None,
              allow_shrink: bool = False) -> BuildOutcome:
    """The whole build as one call: scan ``roots`` (default: the tracked roots
    config), compare with the registry already in ``out_dir``, and write both
    CSVs unless the build would LOSE sessions and ``allow_shrink`` is off.

    Never raises for a data problem: failures come back as ``ok=False``."""
    # Default (no --root): the full recordings search path (primary + archives),
    # resolved fresh so an edited config / env var is honoured (ADR-0113, 0156).
    roots = list(roots) if roots else [str(p) for p in phase3_recordings_roots()]
    out_dir = out_dir or REGISTRY_OUTPUT_DIR
    reg_path = os.path.join(out_dir, REGISTRY_FILENAME)
    tags_path = os.path.join(out_dir, TAGS_FILENAME)
    outcome = BuildOutcome(ok=False, roots=roots, registry_path=reg_path)
    try:
        # Best-effort inputs: an absent daily table just skips the fallback
        # join; the tag vocabulary shapes the wide tag_* columns (tidy file
        # carries all tags); the ignore-list drops non-real bundles (test runs /
        # stray folders) without deleting them (ADR-0097).
        daily = daily_log.load()
        vocab = note_tags.load_tags(NOTE_TAGS_PATH)
        ignore = session_ignore.load_ignored()

        rows, tag_rows = session_registry.build(roots, daily=daily, ignore=ignore)
        outcome.sessions, outcome.tag_rows = len(rows), len(tag_rows)

        # Shrink guard (ADR-0158): a full rebuild that finds FEWER sessions than
        # the file it is about to replace almost always means a root is
        # unreachable, not that sessions ceased to exist. Refuse unless told.
        previous = _previous_ids(reg_path)
        if previous is not None:
            outcome.previous_sessions = len(previous)
            report = session_registry.shrink_check(
                previous, [r["session_id"] for r in rows])
            if not report.ok:
                outcome.shrink = report
                if not allow_shrink:
                    outcome.refused_shrink = True
                    return outcome

        _atomic_write(reg_path, session_registry.render_registry_csv(
            rows, tag_names=vocab.names(), meta=build_meta(roots)))
        _atomic_write(tags_path, session_registry.render_tags_csv(tag_rows))

        # Advisory only - never fail the build (the data fixes are the
        # operator's; bug #260810/ADR-0097). A non-canonical animal id points at
        # a typo or an un-listed test session; `python -m
        # tmaze.tools.records_check` reports the deeper cross-reference gaps.
        outcome.unknown_animals = records_audit.noncanonical_animals(
            [r.get("animal", "") for r in rows], animals.load_registry())
        outcome.ok = True
    except Exception as e:  # noqa: BLE001 - report, don't crash the caller
        outcome.error = f"{type(e).__name__}: {e}"
    return outcome


def _print_vanished(outcome: BuildOutcome, limit: int = 20) -> None:
    ids = outcome.vanished
    for sid in ids[:limit]:
        print(f"    {sid}")
    if len(ids) > limit:
        print(f"    ... and {len(ids) - limit} more")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", action="append", default=None, dest="roots",
                    metavar="PATH",
                    help="Phase 3 recordings root to scan; repeatable to scan "
                         "several (default: the roots in the tracked "
                         "host/recordings_roots.json - primary + archives)")
    ap.add_argument("--out-dir", default=None,
                    help="where to write the CSVs "
                         f"(default: {REGISTRY_OUTPUT_DIR})")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="write the registry even if the scan found fewer "
                         "sessions than the current file holds (default: "
                         "refuse and list the missing session ids)")
    args = ap.parse_args(argv)

    outcome = run_build(args.roots, args.out_dir, allow_shrink=args.allow_shrink)

    if outcome.refused_shrink:
        print("session registry: " + outcome.summary())
        print(f"  the current file holds {outcome.previous_sessions} sessions; "
              f"the scan found {outcome.sessions}. Missing:")
        _print_vanished(outcome)
        print("  Check that every recordings root is reachable (an unmounted "
              "archive drive, a mistyped path in host/recordings_roots.json).")
        print("  If the loss is intended, re-run with --allow-shrink.")
        return 2
    if not outcome.ok:
        print("session registry: " + outcome.summary())
        return 1

    print("session registry: " + outcome.summary())
    if outcome.vanished:
        print("  dropped (allowed by --allow-shrink):")
        _print_vanished(outcome)
    if outcome.unknown_animals:
        print("  WARNING: animal ids not in animals.json: "
              + ", ".join(outcome.unknown_animals))
        print("           add them to animals.json, or list those sessions in "
              "host/session_ignore.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
