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
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable, Optional

import animal_state
import animals
import daily_log
import note_tags
import phase3_config
import phase3_stats
import protocol
import reward_volume
import session_config
import session_notes
import session_summary
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
# End-of-session behavioral summary (JSON + text) folder (docs/decisions/0071).
SUMMARY_SUBDIR = "summary"
# Per-session config snapshot (geometry/task/calibration + runtime params) folder
# so the run is reproducible after the live host/*.json change (docs/decisions/0072).
CONFIG_SUBDIR = "config"

# The central reward well's solenoid port ("Center / start"; docs/decisions/0007,
# 0022) - used to keep only that port's valve acks in the filtered serial file.
CENTER_SOL_PORT = 2

# Operator note streams (docs/decisions/0074). The per-session note log lives in
# the summary/ folder as JSON Lines and is registered under this manifest key;
# the user-defined tag vocabulary persists next to the other host/*.json config.
NOTES_STREAM_KEY = "notes_events"
NOTE_TAGS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "note_tags.json")

# Pre-session animal-state stream (docs/decisions/0077). Written into the bundle's
# config/ folder at Start as its OWN manifest stream (not via config_snapshot),
# following the notes-stream precedent.
ANIMAL_STATE_STREAM_KEY = "animal_state"
ANIMAL_STATE_FILENAME = "animal_state.json"

# How often (ms) to re-check each recorder for its resolved output path while a
# session is active. The camera path resolves immediately; the Phase 3 file only
# once its gate opens (Free Roam + tracking + a zone map), so we poll for it.
RESOLVE_INTERVAL_MS = 500

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
                 config_inputs: Optional[Callable[[], dict]] = None):
        self._parent = parent
        self._camera = camera
        self._phase3 = phase3
        self._send = send                 # callable(str) -> None (RigClient.send)
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
        # Canonical animal-id list (resources repo), loaded best-effort once so
        # Session Start can normalize/validate the entered id (docs/decisions/0075).
        # An empty (unavailable) registry when the resources repo is absent.
        self._animals = animals.load_registry()
        # Open handle for the per-session filtered serial file (None when idle).
        self._serial_file = None
        self._serial_path: Optional[str] = None
        # Pulse widths (ms) of the rare in-task MANUAL reward dispenses seen while
        # a session is active; totalled into the summary's manual_water on Stop
        # (docs/decisions/0078). Reset at each Start.
        self._manual_pulses_ms: list = []
        # Operator notes for the current session as timestamped, editable entries
        # (docs/decisions/0074). The log buffers entries even before Start (so a
        # note jotted while setting up is not lost); on Start it is bound to a
        # summary/notes_<id>.jsonl file, rewritten atomically on every change, and
        # folded from disk into the summary on Stop.
        self._notelog = session_notes.NoteLog()
        self._notes_path: Optional[str] = None
        # User-defined intervention-tag vocabulary, persisted across sessions in
        # host/note_tags.json (docs/decisions/0074).
        self._tag_vocab = note_tags.load_tags(NOTE_TAGS_PATH)
        # The open notes popup (Toplevel), if any, so a second click focuses the
        # existing one instead of stacking duplicates.
        self._notes_win: Optional[tk.Toplevel] = None

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
        if self._active and info.reward is not None:
            manual, dur_ms = info.reward
            if manual:
                # A pre-v4 line without a trailing duration falls back to the
                # configured reward pulse width, mirroring estimated_water's
                # fallback so the two channels stay comparable (ADR-0019/0078).
                width = (dur_ms if dur_ms is not None
                         else reward_volume.DEFAULT_REWARD_PULSE_MS)
                self._manual_pulses_ms.append(float(width))
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
        return datetime.now().isoformat(timespec="milliseconds")

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
        except OSError:
            pass

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
        if row is not None:
            state = animal_state.from_daily_row(self._session_id, animal, date, row)
            note = None
        else:
            recent = table.latest_for(animal, on_or_before=date)
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
        except OSError:
            return "animal state: write failed"
        if (self._manifest is not None
                and path_within_session(path, self._session_dir)):
            self._manifest.set_stream(ANIMAL_STATE_STREAM_KEY, path)
        return note

    def _ask_pre_session_weight(self, animal: str) -> Optional[float]:
        """Prompt for the pre-session weight (g) when the daily table has no row
        for today. Cancel/blank returns None (the operator can skip). Best-effort:
        any Tk error (e.g. headless) just skips the dialog."""
        try:
            return simpledialog.askfloat(
                "Pre-session weight",
                f"Pre-session weight (g) for {animal}?\n"
                "(No daily-log row for today - Cancel to skip.)",
                parent=self.frame, minvalue=0.0)
        except tk.TclError:
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

        # Snapshot the definitions/config that shaped this run into config/ so it
        # stays reproducible after the live host/*.json are re-drawn (ADR-0072).
        if not self._write_config_snapshot(config_dir):
            notes.append("config snapshot incomplete")

        self._mark_session_start(self._manifest.t0)
        self._write_manifest()

        # Fresh accounting of the rare in-task manual reward dispenses for this
        # session; apply() accumulates their pulse widths off the wire and the
        # summary totals them into manual_water on Stop (docs/decisions/0078).
        self._manual_pulses_ms = []
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

    # ------------------------------------------------------ config snapshot
    def _write_config_snapshot(self, config_dir: str) -> bool:
        """Copy the reproducibility-critical config into ``config/`` and record it
        in the manifest (docs/decisions/0072).

        The host-local files (zones/ROI/landmarks/scale, phase3.json, rules.json)
        are found by session_config itself; the injected ``config_inputs``
        provider supplies the extras that aren't plain host files (the lens
        calibration JSON, the active tracker model) and the live scalars
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
            result = session_config.write_config_snapshot(
                config_dir, session_id=self._session_id,
                extra_sources=extra_sources, runtime_params=runtime_params)
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
                manual_pulses_ms=self._manual_pulses_ms)
            summary_dir = os.path.join(self._session_dir, SUMMARY_SUBDIR)
            json_path = os.path.join(
                summary_dir, f"session_summary_{self._session_id}.json")
            text_path = os.path.join(
                summary_dir, f"session_summary_{self._session_id}.txt")
            self._atomic_write(json_path, summary.render_json())
            self._atomic_write(text_path, summary.render_text())
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
            "high_tone_hz": snap.high_tone_hz,
            "low_tone_hz": snap.low_tone_hz,
        }
        return phase3_stats.compute(snap), snap.seed, tones

    def _task_params(self) -> dict:
        """The Phase 3 shaping params to fold into the summary, read as a COPY from
        this session's ``config/phase3.json`` snapshot (docs/decisions/0072/0073).

        Reading the per-session copy - not the live tab state - keeps the summary
        self-contained and consistent with the archived config; the same caveat as
        the config snapshot applies (a value edited in the tab but not Saved to
        phase3.json won't be reflected). ``{}`` if the snapshot is missing/
        unreadable, so the summary just omits the section."""
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
            "reward_incorrect": reward.get("reward_incorrect"),
            "reward_pulse_ms": reward.get("pulse_ms"),
            "max_topups": reward.get("max_topups"),
            "require_up_stem": cue.get("require_up_stem"),
            "min_confidence": cue.get("min_confidence"),
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
        manifest writer, so a crash mid-write never leaves a half-written file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)

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
