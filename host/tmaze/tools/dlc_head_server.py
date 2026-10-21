"""
dlc_head_server.py - DeepLabCut-Live helper process for the head preview.

Runs in the ``dlclivegui`` conda env (CUDA torch + dlclive; DeepLabCut-Live
does not support the rig env's Python 3.13 / numpy 2), NOT in drieu_t_maze.
The rig's Tracking tab starts it through ``tmaze.io.dlc_head_client``; you only
run it by hand to test it. See docs/decisions/0224.

What it does: loads an exported DeepLabCut model (``tmaze.tools.export_dlc_live``),
warms it up, then listens on 127.0.0.1:<port> for ONE rig connection. It sends
HELO (model + versions), then for every CROP it runs the model, keeps the most
confident rat, adds the crop corner and replies POSE in full-frame px. It exits
when the rig says BYE_ or the connection drops, so it never outlives the rig.

The crop arrives in OpenCV's BGR order and is handed to DLC-Live as is:
DLC-Live always swaps a 3-channel frame BGR -> RGB itself (it overrides its own
convert2rgb switch), so converting here first would feed the model swapped colours.

Manual test (from host/, PowerShell):
  $env:PYTHONPATH = (Get-Location).Path
  C:\\Users\\Celine\\anaconda3\\envs\\dlclivegui\\python.exe -m tmaze.tools.dlc_head_server --model <file.pt>
"""

from __future__ import annotations

import argparse
import hashlib
import os
import socket
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from tmaze.core import dlc_head as DH
from tmaze.core import dlc_head_protocol as P

DEFAULT_PORT = 5560
WARMUP_PX = 448


def pick_rat(pose) -> "np.ndarray":
    """The (parts, 3) array of the most confident predicted animal."""
    pose = np.asarray(pose, dtype=float)
    if pose.ndim == 2:
        return pose[:, :3]
    return pose[int(np.argmax(pose[:, :, 2].mean(axis=1))), :, :3]


def answer(pose, parts: Sequence[str], x0: int, y0: int,
           pcutoff: float) -> Tuple[str, List[Tuple[float, float, float]]]:
    """(status, points in full-frame px) for one crop's raw DLC output."""
    rat = pick_rat(pose)
    pts = [(float(x) + x0, float(y) + y0, float(l)) for x, y, l in rat]
    status = DH.status_for({p: v for p, v in zip(parts, pts)}, pcutoff)
    return status, pts


def _read_exact(conn: socket.socket) -> Callable[[int], bytes]:
    def read(n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("rig closed the connection")
            buf += chunk
        return bytes(buf)
    return read


def serve_one(conn: socket.socket, predict: Callable, info: Dict[str, object],
              pcutoff: float) -> None:
    """Answer one rig connection until BYE_ or disconnect. ``predict(crop_bgr)``
    returns DLC's raw pose for a (h, w, 3) uint8 BGR crop."""
    parts = list(info["parts"])
    conn.sendall(P.encode_hello(info))
    read = _read_exact(conn)
    while True:
        try:
            tag, payload = P.read_message(read)
        except OSError:   # includes ConnectionError: the rig disconnected
            return
        if tag == P.TAG_BYE:
            return
        if tag != P.TAG_CROP:
            continue
        bid, x0, y0, h, w, pixels = P.decode_crop(payload)
        t0 = time.perf_counter()
        try:
            crop = np.frombuffer(pixels, dtype=np.uint8).reshape(h, w, 3)
            status, pts = answer(predict(crop), parts, x0, y0, pcutoff)
        except Exception as e:  # one bad crop must not end the session
            print(f"[dlc_head_server] crop {bid} failed: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
            status, pts = DH.STATUS_ERROR, [(0.0, 0.0, 0.0)] * len(parts)
        try:
            conn.sendall(P.encode_pose(bid, status, (time.perf_counter() - t0) * 1000.0, pts))
        except OSError:   # the rig went away mid-reply
            return


def no_power_throttling() -> bool:
    """Opt this process out of Windows 11 power throttling ("EcoQoS").

    Windows treats a windowless process that is busy only part of the time as
    background work and may slow it / move it to the efficiency cores. At the
    camera's 25 fps the helper is idle ~60% of the time, and measured 48 ms per
    crop instead of ~20 ms. Returns True if Windows accepted the request."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    class _State(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                    ("StateMask", wintypes.ULONG)]

    process_power_throttling = 4                      # PROCESS_INFORMATION_CLASS
    execution_speed, ignore_timer_resolution = 0x1, 0x4
    state = _State(1, execution_speed | ignore_timer_resolution, 0)   # 0 = off
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetProcessInformation.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                          ctypes.c_void_p, wintypes.DWORD)
    return bool(k32.SetProcessInformation(k32.GetCurrentProcess(), process_power_throttling,
                                          ctypes.byref(state), ctypes.sizeof(state)))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_dlclive(model_path: str, device: str,
                 cpu_threads: int = 0) -> Tuple[Callable, Dict[str, object]]:
    """Load the model with DLC-Live and warm it up. Returns (predict, info).
    ``cpu_threads`` > 0 caps torch's + OpenCV's CPU threads, so the helper
    doesn't compete with the rig's tracker for cores (0 = library default)."""
    import torch
    import dlclive
    from dlclive import DLCLive
    if cpu_threads > 0:
        torch.set_num_threads(cpu_threads)
        try:
            import cv2
            cv2.setNumThreads(cpu_threads)
        except ImportError:
            pass
    # dlclive 1.1.0 ships IdentityPredictor but never imports it, so a
    # multi-animal model with an identity head fails to build without this.
    import dlclive.pose_estimation_pytorch.models.predictors.identity_predictor  # noqa: F401

    cfg = torch.load(model_path, map_location="cpu", weights_only=True)["config"]
    parts = list(cfg["metadata"]["bodyparts"])
    live = DLCLive(model_path, model_type="pytorch", device=device, single_animal=False)
    blank = np.zeros((WARMUP_PX, WARMUP_PX, 3), np.uint8)
    live.init_inference(blank)
    for _ in range(3):
        live.get_pose(blank)
    info = {"parts": parts, "model_file": os.path.basename(model_path),
            "model_sha256": _sha256(model_path),
            "snapshot": os.path.splitext(os.path.basename(model_path))[0],
            "dlclive_version": getattr(dlclive, "__version__", "?"),
            "torch_version": torch.__version__, "device": device}
    return live.get_pose, info


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="exported DLC-Live model (.pt)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--pcutoff", type=float, default=DH.DEFAULT_PCUTOFF)
    ap.add_argument("--cpu-threads", type=int, default=0,
                    help="cap on CPU threads (0 = library default)")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.model):
        print(f"[dlc_head_server] model not found: {a.model}", file=sys.stderr, flush=True)
        return 2
    if not no_power_throttling() and sys.platform == "win32":
        print("[dlc_head_server] could not opt out of Windows power throttling",
              file=sys.stderr, flush=True)
    predict, info = load_dlclive(a.model, a.device, a.cpu_threads)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", a.port))
    srv.listen(1)
    print(f"[dlc_head_server] ready on 127.0.0.1:{a.port} ({info['model_file']}, "
          f"{a.device})", flush=True)
    conn, _ = srv.accept()
    srv.close()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        serve_one(conn, predict, info, a.pcutoff)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
