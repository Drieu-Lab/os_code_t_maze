"""
head_live.py - the learned head point, run live inside the tracker.

``LiveHead`` holds the trained head network (``head_learned.HeadNet``) and a
``HeadFilter`` (``tmaze.core.head_filter``). RatTracker.process() calls it once
per frame: ``step`` when the tracker has chosen the rat's blob (crop -> network
-> full-frame points -> filter), ``miss`` when it has not. The tracker's own
centre of mass stays the position the task uses; the head points are extra
outputs (see docs/decisions/0219).

The shipped model lives in ``host/head_models/`` (``head_net.onnx`` + its
``head_net.json``), versioned with the code. ``LiveHead.load`` refuses a model
whose crop settings don't match this code (HeadNet's check), so a session can
only run with a model that sees the image it was trained on. ``model_info``
records which file a session used (name, SHA-256, training date + sessions).
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional, Sequence, Tuple

from tmaze.core.head_filter import HeadFilter, HeadFilterParams, HeadSample
from tmaze.paths import host_dir
from tmaze.vision import head_learned as HL

MODEL_FILE = "head_net.onnx"


def default_model_path() -> str:
    return str(host_dir() / "head_models" / MODEL_FILE)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LiveHead:
    """One session's head network + filter. Not thread-safe: the tracker's
    processing thread is its only caller."""

    def __init__(self, net: HL.HeadNet, model_info: dict,
                 filter_params: Optional[HeadFilterParams] = None):
        self.net = net
        self.model_info = model_info
        self.filter = HeadFilter(filter_params)

    @classmethod
    def load(cls, onnx_path: Optional[str] = None,
             filter_params: Optional[HeadFilterParams] = None) -> "LiveHead":
        """Load a model (default: the shipped one). Raises ValueError with a
        readable reason when the file is missing, unreadable or mismatched."""
        path = onnx_path or default_model_path()
        if not os.path.isfile(path):
            raise ValueError(f"head model not found: {path}")
        try:
            net = HL.HeadNet(path)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"head model {path} could not be loaded: {e}") from e
        info = {"file": os.path.basename(path), "sha256": _sha256(path),
                "trained": net.meta.get("trained", ""),
                "sessions": list(net.meta.get("sessions", []))}
        return cls(net, info, filter_params)

    def reset(self) -> None:
        self.filter.reset()

    def step(self, grey, diff, members: Sequence, com_proc: Tuple[float, float],
             scale: float, thresh: int, morph: int, roi_mask=None) -> HeadSample:
        """Filtered head points (full-frame px) for a frame where the tracker
        chose a blob. ``grey``/``diff``/``members``/``com_proc``/``roi_mask`` are
        the tracker's proc-resolution images, blob contours and raw centroid;
        ``scale`` is proc px per full-frame px."""
        inp = HL.net_input(grey, diff, members, com_proc, thresh, morph, roi_mask)
        proc_pts = HL.offsets_to_points(self.net.predict(inp), HL.crop_centre(com_proc))
        s = scale if scale else 1.0
        return self.filter.update({k: (x / s, y / s) for k, (x, y) in proc_pts.items()})

    def miss(self) -> HeadSample:
        """A frame with no tracked blob (rat coasting, lost, or registering)."""
        return self.filter.update(None)
