"""
derivatives.py - the rules of the derived-data folder (docs/decisions/0226).

Derived data is anything computed from the session bundles (DeepLabCut
tracking, movement measures, ...). It lives in its own tree, never inside the
bundles, laid out PIPELINE FIRST:

    <derivatives root>/<pipeline>/<session_id>/
        manifest.json        written LAST: what went in, what came out
        <session_id>_...     the pipeline's own files

A *pipeline* is one tool with fixed settings; its name carries its version
(``dlc_full_iter1``) so a changed model or definition gets a new folder instead
of overwriting old results. Each pipeline writes only inside its own folder.

A session's state for a pipeline follows from its folder alone:

- ``pending``  no folder yet
- ``partial``  a folder but no valid, complete manifest (a run was interrupted)
- ``stale``    a manifest whose recorded inputs no longer match the bundle
- ``done``     a complete manifest whose inputs still match

"Pending" is never stored: it is the sessions the bundle scan finds minus the
done ones, so a newly recorded session is pending for every pipeline at once.
The full spec, for other repos to mirror, is docs/derivatives_format.md.

Pure: no file I/O (``tmaze.io.derivatives_io`` does the reading and writing).
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
LOCK_NAME = ".lock"

STATE_PENDING, STATE_PARTIAL, STATE_STALE, STATE_DONE = "pending", "partial", "stale", "done"
STATES = (STATE_PENDING, STATE_PARTIAL, STATE_STALE, STATE_DONE)
STATUS_COMPLETE = "complete"

# Copies between drives can round a file's modified time (FAT keeps 2 s), so
# an archived bundle still matches its manifest.
MTIME_TOLERANCE_S = 2.0

_PIPELINE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED = ("derivatives_format_version", "pipeline", "session_id", "status",
             "inputs", "outputs", "finished_at")


def valid_pipeline_name(name: str) -> bool:
    """Lowercase letters, digits and ``_``, starting with a letter. Folders
    starting with ``_`` (``_logs``) are the pipeline's own housekeeping."""
    return bool(_PIPELINE_RE.match(name or ""))


def pipeline_dir(root: str, pipeline: str) -> str:
    if not valid_pipeline_name(pipeline):
        raise ValueError(f"not a valid pipeline name: {pipeline!r}")
    return os.path.join(root, pipeline)


def session_dir(root: str, pipeline: str, session_id: str) -> str:
    if not session_id or os.sep in session_id or "/" in session_id:
        raise ValueError(f"not a valid session id: {session_id!r}")
    return os.path.join(pipeline_dir(root, pipeline), session_id)


def session_sort_key(session_id: str) -> Tuple[str, str]:
    """(date_time, session_id): ``<animal>_<YYYYMMDD>_<HHMMSS>`` sorts by when
    it was recorded, whatever the animal id."""
    parts = session_id.rsplit("_", 2)
    when = "_".join(parts[-2:]) if len(parts) == 3 else ""
    return when, session_id


# ------------------------------------------------------------------ inputs
def file_input(role: str, path: str, size: int, mtime_s: float,
               bundle_rel: Optional[str] = None) -> Dict[str, object]:
    """One input file, identified by its size and modified time. The path is
    recorded for people; it is not compared, so archiving a bundle to another
    drive does not make its results stale. ``bundle_rel`` (the file's path
    inside its session bundle, ``/``-separated) lets any tool re-check the
    input without knowing the pipeline."""
    out = {"role": role, "kind": "file", "path": path, "size": int(size),
           "mtime_s": round(float(mtime_s), 3)}
    if bundle_rel:
        out["bundle_rel"] = bundle_rel.replace("\\", "/")
    return out


def upstream_input(pipeline: str, finished_at: str, session_id: str) -> Dict[str, object]:
    """Another pipeline's result for the same session that this one was built
    on. Re-running the upstream pipeline changes its ``finished_at``, which
    makes this result stale."""
    return {"role": f"upstream:{pipeline}", "kind": "upstream", "pipeline": pipeline,
            "session_id": session_id, "finished_at": finished_at}


def _same_input(a: Mapping[str, object], b: Mapping[str, object]) -> bool:
    if a.get("kind") != b.get("kind"):
        return False
    if a.get("kind") == "file":
        try:
            return (int(a["size"]) == int(b["size"]) and
                    abs(float(a["mtime_s"]) - float(b["mtime_s"])) <= MTIME_TOLERANCE_S)
        except (KeyError, TypeError, ValueError):
            return False
    return a.get("pipeline") == b.get("pipeline") and a.get("finished_at") == b.get("finished_at")


def inputs_match(recorded: Sequence[Mapping[str, object]],
                 current: Sequence[Mapping[str, object]]) -> bool:
    """True when every current input has a recorded twin with the same role and
    the same content stamp (and no role was added or dropped)."""
    rec = {str(i.get("role")): i for i in recorded}
    cur = {str(i.get("role")): i for i in current}
    return rec.keys() == cur.keys() and all(_same_input(rec[r], cur[r]) for r in cur)


# ---------------------------------------------------------------- manifest
def new_manifest(pipeline: str, session_id: str, *, inputs: Sequence[Mapping[str, object]],
                 outputs: Sequence[Mapping[str, object]], started_at: str, finished_at: str,
                 params: Optional[Mapping[str, object]] = None,
                 model: Optional[Mapping[str, object]] = None,
                 software: Optional[Mapping[str, object]] = None,
                 adopted: bool = False) -> Dict[str, object]:
    """A complete manifest. ``outputs``: ``{"file": name, "size": bytes}`` per
    file the pipeline wrote (names relative to the session folder)."""
    if not valid_pipeline_name(pipeline):
        raise ValueError(f"not a valid pipeline name: {pipeline!r}")
    return {
        "derivatives_format_version": FORMAT_VERSION,
        "pipeline": pipeline,
        "session_id": session_id,
        "status": STATUS_COMPLETE,
        "adopted": bool(adopted),
        "inputs": [dict(i) for i in inputs],
        "outputs": [dict(o) for o in outputs],
        "params": dict(params or {}),
        "model": dict(model or {}),
        "software": dict(software or {}),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def manifest_problems(manifest: object, pipeline: str, session_id: str) -> List[str]:
    """Why a manifest can't be trusted ([] = fine)."""
    if not isinstance(manifest, dict):
        return ["not a JSON object"]
    out = [f"missing {k}" for k in _REQUIRED if k not in manifest]
    if out:
        return out
    if manifest["derivatives_format_version"] != FORMAT_VERSION:
        out.append(f"format version {manifest['derivatives_format_version']!r}, "
                   f"expected {FORMAT_VERSION}")
    if manifest["pipeline"] != pipeline:
        out.append(f"pipeline {manifest['pipeline']!r}, expected {pipeline!r}")
    if manifest["session_id"] != session_id:
        out.append(f"session {manifest['session_id']!r}, expected {session_id!r}")
    if manifest["status"] != STATUS_COMPLETE:
        out.append(f"status {manifest['status']!r}")
    if not isinstance(manifest["inputs"], list) or not isinstance(manifest["outputs"], list):
        out.append("inputs / outputs are not lists")
    return out


def classify(folder_exists: bool, manifest: Optional[object], pipeline: str,
             session_id: str, current_inputs: Sequence[Mapping[str, object]]) -> str:
    """The session's state for this pipeline (see the module docstring)."""
    if not folder_exists:
        return STATE_PENDING
    if manifest is None or manifest_problems(manifest, pipeline, session_id):
        return STATE_PARTIAL
    return STATE_DONE if inputs_match(manifest["inputs"], current_inputs) else STATE_STALE


def to_run(states: Mapping[str, str], include: Iterable[str] = (STATE_PENDING, STATE_PARTIAL,
                                                                STATE_STALE),
           newest_first: bool = True) -> List[str]:
    """Session ids whose state is in ``include``, newest recording first."""
    keep = set(include)
    ids = [sid for sid, st in states.items() if st in keep]
    return sorted(ids, key=session_sort_key, reverse=newest_first)


def counts(states: Mapping[str, str]) -> Dict[str, int]:
    out = {s: 0 for s in STATES}
    for st in states.values():
        out[st] = out.get(st, 0) + 1
    return out
