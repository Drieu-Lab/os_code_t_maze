"""
session_meta.py - sticky per-session metadata (pure core).

A recording bundle is self-describing about the *machine* side (task params,
animal state, streams) but the few **human** facts a paper training log wants -
who ran it (``trainer``), what stage of training it was (``stage``), and what
reward was used (``reward_type``) - live only in someone's memory or the
spreadsheet. Those facts are mostly **sticky**: the trainer and reward type
rarely change day-to-day, and the stage equals the phase the rig is running. So
rather than re-typing them every session, we capture them at the source and let
the rig remember them (variant A3 of the coordinated-records plan, ADR-0079).

Two data shapes live here, both pure/stdlib (``json``), headlessly testable:

- :class:`SessionMeta` - the per-session record written into a bundle as
  ``config/session_meta.json`` (its own manifest stream, like ``animal_state``,
  ADR-0077). Schema v1: ``{schema_version, session_id, trainer, stage,
  stage_source, reward_type}``. ``stage`` is normally auto-derived from the
  active phase (``stage_source="auto"``); the operator may override it for a rare
  sub-stage (Habituation/Shaping/Reversal/Probe), recorded as
  ``stage_source="override"`` so a reader can tell a typed stage from a derived
  one.

- :class:`SessionMetaDefaults` - the sticky ``trainer`` / ``reward_type`` values
  persisted across sessions in ``host/session_meta.json`` (the same host/ config
  pattern as ``note_tags.json``, ADR-0074). The stage is deliberately NOT sticky
  (it is re-derived each session) so a stale override can never silently
  mislabel a later session.

Layering, like ``note_tags`` / ``phase3_config``: this core builds / renders /
parses / validates the records and does the small JSON load/save; the Tk shell
(``session_controller``) owns the Session-panel fields and the atomic bundle
write.

Headless tests: python -m unittest test_session_meta.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

# Bump when the session_meta.json layout changes so an offline reader can branch.
SCHEMA_VERSION = 1

# How ``stage`` was decided (recorded in ``stage_source``).
STAGE_SOURCE_AUTO = "auto"          # derived from the active phase
STAGE_SOURCE_OVERRIDE = "override"  # operator typed a sub-stage

# A short self-describing block written into a fresh host/session_meta.json
# (mirrors note_tags.json / phase3.json); preserved on round-trip.
_DEFAULTS_SCHEMA_NOTE = (
    "Sticky Session-panel defaults (ADR-0079): trainer + reward_type remembered "
    "across sessions. Stage is NOT stored here - it is re-derived from the active "
    "phase each session so a stale override can't mislabel a later run.")


@dataclass
class SessionMeta:
    """The human-side metadata for one session (written to the bundle).

    Renders to / parses from JSON with no file I/O or Tk, so it is testable
    headlessly like the other pure cores."""
    session_id: str
    trainer: str = ""
    stage: str = ""
    stage_source: str = STAGE_SOURCE_AUTO
    reward_type: str = ""
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        """Pretty JSON text for the shell to write (atomically)."""
        return json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionMeta":
        """Rebuild from a parsed dict, tolerating missing/extra keys."""
        return cls(
            session_id=str(d.get("session_id", "")),
            trainer=str(d.get("trainer", "")),
            stage=str(d.get("stage", "")),
            stage_source=str(d.get("stage_source", STAGE_SOURCE_AUTO)),
            reward_type=str(d.get("reward_type", "")),
            schema_version=int(d.get("schema_version", SCHEMA_VERSION)),
        )


def build(session_id: str, *, trainer: str = "", reward_type: str = "",
          auto_stage: str = "", stage_override: str = "") -> SessionMeta:
    """Build a :class:`SessionMeta` from the Session-panel inputs.

    ``stage`` is the operator's ``stage_override`` when they typed one (recorded
    as ``stage_source="override"``); otherwise the ``auto_stage`` derived from the
    active phase (``stage_source="auto"``). All text is whitespace-trimmed."""
    override = (stage_override or "").strip()
    if override:
        stage, source = override, STAGE_SOURCE_OVERRIDE
    else:
        stage, source = (auto_stage or "").strip(), STAGE_SOURCE_AUTO
    return SessionMeta(
        session_id=session_id,
        trainer=(trainer or "").strip(),
        stage=stage,
        stage_source=source,
        reward_type=(reward_type or "").strip(),
    )


@dataclass
class SessionMetaDefaults:
    """The sticky Session-panel values remembered across sessions."""
    trainer: str = ""
    reward_type: str = ""

    def to_obj(self) -> Dict[str, Any]:
        """JSON-ready dict (with the self-describing block)."""
        return {
            "_schema": {"note": _DEFAULTS_SCHEMA_NOTE},
            "trainer": self.trainer,
            "reward_type": self.reward_type,
        }

    def render(self) -> str:
        return json.dumps(self.to_obj(), indent=2, ensure_ascii=False) + "\n"


def load_defaults(path: str) -> SessionMetaDefaults:
    """Load the sticky defaults from ``path``; empty defaults if it's absent.

    A malformed file (not a JSON object) yields empty defaults rather than
    raising, so the Session panel still builds (the operator can re-type)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return SessionMetaDefaults()
    except (ValueError, OSError):
        return SessionMetaDefaults()
    if not isinstance(raw, dict):
        return SessionMetaDefaults()
    return SessionMetaDefaults(
        trainer=str(raw.get("trainer", "")).strip(),
        reward_type=str(raw.get("reward_type", "")).strip(),
    )


def save_defaults(path: str, defaults: SessionMetaDefaults) -> None:
    """Write ``defaults`` to ``path`` atomically (temp + ``os.replace``)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(defaults.render())
    os.replace(tmp, path)
