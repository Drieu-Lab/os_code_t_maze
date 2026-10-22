"""
derivatives_io.py - reading and writing the derived-data folder
(docs/decisions/0226; the rules are in ``tmaze.core.derivatives``).

- ``stat_file_input``: an input file's size + modified time, for the manifest.
- ``read_manifest`` / ``write_manifest``: the manifest is written to a
  temporary file and renamed into place, so a crash never leaves half of one.
- ``SessionLock``: a ``.lock`` file in the session folder so two runs never
  work on the same session; a lock left by a process that no longer runs on
  this PC is taken over.
- ``session_states``: every session's state for one pipeline.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from tmaze.core import derivatives as D


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stat_file_input(role: str, path: str, bundle_dir: Optional[str] = None
                    ) -> Dict[str, object]:
    st = os.stat(path)
    rel = os.path.relpath(path, bundle_dir) if bundle_dir else None
    return D.file_input(role, os.path.abspath(path), st.st_size, st.st_mtime, rel)


def current_inputs_like(recorded: Sequence[Mapping[str, object]], bundle_dir: Optional[str],
                        root: str) -> List[Dict[str, object]]:
    """The inputs a manifest recorded, as they are NOW: each bundle file
    re-read from ``bundle_dir`` (a missing file is left out, so it no longer
    matches), each upstream pipeline's current ``finished_at``. Works for any
    pipeline, so a status view needs no pipeline-specific code."""
    out: List[Dict[str, object]] = []
    for item in recorded:
        if item.get("kind") == "file":
            rel = item.get("bundle_rel")
            path = os.path.join(bundle_dir, *str(rel).split("/")) if (rel and bundle_dir) \
                else str(item.get("path", ""))
            if os.path.isfile(path):
                out.append(stat_file_input(str(item["role"]), path,
                                           bundle_dir if rel else None))
        elif item.get("kind") == "upstream":
            pipe, sid = str(item.get("pipeline")), str(item.get("session_id", ""))
            up = read_manifest(D.session_dir(root, pipe, sid)) if sid else None
            if up is not None:
                out.append(D.upstream_input(pipe, str(up.get("finished_at")), sid))
    return out


def output_entry(session_folder: str, name: str) -> Dict[str, object]:
    return {"file": name, "size": os.path.getsize(os.path.join(session_folder, name))}


def read_manifest(session_folder: str) -> Optional[dict]:
    """The manifest, or None when absent or unreadable."""
    try:
        with open(os.path.join(session_folder, D.MANIFEST_NAME), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_manifest(session_folder: str, manifest: Mapping[str, object]) -> str:
    path = os.path.join(session_folder, D.MANIFEST_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def remove_manifest(session_folder: str) -> None:
    """Called before re-running a session, so an interrupted re-run shows as partial."""
    try:
        os.remove(os.path.join(session_folder, D.MANIFEST_NAME))
    except FileNotFoundError:
        pass


def session_bundles(roots: Optional[Sequence[str]] = None) -> Dict[str, str]:
    """{session_id: bundle folder} across the recordings roots (rig disk first,
    then the archives; a session in two roots counts once), minus the sessions
    in ``host/session_ignore.json`` - the same set the registry uses."""
    from tmaze.core.records.session_ignore import load_ignored
    from tmaze.core.session import session_registry as R
    from tmaze.paths import phase3_recordings_roots

    if roots is None:
        roots = [str(p) for p in phase3_recordings_roots()]
    ignored = load_ignored()
    out: Dict[str, str] = {}
    for _root, bundle in R.scan_roots_with_root(roots):
        sid = os.path.basename(os.path.normpath(bundle))
        if sid not in ignored:
            out[sid] = bundle
    return out


def list_pipelines(root: str) -> List[str]:
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [n for n in names if D.valid_pipeline_name(n) and os.path.isdir(os.path.join(root, n))]


def session_states(root: str, pipeline: str,
                   current_inputs: Mapping[str, Sequence[Mapping[str, object]]]) -> Dict[str, str]:
    """{session_id: state} for every session in ``current_inputs`` (session id
    -> that session's inputs as they are now)."""
    out: Dict[str, str] = {}
    for sid, inputs in current_inputs.items():
        folder = D.session_dir(root, pipeline, sid)
        exists = os.path.isdir(folder)
        out[sid] = D.classify(exists, read_manifest(folder) if exists else None,
                              pipeline, sid, inputs)
    return out


def _process_alive(pid: int) -> Optional[bool]:
    try:
        import psutil
    except ImportError:
        return None
    return psutil.pid_exists(pid)


class LockedError(RuntimeError):
    pass


class SessionLock:
    """``with SessionLock(folder):`` - exclusive use of one session folder."""

    def __init__(self, session_folder: str,
                 alive: Callable[[int], Optional[bool]] = _process_alive):
        self.path = os.path.join(session_folder, D.LOCK_NAME)
        self._alive = alive
        self._held = False

    def _owner(self) -> Optional[dict]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self._owner() or {}
                same_host = owner.get("host") == socket.gethostname()
                pid = owner.get("pid")
                if same_host and isinstance(pid, int) and self._alive(pid) is False:
                    os.remove(self.path)   # left behind by a run that died
                    continue
                raise LockedError(f"{os.path.dirname(self.path)} is in use by "
                                  f"pid {pid} on {owner.get('host', '?')} "
                                  f"since {owner.get('since', '?')}")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                           "since": now_iso()}, fh)
            self._held = True
            return
        raise LockedError(f"could not take {self.path}")

    def release(self) -> None:
        if self._held:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self._held = False

    def __enter__(self) -> "SessionLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
