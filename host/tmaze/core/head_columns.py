"""
head_columns.py - how the learned head points are written to the camera sidecar.

The camera sidecar (``video/<session_id>.csv``, one row per saved frame) ends
with HEAD_COLUMNS: the filtered output of the live head network
(``tmaze.vision.head_live``; docs/decisions/0219, 0220). They come from the
same tracker result as that row's ``track_*`` columns, so they share its small
delay (join on ``track_src_block_id``).

  * ``head_status`` - ``ok`` (trusted), ``leap`` (the head centre jumped; the
    previous points are held until the new place is confirmed), ``none`` (no
    tracked blob this frame). Blank when head tracking is off.
  * ``head_valid`` - 1 when the points can be trusted (``ok``), else 0.
  * ``<point>_x`` / ``<point>_y`` - full-frame px, for each of POINTS. Blank
    when there are no points.
  * ``head_dir_deg`` - head direction, neck -> head centre, degrees in image
    axes (0 = image right, 90 = image DOWN, range -180..180].

Whether head tracking was on, and which model file it ran, is in the sidecar's
``#`` header (``head_tracking``, ``head_model``, ...). Stdlib only (pure core).
"""

from __future__ import annotations

from typing import Dict, Optional

from tmaze.core.head_filter import LEAP, NO_PREDICTION, OK, HeadSample

POINTS = ("head", "neck", "tail_base", "nose", "right_ear", "left_ear")
STATUSES = (OK, LEAP, NO_PREDICTION)

HEAD_COLUMNS = (("head_status", "head_valid")
                + tuple(f"{p}_{a}" for p in POINTS for a in ("x", "y"))
                + ("head_dir_deg",))
_BLANKS = ("",) * len(HEAD_COLUMNS)

_POINT_DOC = {
    "head": "head centre",
    "neck": "neck",
    "tail_base": "tail base",
    "nose": "nose",
    "right_ear": "rat's right ear",
    "left_ear": "rat's left ear",
}


def head_row(sample: Optional[HeadSample]) -> tuple:
    """HEAD_COLUMNS values for one frame; all blank when head tracking is off."""
    if sample is None:
        return _BLANKS
    row = [sample.status, int(sample.valid)]
    for p in POINTS:
        pt = sample.points.get(p)
        row += ["", ""] if pt is None else [f"{pt[0]:.1f}", f"{pt[1]:.1f}"]
    row.append("" if sample.direction_deg is None else f"{sample.direction_deg:.1f}")
    return tuple(row)


def column_doc() -> Dict[str, str]:
    """Ordered {column: one-line meaning}, for the bundle's format.json."""
    doc = {
        "head_status": "ok / leap (jump not yet confirmed; points held) / none "
                       "(no tracked blob); blank = head tracking off",
        "head_valid": "1 when the head points can be trusted (status ok), else 0",
    }
    for p in POINTS:
        for a in ("x", "y"):
            doc[f"{p}_{a}"] = f"{_POINT_DOC[p]} {a} (full-frame px, learned head network)"
    doc["head_dir_deg"] = ("head direction neck -> head centre, degrees, image axes "
                           "(0 = right, 90 = down)")
    return doc


def header_fields(model_info: Optional[dict]) -> Dict[str, str]:
    """Sidecar ``#`` header lines saying whether head tracking ran and with which
    model (``head_live.LiveHead.model_info``; None = off)."""
    if model_info is None:
        return {"head_tracking": "off"}
    return {"head_tracking": "on",
            "head_model": str(model_info.get("file", "")),
            "head_model_sha256": str(model_info.get("sha256", "")),
            "head_model_trained": str(model_info.get("trained", "")),
            "head_model_sessions": " ".join(model_info.get("sessions", []))}
