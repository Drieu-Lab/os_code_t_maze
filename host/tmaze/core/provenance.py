"""
provenance.py - who/what produced a session: code, environment, firmware version.

A small, dependency-light helper (stdlib + ``importlib.metadata`` only) that
gathers the version facts a future reader needs to reproduce or audit a session
bundle (review findings P1/P6): the code repo's git commit, the Python + key
package versions the rig ran under, the host machine, and the firmware
serial-protocol version - both what ``rig_config.h`` declares and what the live
handshake actually reported. Written into the session manifest at Start
(docs/decisions/0180) and reused for the ``session_registry`` CSV header.

Kept robust: git is shelled out with a short timeout and degrades to ``unknown``;
a package that isn't installed is simply omitted; every lookup is guarded so
collecting provenance can never block Session Start.

Headless tests: python -m unittest test_provenance   (from host/)
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
from importlib import metadata as _md
from typing import Dict, Optional

# Distribution names (as pip / PyPI know them) whose versions we record: the
# rig-critical deps - camera SDK, vision, video encode, arrays, the serial link,
# imaging - plus this package itself. One not installed is omitted, not an error.
PACKAGES = ("pypylon", "opencv-python", "av", "numpy", "pyserial", "Pillow", "tmaze")

# git is walked up from this file, so it finds the code repo regardless of the
# process's working directory (the tool/GUI can be launched from anywhere).
_HERE = os.path.dirname(os.path.abspath(__file__))


def code_commit(cwd: Optional[str] = None) -> str:
    """The code repo's short commit sha (``+dirty`` when the tree has uncommitted
    TRACKED changes), or ``unknown`` when git isn't available.

    Moved here from ``build_session_registry`` so the manifest and the registry
    header share one definition (docs/decisions/0180).
    """
    where = cwd or _HERE
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=where, capture_output=True, text=True, timeout=10)
        if head.returncode != 0:
            return "unknown"
        sha = head.stdout.strip() or "unknown"
        dirty = subprocess.run(["git", "status", "--porcelain",
                                "--untracked-files=no"], cwd=where,
                               capture_output=True, text=True, timeout=10)
        if dirty.returncode == 0 and dirty.stdout.strip():
            sha += "+dirty"
        return sha
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def package_versions(names=PACKAGES) -> Dict[str, str]:
    """``{distribution: version}`` for the installed rig-critical packages; a
    package that isn't installed is omitted (not an error)."""
    out: Dict[str, str] = {}
    for name in names:
        try:
            out[name] = _md.version(name)
        except _md.PackageNotFoundError:
            continue
        except Exception:  # noqa: BLE001 - never let a metadata quirk break Start
            continue
    return out


def firmware_proto_source() -> Optional[int]:
    """The ``PROTO_VERSION`` declared in ``rig_config.h`` (what the firmware
    *should* be), or ``None`` when the header can't be read/parsed."""
    try:
        from tmaze.core.rig_config import load_rig_config
        cfg = load_rig_config()
        if not getattr(cfg, "ok", False):
            return None
        val = cfg.scalars.get("PROTO_VERSION")
        return int(val) if isinstance(val, int) else None
    except Exception:  # noqa: BLE001
        return None


def collect(firmware_proto_seen: Optional[int] = None) -> Dict[str, object]:
    """The full provenance block for a session manifest / registry header.

    ``firmware_proto_seen`` is the last handshake ``PROTO`` the client actually
    saw this session (``None`` -> ``"unknown"``); ``firmware_proto_source`` is
    read from ``rig_config.h`` here. Every field degrades gracefully so this
    never raises.
    """
    return {
        "code_commit": code_commit(),
        "python_version": platform.python_version(),
        "host_name": socket.gethostname(),
        "packages": package_versions(),
        "firmware_proto_source": firmware_proto_source(),
        "firmware_proto_seen": (firmware_proto_seen
                                if firmware_proto_seen is not None else "unknown"),
    }
