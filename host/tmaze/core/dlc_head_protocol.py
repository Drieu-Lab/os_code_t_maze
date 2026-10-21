"""
dlc_head_protocol.py - messages between the rig and the DeepLabCut-Live helper.

The helper (``tmaze.tools.dlc_head_server``) runs in its own conda env and talks
to the rig over a TCP connection on this PC only (127.0.0.1). Every message is
a 4-byte tag, a 4-byte payload length, then the payload (little-endian):

- ``HELO`` helper -> rig, once the model is loaded: UTF-8 JSON (model file,
  sha256, body-part names, dlclive / torch versions, device).
- ``CROP`` rig -> helper: block id (int64, -1 = unknown), crop corner x0, y0
  and height, width (int32 each), then height*width*3 bytes of BGR pixels.
- ``POSE`` helper -> rig: block id, status code (uint8), latency ms (float32),
  part count (uint16), then x, y, likelihood (float32) per part, already in
  full-frame px (the helper adds the crop corner).
- ``BYE_`` rig -> helper: shut down.

Pure stdlib (struct + json); the socket reading is passed in as a function so
this module stays free of I/O and is tested headless.
"""

from __future__ import annotations

import json
import struct
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from tmaze.core import dlc_head as DH

TAG_HELLO = b"HELO"
TAG_CROP = b"CROP"
TAG_POSE = b"POSE"
TAG_BYE = b"BYE_"

_FRAME = struct.Struct("<4sI")
_CROP = struct.Struct("<qiiii")
_POSE = struct.Struct("<qBfH")
_PT = struct.Struct("<fff")

# Status codes on the wire (index into dlc_head.STATUSES).
_CODES = {s: i for i, s in enumerate(DH.STATUSES)}

MAX_PAYLOAD = 64 * 1024 * 1024   # a corrupt length must not trigger a huge read


def _frame(tag: bytes, payload: bytes) -> bytes:
    return _FRAME.pack(tag, len(payload)) + payload


def encode_hello(info: Dict[str, object]) -> bytes:
    return _frame(TAG_HELLO, json.dumps(info).encode("utf-8"))


def decode_hello(payload: bytes) -> Dict[str, object]:
    return json.loads(payload.decode("utf-8"))


def encode_crop(block_id: Optional[int], x0: int, y0: int, height: int, width: int,
                pixels: bytes) -> bytes:
    if len(pixels) != height * width * 3:
        raise ValueError(f"crop is {len(pixels)} bytes, expected {height}x{width}x3")
    bid = -1 if block_id is None else int(block_id)
    return _frame(TAG_CROP, _CROP.pack(bid, x0, y0, height, width) + pixels)


def decode_crop(payload: bytes) -> Tuple[Optional[int], int, int, int, int, bytes]:
    bid, x0, y0, h, w = _CROP.unpack_from(payload)
    pixels = payload[_CROP.size:]
    if len(pixels) != h * w * 3:
        raise ValueError(f"crop payload has {len(pixels)} pixel bytes, expected {h}x{w}x3")
    return (None if bid < 0 else bid), x0, y0, h, w, pixels


def encode_pose(block_id: Optional[int], status: str, latency_ms: float,
                points: Sequence[Tuple[float, float, float]]) -> bytes:
    bid = -1 if block_id is None else int(block_id)
    body = _POSE.pack(bid, _CODES[status], float(latency_ms), len(points))
    body += b"".join(_PT.pack(float(x), float(y), float(l)) for x, y, l in points)
    return _frame(TAG_POSE, body)


def decode_pose(payload: bytes, parts: Sequence[str]) -> DH.DlcHeadSample:
    """A POSE payload as a sample, its points named by ``parts`` (from HELO)."""
    bid, code, latency, n = _POSE.unpack_from(payload)
    if n != len(parts):
        raise ValueError(f"pose has {n} parts, the helper announced {len(parts)}")
    pts: List[Tuple[float, float, float]] = [
        _PT.unpack_from(payload, _POSE.size + i * _PT.size) for i in range(n)]
    return DH.DlcHeadSample(block_id=None if bid < 0 else bid, status=DH.STATUSES[code],
                            latency_ms=float(latency),
                            points={p: (x, y, l) for p, (x, y, l) in zip(parts, pts)})


def encode_bye() -> bytes:
    return _frame(TAG_BYE, b"")


def read_message(read_exact: Callable[[int], bytes]) -> Tuple[bytes, bytes]:
    """(tag, payload) of the next message. ``read_exact(n)`` must return
    exactly n bytes or raise (e.g. ConnectionError when the peer closed)."""
    tag, n = _FRAME.unpack(read_exact(_FRAME.size))
    if n > MAX_PAYLOAD:
        raise ValueError(f"message length {n} is too large (corrupt stream?)")
    return tag, (read_exact(n) if n else b"")
