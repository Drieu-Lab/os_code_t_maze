"""records - pure cores for animal record-keeping that bridge to the sibling
os_code_t_maze_resources repo: the canonical animal-id list, the daily
husbandry table, per-animal run state, and the note-tag vocabulary. Stdlib-only,
headless-tested. This package owns ``animals.resources_root()`` - the one
resolver for the sibling resources repo (env override + landmark default).

It also owns the ONE reader for the ``# schema_version``-prefixed CSVs every
records file uses (:func:`read_commented_csv`) and the header/schema guard the
write paths apply before they merge into a file (:func:`check_header`). See
docs/decisions/0155 (records history) for why the guards exist.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence


class RecordsFileError(Exception):
    """A records file exists on disk but could not be read/parsed.

    The record readers are deliberately *best-effort*: a missing or unreadable
    file yields an empty table (``load_rows`` / ``load_records`` return ``[]``)
    so the rig still runs without the resources repo present. But a **write**
    (a Records Manager Save) first reads the current file and merges the edited
    rows into it. If that read silently returned empty for a file that is
    *present but unreadable*, the Save would overwrite it with only the edited
    rows - dropping every other row (the clobber described in bug
    ``#260810_RecordsIntegrity``). The write path therefore uses a *strict*
    reader (``load_rows_for_write`` / ``load_records_for_write``) that raises
    this instead of returning empty, so the Save can abort rather than destroy
    data. ``file absent`` is still returned as empty (creating a fresh file is
    safe); only ``present but unreadable`` raises.

    Since ADR-0155 the strict readers also raise this for a file whose HEADER
    does not match the fields this code knows (a renamed/reordered column would
    otherwise read as blank and a save would silently blank that column for every
    row) and for a ``schema_version`` NEWER than this code (a newer writer may
    have added a column this code would drop on rewrite).
    """


_SCHEMA_RE = re.compile(r"^#\s*schema_version\s*=\s*(\d+)\s*$")


@dataclass
class CommentedCsv:
    """What :func:`read_commented_csv` returns: the parsed pieces of one file.

    ``schema_version`` is None when no ``# schema_version=N`` comment line was
    found (a hand-made file); ``header`` is the column-name row as written;
    ``rows`` are dicts keyed by that header, values stripped of surrounding
    whitespace; ``comments`` are the leading ``#`` lines verbatim (without the
    newline) so a rewrite can preserve anything a human added there.
    """
    schema_version: Optional[int] = None
    header: List[str] = field(default_factory=list)
    rows: List[Dict[str, str]] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)


def read_commented_csv(path: str) -> CommentedCsv:
    """Parse a records CSV: leading ``#`` comment lines, a header row, data rows.

    Only the ``#`` lines BEFORE the header are treated as comments. Earlier
    readers dropped EVERY line beginning with ``#`` anywhere in the file, which
    silently lost a line of a quoted multi-line ``notes`` cell that happened to
    start with ``#`` (and desynchronised the rest of that record). Everything
    from the header on is handed to :mod:`csv` untouched, so quoted cells parse
    exactly as they were written.

    Raises ``OSError`` / ``ValueError`` (as ``open`` / the codec / :mod:`csv`
    do); callers decide whether that means "empty" (best-effort read) or
    "refuse" (write path).
    """
    out = CommentedCsv()
    with open(path, "r", encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith("#"):
        comment = lines[i].rstrip("\r\n")
        out.comments.append(comment)
        m = _SCHEMA_RE.match(comment.strip())
        if m and out.schema_version is None:
            out.schema_version = int(m.group(1))
        i += 1
    body = lines[i:]
    if not body:
        return out
    reader = csv.DictReader(body)
    out.header = [str(h or "").strip() for h in (reader.fieldnames or [])]
    for row in reader:
        out.rows.append({str(k or "").strip(): (v or "").strip()
                         for k, v in row.items() if k is not None})
    return out


def check_header(header: Sequence[str], fields: Sequence[str],
                 schema_version: Optional[int], max_schema: int) -> List[str]:
    """Problems that make a file UNSAFE to merge into and rewrite ([] = safe).

    The write path (a keyed upsert followed by a full rewrite, see the glossary)
    projects every existing row onto ``fields`` and writes those columns back.
    That is only lossless when the file's header is what this code expects:

    - the header must be a PREFIX of ``fields`` (an older file may lack columns
      that were appended later - those read as blank and are written back blank,
      which is the documented upgrade path - but a renamed, reordered, or
      unknown column would be dropped or blanked on rewrite);
    - ``schema_version`` must not be newer than ``max_schema`` (a newer writer
      may have added a column this code doesn't know; rewriting would drop it).

    A missing ``# schema_version`` line is tolerated (hand-made files), as is an
    empty file (no header at all - nothing to lose).
    """
    problems: List[str] = []
    hdr = [str(h or "").strip() for h in header]
    if hdr and list(fields[:len(hdr)]) != hdr:
        problems.append(
            "header " + ",".join(hdr) + " is not the expected "
            + ",".join(fields) + " (or an older prefix of it); a renamed or "
            "reordered column would be blanked for every row on save.")
    if schema_version is not None and schema_version > max_schema:
        problems.append(
            f"schema_version={schema_version} is newer than this code "
            f"understands (v{max_schema}); saving would drop columns it "
            "does not know.")
    return problems
