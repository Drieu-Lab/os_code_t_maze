"""session_config.py - per-session configuration snapshot (pure core).

A session's data is only reproducible if you also know the *definitions* that
shaped it: the maze zone map, the tracker ROI/landmarks, the cm/px scale, the
lens calibration, the task/reward config, and the live tuning that was applied.
Those live in single-path files under ``host/`` (and one in the sibling
resources repo) that are overwritten in place when re-drawn, plus a handful of
scalars that live in code / GUI state rather than any file. This core copies
each of those files into a session's ``config/`` folder and writes a small index
(which files were present + a SHA-256 of each, so an offline reader can confirm
integrity and detect drift against the live ``host/*.json``), alongside a
``runtime_params.json`` for the non-file scalars (see docs/decisions/0072).

The host-local config files all live beside this module in ``host/``; their
*filenames* are the shared contract. Each is owned/loaded by its GUI module
(``maze_state.ZONES_PATH``, ``tracker_view.ROI_PATH``/``LANDMARKS_PATH``,
``rules_view.SCALE_PATH``/``RULES_PATH``, ``phase3_controller.PHASE3_PATH``), but
this core is a stdlib-only pure core and must not import those Tk/OpenCV shells,
so it lists the filenames here directly. Files that live elsewhere or are chosen
at runtime (the lens calibration JSON in the resources repo, the active tracker
model ``.npz``) are passed in by the caller as ``extra_sources``.

Stdlib only (``os``/``shutil``/``hashlib``/``json``), so it is headlessly
testable like the other pure cores. Best-effort by contract: a missing or
unreadable source is recorded as ``present: false`` and never raises.

Headless tests: python -m unittest test_session_config.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from tmaze.paths import host_dir as _host_dir

# Bump when the snapshot/index layout changes so an offline reader can branch.
SCHEMA_VERSION = 1

# The host-local config files live in host/ (beside the package), found by
# landmark search rather than relative to this module (see docs/decisions/0083).
_HOST = str(_host_dir())

# Host-local config files copied verbatim into a session's config/ folder. Each
# is owned by a GUI module (noted); the filename is the shared contract. These
# are versioned as fixed rig parameters now that the geometry is stable
# (docs/decisions/0072, superseding the gitignore in ADR-0014/0018/0023/0025).
HOST_CONFIG_FILES = (
    "zones.json",        # maze_state.ZONES_PATH - maze zone polygons + well + stem axis
    "roi_polygon.json",  # tracker_view.ROI_PATH - tracker ROI mask
    "landmarks.json",    # tracker_view.LANDMARKS_PATH - reward-well / body-sensor points
    "rig_scale.json",    # rules_view.SCALE_PATH - cm/px scale (+ optional homography)
    "phase3.json",       # phase3_controller.PHASE3_PATH - cued-task reward/cue/sequence
    "rules.json",        # rules_view.RULES_PATH - rules-engine profiles
)


def host_config_sources(host_dir: str = _HOST) -> Dict[str, str]:
    """The ``{filename: absolute path}`` map of the host-local config files."""
    return {name: os.path.join(host_dir, name) for name in HOST_CONFIG_FILES}


def sha256_file(path: str) -> Optional[str]:
    """Hex SHA-256 of ``path`` (streamed), or None if it can't be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _atomic_json(path: str, obj: Any) -> None:
    """Write ``obj`` as pretty JSON to ``path`` atomically (temp + os.replace)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def write_config_snapshot(dest_dir: str, *, session_id: str = "",
                          extra_sources: Optional[Mapping[str, str]] = None,
                          runtime_params: Optional[Mapping[str, Any]] = None,
                          host_dir: str = _HOST) -> Dict[str, Any]:
    """Copy every existing config source into ``dest_dir`` and write the index.

    ``extra_sources`` adds/overrides files by ``{filename: source path}`` (e.g.
    the lens calibration JSON, the active model ``.npz``). ``runtime_params``, if
    given, is written verbatim as ``runtime_params.json``. Creates ``dest_dir``.
    Returns a summary dict (``config_dir``, ``snapshot_path``,
    ``runtime_params_path``, ``present``, ``missing``). Best-effort per file: a
    missing/unreadable source is recorded ``present: false`` and never raises.
    """
    os.makedirs(dest_dir, exist_ok=True)

    sources: Dict[str, str] = dict(host_config_sources(host_dir))
    if extra_sources:
        for name, path in extra_sources.items():
            if name and path:
                sources[name] = path

    entries: List[Dict[str, Any]] = []
    for name in sorted(sources):
        src = sources[name]
        entry: Dict[str, Any] = {
            "name": name, "source": src, "present": False,
            "filename": None, "sha256": None, "bytes": None,
        }
        try:
            if src and os.path.isfile(src):
                dest = os.path.join(dest_dir, name)
                shutil.copy2(src, dest)
                entry.update(present=True, filename=name,
                             sha256=sha256_file(dest),
                             bytes=os.path.getsize(dest))
        except OSError:
            pass
        entries.append(entry)

    runtime_params_path: Optional[str] = None
    if runtime_params is not None:
        runtime_params_path = os.path.join(dest_dir, "runtime_params.json")
        _atomic_json(runtime_params_path, dict(runtime_params))

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "generated_iso": datetime.now().isoformat(timespec="milliseconds"),
        "files": entries,
        "runtime_params_file": (
            "runtime_params.json" if runtime_params_path else None),
    }
    snapshot_path = os.path.join(dest_dir, "config_snapshot.json")
    _atomic_json(snapshot_path, snapshot)

    return {
        "config_dir": dest_dir,
        "snapshot_path": snapshot_path,
        "runtime_params_path": runtime_params_path,
        "present": [e["name"] for e in entries if e["present"]],
        "missing": [e["name"] for e in entries if not e["present"]],
    }
