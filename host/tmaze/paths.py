"""paths.py - the one place that resolves on-disk locations for the tmaze tools.

Modules that need a file path import from here instead of hand-rolling
``os.path.dirname(__file__) + "/.."`` chains, which silently break when a file
moves to a different depth (see ADR-0083). Two kinds of location:

- ``repo_root()`` - the code-repo root, found by searching *upward* for a
  landmark (``.git`` / ``pyproject.toml``) rather than counting ``..`` hops, so
  it is immune to how deep the caller sits. Use it for sibling in-repo files
  (e.g. the firmware pin-map header).
- ``package_data(*parts)`` - a file bundled inside the ``tmaze`` package,
  addressed by package name via ``importlib.resources`` (also depth-immune).

For the *sibling* ``os_code_t_maze_resources`` repo, keep using
``tmaze.core.records`` ``resources_root()`` (env override + landmark default),
not a fixed relative hop.

Pure stdlib; no Tk/OpenCV/serial, so cores may import this freely.
"""
from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

_LANDMARKS = (".git", "pyproject.toml")

# Where the rig writes recordings (camera video + the per-session Phase 3
# bundles). This is a *machine* location (not in-repo), so it is the one thing
# here that isn't landmark-resolved: it defaults to the rig PC's folder but can
# be overridden per-machine via the TMAZE_RECORDINGS_DIR env var, so the box
# isn't hardcoded and tests can point elsewhere (see docs/decisions/0088).
_RECORDINGS_ENV = "TMAZE_RECORDINGS_DIR"
_DEFAULT_CAMERA_OUTPUT_DIR = r"C:\Users\Celine\Documents\t-maze_recordings"

# Extra, read-only ARCHIVE recordings roots (older sessions moved off the rig PC
# when its disk fills, e.g. Z:\Ravi\phase3_recordings). Discovery scans these in
# ADDITION to the primary root; the rig still WRITES only to the primary. Each
# entry is a full `phase3_recordings` folder (the one that directly holds session
# bundles), os.pathsep-separated. See docs/decisions/0113 (extends 0088).
_RECORDINGS_ARCHIVES_ENV = "TMAZE_RECORDINGS_ARCHIVES"


def repo_root() -> Path:
    """Absolute path to the code-repo root (the folder holding ``.git`` /
    ``pyproject.toml``), located by walking up from this file until a landmark
    is found. Raises if none is found (an unexpected, unrecoverable layout)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if any((parent / mark).exists() for mark in _LANDMARKS):
            return parent
    raise RuntimeError(
        f"could not locate the repo root (no {' / '.join(_LANDMARKS)}) above {here}"
    )


def host_dir() -> Path:
    """Absolute path to the repo's ``host/`` folder - the home of the per-rig
    runtime config/data that lives *beside* the package, not inside it:
    ``zones.json`` / ``roi_polygon.json`` / ``landmarks.json`` / ``rig_scale.json``
    / ``phase3.json`` / ``rules.json`` / ``note_tags.json`` / ``session_meta.json``
    and the ``data/`` logs. Anchored off ``repo_root()`` so it is unaffected by how
    deep the calling module sits in the package (see docs/decisions/0083)."""
    return repo_root() / "host"


def package_data(*parts: str) -> Path:
    """Filesystem path to a data file bundled inside the ``tmaze`` package,
    addressed by package name (depth-immune, unlike a relative ``..`` path)."""
    return Path(resources.files("tmaze").joinpath(*parts))


def camera_output_dir() -> Path:
    """Root folder the rig writes recordings under (camera video + the
    per-session bundle tree). The ``TMAZE_RECORDINGS_DIR`` env var overrides the
    machine default, so this is the single source of truth for *where recordings
    live* - shared by the camera/session UI and the session-registry tool so they
    can't drift (see docs/decisions/0088)."""
    return Path(os.environ.get(_RECORDINGS_ENV) or _DEFAULT_CAMERA_OUTPUT_DIR)


def phase3_recordings_root() -> Path:
    """Where the Phase 3 session bundles live: ``<camera_output_dir>/
    phase3_recordings`` (the PRIMARY root the rig writes to and the default the
    tools scan). Use :func:`phase3_recordings_roots` when you also want the
    read-only archive locations (docs/decisions/0113)."""
    return camera_output_dir() / "phase3_recordings"


def phase3_recordings_archives() -> list[Path]:
    """The read-only ARCHIVE recordings roots from ``TMAZE_RECORDINGS_ARCHIVES``
    (os.pathsep-separated full ``phase3_recordings`` folders), in order. Empty
    when the env var is unset/blank. These are scanned in addition to the primary
    root; the rig never writes to them (docs/decisions/0113)."""
    raw = os.environ.get(_RECORDINGS_ARCHIVES_ENV) or ""
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def phase3_recordings_roots() -> list[Path]:
    """The full recordings search path: the primary root first, then any archive
    roots, order-preserving de-duplicated. This is the single source of truth for
    *everywhere sessions may live* - shared by the registry builder and the
    offline analysis reader so a history split across drives is fully discovered
    (docs/decisions/0113, extends 0088)."""
    seen: set[str] = set()
    out: list[Path] = []
    for p in [phase3_recordings_root(), *phase3_recordings_archives()]:
        key = os.path.normcase(os.path.normpath(str(p)))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
