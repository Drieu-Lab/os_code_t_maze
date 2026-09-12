"""
operators.py - the list of people allowed to edit the records (pure core).

Every records edit is logged with WHO made it (``records_changelog``,
docs/decisions/0155). A free-text name would drift (initials vs. names vs.
blanks), so the Records Manager offers a DROPDOWN fed by a small tracked list,
``host/operators.json``:

    {"schema_version": 1, "operators": ["RJ", ...]}

Adding an operator is editing that file (and committing it). The GUI remembers
the last choice per machine in its git-ignored prefs; the resources builders
take the same value via ``--operator``.

Best-effort load, same ``host/`` JSON pattern as ``session_ignore.json``: a
missing/malformed file yields an empty list, which the GUI shows as "no
operators configured" and refuses to save under.

Pure stdlib (``json``/``os``), headlessly testable.

Headless tests: python -m unittest test_operators.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from tmaze.paths import host_dir

# Bumped if the file's shape changes.
SCHEMA_VERSION = 1


def default_operators_path() -> str:
    """Path to ``host/operators.json`` in this (code) repo."""
    return os.path.join(str(host_dir()), "operators.json")


def load_operators(path: Optional[str] = None) -> List[str]:
    """The operator names, in file order, trimmed, deduplicated; [] best-effort.

    A missing/malformed file, or a missing/non-list ``operators`` key, yields
    ``[]`` rather than raising.
    """
    p = path or default_operators_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    names = raw.get("operators")
    if not isinstance(names, list):
        return []
    out: List[str] = []
    for n in names:
        s = str(n).strip()
        if s and s not in out:
            out.append(s)
    return out


def is_operator(name: str, operators: List[str]) -> bool:
    """Whether ``name`` (trimmed) is one of the configured operators."""
    return str(name or "").strip() in operators


def _read_if_present(path: str) -> Optional[List[str]]:
    """The operators list if ``path`` is a readable operators.json, else None.

    Distinguishes "file missing / unreadable" (None — the caller should not
    enforce membership) from "file present with an empty list" ([]).
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("operators"), list):
        return None
    return load_operators(path)


def require_operator(name: str, what: str,
                     path: Optional[str] = None) -> str:
    """The trimmed operator name, or raise ``ValueError`` explaining that
    ``what`` (e.g. ``--export``) must be attributed.

    When ``operators.json`` is readable the name must be on the list; when
    the file is absent or unreadable any non-blank name is accepted, so a
    builder run on a machine that can import ``tmaze`` but whose ``host/``
    folder is not the live one still writes. The resources builders catch
    the ``ValueError`` and exit (ADR-0162).
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError(
            f"{what} rewrites a records file, so it must be attributed: pass "
            f"--operator <your initials> (the names in host/operators.json). "
            f"Nothing was written.")
    p = path or default_operators_path()
    known = _read_if_present(p)
    if known is not None and name not in known:
        raise ValueError(
            f"--operator {name!r} is not in {p} "
            f"(known: {', '.join(known) or 'none'}). Add it there (and "
            f"commit) or pick a listed name. Nothing was written.")
    return name
