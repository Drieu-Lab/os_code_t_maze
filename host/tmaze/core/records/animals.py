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

from tmaze.core.records import RecordsFileError
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


# --------------------------------------------------------------------------- #
# Roster editing (Records Manager GUI). animals.json is the single source of
# truth for identity, so the writer validates before it touches disk and never
# drops the human-readable ``_schema`` note.
# --------------------------------------------------------------------------- #

# Kept in step with animals.json's own ``schema_version`` (ADR-0075).
ANIMALS_SCHEMA_VERSION = 1

# Restored if an existing file has no ``_schema`` block, so a GUI-written file
# stays self-documenting.
_DEFAULT_SCHEMA = {
    "note": (
        "Canonical animal identity (ADR-0075). Each entry is {id, aliases}; the "
        "id is the lab's rat number used to key session recordings to husbandry "
        "records. Aliases match loosely (case-, space- and punctuation-"
        "insensitive), so 'Rat 600' resolves to '600'. This is the single source "
        "of truth for animal identity, read by the rig at Session Start and by "
        "the records builders."
    )
}


def load_records(path: Optional[str] = None) -> List[Dict[str, object]]:
    """The raw ``[{id, aliases}]`` roster (best-effort; [] if missing/malformed).

    Unlike :func:`load_registry`, this keeps each animal's aliases so the GUI can
    show and edit them. Each entry is normalised to ``{"id": str,
    "aliases": [str, ...]}``.
    """
    p = path or animals_json_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict) or not isinstance(raw.get("animals"), list):
        return []
    return normalize_records(raw["animals"])


def load_records_for_write(path: Optional[str] = None) -> List[Dict[str, object]]:
    """Like :func:`load_records`, but distinguish "absent" from "unreadable".

    :func:`write_records` replaces the whole roster, so it must not treat an
    unreadable-but-present ``animals.json`` as an empty roster (saving on top of
    that would wipe the identity source of truth - see :class:`RecordsFileError`
    / bug #260810_RecordsIntegrity). A genuinely empty-but-valid file
    (``{"animals": []}``) reads as ``[]`` and is fine.

    - File absent -> ``[]``.
    - File present but not valid JSON with an ``animals`` list -> raise
      :class:`RecordsFileError`.
    """
    p = path or animals_json_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RecordsFileError(
            f"{p} exists but could not be read ({exc}); refusing to overwrite "
            "the animal roster.") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("animals"), list):
        raise RecordsFileError(
            f"{p} exists but is not a valid animals.json (no 'animals' list); "
            "refusing to overwrite the animal roster.")
    return normalize_records(raw["animals"])


def normalize_records(
        records: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Trim ids, drop blank/duplicate aliases, coerce to ``{id, aliases}``.

    Blank ids are dropped. Alias order is preserved (first occurrence wins).
    """
    out: List[Dict[str, object]] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        aid = str(rec.get("id", "")).strip()
        if not aid:
            continue
        aliases: List[str] = []
        seen = set()
        raw_aliases = rec.get("aliases") or []
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                a = str(alias).strip()
                if a and a not in seen:
                    seen.add(a)
                    aliases.append(a)
        out.append({"id": aid, "aliases": aliases})
    return out


def validate_records(records: List[Dict[str, object]]) -> List[str]:
    """Human-readable problems with a roster; [] means it's safe to write.

    Catches blank ids, duplicate/loosely-colliding ids (e.g. "600" and "Rat 600"
    as two separate animals), and an alias that would resolve to a *different*
    animal than its owner (which would make normalization ambiguous).
    """
    errors: List[str] = []
    id_keys: Dict[str, str] = {}
    for i, rec in enumerate(records or []):
        aid = str(rec.get("id", "")).strip() if isinstance(rec, dict) else ""
        if not aid:
            errors.append(f"Animal #{i + 1}: blank id")
            continue
        k = _key(aid)
        if k in id_keys:
            other = id_keys[k]
            if other == aid:
                errors.append(f"Duplicate id '{aid}'")
            else:
                errors.append(
                    f"Ambiguous ids '{aid}' and '{other}' collapse to the "
                    "same key")
        id_keys[k] = aid

    # Alias collisions across the whole roster (an alias may only ever point at
    # its own animal). Seed the claim map with every canonical id first.
    claim: Dict[str, str] = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        aid = str(rec.get("id", "")).strip()
        if aid:
            claim.setdefault(_key(aid), aid)
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        aid = str(rec.get("id", "")).strip()
        if not aid:
            continue
        for alias in rec.get("aliases") or []:
            k = _key(alias)
            if not k:
                continue
            owner = claim.get(k)
            if owner is None:
                claim[k] = aid
            elif owner != aid:
                errors.append(
                    f"Alias '{alias}' for '{aid}' collides with '{owner}'")
    return errors


def _read_meta(path: str) -> Dict[str, object]:
    """Preserve an existing file's ``_schema`` + ``schema_version`` (or defaults)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            return {
                "_schema": raw.get("_schema", _DEFAULT_SCHEMA),
                "schema_version": raw.get("schema_version",
                                          ANIMALS_SCHEMA_VERSION),
            }
    except (OSError, ValueError):
        pass
    return {"_schema": _DEFAULT_SCHEMA, "schema_version": ANIMALS_SCHEMA_VERSION}


def write_records(records: List[Dict[str, object]],
                  path: Optional[str] = None) -> List[Dict[str, object]]:
    """Validate + write the roster to animals.json atomically; return it cleaned.

    Raises ``ValueError`` (without touching disk) if :func:`validate_records`
    finds a problem, so a bad edit can't corrupt the identity source of truth.
    Preserves the ``_schema`` note and ``schema_version`` of any existing file.
    Writes to a temp file then ``os.replace``.
    """
    cleaned = normalize_records(records)
    errors = validate_records(cleaned)
    if errors:
        raise ValueError("Invalid animal roster:\n  " + "\n  ".join(errors))

    p = path or animals_json_path()
    meta = _read_meta(p)
    doc = {
        "_schema": meta["_schema"],
        "schema_version": meta["schema_version"],
        "animals": cleaned,
    }
    parent = os.path.dirname(os.path.abspath(p))
    os.makedirs(parent, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)
    return cleaned
