"""
animals.py - canonical animal identity + the resources-repo path resolver (pure core).

Session recordings are joined to husbandry data by animal id + date. Historically
that id was whatever free text the operator typed into the Task tab, slugged by
``protocol.sanitize_animal_id`` - so "Rat 600", "600 " and a typo like "60O" all
produced *different* keys, and a mistyped id quietly minted an orphan recording.

This core gives identity a single source of truth: a small canonical list
(``records/animal/animals.json`` in the sibling ``os_code_t_maze_resources`` repo)
of the lab's rat ids plus aliases. At Session Start the entered id is normalized
against it (see docs/decisions/0075). Matching is *loose* - case-, space- and
punctuation-insensitive - so "Rat 600" resolves to the canonical "600".

Both repos are always checked out on the rig, but the rig must still run if the
resources repo is missing/renamed: every read here is best-effort and
:func:`load_registry` returns an empty (``available == False``) registry rather
than raising, so the caller falls back to the old slug behaviour.

This module also owns the one **resources-repo path resolver** (:func:`resources_root`)
that the later workstreams reuse to find the daily husbandry table, etc.

Pure stdlib (``json`` / ``os``), no Tk/serial, headlessly testable.

Headless tests: python -m unittest test_animals.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from tmaze.paths import repo_root

# Environment override for the sibling resources repo location; when unset we use
# the default sibling folder next to this repo (both are checked out together on
# the rig PC). See docs/decisions/0075.
RESOURCES_ENV = "OS_CODE_T_MAZE_RESOURCES"

# The resources repo is the code-repo root's sibling. Find that root by landmark
# search (tmaze.paths.repo_root) rather than counting ".." from this file, so it
# is unaffected by this module's depth in the package (see docs/decisions/0083).
_DEFAULT_RESOURCES = os.path.join(
    os.path.dirname(str(repo_root())), "os_code_t_maze_resources")


def resources_root() -> str:
    """Absolute path to the sibling ``os_code_t_maze_resources`` repo.

    Honours the ``OS_CODE_T_MAZE_RESOURCES`` env override, else the default
    sibling folder. Returns the path whether or not it exists - callers read
    best-effort and degrade gracefully when it's absent (docs/decisions/0075).
    """
    return os.environ.get(RESOURCES_ENV) or _DEFAULT_RESOURCES


def animals_json_path() -> str:
    """Path to the canonical animal-id list inside the resources repo."""
    return os.path.join(resources_root(), "records", "animal", "animals.json")


def _key(value: object) -> str:
    """Loose match key: lower-case alphanumerics only (spaces/punctuation
    dropped), so ``Rat 600``, ``600 `` and ``rat600`` collapse together."""
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


class AnimalRegistry:
    """The canonical ids + their aliases, with loose normalization.

    Single-threaded like the other cores. ``available`` is False for an empty
    registry (e.g. the list wasn't found) so the caller can tell "unknown id"
    apart from "couldn't validate" and only warn in the former case.
    """

    def __init__(self, animals: Optional[List[Dict[str, object]]] = None) -> None:
        self._ids: List[str] = []
        self._by_key: Dict[str, str] = {}
        for rec in (animals or []):
            aid = str(rec.get("id", "")).strip()
            if not aid or aid in self._ids:
                continue
            self._ids.append(aid)
            # The canonical id always maps to itself; aliases fill in only if they
            # don't collide with an already-claimed key (first entry wins).
            self._by_key[_key(aid)] = aid
            aliases = rec.get("aliases") or []
            if isinstance(aliases, list):
                for alias in aliases:
                    k = _key(alias)
                    if k:
                        self._by_key.setdefault(k, aid)

    @property
    def available(self) -> bool:
        """Whether the registry holds at least one canonical id (i.e. loaded)."""
        return bool(self._ids)

    def ids(self) -> List[str]:
        """The canonical ids, in file order."""
        return list(self._ids)

    def normalize(self, raw: str) -> Optional[str]:
        """The canonical id for ``raw`` (matched by id or alias, loosely), or
        None when it isn't recognized / is blank."""
        k = _key(raw)
        if not k:
            return None
        return self._by_key.get(k)

    def is_known(self, raw: str) -> bool:
        return self.normalize(raw) is not None

    def __len__(self) -> int:
        return len(self._ids)


def parse_registry(text: str) -> AnimalRegistry:
    """Build a registry from ``animals.json`` text, tolerating a malformed file
    (anything unparseable yields an empty, unavailable registry)."""
    try:
        raw = json.loads(text)
    except ValueError:
        return AnimalRegistry()
    if not isinstance(raw, dict):
        return AnimalRegistry()
    animals = raw.get("animals")
    if not isinstance(animals, list):
        return AnimalRegistry()
    return AnimalRegistry([a for a in animals if isinstance(a, dict)])


def load_registry(path: Optional[str] = None) -> AnimalRegistry:
    """Load the canonical registry best-effort (default: the resources repo).

    Returns an empty (``available == False``) registry if the file is missing or
    malformed, so the rig runs - and Session Start still works - without the
    resources repo present (docs/decisions/0075).
    """
    p = path or animals_json_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return parse_registry(fh.read())
    except (OSError, ValueError):
        return AnimalRegistry()
