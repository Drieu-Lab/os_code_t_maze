"""
records_changelog.py - the append-only per-field change log for the records (core).

Every records save is a keyed upsert that rewrites the whole file, so git alone
only shows whole-file snapshots at the moments someone commits; who changed
which cell, from what to what, between commits was invisible. This core keeps
that history in ``records/animal/records_changes.csv`` beside the records
files in the resources repo (docs/decisions/0155): one line per changed FIELD,

    ts, operator, source, file, key, field, old, new

behind the usual ``# schema_version=N`` comment line. It is APPEND-ONLY - never
rewritten, never sorted - and is data *about* the records, not a source of
truth: if it disagrees with a records file, the records file wins.

Two pure pieces plus a thin file edge:

- :func:`diff_rows` turns a before/after pair of row lists (as the writers see
  them: the rows read from disk and the merged rows about to be written) into
  the list of ``(key, field, old, new)`` changes. Added and removed rows get a
  ``(row)`` marker entry so an empty new row is still logged.
- :func:`append` writes entries to the log, creating the file (comment line +
  header) on first use. Appends are small single writes; a reader must tolerate
  a partial trailing line after a crash (``load_entries`` does).

The key is the row's identity as one string (``600|2026-09-10`` for the daily
log, the ``session_id`` for annotations, the animal id for the roster).

Pure stdlib (``csv`` / ``os`` / ``datetime``), headlessly testable.

Headless tests: python -m unittest test_records_changelog.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tmaze.core.records import animals, read_commented_csv

FIELDS = ["ts", "operator", "source", "file", "key", "field", "old", "new"]

# Bumped if the line shape changes.
SCHEMA_VERSION = 1

# The ``field`` value used for a whole-row event (a row appearing/vanishing).
ROW_MARKER = "(row)"
ROW_ADDED = "added"
ROW_REMOVED = "removed"

# Joins the parts of a multi-column key into the one ``key`` string.
KEY_SEP = "|"

# A change as computed by :func:`diff_rows`: (key, field, old, new).
Change = Tuple[str, str, str, str]


def default_changelog_path() -> str:
    """Path to ``records/animal/records_changes.csv`` in the resources repo."""
    return os.path.join(animals.resources_root(), "records", "animal",
                        "records_changes.csv")


def row_key(row: Dict[str, str], key_fields: Sequence[str]) -> str:
    """The identity of ``row`` as one string (parts joined by ``|``)."""
    return KEY_SEP.join(str(row.get(f, "") or "").strip() for f in key_fields)


def diff_rows(before: Iterable[Dict[str, str]], after: Iterable[Dict[str, str]],
              key_fields: Sequence[str],
              fields: Optional[Sequence[str]] = None) -> List[Change]:
    """The per-field changes that turn ``before`` into ``after``.

    Rows are matched by ``key_fields``. For a row present in both, one change
    per ``field`` (default: the union of the two rows' columns, minus the key)
    whose stripped value differs. A row only in ``after`` yields a
    ``(row)`` -> ``added`` marker plus one change per NON-BLANK field; a row
    only in ``before`` yields a ``(row)`` -> ``removed`` marker plus one change
    per non-blank field going to "". Rows with an empty key are ignored (they
    can't be identified). Output is ordered by key, then field order.
    """
    b_by: Dict[str, Dict[str, str]] = {}
    for row in before:
        k = row_key(row, key_fields)
        if k.strip(KEY_SEP):
            b_by[k] = row
    a_by: Dict[str, Dict[str, str]] = {}
    for row in after:
        k = row_key(row, key_fields)
        if k.strip(KEY_SEP):
            a_by[k] = row

    def _val(row: Dict[str, str], f: str) -> str:
        return str(row.get(f, "") or "").strip()

    def _fields_of(*rows: Dict[str, str]) -> List[str]:
        if fields is not None:
            return [f for f in fields if f not in key_fields]
        seen: List[str] = []
        for row in rows:
            for f in row:
                if f not in key_fields and f not in seen:
                    seen.append(f)
        return seen

    out: List[Change] = []
    for k in sorted(set(b_by) | set(a_by)):
        b, a = b_by.get(k), a_by.get(k)
        if b is None:
            out.append((k, ROW_MARKER, "", ROW_ADDED))
            for f in _fields_of(a):
                v = _val(a, f)
                if v:
                    out.append((k, f, "", v))
        elif a is None:
            out.append((k, ROW_MARKER, ROW_REMOVED, ""))
            for f in _fields_of(b):
                v = _val(b, f)
                if v:
                    out.append((k, f, v, ""))
        else:
            for f in _fields_of(b, a):
                ov, nv = _val(b, f), _val(a, f)
                if ov != nv:
                    out.append((k, f, ov, nv))
    return out


@dataclass
class ChangeEntry:
    """One line of the change log."""
    ts: str
    operator: str
    source: str
    file: str
    key: str
    field: str
    old: str
    new: str

    def as_row(self) -> Dict[str, str]:
        return {f: getattr(self, f) for f in FIELDS}


def now_iso() -> str:
    """Local wall-clock time to the second, ISO 8601 (what ``ts`` holds)."""
    return _dt.datetime.now().isoformat(timespec="seconds")


def append(changes: Iterable[Change], operator: str, source: str, file: str,
           path: Optional[str] = None, ts: Optional[str] = None) -> int:
    """Append one log line per change; returns how many were written.

    ``operator`` is who made the edit (the dropdown choice), ``source`` which
    tool (``records_manager``, ``build_daily_log --export``, ...), ``file`` the
    records file's basename. All entries of one call share one ``ts`` (the
    save is one event). Creates the file with its comment line + header when
    absent. Nothing is written for an empty ``changes``.
    """
    items = list(changes)
    if not items:
        return 0
    p = path or default_changelog_path()
    stamp = ts or now_iso()
    parent = os.path.dirname(os.path.abspath(p))
    os.makedirs(parent, exist_ok=True)
    fresh = not os.path.exists(p) or os.path.getsize(p) == 0
    with open(p, "a", encoding="utf-8", newline="") as fh:
        if fresh:
            fh.write(f"# schema_version={SCHEMA_VERSION}\n")
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        if fresh:
            writer.writeheader()
        for key, field, old, new in items:
            writer.writerow(ChangeEntry(stamp, str(operator or "").strip(),
                                        str(source or "").strip(),
                                        str(file or "").strip(), key, field,
                                        old, new).as_row())
    return len(items)


def record(before: Iterable[Dict[str, str]], after: Iterable[Dict[str, str]],
           key_fields: Sequence[str], operator: str, source: str, file: str,
           fields: Optional[Sequence[str]] = None,
           path: Optional[str] = None, ts: Optional[str] = None) -> int:
    """``diff_rows`` + ``append`` in one call; returns the number of lines."""
    return append(diff_rows(before, after, key_fields, fields), operator,
                  source, file, path=path, ts=ts)


def load_entries(path: Optional[str] = None) -> List[ChangeEntry]:
    """Every log line, oldest first, best-effort ([] if the file is absent).

    A partial trailing line (a crash mid-append) is dropped rather than raising:
    an entry is kept only if its ``ts``, ``key`` and ``field`` are all
    present (every real entry has all three non-blank).
    """
    p = path or default_changelog_path()
    try:
        parsed = read_commented_csv(p)
    except (OSError, ValueError):
        return []
    out: List[ChangeEntry] = []
    for row in parsed.rows:
        if not (row.get("ts") and row.get("key") and row.get("field")):
            continue
        out.append(ChangeEntry(*[str(row.get(f, "") or "") for f in FIELDS]))
    return out


def entries_since(entries: Iterable[ChangeEntry],
                  ts_iso: Optional[str]) -> List[ChangeEntry]:
    """Entries with ``ts`` strictly after ``ts_iso`` (all of them if None).

    ISO timestamps of the same form compare chronologically as strings."""
    if not ts_iso:
        return list(entries)
    return [e for e in entries if e.ts > ts_iso]
