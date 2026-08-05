"""
note_tags.py - the operator's intervention-tag vocabulary (pure core).

The session note log (``session_notes``, docs/decisions/0074) lets the operator
stamp timestamped entries; tags make those entries QUERYABLE across sessions
(e.g. compare sessions with vs without a "non-stem dissuasion" tag). Rather than
hardcode a fixed set, the experimenter defines their OWN tags at runtime and the
vocabulary persists across sessions in ``host/note_tags.json`` (same host/ file
pattern as ``phase3.json`` / ``rules.json``), so it is reused and editable over
time.

A tag is ``{"name": str, "counter": bool}``. ``counter`` marks a tag whose presses
are meant to accumulate (e.g. "lift-backs" +N); it is a hint for the UI/label -
each press still stores one stamped entry (``count`` 1) so every press stays on
the timeline, and the summary tallies them (docs/decisions/0074).

Layering, like ``phase3_config``: this core reads / mutates / validates / renders
the vocabulary and does the small JSON load/save; the Tk shell owns the manager
dialog and the quick-tag buttons. Pure/stdlib (``json``), headlessly testable.

Headless tests: python -m unittest test_note_tags.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

# A short self-describing block written into a fresh note_tags.json (mirrors
# phase3.json's ``_schema``); preserved on round-trip.
_SCHEMA_NOTE = ("User-defined note-tag vocabulary (ADR-0074). Each tag is "
                "{name, counter}; counter=true means presses accumulate (+N). "
                "Edited from the notes popup's 'Manage tags' dialog.")


class DuplicateTagError(ValueError):
    """Raised when adding/renaming to a name that already exists (case-insensitive)."""


class TagVocabulary:
    """An ordered, de-duplicated list of user-defined note tags.

    Names are compared case-insensitively for uniqueness but stored as typed.
    Single-threaded (the Tk thread is the sole caller), like the other cores."""

    def __init__(self, tags: Optional[List[Dict[str, Any]]] = None) -> None:
        self._tags: List[Dict[str, Any]] = []
        for t in (tags or []):
            name = _clean(t.get("name"))
            if name and not self._find(name):
                self._tags.append({"name": name,
                                   "counter": bool(t.get("counter", False))})

    # ----------------------------------------------------------------- read
    def tags(self) -> List[Dict[str, Any]]:
        """The vocabulary as an ordered list of ``{name, counter}`` dicts."""
        return [dict(t) for t in self._tags]

    def names(self) -> List[str]:
        return [t["name"] for t in self._tags]

    def is_counter(self, name: str) -> bool:
        t = self._find(_clean(name))
        return bool(t and t["counter"])

    def __len__(self) -> int:
        return len(self._tags)

    def _find(self, name: str) -> Optional[Dict[str, Any]]:
        low = name.lower()
        for t in self._tags:
            if t["name"].lower() == low:
                return t
        return None

    # --------------------------------------------------------------- mutate
    def add(self, name: str, *, counter: bool = False) -> Dict[str, Any]:
        """Add a new tag; raise ``ValueError`` on an empty name or
        ``DuplicateTagError`` on a name that already exists."""
        clean = _clean(name)
        if not clean:
            raise ValueError("tag name must not be empty")
        if self._find(clean):
            raise DuplicateTagError(f"tag {clean!r} already exists")
        tag = {"name": clean, "counter": bool(counter)}
        self._tags.append(tag)
        return tag

    def rename(self, old: str, new: str) -> None:
        """Rename ``old`` to ``new`` (in place, preserving order + counter flag).

        Raises ``KeyError`` if ``old`` is absent, ``ValueError`` on an empty new
        name, or ``DuplicateTagError`` if ``new`` collides with a different tag."""
        t = self._find(_clean(old))
        if t is None:
            raise KeyError(old)
        clean = _clean(new)
        if not clean:
            raise ValueError("tag name must not be empty")
        collision = self._find(clean)
        if collision is not None and collision is not t:
            raise DuplicateTagError(f"tag {clean!r} already exists")
        t["name"] = clean

    def set_counter(self, name: str, counter: bool) -> None:
        """Set a tag's counter flag; raise ``KeyError`` if it's absent."""
        t = self._find(_clean(name))
        if t is None:
            raise KeyError(name)
        t["counter"] = bool(counter)

    def delete(self, name: str) -> bool:
        """Remove ``name``; return whether a tag was removed."""
        t = self._find(_clean(name))
        if t is None:
            return False
        self._tags.remove(t)
        return True

    # ----------------------------------------------------------- serialize
    def to_obj(self) -> Dict[str, Any]:
        """The vocabulary as a JSON-ready dict (with the self-describing block)."""
        return {"_schema": {"note": _SCHEMA_NOTE}, "tags": self.tags()}

    def render(self) -> str:
        """Pretty JSON text for the shell to write (atomically)."""
        return json.dumps(self.to_obj(), indent=2, ensure_ascii=False) + "\n"


def _clean(name: Any) -> str:
    return str(name).strip() if name is not None else ""


def load_tags(path: str) -> TagVocabulary:
    """Load the vocabulary from ``path``; an empty vocabulary if it's absent.

    A malformed file (not a JSON object, or a bad ``tags`` list) yields an empty
    vocabulary rather than raising, so the notes UI still opens (the operator can
    just re-add tags). Unknown/invalid tag entries are skipped by the constructor.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return TagVocabulary()
    except (ValueError, OSError):
        return TagVocabulary()
    if not isinstance(raw, dict):
        return TagVocabulary()
    tags = raw.get("tags")
    if not isinstance(tags, list):
        return TagVocabulary()
    return TagVocabulary(tags)


def save_tags(path: str, vocab: TagVocabulary) -> None:
    """Write ``vocab`` to ``path`` atomically (temp + ``os.replace``)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(vocab.render())
    os.replace(tmp, path)
