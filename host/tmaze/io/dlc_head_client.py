"""
dlc_head_client.py - the rig's side of the DeepLabCut-Live head preview.

``DlcHeadClient`` starts the helper process (``tmaze.tools.dlc_head_server``,
in the ``dlclivegui`` env), connects to it on 127.0.0.1, and from then on:

- ``submit(block_id, frame, com, roi_norm)`` (called by the camera's track
  worker after every tracker step) cuts a fixed-size crop around the centre of
  mass and sends it; with no CoM it uses the last good one for up to
  ``hold_s``, then the ROI's bounding box (docs/decisions/0225). It never
  waits: only one crop is in flight, and a frame that arrives while the helper
  is still busy is marked ``skipped``.
- a background thread receives each answer, keeps the newest for the preview
  overlay (``latest``) and, while a recording is open, writes it to the
  session's ``<session>_dlc_head.csv`` (``start_recording`` / ``stop_recording``).

Any failure (helper won't start, crashes, stops answering) turns the client to
state ``error`` with a readable reason; the tracker and the task carry on with
the centre of mass. See docs/decisions/0224. When the Tracking tab's "Use head
for task" switch is on, ``newest()`` feeds the task-position selector
(docs/decisions/0230).
"""

from __future__ import annotations

import csv
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional, Tuple

from tmaze.core import dlc_head as DH
from tmaze.core import dlc_head_protocol as P
from tmaze.core import task_position as TP
from tmaze.paths import host_dir

CONFIG_FILE = "dlc_head.json"
HUNG_AFTER_S = 2.0        # a crop unanswered this long = the helper has stalled
LOG_NAME = "tmaze_dlc_head_server.log"
# Keep the helper off logical processors 10/11 (the core logging CPU parity
# errors on the rig PC; docs/notes/Bugs.md #261018_CpuParityErrors).
AVOID_CPUS = (10, 11)

STATE_OFF, STATE_STARTING, STATE_READY, STATE_ERROR = "off", "starting", "ready", "error"


@dataclass
class DlcHeadConfig:
    python: str
    model_path: str
    port: int = 5560
    device: str = "cuda"
    crop_px: int = 448
    pcutoff: float = DH.DEFAULT_PCUTOFF
    start_timeout_s: float = 120.0
    cpu_threads: int = 0
    hold_s: float = DH.DEFAULT_HOLD_S
    roi_fallback: bool = True
    # Using the head as the task's position (docs/decisions/0230).
    task_max_lag_frames: int = TP.DEFAULT_MAX_LAG_FRAMES
    task_hold_s: float = TP.DEFAULT_HOLD_S


def load_config(path: Optional[str] = None) -> DlcHeadConfig:
    """Read host/dlc_head.json (model_path relative to host/ unless absolute)."""
    path = path or str(host_dir() / CONFIG_FILE)
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    cfg = DlcHeadConfig(**{k: v for k, v in raw.items() if not k.startswith("_")})
    if not os.path.isabs(cfg.model_path):
        cfg.model_path = str(host_dir() / cfg.model_path)
    return cfg


def _helper_env(python: str) -> dict:
    """PYTHONPATH -> this checkout's host/, and the helper env's DLL folders on
    PATH (a conda env's torch needs them when the env isn't 'activated')."""
    env = dict(os.environ)
    root = os.path.dirname(os.path.abspath(python))
    dirs = [root, os.path.join(root, "Library", "bin"),
            os.path.join(root, "Library", "usr", "bin"), os.path.join(root, "Scripts")]
    env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    env["PYTHONPATH"] = str(host_dir())
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _pin(pid: int) -> None:
    try:
        import psutil
        proc = psutil.Process(pid)
        cpus = [c for c in proc.cpu_affinity() if c not in AVOID_CPUS]
        if cpus:
            proc.cpu_affinity(cpus)
    except Exception:
        pass   # pinning is a precaution, never a reason to fail


def _tail(path: str, n: int = 6) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = [l.rstrip() for l in fh if l.strip()]
        return "\n".join(lines[-n:])
    except OSError:
        return ""


class _Recorder:
    """The session's trial file. Written from two threads (answers from the
    receive thread, skipped/no_blob rows from the track worker), so locked."""

    def __init__(self, path: str, header: list, parts: list):
        self._lock = threading.Lock()
        self._parts = parts
        self._fh = open(path, "w", newline="", encoding="utf-8")
        for line in header:
            self._fh.write(line + "\n")
        self._w = csv.writer(self._fh)
        self._w.writerow(DH.columns(parts))
        self._last_flush = time.monotonic()
        self.path = path
        self.rows = 0

    def write(self, sample: DH.DlcHeadSample) -> None:
        with self._lock:
            if self._fh is None:
                return
            self._w.writerow(DH.row(sample, self._parts))
            self.rows += 1
            now = time.monotonic()
            if now - self._last_flush >= 1.0:   # bound a crash's loss to ~1 s
                self._fh.flush()
                self._last_flush = now

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


class DlcHeadClient:
    """One helper process + connection. ``start()`` returns at once; watch
    ``state`` (off / starting / ready / error) and ``error``."""

    def __init__(self, cfg: DlcHeadConfig, *, spawn: bool = True):
        self.cfg = cfg
        self._spawn = spawn
        self._proc: Optional[subprocess.Popen] = None
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self.state = STATE_OFF
        self.error = ""
        self.info: dict = {}
        self.parts: list = []
        self._planner = DH.CropPlanner(cfg.crop_px, cfg.hold_s, cfg.roi_fallback)
        self._in_flight: Optional[Tuple[Optional[int], DH.CropPlan, float]] = None
        self._latest: Optional[DH.DlcHeadSample] = None
        self._latest_mono = 0.0
        self._latency: Deque[float] = deque(maxlen=100)
        self._sent = 0
        self._skipped = 0
        self._recorder: Optional[_Recorder] = None
        self.log_path = os.path.join(tempfile.gettempdir(), LOG_NAME)

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.state = STATE_STARTING
        threading.Thread(target=self._connect, name="dlc-head-connect", daemon=True).start()

    def _fail(self, why: str) -> None:
        with self._lock:
            if self.state == STATE_OFF:
                return
            self.state, self.error = STATE_ERROR, why
        # Off-thread: may be called on the camera's track worker, and stopping
        # a hung helper can take seconds.
        threading.Thread(target=self._shutdown, args=(False,),
                         name="dlc-head-stop", daemon=True).start()

    def _connect(self) -> None:
        cfg = self.cfg
        if self._spawn:
            if not os.path.isfile(cfg.python):
                return self._fail(f"helper Python not found: {cfg.python}")
            if not os.path.isfile(cfg.model_path):
                return self._fail(f"DLC model not found: {cfg.model_path} "
                                  "(run tmaze.tools.export_dlc_live)")
            cmd = [cfg.python, "-u", "-m", "tmaze.tools.dlc_head_server",
                   "--model", cfg.model_path, "--port", str(cfg.port),
                   "--device", cfg.device, "--pcutoff", str(cfg.pcutoff),
                   "--cpu-threads", str(cfg.cpu_threads)]
            log = open(self.log_path, "w", encoding="utf-8")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self._proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                              env=_helper_env(cfg.python), cwd=str(host_dir()),
                                              creationflags=flags)
            except OSError as e:
                return self._fail(f"helper could not start: {e}")
            finally:
                log.close()
            _pin(self._proc.pid)
        deadline = time.monotonic() + cfg.start_timeout_s
        sock = None
        while time.monotonic() < deadline and self.state == STATE_STARTING:
            if self._proc is not None and self._proc.poll() is not None:
                return self._fail(f"helper exited (code {self._proc.returncode}):\n"
                                  f"{_tail(self.log_path)}")
            try:
                sock = socket.create_connection(("127.0.0.1", cfg.port), timeout=1.0)
                break
            except OSError:
                time.sleep(0.5)
        if sock is None:
            return self._fail(f"helper did not answer within {cfg.start_timeout_s:.0f} s:\n"
                              f"{_tail(self.log_path)}")
        try:
            sock.settimeout(30.0)
            tag, payload = P.read_message(_read_exact(sock))
            if tag != P.TAG_HELLO:
                raise ValueError(f"expected HELO, got {tag!r}")
            info = P.decode_hello(payload)
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception as e:
            sock.close()
            return self._fail(f"helper handshake failed: {e}")
        with self._lock:
            if self.state != STATE_STARTING:   # closed while starting
                sock.close()
                return
            self._sock, self.info, self.parts = sock, info, list(info.get("parts", []))
            self.state = STATE_READY
        threading.Thread(target=self._receive, name="dlc-head-recv", daemon=True).start()

    def close(self) -> None:
        """Stop the helper (BYE_, then terminate if it lingers). Idempotent."""
        with self._lock:
            was = self.state
            self.state = STATE_OFF
        self._shutdown(send_bye=(was == STATE_READY))
        self.stop_recording()

    def _shutdown(self, send_bye: bool) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            if send_bye:
                try:
                    with self._send_lock:
                        sock.sendall(P.encode_bye())
                except OSError:
                    pass
            try:
                sock.close()
            except OSError:
                pass
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._in_flight = None

    @property
    def ready(self) -> bool:
        return self.state == STATE_READY

    # ------------------------------------------------------------- per frame
    def submit(self, block_id: Optional[int], frame, com: Optional[Tuple[float, float]],
               roi_norm=None) -> None:
        """Send this frame's crop (see ``DH.CropPlanner``) unless the helper is
        busy. ``frame`` is the full BGR frame, ``com`` the tracker's CoM in
        full-frame px or None, ``roi_norm`` the tracker's normalised ROI
        polygon or None. Never blocks on the helper."""
        if self.state != STATE_READY:
            return
        now = time.monotonic()
        h, w = frame.shape[:2]
        plan = self._planner.plan(now, com, w, h, roi_norm)
        if plan is None:
            return self._record(DH.DlcHeadSample(block_id, DH.STATUS_NO_BLOB))
        flight = self._in_flight
        if flight is not None:
            if now - flight[2] > HUNG_AFTER_S:
                return self._fail(f"helper stopped answering (no reply in {HUNG_AFTER_S:.0f} s)")
            self._skipped += 1
            return self._record(DH.DlcHeadSample(block_id, DH.STATUS_SKIPPED,
                                                 crop_src=plan.source))
        crop = frame[plan.y0:plan.y0 + plan.h, plan.x0:plan.x0 + plan.w]
        msg = P.encode_crop(block_id, plan.x0, plan.y0, crop.shape[0], crop.shape[1],
                            crop.tobytes())
        self._in_flight = (block_id, plan, now)
        try:
            with self._send_lock:
                sock = self._sock
                if sock is None:
                    return
                sock.sendall(msg)
            self._sent += 1
        except OSError as e:
            self._fail(f"lost the helper connection: {e}")

    def _receive(self) -> None:
        sock = self._sock
        read = _read_exact(sock)
        while self.state == STATE_READY:
            try:
                tag, payload = P.read_message(read)
                if tag != P.TAG_POSE:
                    continue
                sample = P.decode_pose(payload, self.parts)
            except Exception as e:
                if self.state == STATE_READY:
                    self._fail(f"helper connection ended: {e}\n{_tail(self.log_path)}")
                return
            flight = self._in_flight
            if flight is not None:
                plan = flight[1]
                sample.crop_x0, sample.crop_y0 = plan.x0, plan.y0
                sample.crop_w, sample.crop_h, sample.crop_src = plan.w, plan.h, plan.source
            self._in_flight = None
            self._latest = sample
            self._latest_mono = time.monotonic()
            if sample.latency_ms is not None:
                self._latency.append(sample.latency_ms)
            self._record(sample)

    def latest(self, max_age_s: float = 0.5) -> Optional[DH.DlcHeadSample]:
        """The newest answered frame, if recent (for the preview overlay)."""
        if self.state != STATE_READY or time.monotonic() - self._latest_mono > max_age_s:
            return None
        return self._latest

    def newest(self) -> Optional[DH.DlcHeadSample]:
        """The newest answered frame, however old, or None when the helper isn't
        ready. The task-position selector judges freshness itself, by
        ``block_id`` (docs/decisions/0230)."""
        if self.state != STATE_READY:
            return None
        return self._latest

    def stats(self) -> dict:
        """Recent helper latency (ms, median of the last 100) and the share of
        submitted frames skipped because the helper was busy."""
        lat = sorted(self._latency)
        total = self._sent + self._skipped
        return {"latency_ms": lat[len(lat) // 2] if lat else None,
                "skipped_frac": (self._skipped / total) if total else 0.0,
                "sent": self._sent, "skipped": self._skipped}

    # ------------------------------------------------------------ recording
    def start_recording(self, path: str) -> Optional[str]:
        """Open the session's trial file; returns its path, or None if the
        helper isn't ready (then nothing is written)."""
        self.stop_recording()
        if self.state != STATE_READY:
            return None
        header = DH.header_lines(self.info, self.cfg.crop_px, self.cfg.pcutoff,
                                 self.cfg.hold_s, self.cfg.roi_fallback)
        self._recorder = _Recorder(path, header, self.parts)
        return path

    @property
    def recording(self) -> bool:
        return self._recorder is not None

    def stop_recording(self) -> None:
        rec, self._recorder = self._recorder, None
        if rec is not None:
            rec.close()

    def _record(self, sample: DH.DlcHeadSample) -> None:
        rec = self._recorder
        if rec is not None:
            try:
                rec.write(sample)
            except Exception as e:   # a full disk must not stop tracking
                print(f"[dlc_head_client] trial-file write failed: {e}", file=sys.stderr)
                self._recorder = None


def _read_exact(sock: socket.socket) -> Callable[[int], bytes]:
    def read(n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("helper closed the connection")
            buf += chunk
        return bytes(buf)
    return read
