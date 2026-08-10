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
    phase3_recordings`` (the folder ``build_session_registry`` scans)."""
    return camera_output_dir() / "phase3_recordings"
