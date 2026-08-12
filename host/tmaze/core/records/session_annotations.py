"""
session_annotations.py - the per-session qualitative annotations store (pure core).

The session registry (``session_registry.csv``, ADR-0082) is *generated* from the
rig's self-describing bundles: the rig knows the numbers (weights, trials, water,
timings) but not the few qualitative fields a human adds afterwards - the "common
procedures across days" and free-text notes for a session. Those used to live only
inside the ``session_training_log.xlsx`` "Annotations" sheet. This core promotes
them to their own machine-readable source of truth,
``records/animal/session_annotations.csv`` in the sibling resources repo: one row
per ``session_id`` behind a leading ``# schema_version=N`` comment line.

Keeping annotations in a diffable CSV (the same shape as ``daily_log.csv``) lets
the Records Manager GUI edit them directly, lets the resources builder join them
onto the View sheets, and keeps the Excel workbook a reconciled, hand-editable
*fallback* rather than the only home for this text.

Mirrors :mod:`tmaze.core.records.daily_log`: best-effort reads (a missing/malformed
file yields an empty store rather than raising), an atomic writer, and an
``upsert`` merge so a GUI save touches only the sessions it edited and never drops
a row another surface added.

Pure stdlib (``csv`` / ``os``), no Tk/serial, headlessly testable.

Headless tests: python -m unittest test_session_annotations.
"""

from __future__ import annotations

import csv
import os
from typing import Dict, List, Optional

from tmaze.core.records import RecordsFileError, animals

# One row per session. ``session_id`` is the identity; ``animal`` and ``date`` are
# denormalised copies (handy when reading the CSV alone); ``procedures`` and
# ``notes`` are the hand-edited qualitative fields.
FIELDS = ["session_id", "animal", "date", "procedures", "notes"]

# Bumped in lockstep with the resources builder's Annotations schema (ADR-0082).
SCHEMA_VERSION = 1

# The single column that identifies a row.
KEY_FIELD = "session_id"


def default_annotations_path() -> str:
    """Path to `records/animal/session_annotations.csv` in the resources repo."""
    return os.path.join(animals.resources_root(), "records", "animal",
                        "session_annotations.csv")


def load_rows(path: Optional[str] = None) -> List[Dict[str, str]]:
    """Read the annotations table best-effort; [] if it's missing/malformed.

    Skips the leading `# schema_version` comment line (and any `#` line), then
    parses the header + rows with ``csv``.
    """
    p = path or default_annotations_path()
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


class SessionAnnotations:
    """The per-session annotation rows, keyed by ``session_id``.

    Single-threaded like the other cores. ``available`` is False for an empty
    store (missing file), so the caller can tell "no annotations file" apart from
    "no annotation for this session".
    """

    def __init__(self, rows: Optional[List[Dict[str, str]]] = None) -> None:
        self._rows: List[Dict[str, str]] = list(rows or [])

    @property
    def available(self) -> bool:
        return bool(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def for_session(self, session_id: str) -> Optional[Dict[str, str]]:
        """The row for a ``session_id``, or None."""
        sid = str(session_id).strip()
        for row in self._rows:
            if row.get("session_id") == sid:
                return dict(row)
        return None

    def as_map(self) -> Dict[str, Dict[str, str]]:
        """`{session_id: row}` for a whole-table join (last row wins on dupes)."""
        return {row.get("session_id", ""): dict(row) for row in self._rows
                if row.get("session_id")}


def load(path: Optional[str] = None) -> SessionAnnotations:
    """Load the annotations table best-effort (default: the resources repo)."""
    return SessionAnnotations(load_rows(path))


def load_rows_for_write(path: Optional[str] = None) -> List[Dict[str, str]]:
    """Like :func:`load_rows`, but distinguish "absent" from "unreadable".

    The Save path merges edits into the *current* file, so an unreadable-but-
    present file must not be treated as empty (that would overwrite it with only
    the edited row - see :class:`RecordsFileError` / bug #260810_RecordsIntegrity).

    - File absent -> ``[]`` (a fresh file is safe to create).
    - File present but unopenable/undecodable -> raise :class:`RecordsFileError`.
    """
    p = path or default_annotations_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8", newline="") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    except (OSError, ValueError) as exc:
        raise RecordsFileError(
            f"{p} exists but could not be read ({exc}); refusing to overwrite "
            "it, which would drop the rows already in the file.") from exc
    reader = csv.DictReader(lines)
    return [{k: (row.get(k) or "").strip() for k in FIELDS} for row in reader]


# --------------------------------------------------------------------------- #
# Writing (Records Manager GUI / builder migration). Same atomic, keyed-upsert
# discipline as daily_log so the CSV stays the single source of truth.
# --------------------------------------------------------------------------- #

def _key(row: Dict[str, str]) -> str:
    """The ``session_id`` identity of a row, whitespace-normalised."""
    return str(row.get(KEY_FIELD, "") or "").strip()


def _normalise(row: Dict[str, str]) -> Dict[str, str]:
    """A row projected onto FIELDS as trimmed strings (None -> "")."""
    return {f: str(row.get(f, "") or "").strip() for f in FIELDS}


def upsert_rows(existing: List[Dict[str, str]],
                edits: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Merge ``edits`` into ``existing`` by ``session_id``, edits winning.

    Rows without a ``session_id`` are dropped (they can't be keyed). Returns a
    fresh list; inputs are not mutated. Ordering is irrelevant because
    :func:`write_rows` sorts.
    """
    by_key: Dict[str, Dict[str, str]] = {}
    for row in existing:
        k = _key(row)
        if k:
            by_key[k] = _normalise(row)
    for row in edits:
        k = _key(row)
        if k:
            by_key[k] = _normalise(row)
    return list(by_key.values())


def write_rows(rows: List[Dict[str, str]],
               path: Optional[str] = None) -> List[Dict[str, str]]:
    """Write the annotations table atomically; returns the deduped, sorted rows.

    On-disk shape mirrors the daily log: a ``# schema_version=N`` comment line,
    the FIELDS header, then one row per ``session_id`` (last-wins on duplicates)
    sorted by ``session_id``. Rows without a ``session_id`` are skipped. Writes to
    a temp file then ``os.replace`` so a crash can't leave a half-file.
    """
    p = path or default_annotations_path()
    deduped: Dict[str, Dict[str, str]] = {}
    for row in rows:
        k = _key(row)
        if k:
            deduped[k] = _normalise(row)
    ordered = [deduped[k] for k in sorted(deduped)]

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
