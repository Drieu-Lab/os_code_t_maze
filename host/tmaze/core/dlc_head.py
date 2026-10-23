"""
dlc_head.py - the DeepLabCut-Live head preview's pure pieces (docs/decisions/0224).

The rig sends a fixed-size crop around the tracker's centre of mass (CoM) to a
helper process running DeepLabCut-Live (``tmaze.tools.dlc_head_server``) and
gets the rat's body parts back. This module holds the parts with no image or
network code, so they are unit-tested here and shared by both processes:

- ``crop_box``: where the crop goes (fixed size, shifted - not shrunk - at the
  frame edges, so the network always sees the same input size).
- ``CropPlanner``: what to crop when the tracker has no CoM this frame - the
  last good CoM for up to ``hold_s``, then the rig ROI's bounding box
  (docs/decisions/0225).
- ``DlcHeadSample``: one frame's answer, in full-frame pixels.
- ``columns`` / ``row`` / ``header_lines``: the per-session trial file
  ``<session>_dlc_head.csv``. It is a separate file, NOT part of the video
  sidecar, so the bundle format is unchanged while this is a trial.

Whether the task uses the head centre instead of the CoM is decided in
``tmaze.core.task_position`` (docs/decisions/0230), not here.
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

# Where a crop came from (the ``crop_src`` column).
SRC_COM = "com"     # the tracker's CoM on this frame
SRC_HOLD = "hold"   # no CoM: the last good CoM, at most hold_s old
SRC_ROI = "roi"     # no recent CoM: the rig ROI's bounding box
CROP_SOURCES = (SRC_COM, SRC_HOLD, SRC_ROI)
DEFAULT_HOLD_S = 1.0

FIXED_COLUMNS = ("cam_block_id", "dlc_status", "dlc_latency_ms", "crop_x0", "crop_y0",
                 "crop_w", "crop_h", "crop_src")


def crop_box(cx: float, cy: float, size: int, width: int, height: int) -> Tuple[int, int]:
    """Top-left corner of a ``size`` x ``size`` box centred on (cx, cy).

    The box is shifted to stay inside a ``width`` x ``height`` frame rather
    than shrunk, so every crop has the same size. A frame smaller than the box
    puts the corner at 0 on that axis (the crop is then just the frame)."""
    x0 = int(round(min(max(cx - size / 2.0, 0.0), max(width - size, 0))))
    y0 = int(round(min(max(cy - size / 2.0, 0.0), max(height - size, 0))))
    return x0, y0


def roi_box(polygon_norm: Optional[Sequence[Tuple[float, float]]], width: int,
            height: int) -> Optional[Tuple[int, int, int, int]]:
    """(x0, y0, w, h) in px of the bounding box of a normalised (0..1) ROI
    polygon, clipped to the frame. None without a usable polygon."""
    if not polygon_norm or len(polygon_norm) < 3:
        return None
    xs = [min(max(float(x), 0.0), 1.0) * width for x, _ in polygon_norm]
    ys = [min(max(float(y), 0.0), 1.0) * height for _, y in polygon_norm]
    x0, y0 = int(min(xs)), int(min(ys))
    x1, y1 = min(int(round(max(xs))), width), min(int(round(max(ys))), height)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return x0, y0, x1 - x0, y1 - y0


@dataclass(frozen=True)
class CropPlan:
    x0: int
    y0: int
    w: int
    h: int
    source: str


class CropPlanner:
    """Decides each frame's crop. With a CoM: a ``crop_px`` square around it.
    Without one: the same square around the last good CoM while it is at most
    ``hold_s`` old (most tracker dropouts are shorter than a second), then the
    ROI's bounding box if ``roi_fallback`` and a ROI is set, else nothing.

    The ROI box is several times the pixels of the square (~43 ms vs ~12 ms on
    the rig GPU), so while it is in use the helper answers about every other
    frame; the one-crop-in-flight rule marks the rest skipped. ``now`` is
    passed in (monotonic seconds) so this stays clock-free and testable."""

    def __init__(self, crop_px: int, hold_s: float = DEFAULT_HOLD_S,
                 roi_fallback: bool = True):
        self.crop_px = int(crop_px)
        self.hold_s = float(hold_s)
        self.roi_fallback = bool(roi_fallback)
        self._last_com: Optional[Tuple[float, float]] = None
        self._last_com_t = 0.0

    def plan(self, now: float, com: Optional[Tuple[float, float]], width: int,
             height: int, roi_norm: Optional[Sequence[Tuple[float, float]]] = None
             ) -> Optional[CropPlan]:
        size = self.crop_px
        if com is not None:
            self._last_com, self._last_com_t = (float(com[0]), float(com[1])), now
            centre, source = self._last_com, SRC_COM
        elif self._last_com is not None and now - self._last_com_t <= self.hold_s:
            centre, source = self._last_com, SRC_HOLD
        else:
            box = roi_box(roi_norm, width, height) if self.roi_fallback else None
            return None if box is None else CropPlan(*box, SRC_ROI)
        x0, y0 = crop_box(centre[0], centre[1], size, width, height)
        return CropPlan(x0, y0, min(size, width), min(size, height), source)


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
    crop_w: Optional[int] = None
    crop_h: Optional[int] = None
    crop_src: str = ""

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
           _num(sample.latency_ms, ".2f"), _num(sample.crop_x0, "d"), _num(sample.crop_y0, "d"),
           _num(sample.crop_w, "d"), _num(sample.crop_h, "d"), sample.crop_src]
    for p in parts:
        v = sample.points.get(p)
        out += ["", "", ""] if v is None else [f"{v[0]:.2f}", f"{v[1]:.2f}", f"{v[2]:.3f}"]
    return out


def header_lines(info: Dict[str, object], crop_px: int, pcutoff: float,
                 hold_s: float = DEFAULT_HOLD_S, roi_fallback: bool = True) -> List[str]:
    """``# key: value`` lines for the top of the trial file (same style as the
    video sidecar). ``info`` is the helper's hello (model file, sha256, versions)."""
    fields = {
        "dlc_head": "DeepLabCut-Live head preview (docs/decisions/0224, 0225)",
        "join_key": "cam_block_id = the video sidecar's cam_block_id",
        "crop_px": crop_px,
        "pcutoff": pcutoff,
        "crop_src": "com = tracker CoM; hold = last CoM <= hold_s old; roi = ROI bounding box",
        "hold_s": hold_s,
        "roi_fallback": "on" if roi_fallback else "off",
    }
    for k in ("model_file", "model_sha256", "snapshot", "dlclive_version",
              "torch_version", "device"):
        fields[k] = info.get(k, "")
    return [f"# {k}: {v}" for k, v in fields.items()]
