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

import json
import os
from importlib import resources
from pathlib import Path
from typing import Optional

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
# bundles). Since docs/decisions/0156 the list is PROJECT CONFIG in the tracked
# host/recordings_roots.json (below); this env var (os.pathsep-separated) is a
# per-process OVERRIDE kept for tests/other machines. See docs/decisions/0113.
_RECORDINGS_ARCHIVES_ENV = "TMAZE_RECORDINGS_ARCHIVES"

# Where sessions may live, as TRACKED project config (docs/decisions/0156). The
# set of folders holding the experiment's history is a fact about the project,
# not a per-machine taste, so it is versioned with the code instead of hidden in
# a git-ignored pref: `{"primary": <folder or null>, "archives": [<folders>]}`.
# `primary` null means "the folder the rig writes to" (phase3_recordings_root).
_ROOTS_CONFIG_FILENAME = "recordings_roots.json"
ROOTS_CONFIG_SCHEMA_VERSION = 1


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
    per-session bundle tree). This is the single source of truth for *where
    recordings live* - shared by the camera/session UI and the session-registry
    tool so they can't drift (see docs/decisions/0088).

    Precedence: the operator-picked GUI pref (persisted in ``rig_prefs.json`` via
    the Session panel), then the ``TMAZE_RECORDINGS_DIR`` env var, then the
    machine default. The GUI pref wins over the env var the same way the Records
    Manager scan root does (docs/decisions/0151, extends 0088); the env var stays
    useful for tests/other machines that have no pref file."""
    pref = recordings_base_pref()
    return Path(pref or os.environ.get(_RECORDINGS_ENV) or _DEFAULT_CAMERA_OUTPUT_DIR)


def phase3_recordings_root() -> Path:
    """Where the Phase 3 session bundles live: ``<camera_output_dir>/
    phase3_recordings`` (the PRIMARY root the rig writes to and the default the
    tools scan). Use :func:`phase3_recordings_roots` when you also want the
    read-only archive locations (docs/decisions/0113)."""
    return camera_output_dir() / "phase3_recordings"


# --------------------------------------------------------------------------- #
# Recording roots as project config (host/recordings_roots.json)
#
# See docs/decisions/0156. Read best-effort (a missing/malformed file means "no
# archives, primary = the rig's write folder"); written by the Records Manager's
# Browse / Archives controls with an atomic replace, preserving the file's
# `_schema` note.

def recordings_roots_config_path() -> Path:
    """Absolute path to the tracked roots config (``host/recordings_roots.json``)."""
    return host_dir() / _ROOTS_CONFIG_FILENAME


def read_recordings_roots_config() -> dict:
    """``{"primary": Optional[str], "archives": list[str]}`` from the config
    file. Best-effort: absent/malformed -> ``{"primary": None, "archives": []}``.
    Blank strings are dropped; the archive list keeps the file's order."""
    out: dict = {"primary": None, "archives": []}
    try:
        with open(recordings_roots_config_path(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    prim = raw.get("primary")
    if isinstance(prim, str) and prim.strip():
        out["primary"] = prim.strip()
    arch = raw.get("archives")
    if isinstance(arch, list):
        out["archives"] = [a.strip() for a in arch
                           if isinstance(a, str) and a.strip()]
    return out


def write_recordings_roots_config(primary: Optional[str],
                                  archives: list[str]) -> None:
    """Persist the roots config, keeping any ``_schema`` note already in the
    file. ``primary`` None/blank is stored as null (= the rig's write folder).
    Atomic (write-then-replace) so a crash can't leave a half-written file."""
    p = recordings_roots_config_path()
    existing: dict = {}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            existing = loaded
    except (OSError, ValueError):
        pass
    data: dict = {}
    if "_schema" in existing:
        data["_schema"] = existing["_schema"]
    data["schema_version"] = ROOTS_CONFIG_SCHEMA_VERSION
    data["primary"] = (str(primary).strip() or None) if primary else None
    data["archives"] = [str(a).strip() for a in archives if str(a).strip()]
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, p)


def scan_primary_root() -> Path:
    """The PRIMARY folder the records tools scan for session bundles: the
    config's ``primary`` when set, else the folder the rig writes to
    (:func:`phase3_recordings_root`). Kept separate from the write location so
    pointing the scan elsewhere never redirects recording (docs/decisions/0156)."""
    prim = read_recordings_roots_config()["primary"]
    return Path(prim) if prim else phase3_recordings_root()


def archives_env_override() -> Optional[list[Path]]:
    """The archive roots from ``TMAZE_RECORDINGS_ARCHIVES`` if that env var is
    set (even to blank = "no archives"), else None. Exposed so a GUI can tell the
    operator the config file is being overridden for this process."""
    raw = os.environ.get(_RECORDINGS_ARCHIVES_ENV)
    if raw is None:
        return None
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def phase3_recordings_archives() -> list[Path]:
    """The read-only ARCHIVE recordings roots, in order: the
    ``TMAZE_RECORDINGS_ARCHIVES`` env override when set, else the tracked
    config's ``archives`` list. These are scanned in addition to the primary
    root; the rig never writes to them (docs/decisions/0113, 0156)."""
    env = archives_env_override()
    if env is not None:
        return env
    return [Path(a) for a in read_recordings_roots_config()["archives"]]


def phase3_recordings_roots() -> list[Path]:
    """The full recordings search path: the scan primary first, then any archive
    roots, order-preserving de-duplicated. This is the single source of truth for
    *everywhere sessions may live* - shared by the registry builder, the Records
    Manager, the past-session reader and the offline analysis reader so a history
    split across drives is fully discovered and every tool scans the SAME set
    (docs/decisions/0113, 0156; extends 0088)."""
    seen: set[str] = set()
    out: list[Path] = []
    for p in [scan_primary_root(), *phase3_recordings_archives()]:
        key = os.path.normcase(os.path.normpath(str(p)))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# --------------------------------------------------------------------------- #
# Machine-local GUI preferences (rig_prefs.json)
#
# A small git-ignored file next to the other host/*.json config that remembers
# operator choices which must survive a restart. Kept HERE in pure ``paths`` (not
# in a UI module) so the single-source path resolver (``camera_output_dir``) and
# the Session panel share one file with one merge-writer, and so headless tools
# can read the same choice. Currently two keys: ``recordings_base`` (the rig
# recordings base folder; highest precedence in ``camera_output_dir``, extends
# ADR-0088) and ``low_disk_warn_gb`` (the operator-configurable free-space
# warning threshold shown on the Session panel). See docs/decisions/0151.
_PREFS_FILENAME = "rig_prefs.json"

# Default free-space warning threshold (GB) when the operator hasn't set one; the
# deliberate value lives in the ADR, callers pass it in so there's no magic number
# baked in here (docs/decisions/0151).
DEFAULT_LOW_DISK_WARN_GB = 40.0


def prefs_path() -> Path:
    """Absolute path to the machine-local GUI-pref file (``host/rig_prefs.json``)."""
    return host_dir() / _PREFS_FILENAME


def read_prefs() -> dict:
    """The machine-local GUI-pref dict (best-effort; ``{}`` if absent/unreadable)."""
    try:
        with open(prefs_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_prefs(**updates) -> None:
    """Merge ``updates`` into the pref file, preserving the other keys
    (best-effort; a failed write is swallowed like the other GUI-pref writers)."""
    prefs = read_prefs()
    prefs.update(updates)
    try:
        with open(prefs_path(), "w", encoding="utf-8") as fh:
            json.dump(prefs, fh, indent=2)
    except OSError:
        pass


def recordings_base_pref() -> Optional[str]:
    """The operator-picked recordings base folder, or ``None`` if unset. Highest
    precedence in :func:`camera_output_dir` (docs/decisions/0151, extends 0088)."""
    val = read_prefs().get("recordings_base")
    return val if isinstance(val, str) and val.strip() else None


def set_recordings_base(path: str) -> None:
    """Persist the recordings base folder the operator picked (docs/decisions/0151)."""
    write_prefs(recordings_base=str(path))


def low_disk_warn_gb(default: float = DEFAULT_LOW_DISK_WARN_GB) -> float:
    """The low-disk warning threshold in GB (operator-configurable, persisted);
    ``default`` when unset or invalid (docs/decisions/0151)."""
    val = read_prefs().get("low_disk_warn_gb")
    try:
        gb = float(val)
    except (TypeError, ValueError):
        return float(default)
    return gb if gb > 0 else float(default)


def set_low_disk_warn_gb(gb: float) -> None:
    """Persist the low-disk warning threshold in GB (docs/decisions/0151)."""
    write_prefs(low_disk_warn_gb=float(gb))
