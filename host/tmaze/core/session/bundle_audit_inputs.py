"""
bundle_audit_inputs.py - read one session bundle from disk into ``BundleInputs``.

The file-reading half of the bundle audit (docs/decisions/0204); the checks in
``bundle_audit`` never touch the disk. Everything is best-effort: a missing or
unreadable file leaves its field empty and the check that needs it reports
"not checked" (or the missing stream). Never writes anything.

Reads only small files plus the video SIDECAR (one CSV row per frame); the video
itself is never opened. Stdlib only.
"""

from __future__ import annotations

import json
import os
from typing import Any, List, Optional

from tmaze.core.phase3.phase3_log_reader import _parse_header
from tmaze.core.records import parse_commented_csv
from tmaze.core.session import session_serial
from tmaze.core.session.bundle_audit import BundleInputs, StreamFile, rebase_stream
from tmaze.core.session.session_config import sha256_file
from tmaze.core.sync import spans

# Manifest stream values that are not file paths.
NOT_A_PATH = ("camera_codec",)


def _json(path: str) -> Optional[Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _size(path: str) -> Optional[int]:
    try:
        return os.path.getsize(path) if os.path.exists(path) else None
    except OSError:
        return None


def _streams(manifest: dict, bundle: str, sid: str) -> List[StreamFile]:
    out = []
    for name, path in (manifest.get("streams") or {}).items():
        if name in NOT_A_PATH or not isinstance(path, str) or not path:
            continue
        expected, inside = rebase_stream(path, bundle, sid)
        direct = os.path.join(bundle, path) if not os.path.isabs(path) else path
        if os.path.exists(direct) and inside:
            out.append(StreamFile(name, path, direct, _size(direct), True))
        elif inside and os.path.exists(expected):
            out.append(StreamFile(name, path, expected, _size(expected), True, relocated=True))
        elif os.path.exists(direct):
            out.append(StreamFile(name, path, direct, _size(direct), False))
        else:
            out.append(StreamFile(name, path, None, None, inside))
    return out


def _firmware_proto(b: BundleInputs) -> Optional[int]:
    """The firmware protocol version this session ran: the board's own
    ``VERSION,<proto>,...`` reply in the capture, else the manifest's record
    (``firmware_proto_seen`` is ``"unknown"`` on every session so far)."""
    for _n, _w, line in b.serial_rows:
        parts = line.split(",")
        if len(parts) > 2 and parts[1] == "VERSION" and parts[2].isdigit():
            return int(parts[2])
    v = ((b.manifest or {}).get("provenance") or {}).get("firmware_proto_seen")
    return v if isinstance(v, int) else None


def load_inputs(bundle: str) -> BundleInputs:
    """Everything ``bundle_audit.audit_bundle`` needs from one bundle folder."""
    sid = os.path.basename(os.path.normpath(bundle))
    b = BundleInputs(folder_id=sid)
    b.manifest = _json(os.path.join(bundle, f"session_{sid}.json"))
    if isinstance(b.manifest, dict):
        b.streams = _streams(b.manifest, bundle, sid)
    else:
        b.manifest = None

    text = _text(os.path.join(bundle, "task", f"phase3_{sid}.csv"))
    if text:
        b.trial_header = _parse_header(text)
        try:
            b.trial_rows = parse_commented_csv(text).rows
        except ValueError:
            b.trial_rows = []

    capture = session_serial.resolve_capture_path(os.path.join(bundle, "serial"), sid)
    serial = _text(capture)
    if serial:
        lines = serial.splitlines()
        b.serial_header = _parse_header("\n".join(l for l in lines if l.startswith("#")))
        b.serial_rows = spans.parse_serial_csv(lines)
        b.serial_filtered = os.path.basename(capture) != session_serial.capture_filename(sid)

    b.summary = _json(os.path.join(bundle, "summary", f"session_summary_{sid}.json"))
    cfg = os.path.join(bundle, "config")
    b.snapshot = _json(os.path.join(cfg, "config_snapshot.json"))
    for f in (b.snapshot or {}).get("files", []) if isinstance(b.snapshot, dict) else []:
        name = f.get("filename") or f.get("name")
        if name and f.get("present"):
            b.config_hashes[name] = sha256_file(os.path.join(cfg, name))
    b.animal_state = _json(os.path.join(cfg, "animal_state.json"))
    b.firmware_proto = _firmware_proto(b)

    sidecar = _text(os.path.join(bundle, "video", f"{sid}.csv"))
    if sidecar:
        header, frames = spans.parse_sidecar_csv(sidecar.splitlines())
        b.sidecar_header = header
        b.sidecar_rows = len(frames)
        times = [f.host_unix_s for f in frames if f.host_unix_s is not None]
        if times:
            b.sidecar_span_s = (min(times), max(times))
    b.camera_stats = _json(os.path.join(bundle, "video", f"{sid}_camera_stats.json"))
    return b
