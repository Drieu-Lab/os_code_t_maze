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
        serial/  serial_<session_id>.csv    (complete raw serial - see below)
        config/  the definitions that shaped the run (zones/ROI/landmarks/scale,
                 phase3.json/rules.json, lens calibration, active model) + a
                 runtime_params.json + config_snapshot.json index - so the
                 session is reproducible even after the live host/*.json are
                 re-drawn (docs/decisions/0072)
        summary/ session_summary_<session_id>.{json,txt}  (behavioral summary,
                 written on Stop - docs/decisions/0071)

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
  - opens a per-session raw-serial file in ``serial/`` holding the **complete**
    verbatim stream: while the session is active it appends EVERY line it sees via
    ``apply()`` (body-beam + all lick edges, reward/solenoid acks, cue echoes,
    MODE/handshake, heartbeat), stamped with the host RECEIPT wall clock
    (docs/decisions/0175) in the ``rig_logger`` column format. This is the bundle's
    own full serial log (docs/decisions/0178, superseding the ADR-0049 filtered
    subset); the ``session_serial`` predicate is kept only as a reader-side helper
    for consumers that want the old sensor/central view;
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
import queue
import shutil
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable, Optional

from tmaze.core import disk_space
from tmaze.core.records import animal_state
from tmaze.core.records import animals
from tmaze.core.records import daily_log
from tmaze.core.records import note_tags
from tmaze.core.phase3 import phase3_config
from tmaze.core.phase3 import phase3_stats
from tmaze.core import protocol
from tmaze.core.reward.reward_calibration import KNOWN_LIQUIDS, liquid_label
from tmaze.core.session import session_config
from tmaze.core.session import session_meta
from tmaze.core.session import session_notes
from tmaze.core.session import session_summary
from tmaze.core import provenance
from tmaze.core.timeutil import now_iso
from tmaze.core.session.session_log import SessionManifest, clock_snapshot, path_within_session
from tmaze.core.session.session_serial import capture_filename
from tmaze.core.session.session_timer import SessionTimer
from tmaze import paths
from tmaze.paths import host_dir, phase3_recordings_root

# Fallback recordings root if experiment_control doesn't pass one; normally the
# GUI passes <camera default>/phase3_recordings (see docs/decisions/0049).
# Single-sourced from tmaze.paths so this fallback and the rig's real output
# agree (env-overridable via TMAZE_RECORDINGS_DIR; see docs/decisions/0088).
DEFAULT_RECORDINGS_ROOT = str(phase3_recordings_root())

# Per-session subfolders (one per stream family).
VIDEO_SUBDIR = "video"
TASK_SUBDIR = "task"
SERIAL_SUBDIR = "serial"
# End-of-session behavioral summary (JSON + text) folder (docs/decisions/0071).
SUMMARY_SUBDIR = "summary"
# Per-session config snapshot (geometry/task/calibration + runtime params) folder
# so the run is reproducible after the live host/*.json change (docs/decisions/0072).
CONFIG_SUBDIR = "config"

# Operator note streams (docs/decisions/0074). The per-session note log lives in
# the summary/ folder as JSON Lines and is registered under this manifest key;
# the user-defined tag vocabulary persists next to the other host/*.json config.
NOTES_STREAM_KEY = "notes_events"
NOTE_TAGS_PATH = os.path.join(str(host_dir()), "note_tags.json")

# Pre-session animal-state stream (docs/decisions/0077). Written into the bundle's
# config/ folder at Start as its OWN manifest stream (not via config_snapshot),
# following the notes-stream precedent.
ANIMAL_STATE_STREAM_KEY = "animal_state"
ANIMAL_STATE_FILENAME = "animal_state.json"

# Sticky per-session metadata (docs/decisions/0079). The per-session record is
# written into the bundle's config/ folder at Start as its own manifest stream
# (like animal_state); the sticky trainer/reward defaults persist next to the
# other host/*.json config (same pattern as note_tags.json).
SESSION_META_STREAM_KEY = "session_meta"
SESSION_META_FILENAME = "session_meta.json"
SESSION_META_DEFAULTS_PATH = os.path.join(str(host_dir()), "session_meta.json")
# Sub-stage choices offered as the (optional) Stage override; blank keeps the
# auto-derived phase. Free-text is still allowed via the combobox.
STAGE_OVERRIDE_CHOICES = ("", "Habituation", "Shaping", "Reversal", "Probe")

# How often (ms) to re-check each recorder for its resolved output path while a
# session is active. The camera path resolves immediately; the Phase 3 file only
# once its gate opens (Free Roam + tracking + a zone map), so we poll for it.
RESOLVE_INTERVAL_MS = 500

# How often (ms) to re-measure free space on the recordings drive for the Session
# panel's disk readout. Slow on purpose - disk space drifts over minutes, not
# frames - and it runs even when no session is active so the operator sees a
# low-space warning before pressing Start (docs/decisions/0151). The warning
# threshold itself is operator-configurable and persisted (paths.low_disk_warn_gb).
DISK_POLL_INTERVAL_MS = 30000
# Fast poll that mirrors the ADR-0190 session cap onto the Start/Stop button
# (docs/decisions/0190): the operator watching the Rig view is told WHERE to
# click. Cheap - it only reconfigures the button on a state change.
CAP_ALERT_POLL_MS = 500

# Foreground colors for the disk readout: red when at/below the warning threshold,
# muted grey otherwise - matching the Records Manager welfare-flag palette.
_DISK_FG_LOW = "#9C0006"
_DISK_FG_OK = "#555"

# Map a recorded video extension to the codec label recorded in the manifest.
_CODEC_BY_EXT = {".mkv": "ffv1", ".mp4": "h264"}


def _hms(iso: Optional[str]) -> str:
    """Time-of-day (HH:MM:SS) from an ISO stamp for the notes list; the raw value
    if it can't be parsed."""
    if not iso:
        return "--:--:--"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(iso)


class SessionControlView:
    """Tk module: the Session panel - one-click start/stop + a shared manifest
    and a per-session folder tree (docs/decisions/0048, 0049)."""

    def __init__(self, parent: tk.Misc, camera=None, phase3=None, send=None,
                 recordings_root: Optional[str] = None,
                 animal_id: Optional[Callable[[], str]] = None,
                 behavior_summary: Optional[Callable[[], Any]] = None,
                 config_inputs: Optional[Callable[[], dict]] = None,
                 ask_weight: Optional[Callable[[str], Optional[float]]] = None,
                 dispatcher=None):
        self._parent = parent
        self._camera = camera
        self._phase3 = phase3
        self._send = send                 # callable(str) -> None (RigClient.send)
        # Shared reward dispatcher (reward_dispatcher.RewardDispatcher). The panel's
        # "Reward" field names the liquid in the line; we push it here as the active
        # liquid so per-fire volume accounting picks the matching calibration curve
        # (docs/decisions/0091). None -> the field stays a pure log label.
        self._dispatcher = dispatcher
        self._recordings_root = recordings_root or DEFAULT_RECORDINGS_ROOT
        # Provider of the runtime bits the config snapshot can't read from a file:
        # ``{"files": {name: path}, "params": {...}}`` - extra files to copy (lens
        # calibration, active model) + live scalars (camera transform, tracker /
        # maze params). None -> the snapshot captures only the host/*.json files
        # (docs/decisions/0072).
        self._config_inputs = config_inputs
        # Provider of the behavior panel's Phase 3 locomotor/compliance snapshot
        # (behavior_stats.BehaviorSnapshot) for the end-of-session summary written
        # on Stop (docs/decisions/0071); None -> the summary omits that group.
        self._behavior_summary = behavior_summary
        # The single host-side animal-id source (the Task tab's shared field;
        # docs/decisions/0053). None -> rely on the ANIMAL-handshake fallback.
        self._animal_id = animal_id
        # How Start asks for the pre-session weight when the daily table has no row
        # (takes the animal id, returns grams or None to skip). Defaults to the
        # operator-facing modal; a headless caller/test injects a non-interactive
        # one so Start never blocks on a dialog (docs/decisions/0083).
        self._ask_weight = ask_weight
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
        # Disk-space readout (docs/decisions/0151): a StringVar for the label text,
        # the label widget (so the poll can repaint its color), and the pending
        # after() id so close() can cancel the standalone poll loop.
        self._disk_var = tk.StringVar(value="Disk: ...")
        self._disk_label: Optional[ttk.Label] = None
        self._disk_after: Optional[str] = None
        # Session-cap alert on the Start/Stop button (docs/decisions/0190): the
        # after() id for the fast poll, and whether the button is currently in its
        # red "cap reached" state (so the poll only reconfigures on a change).
        self._cap_after: Optional[str] = None
        self._cap_btn_alert = False
        self._learned_animal = ""         # last animal id seen on the serial stream
        # Last firmware serial protocol version seen on the 0,PROTO handshake.
        # Recorded into the bundle as provenance - it fixes the S/L state
        # polarity convention (proto >= 8 corrected; see docs/decisions/0164).
        self._firmware_proto: Optional[int] = None
        # Firmware build identity from the VERSION reply (protocol v9,
        # docs/decisions/0193): the git short SHA compiled in + the build stamp,
        # so the bundle records which firmware BUILD produced it. The host requests
        # VERSION at Session Start (after PING); both stay None until a reply is
        # seen (pre-v9 firmware never sends one).
        self._firmware_git_sha: Optional[str] = None
        self._firmware_build: Optional[str] = None
        # Canonical animal-id list (resources repo), loaded best-effort once so
        # Session Start can normalize/validate the entered id (docs/decisions/0075).
        # An empty (unavailable) registry when the resources repo is absent.
        self._animals = animals.load_registry()
        # Open handle for the per-session filtered serial file (None when idle).
        self._serial_file = None
        self._serial_path: Optional[str] = None
        # Pulse widths (ms) of the OPERATOR GUI-button reward dispenses (the
        # "Solenoid test" reward spout) fired while a session is active; totalled
        # into the summary's manual_water on Stop. Collected at the source via
        # note_operator_reward() rather than parsed from the wire, because the
        # firmware echoes task and operator fires identically as REWARD,MANUAL, so
        # the wire cannot tell them apart (docs/decisions/0084, supersedes 0078).
        # Reset at each Start.
        self._operator_pulses_ms: list = []
        # Operator notes for the current session as timestamped, editable entries
        # (docs/decisions/0074). The log buffers entries even before Start (so a
        # note jotted while setting up is not lost); on Start it is bound to a
        # summary/notes_<id>.jsonl file, rewritten atomically on every change, and
        # folded from disk into the summary on Stop.
        self._notelog = session_notes.NoteLog()
        self._notes_path: Optional[str] = None
        # Per-file write-failure tracking (docs/decisions/0174), the session-shell
        # twin of Phase 3's ADR-0121 surface: a slot ("manifest"/"serial"/"summary"
        # /"notes"/"animal_state"/"session_meta") maps to the basename of a bundle
        # file whose last atomic write FAILED, so the status line can warn - by
        # name - that data is not on disk, instead of the old silent ``pass``.
        self._write_errors: dict = {}
        # The base status text (without the write-failure prefix) so the periodic
        # poll can re-render the warning over it without losing the live message.
        self._status_base: str = ""
        # User-defined intervention-tag vocabulary, persisted across sessions in
        # host/note_tags.json (docs/decisions/0074).
        self._tag_vocab = note_tags.load_tags(NOTE_TAGS_PATH)
        # The open notes popup (Toplevel), if any, so a second click focuses the
        # existing one instead of stacking duplicates.
        self._notes_win: Optional[tk.Toplevel] = None
        # Auto-event seam (docs/decisions/0111): Phase 3's trial-control overrides
        # (debias / de-alternation block edges, Back / Skip / Repeat) fire on its
        # ACTUATION thread, so they hand us intervention events through this
        # thread-safe queue rather than touching the note log directly. We drain it
        # on the Tk-thread session poll (_resolve_paths) and fold each into the note
        # log as an ``auto:`` NoteEntry. Manual rewards are handled at their own Tk
        # seam (note_operator_reward), so they don't go through this queue.
        self._intervention_q: "queue.Queue[dict]" = queue.Queue()
        if self._phase3 is not None and hasattr(self._phase3,
                                                 "set_intervention_sink"):
            try:
                self._phase3.set_intervention_sink(self._on_intervention_event)
            except Exception:
                pass

        # Sticky per-session metadata: trainer + reward type are set-and-forget,
        # remembered across sessions in host/session_meta.json; the stage is
        # auto-derived from the active phase with an optional override (ADR-0079).
        meta_defaults = session_meta.load_defaults(SESSION_META_DEFAULTS_PATH)
        self._trainer_var = tk.StringVar(value=meta_defaults.trainer)
        self._reward_type_var = tk.StringVar(value=meta_defaults.reward_type)
        self._stage_var = tk.StringVar(value=self._derive_stage())
        self._stage_override_var = tk.StringVar(value="")
        # Keep the dispatcher's active liquid in sync with the "Reward" field, live
        # (on every edit) and from the restored sticky value (docs/decisions/0091).
        self._reward_type_var.trace_add("write", self._apply_active_liquid)
        self._apply_active_liquid()

        self._build_ui()
        # Standalone slow poll of the recordings drive's free space; runs whether
        # or not a session is active (docs/decisions/0151).
        self._schedule_disk_poll()
        # Fast poll mirroring the ADR-0190 session cap onto the Stop button so an
        # operator watching the Rig view is told to Stop (docs/decisions/0190).
        self._schedule_cap_poll()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def session_timing(self):
        """Immutable Start/Stop wall-clock span (session_timer.SessionTimerSnapshot)
        for the Phase 3 stats panel's session clock (docs/decisions/0060). Runs on
        the Tk thread like the rest of this panel, so no locking is needed."""
        return self._session_timer.snapshot(time.time())

    def apply(self, info) -> None:
        """Two jobs per serial line: (1) record handshake provenance - the last
        animal id seen on the ``ANIMAL`` handshake as a fallback (the Task tab's
        field is the primary source now; docs/decisions/0053) and the firmware
        serial protocol version from ``0,PROTO,<n>`` (stamped into the bundle to
        fix the S/L polarity convention; docs/decisions/0164) - and (2) while a
        session is active,
        append the SENSOR + CENTRAL-PORT lines to this session's filtered serial
        file (docs/decisions/0049). Reads the pre-parsed protocol.LineInfo
        (docs/decisions/0056). Runs on the Tk thread (the sole writer of that
        file), so no locking is needed.

        Note: reward water is NOT collected from the wire here. The firmware
        echoes both task and operator fires as REWARD,MANUAL (they are the same
        R<port>1,<ms> command), so the wire cannot attribute them; operator feeds
        are collected at their source instead (see note_operator_reward,
        docs/decisions/0084)."""
        if info.animal:
            self._learned_animal = info.animal
        proto = protocol.proto_version_from(info.parsed)
        if proto is not None:
            self._firmware_proto = proto
        # VERSION reply (proto v9): record the firmware build identity for provenance.
        ver = protocol.version_info_from(info.parsed)
        if ver is not None:
            vproto, vsha, vbuild = ver
            if self._firmware_proto is None:
                self._firmware_proto = vproto
            self._firmware_git_sha = vsha
            self._firmware_build = vbuild
            # The reply lands a few ms after the VERSION we send at Start, i.e.
            # after the manifest's provenance was already collected/written; fold
            # it in and rewrite so the persisted manifest carries the build id.
            self._backfill_firmware_provenance()
        # Capture the COMPLETE verbatim stream between Start and Stop into the
        # bundle (docs/decisions/0178): every line the session sees, not the old
        # sensor + central-port subset. The full stream keeps the cue echoes,
        # MODE/handshake and heartbeat an offline reader needs and costs only ~KB
        # per session over the filtered set. Stamped at host receipt (ADR-0175).
        if self._active and self._serial_file is not None and info.raw:
            self._write_serial(info.raw, recv_wall=info.recv_wall)

    def _apply_active_liquid(self, *_args) -> None:
        """Push the "Reward" field's liquid to the shared dispatcher so per-fire
        volume accounting uses the matching calibration curve (docs/decisions/0091).

        Called on every field edit (via a trace) and on Start. The dispatcher
        normalizes the free text; an unknown name falls back safely in
        reward_volume. A no-op with no dispatcher wired (headless/standalone)."""
        if self._dispatcher is None:
            return
        try:
            self._dispatcher.set_active_liquid(self._reward_type_var.get())
        except Exception:
            pass

    def note_operator_reward(self, ms: float) -> None:
        """Record one OPERATOR reward dispense (the "Solenoid test" reward-spout
        button) fired at its pulse width ``ms``, so the summary's manual_water
        totals only operator-triggered water - disjoint from and additive with
        the task estimate (docs/decisions/0084).

        Attributing at the source (the button handler calls this) is what fixes
        the old double-count: task and operator fires are the same R<port>1,<ms>
        command and echo identically as REWARD,MANUAL on the wire, so they can
        only be told apart here. A no-op unless a session is active. Runs on the
        Tk thread like the button handler, so no locking is needed.

        Also drops an ``auto:manual_reward`` marker on the note timeline (source=
        "auto"; docs/decisions/0111), so each manual boost is recoverable with its
        timestamp + trial linkage - distinct from and additive with the manual_water
        volume total (which is what _operator_pulses_ms feeds; ADR-0084)."""
        if self._active:
            self._operator_pulses_ms.append(float(ms))
            self._add_auto_note(session_notes.AUTO_MANUAL_REWARD,
                                f"manual reward {float(ms):.0f} ms")

    def close(self) -> None:
        self._closed = True
        if self._resolve_after is not None:
            try:
                self.frame.after_cancel(self._resolve_after)
            except tk.TclError:
                pass
            self._resolve_after = None
        if self._disk_after is not None:
            try:
                self.frame.after_cancel(self._disk_after)
            except tk.TclError:
                pass
            self._disk_after = None
        if self._cap_after is not None:
            try:
                self.frame.after_cancel(self._cap_after)
            except tk.TclError:
                pass
            self._cap_after = None
        if self._active:
            self._stop_session()
        self._close_serial()

    # -------------------------------------------------------- disk-space UI
    def _recordings_drive_path(self) -> str:
        """The nearest EXISTING folder at/above the recordings root, so
        ``shutil.disk_usage`` can measure the drive even before the session tree
        is created. Walks up parents; falls back to the drive root."""
        path = self._recordings_root
        while path and not os.path.isdir(path):
            parent = os.path.dirname(path)
            if not parent or parent == path:
                break
            path = parent
        return path or os.path.abspath(os.sep)

    def _update_disk_free(self) -> None:
        """Measure free space on the recordings drive and paint the readout red
        below the operator's threshold (docs/decisions/0151). Best-effort: any
        measurement error shows as "unknown" rather than crashing the panel."""
        try:
            free_bytes: Optional[int] = shutil.disk_usage(
                self._recordings_drive_path()).free
        except OSError:
            free_bytes = None
        status = disk_space.disk_status(free_bytes, paths.low_disk_warn_gb())
        self._disk_var.set(status.text)
        if self._disk_label is not None:
            try:
                self._disk_label.config(
                    foreground=_DISK_FG_LOW if status.is_low else _DISK_FG_OK)
            except tk.TclError:
                pass

    def _schedule_disk_poll(self) -> None:
        """Self-rescheduling slow poll of the recordings drive's free space; runs
        regardless of session state until the panel is closed."""
        if self._closed:
            return
        try:
            self._update_disk_free()
        finally:
            try:
                self._disk_after = self.frame.after(
                    DISK_POLL_INTERVAL_MS, self._schedule_disk_poll)
            except tk.TclError:
                self._disk_after = None

    def _schedule_cap_poll(self) -> None:
        """Self-rescheduling fast poll that mirrors the ADR-0190 session cap onto
        the Start/Stop button (docs/decisions/0190). Runs until the panel closes."""
        if self._closed:
            return
        try:
            self._update_cap_alert()
        finally:
            try:
                self._cap_after = self.frame.after(
                    CAP_ALERT_POLL_MS, self._schedule_cap_poll)
            except tk.TclError:
                self._cap_after = None

    def _update_cap_alert(self) -> None:
        """Turn the Start/Stop button red + loud while a session is active AND the
        Phase 3 cap has fired (docs/decisions/0190); restore it otherwise. Only
        reconfigures on a state change (idempotent per tick). The Rig-view banner
        does the same off the published snapshot; this points at the button to
        click. Reads the controller's cap_reached() directly - the panel already
        holds the controller ref - and is null-safe when Phase 3 is absent."""
        capped = bool(
            self._active and self._phase3 is not None
            and hasattr(self._phase3, "cap_reached")
            and self._phase3.cap_reached())
        if capped == self._cap_btn_alert:
            return
        self._cap_btn_alert = capped
        try:
            if capped:
                self._start_btn.configure(
                    text="STOP SESSION - cap reached", style="Cap.TButton")
            else:
                # Back to the normal label for the current phase + default style.
                self._start_btn.configure(
                    text="Stop session" if self._active else "Start session",
                    style="TButton")
        except tk.TclError:
            pass

    def _open_storage_dialog(self) -> None:
        """Popup to view/change the recordings base folder AND the low-disk
        warning threshold, both persisted in rig_prefs.json (docs/decisions/0151).
        A folder change is refused mid-session (bundles are being written); a
        threshold change is always allowed."""
        dlg = tk.Toplevel(self.frame)
        dlg.title("Storage")
        dlg.transient(self.frame.winfo_toplevel())

        # Current base folder is the single source of truth (paths.camera_output_dir);
        # session bundles live under <base>/phase3_recordings (ADR-0088/0049).
        base_var = tk.StringVar(value=str(paths.camera_output_dir()))
        warn_var = tk.StringVar(value=f"{paths.low_disk_warn_gb():g}")

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(dlg)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Recordings folder (base):").grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)
        ttk.Entry(frm, textvariable=base_var, width=48).grid(
            row=1, column=0, columnspan=2, sticky="we", padx=8)

        def _browse() -> None:
            chosen = filedialog.askdirectory(
                parent=dlg, initialdir=base_var.get() or None,
                title="Choose recordings base folder")
            if chosen:
                base_var.set(chosen)

        ttk.Button(frm, text="Browse...", command=_browse).grid(
            row=1, column=2, padx=(4, 8))
        ttk.Label(frm, text="Session bundles are written under "
                            "<folder>\\phase3_recordings.",
                  font=("TkDefaultFont", 8), foreground="#777").grid(
            row=2, column=0, columnspan=3, sticky="w", **pad)

        ttk.Separator(frm, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="we", padx=8, pady=(2, 2))

        ttk.Label(frm, text="Warn when free space below (GB):").grid(
            row=4, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=warn_var, width=8).grid(
            row=4, column=1, sticky="w", padx=(0, 8))

        note = ("A folder change applies to the NEXT session (this session keeps "
                "writing where it started) and fully to camera/tools on the next "
                "launch.")
        ttk.Label(frm, text=note, font=("TkDefaultFont", 8), foreground="#777",
                  wraplength=380, justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", **pad)

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, sticky="e", **pad)

        def _save() -> None:
            # Threshold first (always allowed, even mid-session).
            raw = warn_var.get().strip()
            try:
                warn_gb = float(raw)
                if warn_gb <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "Storage", "Enter a positive number of GB for the warning "
                    "threshold.", parent=dlg)
                return
            paths.set_low_disk_warn_gb(warn_gb)

            # Folder change: only if it actually changed, and never mid-session.
            new_base = base_var.get().strip()
            old_base = str(paths.camera_output_dir())
            if new_base and os.path.normcase(os.path.normpath(new_base)) != \
                    os.path.normcase(os.path.normpath(old_base)):
                if self._active:
                    messagebox.showwarning(
                        "Storage", "Can't change the recordings folder while a "
                        "session is running. The warning threshold was saved.",
                        parent=dlg)
                    self._update_disk_free()
                    dlg.destroy()
                    return
                paths.set_recordings_base(new_base)
                # Point the live panel at the new base so the NEXT session and the
                # disk readout use it immediately, without a restart.
                self._recordings_root = os.path.join(new_base, "phase3_recordings")
            self._update_disk_free()
            dlg.destroy()

        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side="right", padx=(4, 0))
        ttk.Button(btns, text="Save", command=_save).pack(side="right")

        frm.columnconfigure(0, weight=1)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        try:
            dlg.grab_set()
        except tk.TclError:
            pass

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
        # A red, bold button variant used only while the ADR-0190 session cap has
        # fired (docs/decisions/0190). The reliable signal is the button TEXT; the
        # colour is best-effort (some Windows ttk themes ignore button fill), so
        # both are set together in _update_cap_alert.
        try:
            ttk.Style(root).configure(
                "Cap.TButton", foreground="#b00020",
                font=("TkDefaultFont", 9, "bold"))
        except tk.TclError:
            pass
        ttk.Label(root, text="(animal id from the Task tab)",
                  font=("TkDefaultFont", 8)).grid(
            row=0, column=1, sticky="w", padx=(4, 6), pady=4)
        # A popup for operator notes rather than an inline box: the Rig view is
        # already at the monitor's full height, so the notes UI must add no rows
        # (docs/decisions/0073/0074). Notes are timestamped entries folded into
        # the summary on Stop; the button label shows the running count.
        self._notes_btn = ttk.Button(root, text="Notes...", width=10,
                                     command=self._open_notes_dialog)
        self._notes_btn.grid(row=0, column=2, sticky="e", padx=(4, 6), pady=4)

        self._status_var = tk.StringVar(value="session: off")
        ttk.Label(root, textvariable=self._status_var, anchor="w",
                  wraplength=360, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))

        # Sticky metadata row (trainer / reward type + auto-derived stage with an
        # optional sub-stage override), kept to a single grid row so the
        # full-height Rig view gains minimal height (docs/decisions/0079). The
        # small widgets pack inside a sub-frame; trainer + reward type persist
        # across sessions, stage is re-derived each time.
        meta = ttk.Frame(root)
        meta.grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))
        ttk.Label(meta, text="Trainer",
                  font=("TkDefaultFont", 8)).pack(side="left")
        ttk.Entry(meta, textvariable=self._trainer_var, width=8).pack(
            side="left", padx=(2, 8))
        ttk.Label(meta, text="Reward",
                  font=("TkDefaultFont", 8)).pack(side="left")
        # Editable combobox pre-filled with the known reward liquids, but still
        # free-typeable so any log label is allowed; the value also drives volume
        # accounting via the dispatcher's active liquid (docs/decisions/0091).
        ttk.Combobox(meta, textvariable=self._reward_type_var,
                     values=[liquid_label(t) for t in KNOWN_LIQUIDS],
                     width=12).pack(side="left", padx=(2, 8))
        ttk.Label(meta, text="Stage",
                  font=("TkDefaultFont", 8)).pack(side="left")
        ttk.Label(meta, textvariable=self._stage_var,
                  font=("TkDefaultFont", 8, "italic")).pack(
            side="left", padx=(2, 2))
        ttk.Combobox(meta, textvariable=self._stage_override_var,
                     values=STAGE_OVERRIDE_CHOICES, width=11).pack(
            side="left", padx=(2, 0))

        # Free-space readout for the recordings drive + a Storage... popup to
        # change where bundles are saved and the low-space warning threshold. Kept
        # to one grid row so the full-height Rig view gains minimal height
        # (docs/decisions/0073/0079/0151). The label color flips to red below the
        # threshold; the poll (started in __init__) keeps both current.
        storage = ttk.Frame(root)
        storage.grid(row=3, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 4))
        self._disk_label = ttk.Label(storage, textvariable=self._disk_var,
                                     font=("TkDefaultFont", 8))
        self._disk_label.pack(side="left")
        ttk.Button(storage, text="Storage...", width=10,
                   command=self._open_storage_dialog).pack(side="left", padx=(8, 0))
        self._update_disk_free()

        # Quick-stamp hotkey: mark the moment on the shared timeline without
        # opening the dialog (text can be filled in later). Bound on the toplevel
        # so it fires from anywhere in the app, matching the *_view.py shells'
        # bind() usage (docs/decisions/0074).
        try:
            self.frame.winfo_toplevel().bind(
                "<Control-Shift-N>", self._on_quick_stamp)
        except tk.TclError:
            pass
        self._refresh_notes_button()

    # ------------------------------------------------------------- notes
    # The note log + tag vocabulary are pure cores (session_notes / note_tags,
    # docs/decisions/0074); this shell owns the Tk popup, the timestamps, and the
    # atomic file write. Entries survive an unclean end because every change is
    # flushed to disk immediately (see _persist_notes) and the summary re-reads
    # from disk on Stop (see _write_session_summary).

    def _now_iso(self) -> str:
        # Offset-aware host wall-clock stamp (docs/decisions/0182).
        return now_iso()

    # -------------------------------------------------- write-failure surface
    def _record_write_result(self, slot: str, path: Optional[str],
                             exc: Optional[OSError], *, note: bool = True) -> None:
        """Track an atomic-write attempt for a bundle ``slot`` (docs/decisions/0174),
        the session-shell twin of Phase 3's ADR-0121 surface. A failure records the
        file name so the status line warns it is NOT on disk (was a silent
        ``pass``); a success clears the slot so a transient hiccup that recovers
        stops warning. On a NEW failure (``note`` True) it also appends an
        ``auto:write_failed`` timeline note IN-MEMORY - not force-persisted, so a
        failing NOTES write can't recurse; it reaches disk on the next healthy
        write and folds into the summary regardless. ``serial`` is recorded on the
        serial reader thread, so it passes ``note=False`` (the note log is
        Tk-thread-owned) and relies on the status warning alone."""
        if exc is None:
            self._write_errors.pop(slot, None)
            return
        new_failure = slot not in self._write_errors
        name = os.path.basename(path) if path else slot
        self._write_errors[slot] = name
        if note and new_failure:
            try:
                self._notelog.add_auto(
                    session_notes.AUTO_WRITE_FAILED,
                    f"{slot} write failed ({name}) - not on disk",
                    iso=self._now_iso(), mono=time.monotonic(),
                    trial_index=self._current_trial_index())
            except Exception:
                pass

    def _write_warn(self) -> str:
        """A loud status-line prefix when a bundle file could not be written to
        disk (docs/decisions/0174), else "". Names the files so the operator knows
        what is at risk; shown while the failure persists since it may outlast the
        run - mirrors Phase 3's ``_write_warn``."""
        if not self._write_errors:
            return ""
        files = ", ".join(sorted(set(self._write_errors.values())))
        return f"!! DISK WRITE FAILED: {files} - data not saved !!  "

    def _set_status(self, text: str) -> None:
        """Set the session status line, remembering ``text`` as the base and
        prepending any active write-failure warning (docs/decisions/0174) so the
        warning rides over whatever the live message is."""
        self._status_base = text
        try:
            self._status_var.set(self._write_warn() + text)
        except tk.TclError:
            pass

    def _current_trial_index(self) -> Optional[int]:
        """The 0-based Phase 3 trial in progress right now, or None when no Phase
        3 controller/snapshot is available - stamped onto a new note so it lands
        on the shared behavior/video/neural timeline (docs/decisions/0048/0074)."""
        if self._phase3 is None or not hasattr(self._phase3, "published_state"):
            return None
        try:
            snap = self._phase3.published_state()
        except Exception:
            snap = None
        return snap.current_index if snap is not None else None

    def _add_note(self, text: str, *, tag: Optional[str] = None,
                  count: int = 1):
        """Stamp and append one note entry (iso + monotonic clock + current trial
        index), persist the log to disk, and refresh the button/list. Returns the
        entry. ``text`` may be empty for a bare quick-stamp."""
        entry = self._notelog.add(
            text, iso=self._now_iso(), mono=time.monotonic(),
            trial_index=self._current_trial_index(), tag=tag, count=count)
        self._persist_notes()
        self._refresh_notes_button()
        return entry

    def _add_auto_note(self, tag: str, text: str, *, iso: Optional[str] = None,
                       mono: Optional[float] = None,
                       trial_index: Optional[int] = None):
        """Append one machine-emitted auto event to the note log (source="auto" +
        an ``auto:`` tag), persist, and refresh - the auto counterpart of
        :meth:`_add_note` (docs/decisions/0111). Stamps default to the current Tk
        clocks / trial when a caller (the manual-reward Tk seam) doesn't supply
        them; the actuation-thread seam supplies the event's own stamps."""
        entry = self._notelog.add_auto(
            tag, text,
            iso=iso if iso is not None else self._now_iso(),
            mono=mono if mono is not None else time.monotonic(),
            trial_index=(trial_index if trial_index is not None
                         else self._current_trial_index()))
        self._persist_notes()
        self._refresh_notes_button()
        return entry

    def _on_intervention_event(self, event: dict) -> None:
        """Thread-safe sink handed to Phase 3 (docs/decisions/0111). Called on the
        ACTUATION thread, so it only enqueues; the Tk-thread poll drains it. Never
        touch Tk / the note log here."""
        try:
            self._intervention_q.put_nowait(event)
        except Exception:
            pass

    def _drain_interventions(self) -> None:
        """Fold any queued Phase 3 intervention events into the note log as auto
        entries (docs/decisions/0111). Runs on the Tk thread from _resolve_paths,
        so it is the sole note-log writer alongside the operator's own notes."""
        while True:
            try:
                event = self._intervention_q.get_nowait()
            except queue.Empty:
                break
            tag = event.get("tag")
            if not tag:
                continue
            self._add_auto_note(
                tag, str(event.get("text", "")),
                iso=event.get("iso"), mono=event.get("mono"),
                trial_index=event.get("trial_index"))
            # A firmware reset also lands in the manifest so an offline reader can
            # split the firmware ms timeline at the reboot (docs/decisions/0184).
            if (tag == session_notes.AUTO_FIRMWARE_RESET
                    and self._manifest is not None):
                self._manifest.add_firmware_reset(
                    str(event.get("iso") or ""), event.get("last_fw_ms"))
                self._write_manifest()

    def _persist_notes(self) -> None:
        """Atomically rewrite the whole notes JSONL file (docs/decisions/0074).

        No-op until the log is bound to a session file on Start; entries jotted
        before then stay buffered in memory and are flushed at bind time. A whole-
        file rewrite (over append-only ops) keeps the record consistent under
        edits/deletes. Best-effort: a write failure must never break note-taking."""
        if self._notes_path is None:
            return
        try:
            self._atomic_write(self._notes_path, self._notelog.render_jsonl())
        except OSError as exc:
            # Record (not swallow) so the status line warns the notes file is not
            # on disk (docs/decisions/0174). note=False: _record_write_result would
            # otherwise append a note here and re-enter note handling while the
            # notes write is the very thing failing.
            self._record_write_result("notes", self._notes_path, exc, note=False)
        else:
            self._record_write_result("notes", self._notes_path, None)

    def _refresh_notes_button(self) -> None:
        """Show the running entry count on the Notes button (quiet feedback that
        a stamp landed, without stealing the session status line)."""
        try:
            n = len(self._notelog)
            self._notes_btn.configure(
                text=f"Notes ({n})..." if n else "Notes...")
        except tk.TclError:
            pass

    def _on_quick_stamp(self, _event=None) -> str:
        """Ctrl+Shift+N: append an immediately-timestamped, empty entry (fill in
        the text later) and flash the count. Returns "break" so the key isn't
        also handled elsewhere."""
        self._add_note("")
        if self._notes_win is not None:
            self._refresh_notes_list()
        return "break"

    def _open_notes_dialog(self) -> None:
        """Open (or re-focus) the session-notes popup (docs/decisions/0074).

        Keeps the notes UI off the full-height Rig view. The popup has: a compose
        box + "Stamp note" (append a timestamped entry), one-click tag buttons
        from the user vocabulary (+ "Manage tags..."), and a list of the entries
        so far with Edit / Delete. Closing with non-empty compose text autosaves
        it (no silent discard). Notes can be jotted any time - before or during a
        session - and are folded into the summary on Stop."""
        if self._notes_win is not None:
            try:
                self._notes_win.deiconify()
                self._notes_win.lift()
                self._notes_win.focus_set()
                self._refresh_notes_list()
                return
            except tk.TclError:
                self._notes_win = None

        win = tk.Toplevel(self.frame)
        win.title("Session notes / interventions")
        win.transient(self.frame.winfo_toplevel())
        self._notes_win = win

        ttk.Label(win, text="Note (Ctrl+Shift+N stamps one from anywhere):").pack(
            anchor="w", padx=8, pady=(8, 2))
        compose = tk.Text(win, width=64, height=3, wrap="word")
        compose.pack(fill=tk.X, padx=8, pady=2)
        compose.focus_set()
        self._notes_compose = compose

        def _stamp() -> None:
            txt = compose.get("1.0", "end-1c").strip()
            self._add_note(txt)
            compose.delete("1.0", "end")
            self._refresh_notes_list()
            compose.focus_set()

        ttk.Button(win, text="Stamp note", command=_stamp).pack(
            anchor="w", padx=8, pady=(0, 4))

        # One-click tag row (event tag -> stamped entry; counter tag -> stamped
        # +1 entry) + the vocabulary manager.
        tagbar = ttk.Frame(win)
        tagbar.pack(fill=tk.X, padx=8, pady=(0, 4))
        self._notes_tagbar = tagbar
        self._build_tag_buttons()

        ttk.Separator(win, orient="horizontal").pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(win, text="Entries this session:").pack(
            anchor="w", padx=8, pady=(0, 2))

        listwrap = ttk.Frame(win)
        listwrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=2)
        cols = ("time", "trial", "tag", "text")
        tree = ttk.Treeview(listwrap, columns=cols, show="headings", height=8)
        for col, head, width in (("time", "Time", 90), ("trial", "Trial", 50),
                                 ("tag", "Tag", 120), ("text", "Text", 320)):
            tree.heading(col, text=head)
            tree.column(col, width=width,
                        anchor="w" if col == "text" else "center")
        vsb = ttk.Scrollbar(listwrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        listwrap.rowconfigure(0, weight=1)
        listwrap.columnconfigure(0, weight=1)
        self._notes_tree = tree

        rowbtns = ttk.Frame(win)
        rowbtns.pack(fill=tk.X, padx=8, pady=(2, 8))
        ttk.Button(rowbtns, text="Edit...",
                   command=self._edit_selected_note).pack(side=tk.LEFT)
        ttk.Button(rowbtns, text="Delete",
                   command=self._delete_selected_note).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(rowbtns, text="Manage tags...",
                   command=self._open_tag_manager).pack(side=tk.RIGHT)
        tree.bind("<Double-1>", lambda _e: self._edit_selected_note())

        def _close() -> None:
            # Footgun fix (docs/decisions/0074): don't silently discard typed-but-
            # unstamped text - autosave it as a final entry.
            try:
                leftover = compose.get("1.0", "end-1c").strip()
            except tk.TclError:
                leftover = ""
            if leftover:
                self._add_note(leftover)
            self._notes_win = None
            self._notes_tree = None
            self._notes_compose = None
            self._notes_tagbar = None
            try:
                win.destroy()
            except tk.TclError:
                pass

        win.protocol("WM_DELETE_WINDOW", _close)
        self._refresh_notes_list()

    def _build_tag_buttons(self) -> None:
        """(Re)build the one-click tag buttons from the current vocabulary."""
        bar = getattr(self, "_notes_tagbar", None)
        if bar is None:
            return
        for child in bar.winfo_children():
            child.destroy()
        names = self._tag_vocab.names()
        if not names:
            ttk.Label(bar, text="(no tags yet - use Manage tags...)",
                      font=("TkDefaultFont", 8)).pack(side=tk.LEFT)
            return
        ttk.Label(bar, text="Tags:").pack(side=tk.LEFT, padx=(0, 4))
        for name in names:
            counter = self._tag_vocab.is_counter(name)
            label = f"{name} +1" if counter else name
            ttk.Button(bar, text=label, width=max(6, len(label) + 2),
                       command=lambda n=name: self._apply_tag(n)).pack(
                side=tk.LEFT, padx=2)

    def _apply_tag(self, name: str) -> None:
        """Append a stamped entry labeled with ``name`` (a counter tag records a
        +1 event; the summary tallies presses per tag). Refreshes the list."""
        self._add_note("", tag=name, count=1)
        if self._notes_win is not None:
            self._refresh_notes_list()

    def _refresh_notes_list(self) -> None:
        """Repopulate the entry Treeview from the log (ids kept in the item id)."""
        tree = getattr(self, "_notes_tree", None)
        if tree is None:
            return
        try:
            tree.delete(*tree.get_children())
        except tk.TclError:
            return
        for e in self._notelog.entries():
            trial = "" if e.trial_index is None else str(e.trial_index)
            tag = e.tag or ""
            txt = e.text or ("(stamp)" if not e.tag else "")
            if e.edited_iso:
                txt += "  (edited)"
            tree.insert("", "end", iid=str(e.id),
                        values=(_hms(e.iso), trial, tag, txt))

    def _selected_note_id(self) -> Optional[int]:
        tree = getattr(self, "_notes_tree", None)
        if tree is None:
            return None
        sel = tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except ValueError:
            return None

    def _edit_selected_note(self) -> None:
        """Edit the selected entry's text in a small dialog, preserving its
        original timestamps and flagging it edited (docs/decisions/0074)."""
        entry_id = self._selected_note_id()
        if entry_id is None:
            return
        entry = next((e for e in self._notelog.entries() if e.id == entry_id),
                     None)
        if entry is None:
            return

        dlg = tk.Toplevel(self._notes_win or self.frame)
        dlg.title("Edit note")
        dlg.transient(self._notes_win or self.frame.winfo_toplevel())
        ttk.Label(dlg, text="Note text (original timestamp is kept):").pack(
            anchor="w", padx=8, pady=(8, 2))
        box = tk.Text(dlg, width=60, height=4, wrap="word")
        box.insert("1.0", entry.text)
        box.pack(fill=tk.BOTH, expand=True, padx=8, pady=2)
        box.focus_set()

        def _apply() -> None:
            self._notelog.edit(entry_id, box.get("1.0", "end-1c").strip(),
                               edited_iso=self._now_iso())
            self._persist_notes()
            self._refresh_notes_list()
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        btns = ttk.Frame(dlg)
        btns.pack(fill=tk.X, padx=8, pady=(2, 8))
        ttk.Button(btns, text="Save", command=_apply).pack(side=tk.LEFT)
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side=tk.LEFT, padx=(6, 0))

    def _delete_selected_note(self) -> None:
        entry_id = self._selected_note_id()
        if entry_id is None:
            return
        if self._notelog.delete(entry_id):
            self._persist_notes()
            self._refresh_notes_button()
            self._refresh_notes_list()

    def _open_tag_manager(self) -> None:
        """Add / rename / delete the user-defined tag vocabulary, persisted across
        sessions to host/note_tags.json (docs/decisions/0074)."""
        dlg = tk.Toplevel(self._notes_win or self.frame)
        dlg.title("Manage note tags")
        dlg.transient(self._notes_win or self.frame.winfo_toplevel())

        lst = tk.Listbox(dlg, width=40, height=8)
        lst.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=8, pady=8)

        def _reload_list() -> None:
            lst.delete(0, "end")
            for name in self._tag_vocab.names():
                suffix = "  (counter)" if self._tag_vocab.is_counter(name) else ""
                lst.insert("end", f"{name}{suffix}")

        def _selected_name() -> Optional[str]:
            sel = lst.curselection()
            if not sel:
                return None
            return self._tag_vocab.names()[sel[0]]

        def _save_vocab() -> None:
            try:
                note_tags.save_tags(NOTE_TAGS_PATH, self._tag_vocab)
            except OSError:
                pass
            self._build_tag_buttons()

        name_var = tk.StringVar()
        counter_var = tk.BooleanVar(value=False)
        ttk.Entry(dlg, textvariable=name_var, width=28).grid(
            row=1, column=0, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(dlg, text="counter (+N)", variable=counter_var).grid(
            row=1, column=1, sticky="w", padx=2)

        def _add() -> None:
            try:
                self._tag_vocab.add(name_var.get(), counter=counter_var.get())
            except ValueError as exc:
                messagebox.showwarning("Tag", str(exc), parent=dlg)
                return
            name_var.set("")
            counter_var.set(False)
            _save_vocab()
            _reload_list()

        def _rename() -> None:
            old = _selected_name()
            if old is None:
                return
            new = name_var.get().strip()
            if not new:
                return
            try:
                self._tag_vocab.rename(old, new)
            except (KeyError, ValueError) as exc:
                messagebox.showwarning("Tag", str(exc), parent=dlg)
                return
            name_var.set("")
            _save_vocab()
            _reload_list()

        def _delete() -> None:
            name = _selected_name()
            if name is None:
                return
            self._tag_vocab.delete(name)
            _save_vocab()
            _reload_list()

        ttk.Button(dlg, text="Add", command=_add).grid(
            row=1, column=2, sticky="w", padx=2)
        btns = ttk.Frame(dlg)
        btns.grid(row=2, column=0, columnspan=3, sticky="w", padx=8, pady=(2, 8))
        ttk.Button(btns, text="Rename to entry", command=_rename).pack(
            side=tk.LEFT)
        ttk.Button(btns, text="Delete selected", command=_delete).pack(
            side=tk.LEFT, padx=(6, 0))
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(
            side=tk.LEFT, padx=(6, 0))
        dlg.columnconfigure(0, weight=1)
        dlg.rowconfigure(0, weight=1)
        _reload_list()

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

    def _resolve_animal_id(self, raw: str) -> "tuple[str, Optional[str]]":
        """Normalize the entered animal id against the canonical registry.

        Returns ``(id_for_session, warning_or_None)``: the canonical id when the
        entry is recognized; otherwise the sanitized slug (the old behaviour) plus
        a short non-blocking warning so a typo is caught at capture time. When the
        registry is unavailable (resources repo absent) it can't validate, so it
        just sanitizes without warning. See docs/decisions/0075.
        """
        canonical = self._animals.normalize(raw) if raw else None
        if canonical is not None:
            return canonical, None
        slug = protocol.sanitize_animal_id(raw)
        if not raw:
            return slug, "no animal id set (Task tab)"
        if self._animals.available:
            return slug, f"animal id {raw!r} not in registry (using {slug!r})"
        return slug, None

    def _write_animal_state(self, config_dir: str, animal: str,
                            date: str) -> Optional[str]:
        """Freeze the pre-session animal state into config/animal_state.json and
        register it as its own manifest stream (docs/decisions/0077).

        Prefill order: today's daily-table row (source=daily_table) -> a weight
        typed into a small Start dialog, with the baseline recovered from the
        animal's most recent daily row (source=start_dialog) -> a minimal record
        (source=manual) if the operator skips. The stream is always written so a
        bundle uniformly carries it. Returns a short status note, or None.
        """
        table = daily_log.load()          # best-effort; empty if resources absent
        row = table.for_date(animal, date)
        # Only prefill from the daily table when today's row actually has a
        # weight; a present-but-weightless row falls through to the Start weight
        # dialog rather than freezing an empty pre_session_weight (#4).
        if daily_log.row_has_weight(row):
            # Derive hours-since-water from the most recent parseable water-off
            # across the animal's daily rows (usually the previous evening; the
            # session start is ~now, at Start). ADR-0101 / FW-0029.
            session_start = datetime.now()
            last_off = animal_state.resolve_last_water_off(
                table.rows_for(animal), session_start)
            state = animal_state.from_daily_row(
                self._session_id, animal, date, row,
                session_start=session_start, last_water_off=last_off)
            note = None
        else:
            # The latest row that actually HAS a baseline - a newer row with a
            # blank baseline cell must not hide an older good one (ADR-0155).
            recent = table.latest_for(animal, on_or_before=date,
                                      having="baseline_g")
            baseline = recent.get("baseline_g") if recent else None
            weight = self._ask_pre_session_weight(animal)
            if weight is not None:
                state = animal_state.from_weight(
                    self._session_id, animal, date, weight, baseline_g=baseline)
                note = "animal state: weight entered at Start"
            else:
                state = animal_state.unknown(self._session_id, animal, date)
                note = "animal state: no husbandry data (manual)"

        path = os.path.join(config_dir, ANIMAL_STATE_FILENAME)
        try:
            self._atomic_write(path, state.render())
        except OSError as exc:
            self._record_write_result("animal_state", path, exc)
            return "animal state: write failed"
        self._record_write_result("animal_state", path, None)
        if (self._manifest is not None
                and path_within_session(path, self._session_dir)):
            self._manifest.set_stream(ANIMAL_STATE_STREAM_KEY, path)
        return note

    def _ask_pre_session_weight(self, animal: str) -> Optional[float]:
        """Ask for the pre-session weight (g) when the daily table has no row for
        today. Uses the injected ``ask_weight`` provider if one was given (a
        headless caller/test supplies a non-interactive one so Start never blocks);
        otherwise pops the operator-facing modal. Cancel/blank returns None (the
        operator can skip). Best-effort: any Tk error (e.g. headless) skips it."""
        if self._ask_weight is not None:
            return self._ask_weight(animal)
        try:
            return simpledialog.askfloat(
                "Pre-session weight",
                f"Pre-session weight (g) for {animal}?\n"
                "(No daily-log row for today - Cancel to skip.)",
                parent=self.frame, minvalue=0.0)
        except tk.TclError:
            return None

    def _derive_stage(self) -> str:
        """The training stage auto-derived from the active phase (docs/decisions/
        0079). The Session panel drives the Phase 3 task (Start requires it armed;
        docs/decisions/0060), so a present Phase 3 controller means "Phase 3";
        with none wired (headless/standalone) the stage is left blank for the
        operator's override."""
        return "Phase 3" if self._phase3 is not None else ""

    def _write_session_meta(self, config_dir: str) -> Optional[str]:
        """Write the sticky per-session metadata into config/session_meta.json and
        register it as its own manifest stream (docs/decisions/0079).

        Trainer + reward type come from the Session-panel fields (and are
        remembered across sessions in host/session_meta.json); the stage is
        auto-derived from the active phase unless the operator picked an override.
        Best-effort: a write failure must not break a clean Start. Returns a short
        status note, or None."""
        meta = session_meta.build(
            self._session_id,
            trainer=self._trainer_var.get(),
            reward_type=self._reward_type_var.get(),
            auto_stage=self._derive_stage(),
            stage_override=self._stage_override_var.get())
        path = os.path.join(config_dir, SESSION_META_FILENAME)
        try:
            self._atomic_write(path, meta.render())
        except OSError as exc:
            self._record_write_result("session_meta", path, exc)
            return "session meta: write failed"
        self._record_write_result("session_meta", path, None)
        if (self._manifest is not None
                and path_within_session(path, self._session_dir)):
            self._manifest.set_stream(SESSION_META_STREAM_KEY, path)
        # Remember trainer + reward type for next time (set-and-forget). The stage
        # is deliberately not persisted so a stale override can't mislabel a later
        # session (docs/decisions/0079).
        try:
            session_meta.save_defaults(
                SESSION_META_DEFAULTS_PATH,
                session_meta.SessionMetaDefaults(
                    trainer=meta.trainer, reward_type=meta.reward_type))
        except OSError:
            pass
        return None

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

        # Make sure the dispatcher's active liquid matches the "Reward" field at
        # the moment the session begins (docs/decisions/0091).
        self._apply_active_liquid()

        raw_animal = (self._animal_text() or self._learned_animal or "").strip()
        animal, id_warning = self._resolve_animal_id(raw_animal)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        session_date = time.strftime("%Y-%m-%d")   # husbandry-join key (docs/decisions/0077)
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
        summary_dir = os.path.join(self._session_dir, SUMMARY_SUBDIR)
        config_dir = os.path.join(self._session_dir, CONFIG_SUBDIR)
        for d in (self._video_dir, task_dir, serial_dir, summary_dir, config_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass

        self._manifest = SessionManifest(
            session_id=self._session_id, animal=animal, t0=clock_snapshot())
        self._manifest_path = os.path.join(
            self._session_dir, f"session_{self._session_id}.json")
        # Record what produced this session - code sha, python/package versions,
        # host, firmware proto (declared in rig_config.h + last seen on the wire).
        # P1/P6, docs/decisions/0180. Guarded inside collect(), so it never blocks
        # Start; firmware_proto_seen is "unknown" until a handshake was observed.
        prov = provenance.collect(firmware_proto_seen=self._firmware_proto,
                                  firmware_git_sha=self._firmware_git_sha,
                                  firmware_build=self._firmware_build)
        # Fold in the resolved per-animal cue->side mapping + its source (ADR-0186),
        # so the manifest is the single top-level provenance home for it too.
        if self._phase3 is not None and hasattr(self._phase3, "resolved_cue_mapping"):
            try:
                prov["cue_mapping"] = self._phase3.resolved_cue_mapping()
            except Exception:
                pass
        # ...and the session cap the run is armed under (docs/decisions/0190), so
        # a bundle says how many opportunities the rat had without opening the
        # archived config.
        if self._phase3 is not None and hasattr(self._phase3, "resolved_session_cap"):
            try:
                prov["session_cap"] = self._phase3.resolved_session_cap()
            except Exception:
                pass
        # ...and how the reward volume was sized for this animal
        # (docs/decisions/0191): the armed pulse width + its source.
        if self._phase3 is not None and hasattr(self._phase3, "resolved_reward_policy"):
            try:
                prov["reward_policy"] = self._phase3.resolved_reward_policy()
            except Exception:
                pass
        self._manifest.set_provenance(prov)

        # Bind the note log to this session's on-disk file and flush whatever the
        # operator jotted before Start (footgun fix: do NOT wipe pre-Start notes;
        # docs/decisions/0074). Registered as a first-class manifest stream so it
        # is discoverable alongside serial/video/task and survives an unclean end.
        self._notes_path = os.path.join(
            summary_dir, f"notes_{self._session_id}.jsonl")
        self._persist_notes()
        if path_within_session(self._notes_path, self._session_dir):
            self._manifest.set_stream(NOTES_STREAM_KEY, self._notes_path)

        notes = []
        # Surface an unknown/blank animal id at capture time (non-blocking): the
        # session still starts (the rig must keep running), but the operator sees
        # it in the status line so a typo doesn't quietly mint an orphan bundle
        # (docs/decisions/0075).
        if id_warning:
            notes.append(id_warning)
        # Freeze the pre-session animal state into the bundle as its own manifest
        # stream: prefill from the daily husbandry table, else prompt for a weight,
        # else write a minimal record so the stream always exists (ADR-0077).
        state_note = self._write_animal_state(config_dir, animal, session_date)
        if state_note:
            notes.append(state_note)
        # Capture the sticky human-side metadata (trainer/stage/reward type) into
        # the bundle as its own stream, and remember trainer/reward for next time
        # (ADR-0079).
        meta_note = self._write_session_meta(config_dir)
        if meta_note:
            notes.append(meta_note)
        # Open the per-session serial file: the COMPLETE verbatim stream received
        # between Start and Stop (docs/decisions/0178), so the bundle carries its
        # own full serial log rather than pointing at the separate rig_logger.py
        # file. Its path is recorded even if the open fails (it just stays empty).
        self._serial_path = os.path.join(serial_dir, capture_filename(self._session_id))
        if not self._open_serial(self._serial_path):
            notes.append("serial capture unavailable")
        self._manifest.set_stream("serial_full", self._serial_path)

        cam_started = self._camera_start(self._video_dir)
        if not cam_started:
            notes.append("camera not recording")
        # Phase 3 is already armed (guarded above); just hand it the session id +
        # task/ folder so its data file correlates with the video + manifest - do
        # NOT arm it here (docs/decisions/0060).
        if self._phase3 is not None and hasattr(self._phase3, "set_session_context"):
            self._phase3.set_session_context(
                self._session_id, out_dir=task_dir, session_t0=self._manifest.t0)
        else:
            notes.append("Phase 3 unavailable")

        # Save-on-arm (P2, ADR-0167): persist the Phase 3 tab's live GUI-editable
        # config to host/phase3.json BEFORE the snapshot below, so the archived
        # config/phase3.json reflects EXACTLY what this armed session runs with -
        # not just what was last hand-Saved. Best-effort: a note, never a break.
        if self._phase3 is not None and hasattr(self._phase3, "persist_live_config"):
            cfg_note = self._phase3.persist_live_config()
            if cfg_note:
                notes.append(cfg_note)

        # Snapshot the definitions/config that shaped this run into config/ so it
        # stays reproducible after the live host/*.json are re-drawn (ADR-0072).
        if not self._write_config_snapshot(config_dir):
            notes.append("config snapshot incomplete")

        self._mark_session_start(self._manifest.t0)
        self._write_manifest()

        # Fresh accounting of the OPERATOR reward dispenses for this session;
        # note_operator_reward() accumulates their pulse widths at the source (the
        # Solenoid-test button) and the summary totals them into manual_water on
        # Stop (docs/decisions/0084).
        self._operator_pulses_ms = []
        self._active = True
        self._start_btn.configure(text="Stop session")
        self._resolve_paths()   # first pass + schedules the poll
        extra = f"  [{'; '.join(notes)}]" if notes else ""
        self._set_status(f"ACTIVE  {self._session_id}{extra}")

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
            # Build + write the behavioral summary first, so the manifest it writes
            # next carries the summary file pointers (docs/decisions/0071).
            self._write_session_summary()
            self._write_manifest()
        self._close_serial()
        # Release Phase 3's session context (it stays armed and running - ADR-0060).
        # Otherwise a re-arm between animals - the gate closing and reopening when
        # tracking is toggled - would mint its data file inside this now-closed
        # session's task/ folder (docs/decisions/0066).
        if self._phase3 is not None and hasattr(self._phase3, "set_session_context"):
            self._phase3.set_session_context()

        # Reset the note log for the next session: the just-ended session's notes
        # are already folded into its summary + persisted to its own file, so start
        # fresh (notes jotted in the idle gap before the next Start are then carried
        # in by the bind-and-flush there; docs/decisions/0074).
        self._notelog = session_notes.NoteLog()
        self._notes_path = None
        self._refresh_notes_button()
        self._refresh_notes_list()

        self._active = False
        try:
            self._start_btn.configure(text="Start session")
        except tk.TclError:
            pass
        # Repeat any write-failure warning at Stop (docs/decisions/0174) so a disk
        # fault that struck during the run is not lost when the status changes; the
        # session still closes cleanly. _set_status prepends _write_warn().
        self._set_status(f"session ended: {self._session_id}")

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
            # Ask the firmware to identify its build (proto v9, docs/decisions/0193);
            # the reply lands on the stream a few ms later and is folded into the
            # manifest provenance by _backfill_firmware_provenance. Harmless no-op on
            # pre-v9 firmware (unknown command -> no reply).
            try:
                self._send("VERSION")
            except Exception:
                pass

    def _backfill_firmware_provenance(self) -> None:
        """Fold the firmware build identity (git SHA + build stamp from the VERSION
        reply) into the manifest provenance and rewrite it.

        The host sends ``VERSION`` at Session Start (in ``_mark_session_start``),
        but the reply arrives on the serial stream a few ms later - after the
        manifest's ``provenance`` block was already collected and first written. So
        when the reply is captured we patch the persisted manifest. No-op before a
        manifest/provenance exists, or when nothing changed (pre-v9 firmware never
        replies, so the fields stay ``"unknown"``). see docs/decisions/0193
        """
        m = self._manifest
        if m is None or not getattr(m, "provenance", None):
            return
        prov = dict(m.provenance)
        sha = self._firmware_git_sha or "unknown"
        build = self._firmware_build or "unknown"
        changed = False
        if prov.get("firmware_git_sha") != sha:
            prov["firmware_git_sha"] = sha
            changed = True
        if prov.get("firmware_build") != build:
            prov["firmware_build"] = build
            changed = True
        if (prov.get("firmware_proto_seen") in (None, "unknown")
                and self._firmware_proto is not None):
            prov["firmware_proto_seen"] = self._firmware_proto
            changed = True
        if changed:
            m.set_provenance(prov)
            self._write_manifest()

    # ------------------------------------------------------- path resolution
    def _resolve_paths(self, reschedule: bool = True) -> None:
        """Fill in each recorder's output path as it becomes available, rewriting
        the manifest when something changes; re-poll while the session is active."""
        if self._manifest is None:
            return
        # Fold any Phase 3 intervention events queued since the last poll into the
        # note log (docs/decisions/0111) - this poll is the Tk-thread drain point.
        self._drain_interventions()
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

        # The per-excursion event log lives beside the trial CSV in task/ (ADR-0109);
        # register it the same way, guarded by the same within-session check so a
        # pre-rebind path (previous session / host-data fallback) is never recorded.
        p3ex = self._phase3_excursion_path()
        if p3ex is not None and not path_within_session(p3ex, self._session_dir or ""):
            p3ex = None
        if p3ex and self._manifest.streams.get("phase3_excursions") != p3ex:
            self._manifest.set_stream("phase3_excursions", p3ex)
            changed = True

        if changed:
            self._write_manifest()

        # Keep the write-failure warning current over the live status line
        # (docs/decisions/0174): a manifest / notes / serial write may have failed
        # since the last poll, so re-render the ACTIVE status with the warning
        # prefixed while any slot is failing.
        if self._write_errors and self._status_base:
            self._set_status(self._status_base)

        if reschedule and self._active and not self._closed:
            try:
                self._resolve_after = self.frame.after(
                    RESOLVE_INTERVAL_MS, self._resolve_paths)
            except tk.TclError:
                self._resolve_after = None

    def _camera_start(self, out_dir: str) -> bool:
        if self._camera is None or not hasattr(self._camera, "start_recording"):
            return False
        t0 = self._manifest.t0 if self._manifest is not None else None
        try:
            # Pass the canonical session anchor so the sidecar header self-anchors
            # to the one shared instant (docs/decisions/0124). Older camera views
            # without the parameter still work (fall back to no anchor).
            try:
                return bool(self._camera.start_recording(
                    self._session_id, out_dir=out_dir, session_t0=t0))
            except TypeError:
                return bool(self._camera.start_recording(
                    self._session_id, out_dir=out_dir))
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

    def _phase3_excursion_path(self) -> Optional[str]:
        """The Phase 3 excursion-event file path (ADR-0109), or None if the tab is
        unavailable / no session has started."""
        if self._phase3 is None or not hasattr(self._phase3,
                                               "current_excursion_path"):
            return None
        try:
            return self._phase3.current_excursion_path()
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
                "# per-session serial capture: the COMPLETE verbatim stream "
                "received between Start and Stop - see docs/decisions/0178, 0124\n")
            self._serial_file.write(f"# session_id={self._session_id}\n")
            self._serial_file.write(
                "# wall_clock_iso = host wall clock at line RECEIPT (time.time() on "
                "the reader thread, docs/decisions/0175), NOT decision time; "
                "firmware_line = ms,TYPE,payload...\n")
            self._serial_file.write(
                "# wall_clock_iso,firmware_line\n")
            self._serial_file.flush()
            return True
        except OSError:
            self._serial_file = None
            return False

    def _write_serial(self, line: str, recv_wall: Optional[float] = None) -> None:
        """Append one matched firmware line, host-wall-clock stamped (rig_logger
        column format). Never raises into the serial callback. A failure is
        recorded so the status line warns the serial log is not on disk
        (docs/decisions/0174); recorded WITHOUT a timeline note (this runs on the
        serial reader thread, not the Tk thread that owns the note log) and only
        on failure - the per-line success path stays off the write-error map so a
        recorded warning persists rather than flapping on this hot stream.

        ``recv_wall`` is the reader-thread RECEIPT wall clock (time.time()) taken
        the instant the line arrived (docs/decisions/0175); it is used verbatim so
        the stamp reflects when the host had the bytes, not when this Tk callback
        ran. It falls back to ``datetime.now()`` only when absent (a line that
        carried no receipt stamp)."""
        try:
            if recv_wall is not None:
                # Offset-aware: attach the host UTC offset to the receipt instant
                # so the stamp is unambiguous off-rig (docs/decisions/0182).
                ts = datetime.fromtimestamp(recv_wall).astimezone().isoformat(
                    timespec="milliseconds")
            else:
                ts = now_iso()
            self._serial_file.write(f"{ts},{line}\n")
            self._serial_file.flush()
        except (OSError, ValueError) as exc:
            self._record_write_result(
                "serial", getattr(self._serial_file, "name", None), exc,
                note=False)

    def _close_serial(self) -> None:
        if self._serial_file is not None:
            try:
                self._serial_file.close()
            except OSError:
                pass
            self._serial_file = None

    # ------------------------------------------------------ config snapshot
    def _write_config_snapshot(self, config_dir: str) -> bool:
        """Copy the reproducibility-critical config into ``config/`` and record it
        in the manifest (docs/decisions/0072).

        The host-local files (zones/ROI/landmarks/scale, phase3.json, rules.json)
        are found by session_config itself; ``phase3.json`` was just refreshed from
        the tab's live values by save-on-arm (see the caller, ADR-0167) so its copy
        matches this run. The injected ``config_inputs`` provider supplies the
        extras that aren't plain host files (the lens calibration JSON, the active
        tracker model) and the live scalars
        (camera transform, tracker/maze params). Best-effort: any failure just
        leaves the snapshot partial and returns False - never breaks Start.
        Returns True when every expected source was captured.
        """
        extra_sources = None
        runtime_params = None
        if self._config_inputs is not None:
            try:
                inputs = self._config_inputs() or {}
                extra_sources = inputs.get("files") or None
                runtime_params = inputs.get("params") or None
            except Exception:
                pass
        try:
            from tmaze.core.phase3 import bundle_format
            result = session_config.write_config_snapshot(
                config_dir, session_id=self._session_id,
                extra_sources=extra_sources, runtime_params=runtime_params,
                format_manifest=bundle_format.format_manifest(
                    firmware_proto=self._firmware_proto))
        except Exception:
            return False
        if self._manifest is not None:
            self._manifest.set_stream("config_dir", result["config_dir"])
            self._manifest.set_stream("config_snapshot", result["snapshot_path"])
        return not result["missing"]

    # ---------------------------------------------------- end-of-session summary
    def _write_session_summary(self) -> None:
        """Build the per-session behavioral summary and write it as JSON + text
        into the session's summary/ folder, recording both paths in the manifest
        (docs/decisions/0071).

        The measures come from the same pure cores the live behavior panel uses -
        the Phase 3 cued stats over the published table (phase3_stats.compute) plus
        the panel's Phase 3 locomotor snapshot (via the injected provider) - so
        nothing is recomputed here. Best-effort: any failure must not break a clean
        Stop, so the whole build/write is guarded.
        """
        if self._manifest is None or self._session_dir is None:
            return
        try:
            cued, seed, tones = self._phase3_summary_inputs()
            locomotor = self._locomotor_snapshot()
            params = self._task_params()
            notes = self._load_notes_entries()
            summary = session_summary.build_summary(
                session_id=self._session_id, animal=self._manifest.animal,
                t0=self._manifest.t0, t_end=self._manifest.t_end,
                cued=cued, locomotor=locomotor, seed=seed, tones=tones,
                params=params, notes=notes,
                manual_pulses_ms=self._operator_pulses_ms,
                pre_start_trials=self._pre_start_trial_count(),
                session_cap=self._session_cap_block(),
                water_target=self._water_target_block())
            summary_dir = os.path.join(self._session_dir, SUMMARY_SUBDIR)
            json_path = os.path.join(
                summary_dir, f"session_summary_{self._session_id}.json")
            text_path = os.path.join(
                summary_dir, f"session_summary_{self._session_id}.txt")
            try:
                self._atomic_write(json_path, summary.render_json())
                self._atomic_write(text_path, summary.render_text())
            except OSError as exc:
                # Surface it rather than silently losing the end-of-session summary
                # (docs/decisions/0174); the stream pointers stay unset so the
                # manifest never points at a file that isn't there.
                self._record_write_result("summary", json_path, exc)
            else:
                self._record_write_result("summary", json_path, None)
                self._manifest.set_stream("summary_json", json_path)
                self._manifest.set_stream("summary_text", text_path)
        except Exception:
            pass

    def _phase3_summary_inputs(self):
        """``(Phase3Stats, seed, tones)`` for the summary from the published Phase
        3 table, or ``(None, None, {})`` when no Phase 3 controller / snapshot is
        available. ``tones`` is the cue->side mapping recorded in the snapshot."""
        if self._phase3 is None or not hasattr(self._phase3, "published_state"):
            return None, None, {}
        try:
            snap = self._phase3.published_state()
        except Exception:
            snap = None
        if snap is None:
            return None, None, {}
        tones = {
            "high_tone_is_left": snap.high_tone_is_left,
            "high_tone_is_left_source": getattr(
                snap, "high_tone_is_left_source", "config"),
            "high_tone_hz": snap.high_tone_hz,
            "low_tone_hz": snap.low_tone_hz,
        }
        return phase3_stats.compute(snap), snap.seed, tones

    def _session_cap_block(self):
        """The summary's ``session_cap`` block (docs/decisions/0190): the cap the
        run was armed under (from the Phase 3 controller) plus ``reached`` - the
        limit that fired, or None when the operator stopped first. None when no
        Phase 3 controller exposes it (pre-cap / headless)."""
        if self._phase3 is None or not hasattr(self._phase3, "resolved_session_cap"):
            return None
        try:
            block = dict(self._phase3.resolved_session_cap())
            block["reached"] = (self._phase3.cap_reached()
                                if hasattr(self._phase3, "cap_reached") else None)
        except Exception:
            return None
        return block

    def _water_target_block(self):
        """The summary's ``water_target`` block (docs/decisions/0191): earned task
        water vs the animal's daily target, from the Phase 3 controller; None when
        unavailable or the animal has no recorded target."""
        if self._phase3 is None or not hasattr(self._phase3, "water_target"):
            return None
        try:
            return self._phase3.water_target()
        except Exception:
            return None

    def _pre_start_trial_count(self):
        """How many Phase 3 trials started BEFORE session_t0 (review finding L7):
        Phase 3 can be armed and run trials before Session Start stamps the anchor
        (docs/decisions/0066), so early rows' ``trial_start_iso`` legitimately
        precede ``t0``. Counted for the summary so an offline reader isn't
        surprised by negative offsets. None when no Phase 3 / snapshot / anchor."""
        if (self._phase3 is None or self._manifest is None
                or not hasattr(self._phase3, "published_state")):
            return None
        try:
            snap = self._phase3.published_state()
        except Exception:
            snap = None
        if snap is None:
            return None
        t0_iso = (self._manifest.t0 or {}).get("iso")
        starts = [getattr(r, "trial_start_iso", None) for r in snap.rows]
        return session_summary.count_trials_started_before(starts, t0_iso)

    def _task_params(self) -> dict:
        """The Phase 3 shaping params to fold into the summary, read as a COPY from
        this session's ``config/phase3.json`` snapshot (docs/decisions/0072/0073).

        Reading the per-session copy - not the live tab state - keeps the summary
        self-contained and consistent with the archived config. Since Start now
        persists the tab's live GUI-editable fields to phase3.json before the
        snapshot (save-on-arm, ADR-0167), this per-session copy reflects the values
        the session armed with; a field changed AFTER Start is applied live but not
        re-persisted. ``{}`` if the snapshot is missing/unreadable, so the summary
        just omits the section."""
        if self._session_dir is None:
            return {}
        path = os.path.join(self._session_dir, CONFIG_SUBDIR, "phase3.json")
        try:
            cfg = phase3_config.load_phase3_config(path)
        except Exception:
            return {}
        seq = cfg.get("sequence", {})
        reward = cfg.get("reward", {})
        cue = cfg.get("cue", {})
        return {
            "max_same_run": seq.get("max_same_run"),
            "block_size": seq.get("block_size"),
            # Closed-loop debias generator + its knobs (ADR-0086), so the summary's
            # params block matches the data-file header + config snapshot.
            "generator": seq.get("generator"),
            "debias_blocks": seq.get("debias_blocks"),
            "reward_incorrect": reward.get("reward_incorrect"),
            "reward_pulse_ms": reward.get("pulse_ms"),
            "max_topups": reward.get("max_topups"),
            "require_up_stem": cue.get("require_up_stem"),
            "min_confidence": cue.get("min_confidence"),
            # The session cap (docs/decisions/0190) as archived; the top-level
            # ``session_cap`` block adds whether it fired.
            "session_cap": cfg.get("session_cap"),
        }

    def _load_notes_entries(self) -> list:
        """The session's note entries for the summary, folded from the on-disk
        JSONL record (docs/decisions/0074) so an edit/delete made in the popup and
        any notes that survived an unclean end are reflected. Falls back to the
        in-memory log if the file is missing/unreadable."""
        if self._notes_path and os.path.isfile(self._notes_path):
            try:
                with open(self._notes_path, "r", encoding="utf-8") as fh:
                    return session_notes.parse_jsonl(fh.read()).as_list()
            except OSError:
                pass
        return self._notelog.as_list()

    def _locomotor_snapshot(self):
        """The behavior panel's Phase 3 locomotor snapshot via the injected
        provider, or None if unavailable/erroring."""
        if self._behavior_summary is None:
            return None
        try:
            return self._behavior_summary()
        except Exception:
            return None

    def _atomic_write(self, path: str, text: str) -> None:
        """Write ``text`` to ``path`` atomically (temp file + os.replace), like the
        manifest writer, so a crash mid-write never leaves a half-written file. The
        temp file is flushed AND fsync'd before the rename (docs/decisions/0174) so
        the bytes are durably on disk, not merely un-torn in the OS write cache."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    # ------------------------------------------------------------- manifest I/O
    def _write_manifest(self) -> None:
        """Atomically persist the manifest (temp file + os.replace), so a crash
        mid-session never leaves a half-written file. The temp file is flushed +
        fsync'd before the rename (docs/decisions/0174) so it is durably on disk."""
        if self._manifest is None or self._manifest_path is None:
            return
        try:
            os.makedirs(os.path.dirname(self._manifest_path), exist_ok=True)
            tmp = self._manifest_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(self._manifest.render())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._manifest_path)
        except OSError as exc:
            self._record_write_result("manifest", self._manifest_path, exc)
        else:
            self._record_write_result("manifest", self._manifest_path, None)
