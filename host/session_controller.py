"""
session_controller.py - the "Session" tab for experiment_control.py.

A thin orchestrator that turns "start recording everything for this session" into
one action and writes a single manifest tying the session's streams together (see
docs/decisions/0048). It owns no task logic: it composes the existing recorders
via their public seams.

On Start it:
  - mints a ``session_id`` (``<animal>_<stamp>``) and captures ``t0`` in the three
    host clocks (wall/mono/iso, via session_log.clock_snapshot);
  - starts the camera recording (``camera.start_recording(session_id)``) and arms
    Phase 3 (``phase3.arm(session_id)``), so both files carry the session stamp;
  - calls ``mark_session_start`` - the single fiducial seam - which today writes
    the host-t0 anchor into the manifest and (best-effort) sends ``PING`` so a
    ``PONG`` lands in the separate serial log as a coarse shared marker. This is
    where a future firmware ``SYNC`` (Open Ephys TTL + camera LED barcode) attaches
    for sub-ms sync, with no other change (docs/future-work.md FW-0018);
  - writes ``host/data/session_<id>.json`` atomically and keeps it updated as each
    stream's file path resolves and again on Stop (with the end anchor).

The raw serial stream is logged by the separate ``rig_logger.py`` process, whose
filename the host can't know (ADR-0002/0003); the manifest records the serial-log
directory + animal + "match by enclosing time window" instead of a path.

Module contract (like the other tabs):
    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None   learn the animal id (prefill convenience)
    close() -> None            stop an active session cleanly on app exit
"""

from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

import protocol
from session_log import SessionManifest, clock_snapshot

# Session manifests live alongside the other per-session data (host/data/, the
# same tree rig_logger.py and the Phase 3 data file use).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# How often (ms) to re-check each recorder for its resolved output path while a
# session is active. The camera path resolves immediately; the Phase 3 file only
# once its gate opens (Free Roam + tracking + a zone map), so we poll for it.
RESOLVE_INTERVAL_MS = 500

# Map a recorded video extension to the codec label recorded in the manifest.
_CODEC_BY_EXT = {".mkv": "ffv1", ".mp4": "h264"}


def _sanitize(name: str) -> str:
    """Filename-safe slug of an id (mirrors rig_logger/phase3_controller)."""
    slug = "".join(ch for ch in name.strip() if ch.isalnum() or ch in "-_")
    return slug or "unknown"


class SessionControlView:
    """Tk module: the Session tab - one-click start/stop + a shared manifest."""

    def __init__(self, parent: tk.Misc, camera=None, phase3=None, send=None):
        self._parent = parent
        self._camera = camera
        self._phase3 = phase3
        self._send = send                 # callable(str) -> None (RigClient.send)
        self.frame = ttk.Frame(parent)
        self._closed = False

        self._active = False
        self._session_id = ""
        self._manifest: Optional[SessionManifest] = None
        self._manifest_path: Optional[str] = None
        self._resolve_after: Optional[str] = None
        self._learned_animal = ""         # last animal id seen on the serial stream

        self._build_ui()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, line: str) -> None:
        """Learn the animal id from the serial handshake to prefill the field
        (convenience only; the field is authoritative once the user edits it)."""
        animal = protocol.animal_id(line)
        if animal:
            self._learned_animal = animal
            try:
                if not self._active and not self._animal_var.get().strip():
                    self._animal_var.set(animal)
            except tk.TclError:
                pass

    def close(self) -> None:
        self._closed = True
        if self._resolve_after is not None:
            try:
                self.frame.after_cancel(self._resolve_after)
            except tk.TclError:
                pass
            self._resolve_after = None
        if self._active:
            self._stop_session()

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 3}
        root = ttk.Frame(self.frame, padding=8)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            root,
            text=("Start/stop a whole session at once: camera recording + Phase 3, "
                  "tied together by a shared session id + manifest."),
            wraplength=460, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(root, text="Animal ID:").grid(row=1, column=0, sticky="w", **pad)
        self._animal_var = tk.StringVar(value="")
        ttk.Entry(root, width=14, textvariable=self._animal_var).grid(
            row=1, column=1, sticky="w", **pad)

        self._start_btn = ttk.Button(root, text="Start session",
                                     command=self._on_toggle)
        self._start_btn.grid(row=2, column=0, columnspan=2, sticky="w", **pad)

        ttk.Separator(root, orient="horizontal").grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        self._status_var = tk.StringVar(value="session: off")
        ttk.Label(root, textvariable=self._status_var, anchor="w",
                  wraplength=460, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", **pad)

    # ------------------------------------------------------- start/stop
    def _on_toggle(self) -> None:
        if self._active:
            self._stop_session()
        else:
            self._start_session()

    def _start_session(self) -> None:
        animal = _sanitize(self._animal_var.get() or self._learned_animal)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self._session_id = f"{animal}_{stamp}"
        self._manifest = SessionManifest(
            session_id=self._session_id, animal=animal, t0=clock_snapshot())
        self._manifest_path = os.path.join(
            DATA_DIR, f"session_{self._session_id}.json")
        # The serial log is a separate process we can't name; record how to find
        # it (its directory + animal + the time window) for offline correlation.
        self._manifest.set_stream("serial_log", {
            "dir": DATA_DIR, "animal": animal,
            "match": "file with the enclosing wall-clock time window",
        })

        notes = []
        cam_started = self._camera_start()
        if not cam_started:
            notes.append("camera not recording")
        if self._phase3 is not None and hasattr(self._phase3, "arm"):
            self._phase3.arm(self._session_id)
        else:
            notes.append("Phase 3 unavailable")

        self._mark_session_start(self._manifest.t0)
        self._write_manifest()

        self._active = True
        self._start_btn.configure(text="Stop session")
        self._resolve_paths()   # first pass + schedules the poll
        extra = f"  [{'; '.join(notes)}]" if notes else ""
        self._status_var.set(f"ACTIVE  {self._session_id}{extra}")

    def _stop_session(self) -> None:
        if self._phase3 is not None and hasattr(self._phase3, "disarm"):
            try:
                self._phase3.disarm()
            except Exception:
                pass
        if self._camera is not None and hasattr(self._camera, "stop_recording"):
            try:
                self._camera.stop_recording()
            except Exception:
                pass
        if self._resolve_after is not None:
            try:
                self.frame.after_cancel(self._resolve_after)
            except tk.TclError:
                pass
            self._resolve_after = None
        # Capture the final resolved paths and stamp the end anchor.
        self._resolve_paths(reschedule=False)
        if self._manifest is not None:
            self._manifest.set_end(clock_snapshot())
            self._write_manifest()

        self._active = False
        try:
            self._start_btn.configure(text="Start session")
            self._status_var.set(f"session ended: {self._session_id}")
        except tk.TclError:
            pass

    def _mark_session_start(self, _t0) -> None:
        """The single session-start fiducial seam (see docs/decisions/0048).

        Today the anchor is the host-t0 already recorded in the manifest; we also
        best-effort send ``PING`` so a ``PONG`` lands in the serial log as a coarse
        shared marker. FUTURE: fire a firmware ``SYNC`` (Open Ephys TTL + camera LED
        barcode) here for sub-ms sync (docs/future-work.md FW-0018) - no other
        code changes.
        """
        if self._send is not None:
            try:
                self._send("PING")
            except Exception:
                pass

    # ------------------------------------------------------- path resolution
    def _resolve_paths(self, reschedule: bool = True) -> None:
        """Fill in each recorder's output path as it becomes available, rewriting
        the manifest when something changes; re-poll while the session is active."""
        if self._manifest is None:
            return
        changed = False

        video = self._camera_path()
        if video and self._manifest.streams.get("camera_video") != video:
            self._manifest.set_stream("camera_video", video)
            self._manifest.set_stream(
                "camera_sidecar", os.path.splitext(video)[0] + ".csv")
            ext = os.path.splitext(video)[1].lower()
            self._manifest.set_stream("camera_codec",
                                      _CODEC_BY_EXT.get(ext, ext.lstrip(".")))
            changed = True

        p3 = self._phase3_path()
        if p3 and self._manifest.streams.get("phase3_task") != p3:
            self._manifest.set_stream("phase3_task", p3)
            changed = True

        if changed:
            self._write_manifest()

        if reschedule and self._active and not self._closed:
            try:
                self._resolve_after = self.frame.after(
                    RESOLVE_INTERVAL_MS, self._resolve_paths)
            except tk.TclError:
                self._resolve_after = None

    def _camera_start(self) -> bool:
        if self._camera is None or not hasattr(self._camera, "start_recording"):
            return False
        try:
            return bool(self._camera.start_recording(self._session_id))
        except Exception:
            return False

    def _camera_path(self) -> Optional[str]:
        if self._camera is None or not hasattr(self._camera, "current_recording_path"):
            return None
        try:
            return self._camera.current_recording_path()
        except Exception:
            return None

    def _phase3_path(self) -> Optional[str]:
        if self._phase3 is None or not hasattr(self._phase3, "current_log_path"):
            return None
        try:
            return self._phase3.current_log_path()
        except Exception:
            return None

    # ------------------------------------------------------------- manifest I/O
    def _write_manifest(self) -> None:
        """Atomically persist the manifest (temp file + os.replace), so a crash
        mid-session never leaves a half-written file."""
        if self._manifest is None or self._manifest_path is None:
            return
        try:
            os.makedirs(os.path.dirname(self._manifest_path), exist_ok=True)
            tmp = self._manifest_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(self._manifest.render())
            os.replace(tmp, self._manifest_path)
        except OSError:
            pass
