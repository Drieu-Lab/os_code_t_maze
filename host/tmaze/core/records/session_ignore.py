"""
session_ignore.py - the operator's session-exclusion list (pure core).

Not every folder under the recordings root is a real experiment session: a test
run, a stray start/stop-glitch folder (e.g. the ``000_...`` bundle from the fixed
bug ``#260729_SessionTaskFolder``), or a mistyped-animal bundle can all land
there. Deleting them would be destructive and irreversible, so instead we keep
the bundles on disk and list their session ids here; the records pipeline then
SKIPS them - no ``session_registry.csv`` row, no per-rat View sheet, no Records
Manager session entry. Removing an id from the list brings the session back
(non-destructive / reversible). This is the code-side response to bug
``#260810_RecordsIntegrity``; see docs/decisions/0097.

The list lives in ``host/session_ignore.json`` (tracked, the same ``host/`` JSON
pattern as ``note_tags.json``/``phase3.json``) so the decision is shared across
both repos - the resources ``build_session_training_log.py`` reads the same file
via the ``OS_CODE_T_MAZE`` resolver.

Pure stdlib (``json``/``os``), best-effort load: a missing/malformed file yields
an empty set, so the pipeline simply excludes nothing. Headlessly testable.

Headless tests: python -m unittest test_session_ignore.
"""

from __future__ import annotations

import json
import os
from typing import Optional, Set

from tmaze.paths import host_dir

# Bumped if the file's shape changes; written into a fresh file's ``_schema``.
SCHEMA_VERSION = 1


def default_ignore_path() -> str:
    """Path to ``host/session_ignore.json`` in this (code) repo."""
    return os.path.join(str(host_dir()), "session_ignore.json")


def load_ignored(path: Optional[str] = None) -> Set[str]:
    """The set of session ids to skip, read best-effort.

    Reads the ``ignore`` list from the JSON file (default: ``host/session_ignore.json``).
    A missing/malformed file, or a missing/non-list ``ignore`` key, yields an
    empty set so the pipeline excludes nothing rather than failing. Ids are
    whitespace-trimmed; blanks are dropped.
    """
    p = path or default_ignore_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return set()
    if not isinstance(raw, dict):
        return set()
    ids = raw.get("ignore")
    if not isinstance(ids, list):
        return set()
    return {s.strip() for s in (str(i) for i in ids) if s.strip()}
