"""
dlc_head.py - the DeepLabCut-Live head preview's pure pieces (docs/decisions/0224).

The rig sends a fixed-size crop around the tracker's centre of mass (CoM) to a
helper process running DeepLabCut-Live (``tmaze.tools.dlc_head_server``) and
gets the rat's body parts back. This module holds the parts with no image or
network code, so they are unit-tested here and shared by both processes:

- ``crop_box``: where the crop goes (fixed size, shifted - not shrunk - at the
  frame edges, so the network always sees the same input size).
- ``DlcHeadSample``: one frame's answer, in full-frame pixels.
- ``columns`` / ``row`` / ``header_lines``: the per-session trial file
  ``<session>_dlc_head.csv``. It is a separate file, NOT part of the video
  sidecar, so the bundle format is unchanged while this is a trial.

The task's position is not touched: the CoM is still what the rules use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

HEAD_PART = "head_center"
DEFAULT_PCUTOFF = 0.6   # DeepLabCut's usual "trust this point" likelihood

# Per-frame status. The helper sets ok / low / error; the rig sets the rest
# for frames it never sent.
STATUS_OK = "ok"            # head_center likelihood >= pcutoff
STATUS_LOW = "low"          # answered, but head_center below pcutoff
STATUS_ERROR = "error"      # the helper failed on this crop
STATUS_SKIPPED = "skipped"  # helper still busy with the previous crop
STATUS_NO_BLOB = "no_blob"  # tracker had no rat this frame, nothing to crop
STATUSES = (STATUS_OK, STATUS_LOW, STATUS_ERROR, STATUS_SKIPPED, STATUS_NO_BLOB)

FIXED_COLUMNS = ("cam_block_id", "dlc_status", "dlc_latency_ms", "crop_x0", "crop_y0")


def crop_box(cx: float, cy: float, size: int, width: int, height: int) -> Tuple[int, int]:
    """Top-left corner of a ``size`` x ``size`` box centred on (cx, cy).

    The box is shifted to stay inside a ``width`` x ``height`` frame rather
    than shrunk, so every crop has the same size. A frame smaller than the box
    puts the corner at 0 on that axis (the crop is then just the frame)."""
    x0 = int(round(min(max(cx - size / 2.0, 0.0), max(width - size, 0))))
    y0 = int(round(min(max(cy - size / 2.0, 0.0), max(height - size, 0))))
    return x0, y0


@dataclass
class DlcHeadSample:
    """One frame's DeepLabCut answer. ``points`` maps each body part to
    (x, y, likelihood) in full-frame px; empty for skipped / no_blob frames."""
    block_id: Optional[int]
    status: str
    latency_ms: Optional[float] = None
    crop_x0: Optional[int] = None
    crop_y0: Optional[int] = None
    points: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)

    @property
    def head_center(self) -> Optional[Tuple[float, float]]:
        p = self.points.get(HEAD_PART)
        return None if p is None else (p[0], p[1])

    @property
    def valid(self) -> bool:
        """True when the head centre is trusted (status ok)."""
        return self.status == STATUS_OK and HEAD_PART in self.points


def status_for(points: Dict[str, Tuple[float, float, float]], pcutoff: float) -> str:
    """ok / low from the head centre's likelihood."""
    p = points.get(HEAD_PART)
    return STATUS_OK if p is not None and p[2] >= pcutoff else STATUS_LOW


# ----------------------------------------------------------- trial file
def columns(parts: Sequence[str]) -> List[str]:
    cols = list(FIXED_COLUMNS)
    for p in parts:
        cols += [f"{p}_x", f"{p}_y", f"{p}_likelihood"]
    return cols


def _num(v: Optional[float], fmt: str) -> str:
    return "" if v is None else format(v, fmt)


def row(sample: DlcHeadSample, parts: Sequence[str]) -> List[str]:
    out = ["" if sample.block_id is None else str(sample.block_id), sample.status,
           _num(sample.latency_ms, ".2f"), _num(sample.crop_x0, "d"), _num(sample.crop_y0, "d")]
    for p in parts:
        v = sample.points.get(p)
        out += ["", "", ""] if v is None else [f"{v[0]:.2f}", f"{v[1]:.2f}", f"{v[2]:.3f}"]
    return out


def header_lines(info: Dict[str, object], crop_px: int, pcutoff: float) -> List[str]:
    """``# key: value`` lines for the top of the trial file (same style as the
    video sidecar). ``info`` is the helper's hello (model file, sha256, versions)."""
    fields = {
        "dlc_head": "DeepLabCut-Live head preview (docs/decisions/0224)",
        "join_key": "cam_block_id = the video sidecar's cam_block_id",
        "crop_px": crop_px,
        "pcutoff": pcutoff,
    }
    for k in ("model_file", "model_sha256", "snapshot", "dlclive_version",
              "torch_version", "device"):
        fields[k] = info.get(k, "")
    return [f"# {k}: {v}" for k, v in fields.items()]
