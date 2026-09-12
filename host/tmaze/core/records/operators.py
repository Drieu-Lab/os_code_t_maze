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
