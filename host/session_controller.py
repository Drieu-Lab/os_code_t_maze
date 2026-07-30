"""
session_controller.py - the "Session" panel for experiment_control.py.

A thin orchestrator that turns "start recording everything for this session" into
one action and writes a single manifest tying the session's streams together (see
docs/decisions/0048). It owns no task logic: it composes the existing recorders
via their public seams. It lives in the Rig view tab's bottom strip - in the
right column, under the live behavior readout (filling the dead space there so it
doesn't lengthen the window) - rendered as a titled panel like the other
rig/firmware controls.

On Start it creates a per-session folder tree under the recordings root
(``<root>/<session_id>/``) and gathers everything this session produces into it
(see docs/decisions/0049):

    <session_id>/
        session_<session_id>.json     the manifest (correlation index)
        video/   <session_id>.<mkv|mp4> + sidecar .csv (+ _background.png)
        task/    phase3_<session_id>.csv
        serial/  sensors_<session_id>.csv   (filtered raw serial - see below)

Start REQUIRES Phase 3 to already be armed (the "Arm Phase 3" checkbox on); if it
is not, Start refuses and does nothing. The button no longer arms Phase 3 itself,
and Stop no longer disarms it - the Session button is the canonical session
boundary but the Phase 3 task keeps running across a Stop (docs/decisions/0060),
so its live stats stay visible. The button's Start/Stop times also drive the
Phase 3 stats panel's session clock (exposed via ``session_timing``).

On Start (once Phase 3 is confirmed armed) it:
  - mints a ``session_id`` (``<animal>_<stamp>``) and captures ``t0`` in the three
    host clocks (wall/mono/iso, via session_log.clock_snapshot);
  - starts the camera recording into ``video/`` (``camera.start_recording(id, out_dir)``)
    and hands Phase 3 the session id + ``task/`` folder so its data file correlates
    with the video + manifest (``phase3.set_session_context(id, out_dir)`` - it does
    NOT arm Phase 3, which the operator has already done; docs/decisions/0060).
    Phase 3 re-points an already-open data file into that folder, since its gate
    opened before this moment and so cannot bind it (docs/decisions/0066);
  - opens a per-session **filtered** raw-serial file in ``serial/``: while the
    session is active it appends the SENSOR + CENTRAL-PORT lines it already sees
    via ``apply()`` (body-beam edges + the central well's lick/reward/solenoid; the
    subset chosen by the experimenter), stamped with the host wall clock in the
    ``rig_logger`` column format. The predicate lives in ``session_serial.py``. The
    complete verbatim stream still belongs to the separate ``rig_logger.py`` process
    (ADR-0002/0003), recorded as a descriptor in the manifest;
  - calls ``mark_session_start`` - the single fiducial seam - which today writes
    the host-t0 anchor into the manifest and (best-effort) sends ``PING`` so a
    ``PONG`` lands in the serial log as a coarse shared marker. This is where a
    future firmware ``SYNC`` (Open Ephys TTL + camera LED barcode) attaches for
    sub-ms sync, with no other change (docs/future-work.md FW-0018);
  - writes the manifest atomically and keeps it updated as each stream's file path
    resolves and again on Stop (with the end anchor).

On Stop it stops the camera, stamps the manifest end anchor, closes the filtered
serial file, and releases Phase 3's session context - but leaves Phase 3 armed and
running (docs/decisions/0060), so the task and its live stats persist. Releasing
the context matters because Phase 3 binds its data-file path on the gate's rising
edge: without it, a re-arm between animals would write into this closed session's
folder, and the next session's manifest would then point there
(docs/decisions/0066).

The animal id is no longer entered here: it comes from the Task tab's single
shared Animal ID field via the injected ``animal_id`` getter (docs/decisions/
0053, FW-0005), so the session manifest and the Phase 3 log always agree. We
still record the last id seen on the ``ANIMAL`` handshake as a fallback for when
the field is empty.

Module contract (like the other modules):
    widget() -> a Tk widget to place in the Rig view bottom strip
    apply(line: str) -> None   record the animal-id fallback + append filtered
                               serial lines
    close() -> None            stop an active session cleanly on app exit
    session_timing() -> SessionTimerSnapshot   Start/Stop wall-clock span, read by
                               the Phase 3 stats panel (docs/decisions/0060)
"""

from __future__ import annotations

import os
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Callable, Optional

import protocol
from rig_config import load_rig_config
from session_log import SessionManifest, clock_snapshot, path_within_session
from session_serial import is_sensor_or_central
from session_timer import SessionTimer

# Fallback recordings root if experiment_control doesn't pass one; normally the
# GUI passes <camera default>/phase3_recordings (see docs/decisions/0049).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_RECORDINGS_ROOT = os.path.join(DATA_DIR, "phase3_recordings")

# Per-session subfolders (one per stream family).
VIDEO_SUBDIR = "video"
TASK_SUBDIR = "task"
SERIAL_SUBDIR = "serial"

# The central reward well's solenoid port ("Center / start"; docs/decisions/0007,
# 0022) - used to keep only that port's valve acks in the filtered serial file.
CENTER_SOL_PORT = 2

# How often (ms) to re-check each recorder for its resolved output path while a
# session is active. The camera path resolves immediately; the Phase 3 file only
# once its gate opens (Free Roam + tracking + a zone map), so we poll for it.
RESOLVE_INTERVAL_MS = 500

# Map a recorded video extension to the codec label recorded in the manifest.
_CODEC_BY_EXT = {".mkv": "ffv1", ".mp4": "h264"}


class SessionControlView:
    """Tk module: the Session panel - one-click start/stop + a shared manifest
    and a per-session folder tree (docs/decisions/0048, 0049)."""

    def __init__(self, parent: tk.Misc, camera=None, phase3=None, send=None,
                 recordings_root: Optional[str] = None,
                 animal_id: Optional[Callable[[], str]] = None):
        self._parent = parent
        self._camera = camera
        self._phase3 = phase3
        self._send = send                 # callable(str) -> None (RigClient.send)
        self._recordings_root = recordings_root or DEFAULT_RECORDINGS_ROOT
        # The single host-side animal-id source (the Task tab's shared field;
        # docs/decisions/0053). None -> rely on the ANIMAL-handshake fallback.
        self._animal_id = animal_id
        # Central-well lick channel (from the rig pin map; None -> central licks
        # simply aren't captured) for the filtered serial file. ADR-0022/0049.
        self._center_lick = load_rig_config().lick_index_by_role.get("center")
        # A titled panel matching the other Rig-view bottom-strip controls
        # (Training mode / Solenoid test / ...), placed in the right column under
        # the behavior readout; see docs/decisions/0048.
        self.frame = ttk.LabelFrame(
            parent,
            text=("Session  -  start/stop recording everything "
                  "(camera + Phase 3) under one id"))
        self._closed = False

        self._active = False
        # Wall-clock span of the Start->Stop session, surfaced to the Phase 3 stats
        # panel as its session clock (docs/decisions/0060). Reused pure core.
        self._session_timer = SessionTimer()
        self._session_id = ""
        self._session_dir: Optional[str] = None
        self._video_dir: Optional[str] = None
        self._manifest: Optional[SessionManifest] = None
        self._manifest_path: Optional[str] = None
        self._resolve_after: Optional[str] = None
        self._learned_animal = ""         # last animal id seen on the serial stream
        # Open handle for the per-session filtered serial file (None when idle).
        self._serial_file = None
        self._serial_path: Optional[str] = None

        self._build_ui()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def session_timing(self):
        """Immutable Start/Stop wall-clock span (session_timer.SessionTimerSnapshot)
        for the Phase 3 stats panel's session clock (docs/decisions/0060). Runs on
        the Tk thread like the rest of this panel, so no locking is needed."""
        return self._session_timer.snapshot(time.time())

    def apply(self, info) -> None:
        """Two jobs per serial line: (1) record the last animal id seen on the
        ``ANIMAL`` handshake as a fallback (the Task tab's field is the primary
        source now; docs/decisions/0053), and (2) while a session is active,
        append the SENSOR + CENTRAL-PORT lines to this session's filtered serial
        file (docs/decisions/0049). Reads the pre-parsed protocol.LineInfo
        (docs/decisions/0056). Runs on the Tk thread (the sole writer of that
        file), so no locking is needed."""
        if info.animal:
            self._learned_animal = info.animal
        if (self._active and self._serial_file is not None
                and is_sensor_or_central(
                    info, center_lick_index=self._center_lick,
                    center_sol_port=CENTER_SOL_PORT)):
            self._write_serial(info.raw)

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
        self._close_serial()

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        """Compact Start/Stop control with a status line below, sized to sit in
        the Rig view bottom strip beside Training mode. The animal id comes from
        the Task tab's single shared field (docs/decisions/0053), not here. Start
        requires Phase 3 to be armed first (docs/decisions/0060)."""
        root = self.frame

        self._start_btn = ttk.Button(root, text="Start session",
                                     command=self._on_toggle)
        self._start_btn.grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Label(root, text="(animal id from the Task tab)",
                  font=("TkDefaultFont", 8)).grid(
            row=0, column=1, sticky="w", padx=(4, 6), pady=4)

        self._status_var = tk.StringVar(value="session: off")
        ttk.Label(root, textvariable=self._status_var, anchor="w",
                  wraplength=360, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))

    # ------------------------------------------------------- start/stop
    def _animal_text(self) -> str:
        """Current animal id from the Task tab's shared field (docs/decisions/
        0053); "" when no getter is wired or it errors."""
        if self._animal_id is None:
            return ""
        try:
            return (self._animal_id() or "").strip()
        except Exception:
            return ""

    def _on_toggle(self) -> None:
        if self._active:
            self._stop_session()
        else:
            self._start_session()

    def _start_session(self) -> None:
        # The Session button no longer arms Phase 3; it REQUIRES the operator to
        # have armed it first, so a recording is never started without the task
        # running (docs/decisions/0060). Only enforced when a Phase 3 controller
        # exists (headless/standalone use still works).
        if (self._phase3 is not None and hasattr(self._phase3, "is_armed")
                and not self._phase3.is_armed()):
            self._status_var.set("blocked: arm Phase 3 first (Phase 3 tab)")
            messagebox.showwarning(
                "Session - Phase 3 not armed",
                'Arm Phase 3 first (the "Arm Phase 3" checkbox on the Phase 3 '
                "tab).\n\nThe session records the Phase 3 task, so it won't start "
                "until Phase 3 is armed. See docs/decisions/0060.")
            return

        animal = protocol.sanitize_animal_id(
            self._animal_text() or self._learned_animal)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self._session_id = f"{animal}_{stamp}"
        # Start the session clock now that the session is committed; its span
        # drives the Phase 3 stats panel's session line (docs/decisions/0060).
        self._session_timer.reset()
        self._session_timer.start(time.time())

        # Per-session folder tree: <root>/<session_id>/{video,task,serial}/ with the
        # manifest at its root (docs/decisions/0049). Create it up front so every
        # stream has a home even if one recorder doesn't start.
        self._session_dir = os.path.join(self._recordings_root, self._session_id)
        self._video_dir = os.path.join(self._session_dir, VIDEO_SUBDIR)
        task_dir = os.path.join(self._session_dir, TASK_SUBDIR)
        serial_dir = os.path.join(self._session_dir, SERIAL_SUBDIR)
        for d in (self._video_dir, task_dir, serial_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass

        self._manifest = SessionManifest(
            session_id=self._session_id, animal=animal, t0=clock_snapshot())
        self._manifest_path = os.path.join(
            self._session_dir, f"session_{self._session_id}.json")

        notes = []
        # Open the per-session filtered serial file (sensors + central port). Its
        # path is recorded even if the open fails (it just stays empty).
        self._serial_path = os.path.join(serial_dir, f"sensors_{self._session_id}.csv")
        if not self._open_serial(self._serial_path):
            notes.append("serial capture unavailable")
        self._manifest.set_stream("serial_sensors", self._serial_path)
        # The COMPLETE verbatim stream still belongs to the separate rig_logger.py
        # process, whose filename the host can't know (ADR-0002/0003) - record how
        # to find it (dir + animal + time window) rather than a path.
        self._manifest.set_stream("serial_full", {
            "dir": DATA_DIR, "animal": animal,
            "match": "rig_logger.py file with the enclosing wall-clock time window",
        })

        cam_started = self._camera_start(self._video_dir)
        if not cam_started:
            notes.append("camera not recording")
        # Phase 3 is already armed (guarded above); just hand it the session id +
        # task/ folder so its data file correlates with the video + manifest - do
        # NOT arm it here (docs/decisions/0060).
        if self._phase3 is not None and hasattr(self._phase3, "set_session_context"):
            self._phase3.set_session_context(self._session_id, out_dir=task_dir)
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
        # Stop the session clock (freezes the Phase 3 panel's session line at its
        # final span). Phase 3 is deliberately left armed/running so the task and
        # its live stats persist across a Stop (docs/decisions/0060).
        self._session_timer.stop(time.time())
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
        self._close_serial()
        # Release Phase 3's session context (it stays armed and running - ADR-0060).
        # Otherwise a re-arm between animals - the gate closing and reopening when
        # tracking is toggled - would mint its data file inside this now-closed
        # session's task/ folder (docs/decisions/0066).
        if self._phase3 is not None and hasattr(self._phase3, "set_session_context"):
            self._phase3.set_session_context()

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
        # Only ever record a task file from THIS session's tree. Phase 3 binds its
        # path on the gate's rising edge, which (per ADR-0060) happens before Start,
        # so until its rebind lands the path can still name the previous session or
        # the host/data fallback - recording that made the manifest point at another
        # session's data (docs/decisions/0066).
        if p3 is not None and not path_within_session(p3, self._session_dir or ""):
            p3 = None
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

    def _camera_start(self, out_dir: str) -> bool:
        if self._camera is None or not hasattr(self._camera, "start_recording"):
            return False
        try:
            return bool(self._camera.start_recording(self._session_id,
                                                     out_dir=out_dir))
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

    # ---------------------------------------------------- filtered serial file
    def _open_serial(self, path: str) -> bool:
        """Open the per-session filtered serial file and write its header. Returns
        False (leaving no handle) if it can't be opened."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._serial_file = open(path, "w", encoding="utf-8", newline="")
            self._serial_file.write(
                "# per-session serial capture (sensors + central port) - "
                "see docs/decisions/0049\n")
            self._serial_file.write(f"# session_id={self._session_id}\n")
            self._serial_file.write(
                "# wall_clock_iso,firmware_line (ms,TYPE,payload...)\n")
            self._serial_file.flush()
            return True
        except OSError:
            self._serial_file = None
            return False

    def _write_serial(self, line: str) -> None:
        """Append one matched firmware line, host-wall-clock stamped (rig_logger
        column format). Best-effort; never raises into the serial callback."""
        try:
            ts = datetime.now().isoformat(timespec="milliseconds")
            self._serial_file.write(f"{ts},{line}\n")
            self._serial_file.flush()
        except (OSError, ValueError):
            pass

    def _close_serial(self) -> None:
        if self._serial_file is not None:
            try:
                self._serial_file.close()
            except OSError:
                pass
            self._serial_file = None

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
