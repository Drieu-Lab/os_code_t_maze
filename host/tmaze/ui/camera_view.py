"""
camera_view.py - Basler camera module for experiment_control.py (Step 0).

A live-preview + lossless-recording tab for the rig's Basler acA1300-60gc GigE
camera. It plugs into experiment_control.py with the same module contract the
other modules use:

    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None  to handle each serial line
    close() -> None  (optional) to release threads/camera on app exit

Design (free-run, no hardware trigger - see docs/decisions/0011):
  - A background GRAB thread owns the camera (pypylon), runs it free-running,
    debayers to BGR, updates a "latest frame" slot for preview, and - while
    recording - hands frames to a bounded queue. It tracks dropped/incomplete
    frames via the camera BlockID and GrabSucceeded().
  - A background ENCODE thread drains the queue, writes video (PyAV: lossless
    FFV1/.mkv or compressed H.264/.mp4, selected in the GUI) plus a per-frame
    sidecar CSV. Decoupling grab from encode
    means a slow disk back-pressures the queue (a controlled, counted frame drop)
    instead of stalling acquisition or freezing the Tk GUI.
  - The Tk main thread only does preview (throttled via after) and controls.

All heavy dependencies (pypylon, OpenCV, numpy, Pillow) are imported defensively
so that importing this module never breaks experiment_control.py; if anything is
missing (or no camera is found, or the camera is busy because Pylon Viewer is
open) the tab shows a clear message and the rest of the GUI runs normally.

The camera is opened by a stable identifier (serial / IP / MAC), defaulting to
the rig camera's known IP, so it always grabs the right interface.
"""

from __future__ import annotations

import csv
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, ttk
from typing import Optional, Tuple

from tmaze.ui.gui_deps import (  # noqa: E402
    Image, ImageTk, _AV_ERR, av, cv2, missing_dependency, np, pylon,
)
from tmaze.vision.calibration import Undistorter, load_calibration  # noqa: E402
from tmaze.core import protocol  # noqa: E402  (shared serial-line parser; see docs/decisions/0024)
from tmaze.core.camera_health import account_block_gap, fps_measured, recording_deltas  # noqa: E402  (pure BlockID-gap accounting + per-recording deltas + measured fps; ADR-0147)
from tmaze.core.records.animals import resources_root  # noqa: E402  (sibling resources-repo resolver)
from tmaze.paths import camera_output_dir  # noqa: E402  (single source for the recordings root; ADR-0088)
from tmaze.core.maze_state import IN_WELL as _IN_WELL  # noqa: E402  (HUB well substate label)
from tmaze.ui.preview import render_bgr_to_label, safe_after  # noqa: E402
from tmaze.ui.tooltips import add_dynamic_tooltip, tip  # noqa: E402
from tmaze.vision.tracker import TRACK_COLUMNS, track_row  # noqa: E402
from tmaze.vision.track_worker import LatestFrameSlot, TrackPublisher  # noqa: E402

# Shared tracking-overlay drawing (also used by the Tracking tab so previews
# match). Guarded: tracking deps may be absent without breaking the camera tab.
# draw_landmarks_overlay lets the Rig view toggle landmarks on their own; the
# zone overlay + colours are shared with the Maze state tab.
try:
    from tmaze.vision.tracker import (  # noqa: E402
        draw_overlay as _draw_track_overlay_shared,
        draw_landmarks_overlay as _draw_landmarks_shared,
    )
except Exception:  # pragma: no cover
    _draw_track_overlay_shared = None  # type: ignore
    _draw_landmarks_shared = None  # type: ignore
try:
    from tmaze.ui.zone_overlay import draw_zone_overlay as _draw_zone_overlay_shared  # noqa: E402
except Exception:  # pragma: no cover
    _draw_zone_overlay_shared = None  # type: ignore


# ================================== CONFIG ===================================
# Stable identifier for the rig camera. The grab thread matches a connected
# device on (in order) serial, IP, then MAC; if none match it falls back to the
# first device found. The known-good rig camera is on an isolated subnet.
CAMERA_IP = "192.168.3.3"
CAMERA_SERIAL = ""            # set if you prefer to pin by serial number
CAMERA_MAC = "0030531AD4B0"   # 00:30:53:1A:D4:B0, colons stripped

# The rig recordings root. Single-sourced from tmaze.paths (env-overridable via
# TMAZE_RECORDINGS_DIR) so the camera/session UI and the session-registry tool
# agree on where recordings live (see docs/decisions/0088).
DEFAULT_OUTPUT_DIR = str(camera_output_dir())
DEFAULT_FPS = 30.0            # used for the video container if the camera can't report one

# Orientation correction so the camera frame matches the Rig view schematic
# (reward ports at top, start box at bottom). Applied at the source, so both the
# live preview and the recorded video share the same orientation. One of:
# "none", "rot180", "mirror_h", "flip_v", "rot90_cw", "rot90_ccw".
ORIENTATION = "rot180"
PREVIEW_INTERVAL_MS = 66      # ~15 fps preview, decoupled from capture rate
PREVIEW_MAX_W = 480           # downscale preview to keep the GUI light
RETRIEVE_TIMEOUT_MS = 1000    # how long RetrieveResult waits before we re-check stop
QUEUE_SECONDS = 2.0           # bounded queue holds ~this many seconds of frames

# GigE acquisition tuning (see docs/decisions/0147). The app loads no .pfs by
# default, so WITHOUT these the camera streams at its power-up packet size
# (~1500 B) even on the rig's jumbo-frame NIC (MTU 9014) - many more packets per
# frame, each an extra chance to drop one (the steady incomplete-frame climb in
# bug #260810). All are applied guarded (a non-GigE / read-only node is skipped).
GRAB_MAX_NUM_BUFFER = 32      # receive-buffer pool; headroom vs encode-thread CPU spikes
TARGET_PACKET_SIZE = 8192     # GevSCPSPacketSize target, CLAMPED to the node max (<= NIC MTU)
INTER_PACKET_DELAY = 0        # GevSCPD ticks; raise only if resends persist after jumbo
TRANSPORT_STATS_INTERVAL_S = 1.0  # how often the grab thread refreshes Basler stream stats

# Smoothing factor for the displayed tracker.process() time (exponential moving
# average). Small alpha => stable readout that still tracks sustained load; the
# separate peak-hold catches transient spikes. Diagnostic only (see #260720).
TRACK_MS_EMA_ALPHA = 0.1

# The status bar shows the live grab rate (fps), measured by differentiating the
# cumulative grabbed count over wall time. Recompute over at least this window so
# the readout is stable (the preview refresh is much faster, ~15/s, which would
# otherwise make fps jump between quantized frame-per-tick values).
FPS_WINDOW_S = 0.5

# Lens-distortion correction (see docs/decisions/0015). The calibration is
# computed offline in MATLAB cameraCalibrator and exported to an OpenCV-
# convention JSON (rig-hardware/camera/export_calibration_to_json.m -> .json,
# versioned in the resources repo). When a valid calibration is loaded it is
# applied at the source (debayer -> undistort -> orient), so the preview, the
# tracking pipeline, and the recording all share the corrected geometry. The
# default resolves to the sibling resources repo via resources_root() (env
# override + landmark search), not a fixed "../.." hop (ADR-0075, ADR-0083).
CALIBRATION_PATH = os.path.normpath(os.path.join(
    resources_root(), "rig-hardware", "camera", "camera_calibration.json"))
# getOptimalNewCameraMatrix alpha: 0 scales so the undistorted image has no
# invalid (black) border at the cost of cropping a little; 1 keeps all pixels
# with black wedges. 0 keeps the recorded frame full and border-free.
UNDISTORT_ALPHA = 0.0

# Sidecar CSV columns (future-proofed: cam_block_id / cam_timestamp are written
# now even though Step 0 doesn't analyze them, so early recordings stay usable).
# Clocks per frame (docs/decisions/0124): host_unix_s is the host wall time at
# grab (the reliable cross-stream timeline); cam_block_id is the camera's frame
# counter (monotonic; gaps = src-drops, so frame->time survives dropped frames);
# rig_ms is the LATEST firmware session ms seen at grab - it lags and RESETS to 0
# on a MODE switch (blanked on that line here), so it is NOT monotonic and must
# not be used as a standalone clock.
SIDECAR_COLUMNS = ("frame_idx", "host_unix_s", "rig_ms", "cam_block_id", "cam_timestamp")

# Nominal camera-clock tick rate (Hz) for GigE Vision: the device timestamp counts
# in nanoseconds. Written into the sidecar header as the fallback when the camera's
# GevTimestampTickFrequency node can't be read, so an offline reader can convert
# raw cam_timestamp ticks -> seconds. tick_hz_source records whether it came from
# the node ("node") or this default ("default"); an offline fit can supersede it
# with a measured value (see tools/led_sync_prototype.py).
CAM_TICK_HZ_DEFAULT = 1_000_000_000

# Extra sidecar column written after TRACK_COLUMNS. Tracking now runs on its own
# worker off the grab thread (see docs/decisions/0040), so a recorded frame carries
# the LATEST-available track result rather than one computed from that exact frame;
# this records the BlockID of the frame the track result WAS computed on, making
# the (usually small) staleness explicit and measurable for offline alignment.
TRACK_SRC_COLUMN = ("track_src_block_id",)

# How many FFV1 slices to split each frame into for multithreaded encoding. More
# slices => more parallelism across cores (at a tiny size cost). See ADR-0012.
FFV1_SLICES = 24

# Selectable recording formats (GUI labels -> internal codec id). Lossless FFV1
# is bit-exact but large (~250 GB/hr at 1.3 MP/42 fps); H.264 is lossy but
# 25-100x smaller and fine for behavioral tracking. See docs/decisions/0013.
RECORD_FORMATS = (("Lossless (FFV1)", "ffv1"), ("Compressed (H.264)", "h264"))
FFV1_EXT, H264_EXT = ".mkv", ".mp4"
H264_PRESET = "superfast"   # keeps up at full res with margin; good compression
DEFAULT_CRF = 18            # H.264 quality: lower = better/larger (~18 visually lossless)


@dataclass
class _Counters:
    """Live acquisition health, shown in the status bar. Guarded by a lock."""
    grabbed: int = 0       # frames successfully retrieved from the camera
    saved: int = 0         # frames written to the current/last recording
    # BlockID gaps between DELIVERED buffers = frames the camera produced but the
    # host never received at all. Counted via account_block_gap on every delivered
    # buffer (complete OR incomplete), so an incomplete grab no longer manufactures
    # a phantom gap for the next good frame (the pre-0147 double-count; ADR-0147).
    src_drops: int = 0
    incomplete: int = 0    # grabs that arrived corrupt/partial (GrabSucceeded() == False)
    queue_drops: int = 0   # frames dropped because the encode queue was full
    track_skips: int = 0   # frames handed to the tracker but dropped unprocessed (worker behind)
    track_ms_avg: float = 0.0   # EMA of tracker.process() wall time, ms (0 = no samples)
    track_ms_peak: float = 0.0  # peak-hold tracker.process() time, ms
    # Basler stream-grabber transport statistics - the driver's OWN view of the
    # link, ground truth vs our BlockID-derived counts (None = node unavailable /
    # not GigE). Refreshed ~1/s by the grab thread. See ADR-0147.
    stat_failed_buffer: Optional[int] = None    # driver's own incomplete-frame count
    stat_buffer_underrun: Optional[int] = None  # frames dropped: no free buffer (PC/buffers too small)
    stat_failed_packet: Optional[int] = None    # GigE packets that never arrived (link loss)
    stat_resend_request: Optional[int] = None   # GigE packet-resend requests (link loss)
    stat_total_packet: Optional[int] = None     # total GigE packets seen (context)

    def note_track_ms(self, ms: float) -> None:
        """Fold one process() timing into the EMA + peak-hold (caller holds lock)."""
        self.track_ms_avg = (ms if self.track_ms_avg == 0.0
                             else (1.0 - TRACK_MS_EMA_ALPHA) * self.track_ms_avg
                                   + TRACK_MS_EMA_ALPHA * ms)
        if ms > self.track_ms_peak:
            self.track_ms_peak = ms


@dataclass
class _RecParams:
    """Everything the encode thread needs to open a recording's outputs."""
    video_path: str
    csv_path: str
    fps: float
    size: Tuple[int, int]   # (width, height)
    header: dict
    codec: str = "ffv1"     # "ffv1" (lossless) or "h264" (compressed)
    crf: int = DEFAULT_CRF  # H.264 quality (ignored for FFV1)
    stats_path: str = ""    # companion <id>_camera_stats.json written at close (ADR-0147)


class _VideoWriter:
    """PyAV-backed video writer supporting lossless FFV1 or lossy H.264.

    Input frames are BGR uint8 (HxWx3). FFV1 is intra-only and uses slice
    multithreading, stored as "bgr0" (BGR + ignored pad byte), which is bit-exact
    lossless for 8-bit BGR. (Plain 8-bit "gbrp" is not among FFmpeg's FFV1 pixel
    formats - only gbrp9/10/12... - so bgr0 is the 8-bit lossless choice.) H.264
    (libx264) encodes to yuv420p at the given CRF; much smaller, lossy.
    """

    def __init__(self, path: str, fps: float, size: Tuple[int, int],
                 codec: str = "ffv1", crf: int = DEFAULT_CRF):
        self.container = av.open(path, mode="w")
        rate = max(1, int(round(fps)))
        if codec == "h264":
            self.stream = self.container.add_stream("libx264", rate=rate, options={
                "crf": str(crf),
                "preset": H264_PRESET,
            })
            self._enc_fmt = "yuv420p"
        else:  # ffv1 lossless
            self.stream = self.container.add_stream("ffv1", rate=rate, options={
                "slices": str(FFV1_SLICES),
                "slicecrc": "1",   # per-slice CRC: cheap integrity check
            })
            self._enc_fmt = "bgr0"  # BGR + pad byte: bit-exact lossless for 8-bit BGR
        self.stream.width, self.stream.height = size
        self.stream.pix_fmt = self._enc_fmt
        # Enable multithreading across all cores.
        for obj in (self.stream, getattr(self.stream, "codec_context", None)):
            if obj is None:
                continue
            try:
                obj.thread_type = "AUTO"
            except Exception:
                pass
        try:
            self.stream.codec_context.thread_count = 0  # 0 == auto (all cores)
        except Exception:
            pass
        self._pts = 0

    def write(self, frame_bgr) -> None:
        vframe = av.VideoFrame.from_ndarray(frame_bgr, format="bgr24")
        vframe = vframe.reformat(format=self._enc_fmt)
        vframe.pts = self._pts
        self._pts += 1
        for packet in self.stream.encode(vframe):
            self.container.mux(packet)

    def close(self) -> None:
        try:
            for packet in self.stream.encode(None):  # flush the encoder
                self.container.mux(packet)
        except Exception:
            pass
        finally:
            try:
                self.container.close()
            except Exception:
                pass


class CameraView:
    """Tk module: live Basler preview + lossless FFV1 recording.

    Module contract used by experiment_control.py: widget(), apply(line), and
    (optionally) close().
    """

    def __init__(self, parent: tk.Misc, tracker=None, pipeline=None):
        self._parent = parent
        self.frame = ttk.Frame(parent)

        # Shared maze-state pipeline (display-only here): supplies the zone map for
        # the optional zone overlay and the current zone for the persistent
        # readout. May be None (no tracker / deps); the overlay code guards for it.
        self._pipeline = pipeline

        # ---- shared state across threads ----
        self._stop = threading.Event()
        self._recording = threading.Event()
        self._latest_lock = threading.Lock()
        self._latest_frame = None              # most recent BGR numpy frame (preview)

        # Optional markerless tracker (shared RatTracker injected by
        # experiment_control.py). Tracking is OFF by default - flipped on from the
        # Tracking tab - so this tab behaves exactly as before unless requested.
        # process() runs on a SEPARATE worker thread (not the grab thread) so it can
        # never delay the next RetrieveResult / drop source frames (see ADR-0040,
        # extends ADR-0014). The grab thread hands the newest frame to _track_slot
        # (drop-oldest); the worker publishes results via _track_pub, which feeds the
        # preview overlay, the per-frame sidecar track columns, and the Tk pollers.
        self._tracker = tracker
        self._track_on = threading.Event()
        self._track_slot = LatestFrameSlot()   # newest-frame handoff to the worker
        self._track_pub = TrackPublisher()     # freshest (result, block_id, mono_ts)
        self._counters_lock = threading.Lock()
        self._counters = _Counters()
        # Cumulative-counter snapshot taken when the current recording opened, so
        # the per-recording camera_stats can report THIS video's deltas (L8; the
        # counters below are lifetime-since-launch except ``saved``). See ADR-0147.
        self._rec_counter_baseline: dict = {}
        # First/last host_unix_s (grab wall time) of the frames saved in the
        # current recording, so camera_stats can report the ACTUAL fps (S5). Reset
        # when a recording opens; updated as each frame is written.
        self._rec_first_hu: Optional[float] = None
        self._rec_last_hu: Optional[float] = None
        self._cam_fps = DEFAULT_FPS
        # Camera hardware-clock tick rate (Hz) + where it came from, read off the
        # camera at open by the grab thread and written into each sidecar header so
        # cam_timestamp ticks are convertible to seconds offline (S6, L9).
        self._cam_tick_hz: int = CAM_TICK_HZ_DEFAULT
        self._cam_tick_hz_source: str = "default"
        # Live grab-rate readout state (see _measure_fps): last sampled grab count
        # + monotonic time, and the last computed fps we keep showing between windows.
        self._fps_last_grabbed: Optional[int] = None
        self._fps_last_mono: Optional[float] = None
        self._fps_display = 0.0
        self._animal_id = ""                   # learned from the serial ANIMAL line
        self._latest_rig_ms = ""               # latest <ms> seen on the serial stream

        # .pfs profile request (applied by the grab thread, which owns the camera)
        self._pfs_lock = threading.Lock()
        self._pfs_path: Optional[str] = None
        self._pfs_pending = False

        # Applied transport settings (packet size, buffers, ...), written by the
        # grab thread at open and read for the recording provenance (ADR-0147).
        self._cam_settings_lock = threading.Lock()
        self._cam_settings: dict = {}

        # Lens-distortion correction. The Undistorter (shared with the tracker
        # prototype) builds/applies the remap on the grab thread; the calibration
        # and enable flag are set from the Tk thread (startup auto-load / Load
        # button). See docs/decisions/0015.
        self._undistorter = Undistorter(alpha=UNDISTORT_ALPHA)
        self._undistort_enabled = True
        self._calib_mismatch_warned = False      # warn once when calib size != frame size

        # recording handoff (set before _recording is set; read by encode thread)
        self._rec_params: Optional[_RecParams] = None
        # Bounded queue sized to absorb encoder spin-up / transient disk stalls.
        # Each color frame is a few MB, so keep this modest (RAM = maxsize * frame).
        maxsize = max(16, int(QUEUE_SECONDS * 45))
        self._queue: "queue.Queue" = queue.Queue(maxsize=maxsize)
        # Recording needs PyAV; preview does not. Gate the record button on it.
        self._can_record = av is not None
        self._encode_error_posted = False  # surface the first encode error, once

        # session/record mismatch warning bookkeeping (one nag per session)
        self._session_active = False
        self._warned_not_recording = False
        self._warned_still_recording = False

        self._photo = None  # keep a reference so Tk doesn't GC the preview image

        # If a hard dependency is missing, show why and stop here (no threads).
        missing = self._missing_dependency()
        if missing:
            self._build_message(
                f"Camera tab unavailable - missing dependency:\n{missing}\n\n"
                "Install the host camera requirements (see host/requirements.txt) "
                "and restart experiment_control.py.")
            self._grab_thread = None
            self._encode_thread = None
            self._track_thread = None
            return

        self._build_ui()

        # Auto-load the rig calibration if present (failure is silent here; the
        # status label shows whether a calibration is active).
        if os.path.exists(CALIBRATION_PATH):
            self._load_calibration(CALIBRATION_PATH, announce_failure=False)

        # Start workers. The grab thread opens the camera and streams for preview
        # immediately; recording only gates the queue + writer. The track worker
        # (only when a tracker is present) drains the newest-frame slot off the grab
        # thread so a slow process() can't stall acquisition (ADR-0040).
        self._encode_thread = threading.Thread(
            target=self._encode_loop, name="CameraView-encode", daemon=True)
        self._grab_thread = threading.Thread(
            target=self._grab_loop, name="CameraView-grab", daemon=True)
        self._track_thread = (
            threading.Thread(target=self._track_loop, name="CameraView-track",
                             daemon=True)
            if self._tracker is not None else None)
        self._encode_thread.start()
        self._grab_thread.start()
        if self._track_thread is not None:
            self._track_thread.start()

        self._schedule_preview()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, info: protocol.LineInfo) -> None:
        """Handle one serial line (runs on the Tk main thread).

        Captures the latest rig <ms> for the sidecar, learns the animal id, and
        drives the dismissable session/record mismatch warnings. Reads the
        pre-parsed protocol.LineInfo (docs/decisions/0056).
        """
        parsed = info.parsed
        if parsed is None:
            return
        # rig_ms is the LATEST firmware session ms seen on the stream at grab time.
        # It is NOT a monotonic clock: the firmware resets its session clock to 0
        # on every MODE switch (docs/decisions/0124). On a MODE line we blank it so
        # the sidecar marks that discontinuity explicitly (an empty cell between two
        # ascending runs) instead of recording a deceptive "0" that reads like a
        # real event time; offline timelines should use cam_block_id (the camera
        # frame counter; gaps = src-drops) + host_unix_s, not rig_ms.
        if info.mode is not None:
            self._latest_rig_ms = ""
            return
        self._latest_rig_ms = str(parsed.ms)
        typ = parsed.type

        if info.animal is not None:
            self._animal_id = info.animal
            return

        # Session start: first TRIAL (or the sequence dump that precedes it).
        if typ in ("TRIAL", "SEQ_START"):
            if not self._session_active:
                self._session_active = True
                self._warned_not_recording = False
                self._warned_still_recording = False
            if not self._recording.is_set() and not self._warned_not_recording:
                self._warned_not_recording = True
                self._show_warning(
                    "Session started but the camera is NOT recording.")
            return

        if typ == "SESSION_COMPLETE":
            if self._recording.is_set() and not self._warned_still_recording:
                self._warned_still_recording = True
                self._show_warning(
                    "Session ended but the camera is STILL recording.")
            self._session_active = False
            return

    def close(self) -> None:
        """Stop threads and release the camera + any open recording."""
        self._stop.set()
        self._recording.clear()
        self._track_slot.stop()   # unblock the track worker's take()
        for t in (self._grab_thread, self._encode_thread, self._track_thread):
            if t is not None and t.is_alive():
                t.join(timeout=3.0)

    # -------------------------------------------------- tracking integration
    def get_latest_frame(self):
        """Return a copy of the most recent oriented BGR frame (or None).

        Used by the Tracking tab's still-frame ROI editor. Returns a copy so the
        caller can hold/annotate it without racing the grab thread.
        """
        with self._latest_lock:
            frame = self._latest_frame
        return None if frame is None else frame.copy()

    def subscribe_track(self, cb) -> None:
        """Register a callback invoked on the track worker for each published
        result, as ``cb(track_result, block_id, mono_ts)``.

        This is the low-latency seam the shared maze-state pipeline consumes
        (maze_pipeline.MazeStatePipeline.on_track) so estimation runs once, at the
        camera frame rate, off the Tk poll. Callbacks run on the worker thread and
        are isolated (a raising subscriber can't kill the worker). See ADR-0041,
        extends ADR-0040.
        """
        self._track_pub.subscribe(cb)

    def enable_tracking(self, on: bool) -> None:
        """Enable/disable running the tracker on each grabbed frame."""
        if self._tracker is None:
            return
        if on:
            self._track_on.set()
        else:
            self._track_on.clear()
            # Drop any pending frame + stale result so the overlay/sidecar clear.
            self._track_slot.clear()
            self._track_pub.clear()

    def tracking_enabled(self) -> bool:
        return self._tracker is not None and self._track_on.is_set()

    def get_output_dir(self) -> str:
        """The currently-configured recordings folder (UI field, or the default).

        Used by the Tracking tab so its track logs land under the same folder.
        """
        try:
            return self._outdir_var.get().strip() or DEFAULT_OUTPUT_DIR
        except Exception:
            return DEFAULT_OUTPUT_DIR

    # ---------------------------------------------------------- dependencies
    @staticmethod
    def _missing_dependency() -> Optional[str]:
        return missing_dependency("pypylon", "numpy", "opencv-python", "pillow")

    # ------------------------------------------------------------------- UI
    def _build_message(self, text: str) -> None:
        lbl = ttk.Label(self.frame, text=text, anchor="center", justify="center",
                        padding=24)
        lbl.pack(fill=tk.BOTH, expand=True)

    def _build_ui(self) -> None:
        # Dismissable warning banner (hidden until a mismatch fires).
        self._banner = tk.Frame(self.frame, bg="#f0d000")
        self._banner_var = tk.StringVar(value="")
        tk.Label(self._banner, textvariable=self._banner_var, bg="#f0d000",
                 fg="#222222", anchor="w").pack(side=tk.LEFT, fill=tk.X,
                                                expand=True, padx=(8, 4), pady=4)
        tk.Button(self._banner, text="Dismiss", command=self._hide_banner).pack(
            side=tk.RIGHT, padx=4, pady=2)
        # (packed/unpacked dynamically by _show_warning/_hide_banner)

        # Live preview.
        self._preview = ttk.Label(self.frame, anchor="center")
        self._preview.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Status line (connection + counters).
        self._status_var = tk.StringVar(value="Opening camera...")
        status_lbl = ttk.Label(self.frame, textvariable=self._status_var,
                               anchor="w")
        status_lbl.pack(side=tk.TOP, fill=tk.X, padx=6)
        # Live tooltip: the field key description plus the current Basler transport
        # stats (the inline line stays tiny; full breakdown on hover). See ADR-0147.
        add_dynamic_tooltip(status_lbl, self._status_tooltip_text)

        # Controls.
        controls = ttk.Frame(self.frame)
        controls.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)

        self._record_btn = ttk.Button(
            controls, text="Start recording", command=self._toggle_record,
            state=tk.DISABLED)
        self._record_btn.pack(side=tk.LEFT, padx=2)
        tip(self._record_btn, "camera.record")

        if not self._can_record:
            ttk.Label(controls, foreground="#a00000",
                      text=f"(install 'av' to record: {_AV_ERR})").pack(
                side=tk.LEFT, padx=(2, 12))

        # Recording format: lossless FFV1 vs compressed H.264 (per recording).
        ttk.Label(controls, text="Format:").pack(side=tk.LEFT, padx=(8, 0))
        self._format_var = tk.StringVar(value=RECORD_FORMATS[0][0])
        self._format_combo = ttk.Combobox(
            controls, textvariable=self._format_var, state="readonly", width=18,
            values=[label for label, _ in RECORD_FORMATS])
        self._format_combo.pack(side=tk.LEFT, padx=2)
        self._format_combo.bind("<<ComboboxSelected>>",
                                lambda _e: self._on_format_change())
        tip(self._format_combo, "camera.format")
        # H.264 quality (CRF): lower = better/larger. Only shown for compressed.
        self._crf_lbl = ttk.Label(controls, text="quality (CRF):")
        self._crf_var = tk.IntVar(value=DEFAULT_CRF)
        self._crf_spin = ttk.Spinbox(controls, from_=0, to=51, width=4,
                                     textvariable=self._crf_var)
        tip(self._crf_spin, "camera.crf")
        self._on_format_change()  # set initial CRF widget visibility

        pfs_btn = ttk.Button(controls, text="Load .pfs...",
                             command=self._choose_pfs)
        pfs_btn.pack(side=tk.LEFT, padx=2)
        tip(pfs_btn, "camera.pfs")
        self._pfs_var = tk.StringVar(value="(camera defaults)")
        ttk.Label(controls, textvariable=self._pfs_var).pack(side=tk.LEFT, padx=(2, 12))

        ttk.Label(controls, text="Output:").pack(side=tk.LEFT)
        self._outdir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        outdir_entry = ttk.Entry(controls, textvariable=self._outdir_var, width=22)
        outdir_entry.pack(side=tk.LEFT, padx=2)
        tip(outdir_entry, "camera.output")
        browse_btn = ttk.Button(controls, text="Browse...",
                                command=self._choose_outdir)
        browse_btn.pack(side=tk.LEFT, padx=2)
        tip(browse_btn, "camera.browse")

        # Lens-distortion correction (camera source). Applied to preview,
        # tracking, and recording when a valid calibration is loaded.
        calib_row = ttk.Frame(self.frame)
        calib_row.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 4))

        # Optional preview overlays (drawn on the live preview only). Placed
        # INLINE on this existing row - not a new row - so they add no vertical
        # height (the Rig view window is at the monitor's vertical limit). Read
        # each frame by the draw callback, so no command is needed. Zones need the
        # pipeline; landmarks need the tracker. Zones default ON (the request),
        # landmarks default OFF. The persistent "Zone: <name>" readout is separate
        # (always shown while tracking is on, regardless of the Zones checkbox).
        self._show_zones_var = tk.BooleanVar(value=True)
        self._show_landmarks_var = tk.BooleanVar(value=False)
        if self._pipeline is not None:
            zones_cb = ttk.Checkbutton(calib_row, text="Zones",
                                       variable=self._show_zones_var)
            zones_cb.pack(side=tk.LEFT, padx=(2, 0))
            tip(zones_cb, "camera.show_zones")
        if self._tracker is not None:
            landmarks_cb = ttk.Checkbutton(calib_row, text="Landmarks",
                                           variable=self._show_landmarks_var)
            landmarks_cb.pack(side=tk.LEFT, padx=(2, 10))
            tip(landmarks_cb, "camera.show_landmarks")

        self._undistort_var = tk.BooleanVar(value=self._undistort_enabled)
        undistort_cb = ttk.Checkbutton(
            calib_row, text="Undistort", variable=self._undistort_var,
            command=self._on_undistort_toggle)
        undistort_cb.pack(side=tk.LEFT, padx=2)
        tip(undistort_cb, "camera.undistort")
        calib_btn = ttk.Button(calib_row, text="Load calibration...",
                               command=self._choose_calibration)
        calib_btn.pack(side=tk.LEFT, padx=2)
        tip(calib_btn, "camera.calibration")
        self._calib_var = tk.StringVar(value="(no calibration)")
        calib_lbl = ttk.Label(calib_row, textvariable=self._calib_var)
        calib_lbl.pack(side=tk.LEFT, padx=(2, 12))
        tip(calib_lbl, "camera.calib_status")

    def _show_warning(self, text: str) -> None:
        self._banner_var.set(text)
        # Insert the banner at the very top, above the preview.
        self._banner.pack(side=tk.TOP, fill=tk.X, before=self._preview)

    def _hide_banner(self) -> None:
        self._banner.pack_forget()

    def _choose_pfs(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a Pylon feature file",
            filetypes=[("Pylon feature stream", "*.pfs"), ("All files", "*.*")])
        if not path:
            return
        with self._pfs_lock:
            self._pfs_path = path
            self._pfs_pending = True
        self._pfs_var.set(os.path.basename(path))

    def _choose_outdir(self) -> None:
        path = filedialog.askdirectory(title="Select an output folder")
        if path:
            self._outdir_var.set(path)

    # ---------------------------------------------------- lens distortion
    def _choose_calibration(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a camera calibration JSON",
            filetypes=[("Calibration JSON", "*.json"), ("All files", "*.*")])
        if path:
            self._load_calibration(path)

    def _on_undistort_toggle(self) -> None:
        self._undistort_enabled = bool(self._undistort_var.get())

    def _set_calib_status(self, text: str) -> None:
        try:
            self._calib_var.set(text)
        except (tk.TclError, AttributeError):
            pass

    def _load_calibration(self, path: str, announce_failure: bool = True) -> bool:
        """Load an OpenCV-convention calibration JSON and arm undistortion.

        The JSON is produced from MATLAB by rig-hardware/camera/
        export_calibration_to_json.m (see docs/decisions/0015).
        """
        try:
            calib = load_calibration(path)
        except Exception as e:
            if announce_failure:
                self._post(self._set_calib_status,
                           f"Calibration load failed: {type(e).__name__}: {e}")
            return False

        self._undistorter.set_calibration(calib)
        self._calib_mismatch_warned = False
        cal_w, cal_h = calib["image_size"]
        rms = calib["rms"]
        rms_txt = f", rms={rms:.3f}" if isinstance(rms, (int, float)) else ""
        self._post(self._set_calib_status,
                   f"Calibration: {os.path.basename(path)} "
                   f"({cal_w}x{cal_h}{rms_txt})")
        return True

    def current_calibration_path(self) -> Optional[str]:
        """Path of the calibration JSON currently loaded into the undistorter, or
        ``None`` when none is loaded. Lets the session config snapshot hash the
        calibration actually IN USE rather than the default constant (P5,
        docs/decisions/0180)."""
        calib = self._undistorter.calibration
        path = calib.get("path") if calib else None
        return path or None

    def _maybe_undistort(self, arr):
        """Undistort a raw BGR frame if a matching calibration is active.

        Runs on the grab thread. On a calibration/frame size mismatch it warns
        once and returns the frame unchanged rather than applying a wrong map.
        """
        if not self._undistort_enabled:
            return arr
        out, status = self._undistorter.apply(arr)
        if status == "mismatch" and not self._calib_mismatch_warned:
            self._calib_mismatch_warned = True
            calib = self._undistorter.calibration
            cal_w, cal_h = calib["image_size"] if calib else (0, 0)
            h, w = arr.shape[:2]
            self._post(self._set_calib_status,
                       f"Calibration is {cal_w}x{cal_h} but frames are "
                       f"{w}x{h} - undistortion skipped")
        return out

    def _selected_codec(self) -> Tuple[str, str]:
        """Return (codec_id, file_extension) for the current Format selection."""
        label = self._format_var.get()
        for lbl, codec in RECORD_FORMATS:
            if lbl == label:
                return codec, (H264_EXT if codec == "h264" else FFV1_EXT)
        return "ffv1", FFV1_EXT

    def _on_format_change(self) -> None:
        """Show the CRF quality control only when the compressed codec is chosen."""
        codec, _ = self._selected_codec()
        if codec == "h264":
            self._crf_lbl.pack(side=tk.LEFT, padx=(8, 0), after=self._format_combo)
            self._crf_spin.pack(side=tk.LEFT, padx=2, after=self._crf_lbl)
        else:
            self._crf_spin.pack_forget()
            self._crf_lbl.pack_forget()

    # ---------------------------------------------------------- preview loop
    def _schedule_preview(self) -> None:
        if self._stop.is_set():
            return
        self._update_status()
        with self._latest_lock:
            frame = self._latest_frame
        if frame is not None:
            self._render_preview(frame)
        safe_after(self.frame, PREVIEW_INTERVAL_MS, self._schedule_preview)

    def _render_preview(self, frame_bgr) -> None:
        def draw(disp, scale: float) -> None:
            # Current zone + substate from the shared pipeline (display-only), used
            # for the persistent readout and to bold the occupied zone / well. The
            # well is a HUB substate (in_well), not a zone, so it is signalled via
            # its own bold outline rather than a distinct zone name.
            zone, substate = self._current_zone_substate()
            in_well = substate == _IN_WELL
            if self.tracking_enabled():
                # ROI/path/centroid + the tracker's state text (landmarks are
                # drawn separately below so they can be toggled on their own).
                self._draw_track_overlay(disp, scale)
                self._draw_zone_readout(disp, zone, substate)
            if self._show_landmarks_var.get() and _draw_landmarks_shared is not None:
                _draw_landmarks_shared(disp, self._tracker)
            if (self._show_zones_var.get() and self._pipeline is not None
                    and _draw_zone_overlay_shared is not None):
                _draw_zone_overlay_shared(disp, self._pipeline.zone_map, zone,
                                          in_well=in_well)

        photo = render_bgr_to_label(
            self._preview, frame_bgr, PREVIEW_MAX_W, draw=draw,
            copy_unscaled=self.tracking_enabled())
        if photo is not None:
            self._photo = photo

    def _current_zone_substate(self) -> Tuple[Optional[str], Optional[str]]:
        """The (zone, substate) the rat is presently in per the shared pipeline.

        Both None when there's no pipeline or no snapshot yet (e.g. tracking off).
        ``substate`` is in_well/outside_well while in the HUB, else None.
        """
        if self._pipeline is None:
            return None, None
        bundle = self._pipeline.snapshot()
        if bundle is None:
            return None, None
        return bundle.state.zone, bundle.state.substate

    def _draw_zone_readout(self, disp, zone: Optional[str],
                           substate: Optional[str]) -> None:
        """Draw the persistent "Zone: <name>" line under the tracker state text.

        Shown whenever tracking is on (independent of the Zones overlay toggle);
        falls back to UNKNOWN when the animal is outside all zones / no zone map.
        The HUB substate (in_well/outside_well) is appended so the well - a HUB
        sub-region, not its own zone - is visible in the readout.
        """
        if cv2 is None:
            return
        text = f"Zone: {zone or 'UNKNOWN'}"
        if substate:
            text += f" ({substate})"
        cv2.putText(disp, text, (6, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)

    def _draw_track_overlay(self, disp, scale: float) -> None:
        """Draw the (shared) tracking overlay on the preview.

        Landmarks are suppressed here (draw_landmarks=False) so the Rig view can
        toggle them independently via the "Landmarks" checkbox (no double-draw).
        """
        if _draw_track_overlay_shared is None:
            return
        snap = self._track_pub.snapshot()
        result = snap[0] if snap is not None else None
        _draw_track_overlay_shared(disp, scale, self._tracker, result,
                                   draw_landmarks=False)

    def _measure_fps(self, grabbed: int) -> float:
        """Estimate the live grab rate (frames/s) from the cumulative grab count.

        Called on each status refresh. Differentiates ``grabbed`` over wall time,
        but only recomputes once at least FPS_WINDOW_S has elapsed (otherwise it
        keeps returning the last value), so the readout stays stable rather than
        jumping with the fast preview refresh. Diagnostic only.
        """
        now = time.monotonic()
        if self._fps_last_mono is None:
            self._fps_last_grabbed = grabbed
            self._fps_last_mono = now
            return 0.0
        dt = now - self._fps_last_mono
        if dt >= FPS_WINDOW_S:
            self._fps_display = (grabbed - self._fps_last_grabbed) / dt
            self._fps_last_grabbed = grabbed
            self._fps_last_mono = now
        return self._fps_display

    def _update_status(self) -> None:
        track_on = self._tracker is not None and self._track_on.is_set()
        with self._counters_lock:
            c = self._counters
            fps = self._measure_fps(c.grabbed)
            line = (f"fps: {fps:.1f}   saved: {c.saved}   "
                    f"src-drop: {c.src_drops}   incomplete: {c.incomplete}   "
                    f"q-drop: {c.queue_drops}   queue: {self._queue.qsize()}/"
                    f"{self._queue.maxsize}")
            if track_on:
                line += (f"   track: {c.track_ms_avg:.1f}/{c.track_ms_peak:.1f} ms"
                         f"   track-skip: {c.track_skips}")
            net = self._net_token(c)  # tiny transport-health token; detail on hover
        if net:
            line += "   " + net
        rec = "  [RECORDING]" if self._recording.is_set() else ""
        self._status_var.set(self._conn_status + "   |   " + line + rec)

    @staticmethod
    def _net_token(c) -> str:
        """A tiny inline link-health token: resend-requests / buffer-underruns.

        Kept small because the status line lives in the cramped Rig-view tab; the
        full Basler stat breakdown is in the status hover tooltip (ADR-0147).
        Empty when no transport stats are available (e.g. not a GigE camera).
        """
        r, u = c.stat_resend_request, c.stat_buffer_underrun
        if r is None and u is None:
            return ""
        return f"net: {'-' if r is None else r}r/{'-' if u is None else u}u"

    def _status_tooltip_text(self) -> str:
        """Field-key description + the current Basler transport stats (ADR-0147)."""
        from tmaze.ui.tooltip_text import resolve
        base = resolve("camera.status") or ""
        with self._counters_lock:
            c = self._counters
            rows = (
                ("failed buffers", c.stat_failed_buffer),
                ("buffer underruns (PC / buffers too small)", c.stat_buffer_underrun),
                ("packet resend requests (link loss)", c.stat_resend_request),
                ("failed packets (link loss)", c.stat_failed_packet),
                ("total packets", c.stat_total_packet),
            )
        detail = "\n".join(f"  {label}: {'n/a' if v is None else v}"
                           for label, v in rows)
        return f"{base}\n\nBasler transport stats (ground truth):\n{detail}"

    # set by the grab thread via _post; read on the Tk thread
    _conn_status = "Opening camera..."

    def _post(self, fn, *args) -> None:
        safe_after(self.frame, 0, fn, *args)

    def _set_conn_status(self, text: str) -> None:
        self._conn_status = text

    def _set_record_enabled(self, enabled: bool) -> None:
        try:
            self._record_btn.configure(
                state=(tk.NORMAL if (enabled and self._can_record)
                       else tk.DISABLED))
        except tk.TclError:
            pass

    def _reset_record_button(self) -> None:
        try:
            self._record_btn.configure(text="Start recording")
        except tk.TclError:
            pass

    # ------------------------------------------------------- recording control
    def is_recording(self) -> bool:
        """True while a recording is in progress. Public seam (docs/decisions/0048)."""
        return self._recording.is_set()

    def current_recording_path(self) -> Optional[str]:
        """The in-progress recording's video path, or None if not recording.

        The per-frame sidecar CSV shares the base name with a ``.csv`` extension.
        Used by the Session controller to record this stream in the session
        manifest (see docs/decisions/0048).
        """
        if not self._recording.is_set() or self._rec_params is None:
            return None
        return self._rec_params.video_path

    def _toggle_record(self) -> None:
        """Record-button handler: start or stop the recording."""
        if self._recording.is_set():
            self.stop_recording()
        else:
            self.start_recording()

    def stop_recording(self) -> None:
        """Stop the in-progress recording (encode thread finalizes the file).

        Public seam so the Session controller can end recording with the session
        (docs/decisions/0048); also the Record button's stop path. No-op if idle.
        """
        if not self._recording.is_set():
            return
        self._recording.clear()
        try:
            self._record_btn.configure(text="Start recording")
        except (tk.TclError, AttributeError):
            pass
        self._set_format_locked(False)

    def start_recording(self, session_id: Optional[str] = None,
                        out_dir: Optional[str] = None,
                        session_t0: Optional[dict] = None) -> bool:
        """Begin a recording; return True iff it started.

        Public seam used by the Session controller (one-click session start; see
        docs/decisions/0048) as well as the Record button. When ``session_id`` is
        given it names the file (so the video + Phase 3 file share the session
        stamp) and is stamped into the sidecar header for correlation; otherwise
        the file is named ``<animal>_<stamp>`` as before. ``out_dir`` redirects the
        output into the session's folder (docs/decisions/0049); when omitted the
        file goes to the Output: box / the default recordings folder as before.
        ``session_t0`` is the manifest anchor (host wall+mono+iso at Start Session)
        stamped into the sidecar header so the video self-anchors to the one shared
        instant, at millisecond resolution (docs/decisions/0124); None for a manual
        Record-button recording. Needs the camera UI + PyAV; a no-op (returns False)
        if recording isn't available or already on.
        """
        if not self._can_record or not hasattr(self, "_record_btn"):
            return False
        if self._recording.is_set():
            return False

        with self._latest_lock:
            frame = self._latest_frame
        if frame is None:
            self._show_warning("No frames yet - cannot start recording.")
            return False

        h, w = frame.shape[:2]
        out_dir = out_dir or self._outdir_var.get().strip() or DEFAULT_OUTPUT_DIR
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            self._show_warning(f"Cannot create output folder: {e}")
            return False

        codec, ext = self._selected_codec()
        try:
            crf = int(self._crf_var.get())
        except (tk.TclError, ValueError):
            crf = DEFAULT_CRF
        crf = max(0, min(51, crf))

        stamp = time.strftime("%Y%m%d_%H%M%S")
        animal = self._animal_id or "animal"
        # A session_id (from the Session controller) names the file so the video,
        # its sidecar, and the Phase 3 file share the session stamp; otherwise
        # fall back to <animal>_<stamp> (docs/decisions/0048).
        base = os.path.join(out_dir, session_id if session_id else f"{animal}_{stamp}")
        # Record whether the saved frames are lens-corrected, so downstream
        # analysis knows the video is already undistorted (ADR-0015).
        calib = self._undistorter.calibration
        undistorted = self._undistort_enabled and self._undistorter.is_active()
        calib_rms = calib["rms"] if (calib and isinstance(calib.get("rms"), (int, float))) else ""
        # Pair the empty-rig background image with this recording so the tracker
        # can be re-tuned offline against the video (which has no empty-rig
        # frame). See docs/decisions/0020.
        bg_name = ""
        if self._tracker is not None:
            try:
                bg = self._tracker.get_background_image()
            except Exception:
                bg = None
            if bg is not None:
                bg_path = base + "_background.png"
                try:
                    if cv2.imwrite(bg_path, bg):
                        bg_name = os.path.basename(bg_path)
                except Exception:
                    bg_name = ""
        # Applied transport settings (packet size, buffers) for provenance; the
        # grab thread set these at open, before recording could be enabled (ADR-0147).
        with self._cam_settings_lock:
            cam_settings = dict(self._cam_settings)
        header = {
            "animal": animal,
            "session_id": session_id or "",
            "fps": f"{self._cam_fps:.3f}",
            "width": w,
            "height": h,
            "pfs": self._pfs_var.get(),
            "packet_size": cam_settings.get("packet_size", ""),
            "packet_size_max": cam_settings.get("packet_size_max", ""),
            "max_num_buffer": cam_settings.get("max_num_buffer", ""),
            "inter_packet_delay": cam_settings.get("inter_packet_delay", ""),
            "codec": codec,
            "crf": (crf if codec == "h264" else ""),
            "start_wall_clock": time.strftime("%Y-%m-%d %H:%M:%S"),
            # Canonical session anchor shared by every stream (ms resolution),
            # so the video self-anchors to one instant (docs/decisions/0124).
            # start_wall_clock above is the (coarser) record-start, kept as a
            # labeled secondary event.
            "session_t0_iso": (session_t0.get("iso", "") if session_t0 else ""),
            "session_t0_wall": (session_t0.get("wall", "") if session_t0 else ""),
            # The monotonic partner to the anchor above, so the sidecar carries the
            # SAME three-part session zero as the trial CSV + manifest (L9,
            # docs/decisions/0124). Blank for a manual Record-button recording.
            "session_t0_mono": (session_t0.get("mono", "") if session_t0 else ""),
            # Camera hardware-clock tick rate (Hz) + its source, so raw
            # cam_timestamp ticks convert to seconds offline (S6). Read off the
            # camera at open; "default" = the GigE nominal fallback.
            "cam_timestamp_tick_hz": self._cam_tick_hz,
            "tick_hz_source": self._cam_tick_hz_source,
            # rig_ms is the latest firmware session ms at grab; it resets on a MODE
            # switch and is NOT monotonic - use host_unix_s + cam_block_id offline
            # (docs/decisions/0124).
            "rig_ms_note": "latest firmware ms at grab; resets on MODE; not monotonic",
            "camera": f"ip={CAMERA_IP} mac={CAMERA_MAC}",
            "undistorted": undistorted,
            "calibration": (os.path.basename(calib["path"]) if calib else ""),
            "calib_rms": calib_rms,
            "background": bg_name,
        }
        self._rec_params = _RecParams(
            video_path=base + ext, csv_path=base + ".csv",
            fps=self._cam_fps, size=(w, h), header=header, codec=codec, crf=crf,
            stats_path=base + "_camera_stats.json")

        # Clear any stale frames so frame_idx starts clean at 0.
        self._drain_queue()
        self._recording.set()
        try:
            self._record_btn.configure(text="Stop recording")
        except (tk.TclError, AttributeError):
            pass
        self._set_format_locked(True)  # don't change format mid-recording
        return True

    def _set_format_locked(self, locked: bool) -> None:
        """Disable the format/quality controls while a recording is in progress."""
        state = tk.DISABLED if locked else "readonly"
        try:
            self._format_combo.configure(state=state)
            self._crf_spin.configure(state=(tk.DISABLED if locked else tk.NORMAL))
        except tk.TclError:
            pass

    def _drain_queue(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    # --------------------------------------------------------------- threads
    def _grab_loop(self) -> None:
        """Own the camera: open it, stream free-running, feed preview + queue."""
        camera = None
        converter = None
        last_block: Optional[int] = None
        try:
            device, factory = self._find_device()
            if device is None:
                self._post(self._set_conn_status, "No Basler camera found")
                return
            camera = pylon.InstantCamera(factory.CreateDevice(device))
            camera.Open()
            self._apply_pfs_if_pending(camera)
            settings = self._configure_transport(camera)  # buffers + packet size (ADR-0147)
            with self._cam_settings_lock:
                self._cam_settings = settings
            converter = pylon.ImageFormatConverter()
            converter.OutputPixelFormat = pylon.PixelType_BGR8packed
            converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
            self._read_fps(camera)
            self._read_tick_hz(camera)
            camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
            self._post(self._set_conn_status, self._describe_device(device))
            self._post(self._set_record_enabled, True)
        except Exception as e:  # camera busy (Pylon Viewer open), unreachable, etc.
            self._post(self._set_conn_status,
                       f"Camera open failed: {type(e).__name__}: {e} "
                       "(is Pylon Viewer holding it?)")
            self._safe_close_camera(camera)
            return

        last_stats_mono = 0.0
        try:
            while not self._stop.is_set():
                # Apply a freshly chosen .pfs (we own the camera here).
                if self._pfs_pending:
                    self._reload_pfs(camera)

                # Refresh the Basler transport stats ~1/s (cheap, guarded); the
                # status readout + recording provenance read the cached snapshot.
                now_mono = time.monotonic()
                if now_mono - last_stats_mono >= TRANSPORT_STATS_INTERVAL_S:
                    last_stats_mono = now_mono
                    self._refresh_transport_stats(camera)

                try:
                    grab = camera.RetrieveResult(
                        RETRIEVE_TIMEOUT_MS, pylon.TimeoutHandling_Return)
                except Exception:
                    continue
                if grab is None:
                    continue
                try:
                    if not grab.IsValid():
                        continue

                    # Account the BlockID on EVERY delivered buffer (complete or
                    # not) so an incomplete grab advances last_block instead of
                    # manufacturing a phantom src-drop for the next good frame
                    # (the pre-0147 double-count; ADR-0147, bug #260810).
                    block_id = self._safe_block_id(grab)
                    last_block, gap = account_block_gap(last_block, block_id)
                    succeeded = grab.GrabSucceeded()
                    if gap or not succeeded:
                        with self._counters_lock:
                            if gap:
                                self._counters.src_drops += gap
                            if not succeeded:
                                self._counters.incomplete += 1
                    if not succeeded:
                        continue

                    # Stamp host wall time at GRAB - the instant this buffer came
                    # back from the camera - NOT after debayer/undistort/orient
                    # (which add variable latency). This is the reliable
                    # cross-stream anchor the sidecar's host_unix_s column records
                    # (S4; docs/decisions/0124).
                    grab_wall = time.time()
                    cam_ts = self._safe_timestamp(grab)
                    arr = converter.Convert(grab).GetArray()  # HxWx3 BGR uint8
                    arr = self._maybe_undistort(arr)          # lens correction (ADR-0015)
                    frame = self._orient(arr)

                    # Markerless tracking (optional, default off) runs on a SEPARATE
                    # worker (ADR-0040): hand it the NEWEST frame + alignment metadata
                    # and move on. If the worker is behind, this overwrites the pending
                    # frame (a counted track-skip) so a slow process() degrades tracking
                    # RATE, not capture - it can never stall the next RetrieveResult or
                    # drop source frames (the root cause in #260720). frame is a fresh
                    # array each iteration and process() never mutates it in place, so
                    # sharing the reference across threads read-only is safe.
                    if self._tracker is not None and self._track_on.is_set():
                        skipped = self._track_slot.put(
                            frame, block_id, cam_ts, time.monotonic())
                        if skipped:
                            with self._counters_lock:
                                self._counters.track_skips += 1

                    with self._latest_lock:
                        self._latest_frame = frame
                    with self._counters_lock:
                        self._counters.grabbed += 1

                    if self._recording.is_set():
                        # The frame-aligned track result was computed on the worker;
                        # attach the LATEST-available one plus the BlockID it came from
                        # (track_src_block_id) so its staleness is explicit (ADR-0040).
                        snap = self._track_pub.snapshot()
                        track = track_row(snap[0] if snap is not None else None)
                        track_src = ("" if snap is None or snap[1] is None
                                     else snap[1])
                        meta = (grab_wall, self._latest_rig_ms,
                                "" if block_id is None else block_id,
                                "" if cam_ts is None else cam_ts,
                                track, track_src)
                        self._enqueue(frame, meta)
                finally:
                    grab.Release()
        finally:
            self._safe_stop_grabbing(camera)
            self._safe_close_camera(camera)
            self._post(self._set_record_enabled, False)

    def _track_loop(self) -> None:
        """Run the tracker off the grab thread on the newest available frame.

        Blocks on the drop-oldest _track_slot, so it processes the freshest frame
        the grab thread produced (skipping any it fell behind on) and publishes the
        result via _track_pub. Timing is measured here (note_track_ms) so the
        `track: <avg>/<peak> ms` readout still reflects real process() cost. See
        docs/decisions/0040.
        """
        while not self._stop.is_set():
            item = self._track_slot.take(timeout=0.2)
            if item is None:
                continue
            frame, block_id, _cam_ts, mono_ts = item
            tracker = self._tracker
            if tracker is None:
                continue
            t0 = time.perf_counter()
            try:
                result = tracker.process(frame)   # never let a hiccup kill the worker
            except Exception:
                result = None
            with self._counters_lock:
                self._counters.note_track_ms((time.perf_counter() - t0) * 1000.0)
            self._track_pub.publish(result, block_id, mono_ts)

    def _enqueue(self, frame, meta) -> None:
        try:
            self._queue.put_nowait((frame, meta))
        except queue.Full:
            with self._counters_lock:
                self._counters.queue_drops += 1  # controlled, counted drop

    def _encode_loop(self) -> None:
        """Drain the queue to FFV1 + sidecar CSV while recording."""
        writer = None
        csv_file = None
        csv_writer = None
        saved_idx = 0
        # Wall-time of the last sidecar flush. The CSV is otherwise only flushed on
        # close, so an unclean end would lose the whole session's buffered per-frame
        # rows; we force a flush at least once a second (docs/decisions/0174), which
        # bounds a crash's sidecar loss to ~1 s regardless of frame rate.
        last_sidecar_flush = 0.0
        active_params: Optional[_RecParams] = None
        try:
            while not self._stop.is_set():
                recording = self._recording.is_set()

                if recording and writer is None:
                    params = self._rec_params
                    if params is not None:
                        self._encode_error_posted = False
                        writer, csv_file, csv_writer = self._open_outputs(params)
                        active_params = params
                        saved_idx = 0
                        # Reset the grab-time span so fps_measured covers only this
                        # recording (S5).
                        self._rec_first_hu = None
                        self._rec_last_hu = None
                        last_sidecar_flush = time.monotonic()
                        with self._counters_lock:
                            self._counters.saved = 0
                            # Snapshot the cumulative counters so this recording's
                            # camera_stats can report its own deltas (L8, ADR-0147).
                            c = self._counters
                            self._rec_counter_baseline = {
                                "grabbed": c.grabbed, "saved": 0,
                                "src_drops": c.src_drops,
                                "incomplete": c.incomplete,
                                "queue_drops": c.queue_drops,
                                "track_skips": c.track_skips,
                            }
                        if writer is None:
                            self._recording.clear()  # open failed; bail cleanly
                            self._post(self._reset_record_button)
                            self._post(self._set_format_locked, False)

                if not recording and writer is not None:
                    self._flush_remaining(writer, csv_writer, saved_idx)
                    path = self._close_outputs(writer, csv_file)
                    self._write_camera_stats(active_params)
                    writer = csv_file = csv_writer = active_params = None
                    self._post(self._set_conn_status_keep(path))

                try:
                    frame, meta = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if writer is not None:
                    ok = self._write_frame(writer, csv_writer, saved_idx, frame, meta)
                    saved_idx += 1
                    # Keep the sidecar durable within ~1 s of the newest frame
                    # (docs/decisions/0174), so an unclean end loses at most the
                    # last second of per-frame rows rather than the whole buffer.
                    now_mono = time.monotonic()
                    if csv_file is not None and now_mono - last_sidecar_flush >= 1.0:
                        try:
                            csv_file.flush()
                            os.fsync(csv_file.fileno())
                        except (OSError, ValueError):
                            pass
                        last_sidecar_flush = now_mono
                    if not ok:
                        # Surface the failure and stop cleanly rather than
                        # silently producing an empty video + header-only CSV.
                        self._recording.clear()
                        self._post(self._reset_record_button)
                        self._post(self._set_format_locked, False)
        finally:
            if writer is not None:
                self._flush_remaining(writer, csv_writer, saved_idx)
                self._close_outputs(writer, csv_file)
                self._write_camera_stats(active_params)

    # ------------------------------------------------------ encode helpers
    def _open_outputs(self, params: _RecParams):
        try:
            writer = _VideoWriter(params.video_path, params.fps, params.size,
                                  codec=params.codec, crf=params.crf)
            csv_file = open(params.csv_path, "w", newline="")
            for k, v in params.header.items():
                csv_file.write(f"# {k}: {v}\n")
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(SIDECAR_COLUMNS + TRACK_COLUMNS + TRACK_SRC_COLUMN)
            self._post(self._set_conn_status,
                       f"Recording -> {os.path.basename(params.video_path)}")
            return writer, csv_file, csv_writer
        except Exception as e:
            self._post(self._set_conn_status, f"Recording failed to start: {e}")
            return None, None, None

    def _write_frame(self, writer, csv_writer, idx, frame, meta) -> bool:
        try:
            writer.write(frame)
            host_unix_s, rig_ms, block_id, cam_ts, track, track_src = meta
            csv_writer.writerow(
                [idx, f"{host_unix_s:.6f}", rig_ms, block_id, cam_ts]
                + list(track) + [track_src])
            # Track the saved-frame grab-time span for fps_measured (S5).
            if self._rec_first_hu is None:
                self._rec_first_hu = host_unix_s
            self._rec_last_hu = host_unix_s
            with self._counters_lock:
                self._counters.saved = idx + 1
            return True
        except Exception as e:
            if not self._encode_error_posted:
                self._encode_error_posted = True
                self._post(self._set_conn_status,
                           f"Recording error: {type(e).__name__}: {e}")
                print(f"[camera_view] encode error: {e}", file=sys.stderr, flush=True)
            return False

    def _flush_remaining(self, writer, csv_writer, saved_idx) -> None:
        idx = saved_idx
        try:
            while True:
                frame, meta = self._queue.get_nowait()
                self._write_frame(writer, csv_writer, idx, frame, meta)
                idx += 1
        except queue.Empty:
            pass

    def _close_outputs(self, writer, csv_file) -> str:
        path = ""
        try:
            if writer is not None:
                writer.close()
        except Exception:
            pass
        try:
            if csv_file is not None:
                path = csv_file.name
                csv_file.close()
        except Exception:
            pass
        return path

    def _write_camera_stats(self, params: "Optional[_RecParams]") -> None:
        """Write the per-recording camera provenance JSON at close (ADR-0147).

        Captures WHAT WAS CONFIGURED (packet size, buffers) and WHAT THE LINK
        ACTUALLY DID (our final saved/incomplete/src-drop counts + the Basler
        transport stats), so each session bundle carries a self-contained record
        of the acquisition health. Best-effort: a failure is logged, never fatal.
        """
        if params is None or not params.stats_path:
            return
        with self._counters_lock:
            c = self._counters
            # Lifetime-since-launch totals (except ``saved``, which resets per
            # recording). ``grabbed`` + ``track_skips`` are now written too (L8;
            # they were tracked but never persisted, so #260810 couldn't see them).
            counters = {
                "grabbed": c.grabbed,
                "saved": c.saved,
                "src_drops": c.src_drops,
                "incomplete": c.incomplete,
                "queue_drops": c.queue_drops,
                "track_skips": c.track_skips,
            }
            transport = {
                "failed_buffer": c.stat_failed_buffer,
                "buffer_underrun": c.stat_buffer_underrun,
                "failed_packet": c.stat_failed_packet,
                "resend_request": c.stat_resend_request,
                "total_packet": c.stat_total_packet,
            }
        with self._cam_settings_lock:
            settings = dict(self._cam_settings)
        payload = {
            "session_id": params.header.get("session_id", ""),
            "video": os.path.basename(params.video_path),
            "settings": settings,
            "counters": counters,
            # Deltas FOR THIS recording (current cumulative - the open-time
            # snapshot), so a multi-recording session can attribute drops to the
            # right video (L8; ADR-0147). ``saved`` is already per-recording.
            "counters_this_recording": recording_deltas(
                counters, self._rec_counter_baseline),
            "transport": transport,
            # The ACTUAL frame rate from the sidecar host-grab stamps (S5): the mp4
            # container timestamps are nominal-rate, so this is what an offline
            # reader should trust. None when < 2 frames were saved.
            "fps_nominal": params.header.get("fps", ""),
            "fps_measured": fps_measured(
                counters["saved"], self._rec_first_hu, self._rec_last_hu),
            "written_wall_clock": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            with open(params.stats_path, "w", newline="") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:  # provenance is best-effort; never break a recording
            print(f"[camera_view] camera stats write failed: {e}",
                  file=sys.stderr, flush=True)

    def _set_conn_status_keep(self, video_or_csv_path: str):
        name = os.path.basename(video_or_csv_path).rsplit(".", 1)[0] if video_or_csv_path else ""
        msg = f"Saved recording: {name}" if name else "Recording stopped"
        return lambda: self._set_conn_status(msg)

    # ------------------------------------------------------- camera helpers
    def _find_device(self):
        factory = pylon.TlFactory.GetInstance()
        devices = factory.EnumerateDevices()
        if not devices:
            return None, factory
        for d in devices:
            if CAMERA_SERIAL and self._dev_attr(d, "GetSerialNumber") == CAMERA_SERIAL:
                return d, factory
        for d in devices:
            if CAMERA_IP and self._dev_attr(d, "GetIpAddress") == CAMERA_IP:
                return d, factory
        for d in devices:
            mac = (self._dev_attr(d, "GetMacAddress") or "").replace(":", "").upper()
            if CAMERA_MAC and mac == CAMERA_MAC.upper():
                return d, factory
        return devices[0], factory

    @staticmethod
    def _dev_attr(device, attr: str) -> str:
        try:
            return getattr(device, attr)()
        except Exception:
            return ""

    def _describe_device(self, device) -> str:
        model = self._dev_attr(device, "GetModelName") or "Basler"
        ip = self._dev_attr(device, "GetIpAddress")
        serial = self._dev_attr(device, "GetSerialNumber")
        extra = f" @ {ip}" if ip else (f" #{serial}" if serial else "")
        return f"Connected: {model}{extra}"

    def _configure_transport(self, camera) -> dict:
        """Size the receive-buffer pool + GigE packet size before grabbing.

        The app loads no .pfs by default, so the camera would otherwise stream at
        its power-up packet size (~1500 B) even on the rig's jumbo NIC. Every
        write is guarded so a non-GigE camera or a read-only node can never break
        camera open. Returns the applied/observed values for recording provenance
        (ADR-0147).
        """
        settings: dict = {}
        # Receive-buffer pool (USB + GigE): headroom for encode-thread CPU spikes.
        try:
            camera.MaxNumBuffer.SetValue(GRAB_MAX_NUM_BUFFER)
        except Exception:
            pass
        try:
            settings["max_num_buffer"] = int(camera.MaxNumBuffer.GetValue())
        except Exception:
            pass
        # GigE packet size: match the jumbo NIC. Read the node max, clamp, set.
        try:
            node = camera.GevSCPSPacketSize
            pmax = int(node.GetMax())
            node.SetValue(min(TARGET_PACKET_SIZE, pmax))
            settings["packet_size"] = int(node.GetValue())
            settings["packet_size_max"] = pmax
        except Exception:
            pass
        # Inter-packet delay (GevSCPD): only nudge it if configured non-zero.
        try:
            if INTER_PACKET_DELAY:
                camera.GevSCPD.SetValue(INTER_PACKET_DELAY)
            settings["inter_packet_delay"] = int(camera.GevSCPD.GetValue())
        except Exception:
            pass
        return settings

    def _refresh_transport_stats(self, camera) -> None:
        """Cache the Basler stream-grabber statistics into the counters (guarded).

        These are the driver's OWN view of the link (failed buffers, buffer
        underruns, packet resends/loss) - ground truth that tells apart PC/buffer
        pressure (underruns) from link packet loss (resends) vs. our BlockID-
        derived counts. All access is guarded; missing nodes stay None (ADR-0147).
        """
        try:
            nodemap = camera.GetStreamGrabberNodeMap()
        except Exception:
            return

        def val(name):
            # Raw INodeMap needs a typed pointer; fall back to attribute access.
            try:
                from pypylon import genicam
                node = nodemap.GetNode(name)
                if node is not None:
                    return int(genicam.CIntegerPtr(node).GetValue())
            except Exception:
                pass
            try:
                return int(getattr(nodemap, name).GetValue())
            except Exception:
                return None

        failed_buffer = val("Statistic_Failed_Buffer_Count")
        buffer_underrun = val("Statistic_Buffer_Underrun_Count")
        failed_packet = val("Statistic_Failed_Packet_Count")
        resend_request = val("Statistic_Resend_Request_Count")
        total_packet = val("Statistic_Total_Packet_Count")
        with self._counters_lock:
            c = self._counters
            c.stat_failed_buffer = failed_buffer
            c.stat_buffer_underrun = buffer_underrun
            c.stat_failed_packet = failed_packet
            c.stat_resend_request = resend_request
            c.stat_total_packet = total_packet

    def _read_fps(self, camera) -> None:
        for attr in ("ResultingFrameRate", "ResultingFrameRateAbs",
                     "AcquisitionFrameRate", "AcquisitionFrameRateAbs"):
            try:
                node = getattr(camera, attr)
                val = float(node.GetValue())
                if val > 0:
                    self._cam_fps = val
                    return
            except Exception:
                continue

    def _read_tick_hz(self, camera) -> None:
        """Read the camera-clock tick frequency (Hz) so cam_timestamp ticks are
        convertible to seconds offline (S6). Falls back to the GigE nominal
        (CAM_TICK_HZ_DEFAULT) and records the source for the sidecar header."""
        for attr in ("GevTimestampTickFrequency", "TimestampTickFrequency"):
            try:
                node = getattr(camera, attr)
                val = int(node.GetValue())
                if val > 0:
                    self._cam_tick_hz = val
                    self._cam_tick_hz_source = "node"
                    return
            except Exception:
                continue
        self._cam_tick_hz = CAM_TICK_HZ_DEFAULT
        self._cam_tick_hz_source = "default"

    def _apply_pfs_if_pending(self, camera) -> None:
        with self._pfs_lock:
            path = self._pfs_path if self._pfs_pending else None
            self._pfs_pending = False
        if path:
            try:
                pylon.FeaturePersistence.Load(path, camera.GetNodeMap(), True)
            except Exception as e:
                self._post(self._set_conn_status, f".pfs load failed: {e}")

    def _reload_pfs(self, camera) -> None:
        with self._pfs_lock:
            path = self._pfs_path
            self._pfs_pending = False
        if not path:
            return
        try:
            camera.StopGrabbing()
            pylon.FeaturePersistence.Load(path, camera.GetNodeMap(), True)
            self._read_fps(camera)
            camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
            self._post(self._set_conn_status, f"Loaded {os.path.basename(path)}")
        except Exception as e:
            self._post(self._set_conn_status, f".pfs reload failed: {e}")

    @staticmethod
    def _orient(frame):
        """Apply the configured orientation so frames match the rig schematic.

        Done once at the source, so preview and recording stay consistent. Note
        rot90_* swap width/height; rot180/mirror/flip keep dimensions.
        """
        if ORIENTATION == "rot180":
            return cv2.rotate(frame, cv2.ROTATE_180)
        if ORIENTATION == "mirror_h":
            return cv2.flip(frame, 1)
        if ORIENTATION == "flip_v":
            return cv2.flip(frame, 0)
        if ORIENTATION == "rot90_cw":
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if ORIENTATION == "rot90_ccw":
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    @staticmethod
    def _safe_block_id(grab) -> Optional[int]:
        try:
            return int(grab.BlockID)
        except Exception:
            return None

    @staticmethod
    def _safe_timestamp(grab):
        try:
            return int(grab.TimeStamp)
        except Exception:
            return None

    @staticmethod
    def _safe_stop_grabbing(camera) -> None:
        try:
            if camera is not None and camera.IsGrabbing():
                camera.StopGrabbing()
        except Exception:
            pass

    @staticmethod
    def _safe_close_camera(camera) -> None:
        try:
            if camera is not None and camera.IsOpen():
                camera.Close()
        except Exception:
            pass
