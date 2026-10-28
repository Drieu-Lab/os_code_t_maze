"""
dlc_frame_pick - which video frames to label next for the DeepLabCut model
(docs/decisions/0231).

From a session's live head file (``<session>_dlc_head.csv``, ADR-0224) joined
to its video sidecar: frames where the model clearly sees the rat's body but
is unsure of the head centre, in the regions asked for, lowest head
likelihood first, at least ``min_gap`` frames apart so the picks are not
near-copies of each other. Frames where no body part is found are left out:
they are mostly not the rat (an operator's hand, the rat being lifted out).

Also ``unwrap_block_ids``: the camera's ``cam_block_id`` counts to 65535 and
starts again at 0, so in a session longer than 65,536 frames (~44 min at
25 fps) the same id appears twice. Joining the two files on the raw id pairs
frames ~44 min apart; join on the unwrapped id instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

BLOCK_ID_PERIOD = 65536
# A drop this large between consecutive rows is a restart, not dropped frames.
_RESTART_DROP = BLOCK_ID_PERIOD // 2

# Region name -> the zones (tmaze.core.maze_state) it covers.
REGIONS: Dict[str, Sequence[str]] = {
    "well_side": ("HUB",),
    "stem": ("CENTRAL_STEM",),
    "junction_side": ("JUNCTION", "LEFT_CHOICE", "RIGHT_CHOICE"),
    "returns": ("LEFT_RETURN", "RIGHT_RETURN"),
}


def unwrap_block_ids(ids: Iterable[int]) -> List[int]:
    """Each id plus 65536 per restart seen so far, so every frame is unique.
    The ids must be in recording order."""
    out: List[int] = []
    offset, prev = 0, None
    for i in ids:
        i = int(i)
        if prev is not None and i < prev - _RESTART_DROP:
            offset += BLOCK_ID_PERIOD
        out.append(i + offset)
        prev = i
    return out


BODY_PARTS = ("neck", "mid_spine_neck", "mid_spine_tail", "tail_base")


def head_unsure_body_seen(head: Optional[float], body: Iterable[Optional[float]],
                          cutoff: float = 0.6) -> bool:
    """True when the head centre is below ``cutoff`` and at least one body part
    is at or above it."""
    return head is not None and head < cutoff and \
        any(b is not None and b >= cutoff for b in body)


def region_of(zone: Optional[str]) -> Optional[str]:
    for name, zones in REGIONS.items():
        if zone in zones:
            return name
    return None


@dataclass(frozen=True)
class Candidate:
    frame_idx: int          # the video frame (the sidecar's frame_idx)
    region: str
    likelihood: float       # head_center likelihood


def pick(cands: Sequence[Candidate], per_region: int, min_gap: int) -> List[Candidate]:
    """Up to ``per_region`` candidates per region, lowest likelihood first
    (earliest frame on a tie), none closer than ``min_gap`` frames to another
    pick from any region. Returned in frame order."""
    chosen: List[Candidate] = []
    count: Dict[str, int] = {}
    for c in sorted(cands, key=lambda c: (c.likelihood, c.frame_idx)):
        if count.get(c.region, 0) >= per_region:
            continue
        if any(abs(c.frame_idx - p.frame_idx) < min_gap for p in chosen):
            continue
        chosen.append(c)
        count[c.region] = count.get(c.region, 0) + 1
    return sorted(chosen, key=lambda c: c.frame_idx)


def image_name(frame_idx: int, n_frames: int) -> str:
    """DeepLabCut's labelled-frame name: ``img`` + the frame index padded to the
    number of digits of the video's frame count."""
    return f"img{frame_idx:0{len(str(max(n_frames, 1)))}d}.png"
