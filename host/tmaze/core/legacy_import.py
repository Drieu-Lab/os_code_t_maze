"""
legacy_import.py - turn pre-bundle recordings into bundles the offline tracker reads.

Before session bundles existed (ADR-0048/0049), the camera wrote loose files into
the recordings root, one set per recording:

    <stem>.csv                        the video sidecar (frame times; the live track
                                      from 2026-06-30 on)
    <stem>[_<label>].mp4|.mkv         the video, often renamed with a label such as
                                      ``rat600_lowlight``
    <stem>_background[_<label>].png   the empty-rig background

with ``<stem> = <prefix>_<YYYYMMDD>_<HHMMSS>`` (the prefix was the placeholder
animal id ``animal``). The offline tracker only reads a bundle,
``<id>/video/<id>.mp4``, with the session's model / ROI / landmarks in
``<id>/config/`` (ADR-0211), and these recordings have neither.

The import works from a TABLE the operator reviews (one row per recording:
include?, session id, rat, which rat model, notes). This module drafts that
table from the files, checks an edited table, and plans each bundle: hard links
to the original video / sidecar / background (no second copy of the video, and
the originals stay untouched) plus copies of the chosen model and the rig's
ROI / landmarks / zones files into ``config/``. See docs/decisions/0217.

Stdlib only; the file work is in ``tmaze.tools.import_legacy_recordings``.
"""
from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

TOOL_VERSION = 1
TABLE_NAME = "legacy_recordings.csv"
PROVENANCE_NAME = "legacy_import.json"
UNKNOWN_RAT = "unk"
# Recordings smaller than this were short tests or false starts; the real
# sessions of 2026-07 are all larger (see docs/decisions/0217).
MIN_VIDEO_BYTES = 1_000_000_000
# Rig files copied into every bundle's config/, as the session snapshot does
# (ADR-0072). The camera and rig have not moved since these recordings.
HOST_CONFIG_FILES = ("roi_polygon.json", "landmarks.json", "zones.json")

COLUMNS = ("stem", "include", "session_id", "rat", "model", "model_quality",
           "video", "background", "notes")
YES, NO = "yes", "no"
QUALITY_OK = "ok"                    # the model in use when the video was recorded
QUALITY_APPROX = "approximate"       # closest model available now; re-register later
QUALITY_NONE = "none"                # no model chosen: the tracker's fallback is used
QUALITIES = (QUALITY_OK, QUALITY_APPROX, QUALITY_NONE)

VIDEO_EXTS = (".mp4", ".mkv")
_STEM_RE = re.compile(r"^(?P<prefix>[A-Za-z0-9]+)_(?P<date>\d{8})_(?P<time>\d{6})$")
_RAT_RE = re.compile(r"(?:^|_)rat(?P<rat>\d+)(?:_|$)")
_ID_RE = re.compile(r"^[A-Za-z0-9]+_\d{8}_\d{6}$")

# Rat model file per lighting label, most faithful first. The rig had per-rat
# models named ``animal_ratmodel_rat<id>[_lowlight].npz``; no two-lamp model was
# ever saved for most rats, so two-lamp videos fall back to the low-light one.
_LIGHTING_PREFERENCE = (
    ("lowlight", ("_lowlight", "")),
    ("twolamps", ("_twolamps", "_lowlight", "")),
)
_DEFAULT_PREFERENCE = ("", "_lowlight")


# ----------------------------------------------------------------- finding files
@dataclass(frozen=True)
class LooseRecording:
    """One recording's files in the root (names only)."""
    stem: str
    sidecar: str
    video: Optional[str]
    video_bytes: int
    background: Optional[str]

    @property
    def label(self) -> str:
        """The text after the stem in the video name (``rat600_lowlight``), or ""."""
        if not self.video:
            return ""
        base = os.path.splitext(self.video)[0]
        return base[len(self.stem):].lstrip("_")


def find_recordings(files: Mapping[str, int]) -> List[LooseRecording]:
    """Group a folder listing (``name -> size in bytes``) into recordings, one
    per ``<stem>.csv`` whose stem looks like ``<prefix>_<date>_<time>``."""
    out: List[LooseRecording] = []
    for name in sorted(files):
        stem, ext = os.path.splitext(name)
        if ext.lower() != ".csv" or not _STEM_RE.match(stem):
            continue
        videos = sorted(n for n in files if n.startswith(stem)
                        and os.path.splitext(n)[1].lower() in VIDEO_EXTS)
        bgs = sorted(n for n in files if n.startswith(stem + "_background")
                     and n.lower().endswith(".png"))
        video = max(videos, key=lambda n: files[n]) if videos else None
        out.append(LooseRecording(stem=stem, sidecar=name, video=video,
                                  video_bytes=files[video] if video else 0,
                                  background=bgs[0] if bgs else None))
    return out


def rat_from_label(label: str) -> Optional[str]:
    m = _RAT_RE.search(label)
    return m.group("rat") if m else None


def session_id(stem: str, rat: Optional[str]) -> str:
    """``<rat>_<date>_<time>`` (``unk_...`` when the rat is not known)."""
    m = _STEM_RE.match(stem)
    if not m:
        raise ValueError(f"not a recording stem: {stem!r}")
    return f"{rat or UNKNOWN_RAT}_{m.group('date')}_{m.group('time')}"


# ------------------------------------------------------------------ model choice
def _fmt_day(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def propose_model(rat: Optional[str], label: str, recording_end: Optional[float],
                  models: Mapping[str, float]) -> Tuple[str, str, str]:
    """``(model file name, quality, note)`` for one recording.

    ``models`` maps each model file name in ``host/ratmodels/`` to its save
    time. The model for the recording's lighting is ``ok`` if it was saved no
    later than the recording ended (a model registered during the session
    counts); a model saved afterwards overwrote the one that was used, and any
    other lighting is only the closest one, so both are ``approximate``.
    """
    if not rat:
        return "", QUALITY_NONE, "rat unknown: the tracker's fallback model is used"
    prefs = next((p for key, p in _LIGHTING_PREFERENCE if key in label), _DEFAULT_PREFERENCE)
    for i, suffix in enumerate(prefs):
        name = f"animal_ratmodel_rat{rat}{suffix}.npz"
        if name not in models:
            continue
        if i > 0:
            return name, QUALITY_APPROX, (f"no animal_ratmodel_rat{rat}{prefs[0]}.npz; "
                                          f"closest is {name}")
        if recording_end is not None and models[name] > recording_end:
            return name, QUALITY_APPROX, (f"{name} was saved {_fmt_day(models[name])}, after "
                                          "this recording, so it is not the one used")
        return name, QUALITY_OK, ""
    return "", QUALITY_NONE, f"no model for rat {rat}: the tracker's fallback model is used"


# ----------------------------------------------------------------------- draft
@dataclass(frozen=True)
class SidecarInfo:
    undistorted: bool
    end_unix: Optional[float]


def draft_row(rec: LooseRecording, info: SidecarInfo, models: Mapping[str, float],
              *, min_bytes: int = MIN_VIDEO_BYTES) -> Dict[str, str]:
    """The proposed table row for one recording (the operator then edits it)."""
    rat = rat_from_label(rec.label)
    model, quality, note = propose_model(rat, rec.label, info.end_unix, models)
    skip = None
    if not rec.video:
        skip = "no video"
    elif not info.undistorted:
        skip = "recorded before undistortion: ROI / landmarks / models do not line up"
    elif rec.video_bytes < min_bytes:
        skip = f"video under {min_bytes / 1e9:g} GB (test or short clip)"
    notes = "; ".join(n for n in (skip, note) if n)
    return {"stem": rec.stem, "include": NO if skip else YES,
            "session_id": session_id(rec.stem, rat), "rat": rat or UNKNOWN_RAT,
            "model": model, "model_quality": quality, "video": rec.video or "",
            "background": rec.background or "", "notes": notes}


def format_table(rows: Sequence[Mapping[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in COLUMNS})
    return buf.getvalue()


def parse_table(text: str) -> List[Dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if rows and set(COLUMNS) - set(rows[0]):
        raise ValueError(f"table is missing columns: {sorted(set(COLUMNS) - set(rows[0]))}")
    return [{k: (r.get(k) or "").strip() for k in COLUMNS} for r in rows]


def validate_table(rows: Sequence[Mapping[str, str]]) -> List[str]:
    """Problems that stop an import (empty list = fine). Only included rows
    must be complete; their session ids must be unique and well formed."""
    errors: List[str] = []
    seen: Dict[str, str] = {}
    for r in rows:
        stem = r["stem"] or "?"
        if r["include"] not in (YES, NO):
            errors.append(f"{stem}: include must be {YES!r} or {NO!r}")
            continue
        if r["include"] == NO:
            continue
        sid = r["session_id"]
        if not _ID_RE.match(sid):
            errors.append(f"{stem}: session_id {sid!r} is not <rat>_<YYYYMMDD>_<HHMMSS>")
        elif sid in seen:
            errors.append(f"{stem}: session_id {sid} also used by {seen[sid]}")
        seen.setdefault(sid, stem)
        if not r["video"]:
            errors.append(f"{stem}: no video")
        if r["model_quality"] not in QUALITIES:
            errors.append(f"{stem}: model_quality must be one of {', '.join(QUALITIES)}")
        if r["model"] and r["model_quality"] == QUALITY_NONE:
            errors.append(f"{stem}: model given but model_quality is {QUALITY_NONE!r}")
        if not r["model"] and r["model_quality"] != QUALITY_NONE:
            errors.append(f"{stem}: no model but model_quality is {r['model_quality']!r}")
    return errors


# ------------------------------------------------------------------------ plan
@dataclass(frozen=True)
class Step:
    """One file to put in a bundle: ``link`` (hard link) or ``copy`` ``src`` to
    ``dst`` (``dst`` relative to the bundle folder)."""
    action: str
    src: str
    dst: str


def plan_bundle(row: Mapping[str, str], *, root: str, models_dir: str, host_dir: str,
                background_name: Optional[str]) -> List[Step]:
    """The files of one bundle. ``background_name`` is what the sidecar header
    calls the background (the offline tracker looks for that name); default
    ``<stem>_background.png``."""
    sid, stem = row["session_id"], row["stem"]
    ext = os.path.splitext(row["video"])[1].lower()
    steps = [Step("link", os.path.join(root, row["video"]), os.path.join("video", sid + ext)),
             Step("link", os.path.join(root, stem + ".csv"), os.path.join("video", sid + ".csv"))]
    if row["background"]:
        steps.append(Step("link", os.path.join(root, row["background"]),
                          os.path.join("video", background_name or f"{stem}_background.png")))
    if row["model"]:
        steps.append(Step("copy", os.path.join(models_dir, row["model"]),
                          os.path.join("config", "model_" + row["model"])))
    for name in HOST_CONFIG_FILES:
        steps.append(Step("copy", os.path.join(host_dir, name), os.path.join("config", name)))
    return steps


def provenance(row: Mapping[str, str], steps: Sequence[Step],
               hashes: Mapping[str, Optional[str]], *, created: str) -> Dict[str, Any]:
    """What ``config/legacy_import.json`` records: where every file came from
    and the operator's choices, so a bundle explains itself (``hashes`` maps
    each copied source path to its SHA-256)."""
    return {
        "tool": "tmaze.tools.import_legacy_recordings", "tool_version": TOOL_VERSION,
        "created": created, "source_stem": row["stem"], "session_id": row["session_id"],
        "rat": row["rat"], "model": row["model"] or None,
        "model_quality": row["model_quality"], "notes": row["notes"],
        "files": [{"action": s.action, "source": s.src, "bundle_path": s.dst.replace(os.sep, "/"),
                   **({"sha256": hashes.get(s.src)} if s.action == "copy" else {})}
                  for s in steps],
    }
