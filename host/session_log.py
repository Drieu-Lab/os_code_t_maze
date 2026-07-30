"""
session_log.py - the host session manifest (pure core).

A single session spreads across several independently-written files that share
no common id today: the Phase 3 task record (``phase3_<animal>_<ts>.csv``, see
docs/decisions/0047), the camera video + per-frame sidecar (see docs/decisions/
0011), and the raw serial stream from the separate ``rig_logger.py`` process
(see docs/decisions/0009). Alignment already works (host wall clock everywhere;
firmware ``ms`` <-> wall via the serial log), but nothing records WHICH files
belong to one session or anchors them to a shared instant.

This core builds that index: a ``SessionManifest`` holding a ``session_id``, the
animal, a start/end clock anchor, and a map of the session's stream files, plus
``path_within_session`` - the rule for whether a resolved path actually belongs to
this session's folder tree (see docs/decisions/0066). The thin
``session_controller.py`` shell mints the manifest, fills the stream paths as they
resolve, and writes it to disk; this module only builds and renders it (no file
I/O, no Tk/serial), so it is headlessly testable like the other pure cores.

The clock anchor (``clock_snapshot``) captures the same instant in three host
clocks - ``wall`` (``time.time()`` epoch seconds, absolute + shareable),
``mono`` (``time.monotonic()``, steady but arbitrary-origin), and ``iso`` (human
readable) - because capturing ``wall`` and ``mono`` together is the only way to
place the wall-clocked disk logs and the monotonic-clocked live vision/rules
streams on one timeline (see docs/decisions/0048).

The ``fiducial`` field records that today the anchor is host-only ("host-t0",
tens-of-ms precision, serial latency in the loop) and points at the deferred
hardware upgrade (a firmware SYNC TTL + a camera-visible LED barcode) that would
attach at the SAME session-start seam for sub-ms, Open-Ephys-grade sync.

Headless tests: python -m unittest test_session_log.
"""

from __future__ import annotations

import json
import os.path
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

# Bump when the manifest layout changes so an offline reader can branch on it.
SCHEMA_VERSION = 1

# How the session is anchored today, and the deferred hardware upgrade that would
# attach at the same session-start seam (see docs/future-work.md FW-0018).
FIDUCIAL_METHOD = "host-t0"
FIDUCIAL_PENDING = "firmware SYNC TTL + camera LED barcode (see docs/future-work.md FW-0018)"


def clock_snapshot() -> Dict[str, Any]:
    """Capture one instant in the three host clocks (see module docstring).

    ``wall``/``iso`` are the same real-time clock (epoch float vs ISO string);
    ``mono`` is the steady monotonic clock. The wall+mono PAIR fixes the offset
    between the absolute and monotonic timelines at this instant.
    """
    return {
        "wall": time.time(),
        "mono": time.monotonic(),
        "iso": datetime.now().isoformat(timespec="milliseconds"),
    }


def path_within_session(path: str, session_dir: str) -> bool:
    """Whether ``path`` lies inside ``session_dir``'s folder tree.

    The test behind the manifest's "a stream file belongs to this session" rule
    (see docs/decisions/0049, 0066): a recorder can hand back a path it bound
    before this session existed, and the manifest must refuse it rather than point
    at another session's data.

    Pure path-*string* logic (``os.path`` normalisation only - no filesystem
    access, no current directory), so it stays headlessly testable like the rest of
    this core. Case- and separator-insensitive, as Windows paths require. A path on
    a different drive (the ``host/data`` fallback vs a recordings drive) or the
    folder itself is not "within".
    """
    if not path or not session_dir:
        return False
    base = os.path.normcase(os.path.normpath(session_dir))
    target = os.path.normcase(os.path.normpath(path))
    if target == base:
        return False
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:
        # Different drives, or one path relative and the other absolute.
        return False


def _default_fiducial() -> Dict[str, Any]:
    return {"method": FIDUCIAL_METHOD, "pending": FIDUCIAL_PENDING}


@dataclass
class SessionManifest:
    """The per-session index: id + animal + clock anchor + the stream files.

    ``t0`` / ``t_end`` are ``clock_snapshot()`` dicts (``t_end`` set on stop).
    ``streams`` maps a stream name (``phase3_task``, ``camera_video``, ...) to its
    resolved path (or a small descriptor dict for the serial log, whose exact
    filename the host can't know - it names itself in a separate process).
    """
    session_id: str
    animal: str
    t0: Dict[str, Any]
    t_end: Optional[Dict[str, Any]] = None
    streams: Dict[str, Any] = field(default_factory=dict)
    fiducial: Dict[str, Any] = field(default_factory=_default_fiducial)
    schema_version: int = SCHEMA_VERSION

    def set_stream(self, name: str, value: Any) -> None:
        """Record (or update) one stream's path/descriptor. Idempotent."""
        self.streams[name] = value

    def set_end(self, t_end: Dict[str, Any]) -> None:
        """Stamp the session-end clock anchor."""
        self.t_end = t_end

    def render(self) -> str:
        """Render the whole manifest as pretty JSON text (the shell writes it).

        Cheap enough to re-render on every update (a handful of keys)."""
        return json.dumps(asdict(self), indent=2) + "\n"
