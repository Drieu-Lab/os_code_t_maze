"""
session_notes.py - the operator's per-session note log (pure core).

The rig aligns behavior, video, and neural data on one host timeline (see
docs/decisions/0048), so an operator's observations - "dissuaded a non-stem
shortcut", "lifted the rat back to the hub" - are far more useful as TIMESTAMPED,
machine-parseable events than as one free-text blob. ADR-0073 stored notes as a
single string; this core replaces that with an ordered list of stamped, editable
entries (see docs/decisions/0074).

Each :class:`NoteEntry` carries when it happened in the shared clocks - ``iso``
(human wall-clock), ``mono`` (the steady ``time.monotonic()`` used by the live
vision/rules streams) - plus the Phase 3 ``trial_index`` in progress at the time,
the free ``text``, and (for a tag press) the ``tag`` name and a ``count`` (so a
"counter" tag press records a ``+1`` event on the timeline). ``edited_iso`` is set
when an entry's text is later changed; the original stamps are preserved.

Layering, like the other cores (``phase3_log`` renders CSV text, the controller
writes it): this module is PURE - it owns the entry list + edit/delete + JSON
Lines rendering/parsing, but does NO file I/O and imports no Tk. The thin
``session_controller.py`` shell owns the file path and writes ``render_jsonl()``
atomically (temp + ``os.replace``) on every change, and reloads via
:func:`parse_jsonl` on Stop so notes survive an unclean end (app crash or the
documented Due serial wedge). We chose a whole-file atomic rewrite over
append-only op-logging because entries can be edited/deleted, and rewriting the
few-line file each change is the simplest way to keep the on-disk record
consistent (see docs/decisions/0074).

Headless tests: python -m unittest test_session_notes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NoteEntry:
    """One stamped operator note on the session timeline.

    ``id`` is a per-session sequence number (stable across edits, so the GUI can
    address an entry to edit/delete it). ``mono`` / ``trial_index`` are None when
    they weren't available (e.g. a note jotted before Phase 3 published a state).
    ``tag`` names the intervention tag when the entry came from a tag press;
    ``count`` is that press's increment (1 for an event tag, +N accumulates across
    presses for a counter tag). ``edited_iso`` records a later text edit, leaving
    the original ``iso`` / ``mono`` / ``trial_index`` untouched.
    """
    id: int
    iso: str
    text: str
    mono: Optional[float] = None
    trial_index: Optional[int] = None
    tag: Optional[str] = None
    count: int = 1
    edited_iso: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        """The entry as a plain JSON-ready dict (field names preserved)."""
        return {
            "id": self.id,
            "iso": self.iso,
            "text": self.text,
            "mono": self.mono,
            "trial_index": self.trial_index,
            "tag": self.tag,
            "count": self.count,
            "edited_iso": self.edited_iso,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NoteEntry":
        """Rebuild an entry from a parsed dict, tolerating missing keys (an older
        record or a partially-written line) by falling back to the defaults."""
        return cls(
            id=int(d["id"]),
            iso=str(d.get("iso", "")),
            text=str(d.get("text", "")),
            mono=d.get("mono"),
            trial_index=d.get("trial_index"),
            tag=d.get("tag"),
            count=int(d.get("count", 1) or 1),
            edited_iso=d.get("edited_iso"),
        )


class NoteLog:
    """An ordered list of :class:`NoteEntry` with add/edit/delete + JSONL I/O.

    Single-threaded like the other cores (the controller's Tk thread is the sole
    caller). Not crash-safe by itself - the shell persists ``render_jsonl()``
    atomically after each mutation (see the module docstring)."""

    def __init__(self, entries: Optional[List[NoteEntry]] = None,
                 next_id: Optional[int] = None) -> None:
        self._entries: List[NoteEntry] = list(entries or [])
        # Next id to hand out: one past the max existing id, so ids stay unique
        # and monotonic even after a reload (parse_jsonl passes this in).
        if next_id is not None:
            self._next_id = next_id
        else:
            self._next_id = (max((e.id for e in self._entries), default=-1) + 1)

    # --------------------------------------------------------------- mutate
    def add(self, text: str, *, iso: str, mono: Optional[float] = None,
            trial_index: Optional[int] = None, tag: Optional[str] = None,
            count: int = 1) -> NoteEntry:
        """Append a new stamped entry and return it. ``text`` may be empty (a bare
        quick-stamp marks the moment; its text can be filled in later via
        :meth:`edit`)."""
        entry = NoteEntry(id=self._next_id, iso=iso, text=text, mono=mono,
                          trial_index=trial_index, tag=tag, count=count)
        self._next_id += 1
        self._entries.append(entry)
        return entry

    def edit(self, entry_id: int, text: str, *,
             edited_iso: str) -> Optional[NoteEntry]:
        """Replace an entry's ``text`` in place, stamping ``edited_iso`` and
        preserving its original ``iso`` / ``mono`` / ``trial_index``. Returns the
        edited entry, or None if ``entry_id`` isn't found."""
        e = self._by_id(entry_id)
        if e is None:
            return None
        e.text = text
        e.edited_iso = edited_iso
        return e

    def delete(self, entry_id: int) -> bool:
        """Remove the entry with ``entry_id``; return whether one was removed."""
        e = self._by_id(entry_id)
        if e is None:
            return False
        self._entries.remove(e)
        return True

    # ----------------------------------------------------------------- read
    def _by_id(self, entry_id: int) -> Optional[NoteEntry]:
        for e in self._entries:
            if e.id == entry_id:
                return e
        return None

    def entries(self) -> List[NoteEntry]:
        """The live ordered entry list (do not mutate in place)."""
        return self._entries

    def as_list(self) -> List[Dict[str, Any]]:
        """The entries as JSON-ready dicts, in order (what the summary folds)."""
        return [e.as_dict() for e in self._entries]

    def __len__(self) -> int:
        return len(self._entries)

    # ----------------------------------------------------------- serialize
    def render_jsonl(self) -> str:
        """Render the whole log as JSON Lines (one compact object per entry).

        The shell writes this atomically after each change; cheap for the handful
        of entries a session accrues. Ends with a trailing newline so an append
        would land on a fresh line."""
        if not self._entries:
            return ""
        return "".join(
            json.dumps(e.as_dict(), ensure_ascii=False) + "\n"
            for e in self._entries)


def parse_jsonl(text: str) -> NoteLog:
    """Rebuild a :class:`NoteLog` from JSON Lines text, tolerating a corrupt tail.

    Blank lines and lines that don't parse as a JSON object with an ``id`` are
    skipped rather than raising, so a file half-written when the app died still
    loads its intact entries (see docs/decisions/0074). The next-id counter is
    restored to one past the largest id seen, keeping ids unique after a reload.
    """
    entries: List[NoteEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or "id" not in obj:
            continue
        try:
            entries.append(NoteEntry.from_dict(obj))
        except (TypeError, ValueError):
            continue
    return NoteLog(entries)
