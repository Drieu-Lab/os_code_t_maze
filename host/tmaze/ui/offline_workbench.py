"""
offline_workbench.py - tune the rat tracker on a recorded session video (FW-0074).

One window, three columns:

- left: the live rig's Tracking tab, hosted unchanged (sliders, model buttons,
  Define region / landmarks), fed by a video player instead of the camera
  (ADR-0213). It never reads or writes the rig's ``host/`` files;
- centre: the video with the tracking overlay, the maze zones, the live track
  and an earlier offline run drawn as "ghosts", and a transport bar (scrub bar
  with trial markers, play / step / jump, speed, A-B loop);
- right: the offline-only controls - which file each input comes from (swap /
  save as), every tracker setting with the layer that set it, building models
  from marked frames, the ghosts, and running the whole video (or a range) with
  progress and Cancel, or copying the equivalent command line.

Nothing is saved unless you ask: edits live in memory; "Save as" opens in
``host/ratmodels/`` for models and in the bundle's ``video/`` for the rest. A
run writes ``video/<id>_offline_track_<tag>.csv`` + ``.json`` and gives the same
rows as the copied command (ADR-0214). "Save A-B as clip" writes the A-B range,
with the overlay as shown, as an H.264 ``.mp4`` for presentations, at a chosen
speed (ADR-0218).

Run (env ``drieu_t_maze``, any directory):

    python -m tmaze.ui.offline_workbench [<bundle_dir>] [--model PATH] [--set k=v ...]

Keys: Space play/pause, Left/Right step 1 frame, Shift+Left/Right step 1 s,
[ / ] set loop start / end (A / B), T go to trial.

See docs/decisions/0213-0216.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import tkinter as tk
from dataclasses import dataclass, field, replace
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tmaze.core import offline_track as OT
from tmaze.core import offline_workbench as WB
from tmaze.core.maze_state import ZoneMap, load_zone_map
from tmaze.core.phase3.phase3_log_reader import snapshot_from_csv
from tmaze.core.phase3.phase3_task import CORRECT, INCORRECT
from tmaze.core.session.session_config import sha256_file
from tmaze.paths import phase3_recordings_root
from tmaze.ui.gui_deps import cv2, missing_dependency
from tmaze.ui.preview import render_bgr_to_label
from tmaze.ui.tracker_view import (HIDE_ENABLE, HIDE_GRAB_BG, HIDE_LED_BOARD,
                                   HIDE_TRACK_LOG, TrackerControlView, TrackerTabOptions)
from tmaze.ui.zone_overlay import draw_zone_overlay

POLL_MS = 40                 # redraw / state check
PARAMS_CHECK_MS = 250        # how often settings are compared for changes
RETRACK_DELAY_MS = 300       # settle time after a setting change before re-tracking
LEFT_W = 430                 # hosted Tracking tab column
RIGHT_W = 470                # offline panel column
MARKER_H = 26                # trial-marker strip under the scrub bar
MASK_INSET_FRAC = 0.28       # mask inset width, as a fraction of the view

OUTCOME_COLORS = {CORRECT: "#2e9e44", INCORRECT: "#d03b3b"}
OTHER_TRIAL_COLOR = "#999999"
LIVE_GHOST_BGR = (255, 255, 0)       # cyan cross
RUN_GHOST_BGR = (255, 0, 255)        # magenta circle

KINDS = ("model", "blocker", "roi", "landmarks", "background")
KIND_LABELS = {"model": "Rat model", "blocker": "Blocker model", "roi": "ROI",
               "landmarks": "Landmarks", "background": "Background"}


# ------------------------------------------------------------------ loading
@dataclass
class Session:
    """Everything the window needs about one bundle, loaded before it opens."""
    defaults: OT.OfflineSettings          # what the command line picks with no flags
    launch: OT.OfflineSettings            # with this launch's overrides
    tracker: Any
    sources: Dict[str, str]               # settings layer per key (launch)
    n_frames: int
    fps: float
    frame_times: List[float]
    markers: List[WB.TrialMarker] = field(default_factory=list)
    zones: Optional[ZoneMap] = None
    live_track: Dict[int, WB.TrackPoint] = field(default_factory=dict)


def _trial_rows(bundle: str):
    for path in sorted(glob.glob(os.path.join(bundle, "task", "*.csv"))):
        if path.endswith("_excursions.csv"):
            continue
        got = snapshot_from_csv(path)
        return got[0].rows if got else []
    return []


def load_session(bundle: str, overrides: OT.Overrides = OT.Overrides(), *,
                 ratmodels_dir: str) -> Session:
    """Resolve the settings, build the tracker with its background (a median of
    the video when the bundle has no PNG - a few seconds), and read the trial
    log, zones and live track."""
    from tmaze.vision import offline_tracker as OTR
    defaults = OT.resolve_settings(bundle, OT.Overrides(), ratmodels_dir=ratmodels_dir)
    launch = OT.resolve_settings(bundle, overrides, ratmodels_dir=ratmodels_dir)
    header, frames = OTR.read_sidecar(launch.sidecar)
    tracker, _, sources = OTR.build_tracker(launch)
    OTR.install_background(tracker, OTR.load_background(launch, len(frames)))
    cap = OTR._open(launch.video)
    try:
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) or len(frames)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
    finally:
        cap.release()
    times = [f.host_unix_s for f in frames]
    zones = None
    zpath = os.path.join(bundle, "config", "zones.json")
    if os.path.isfile(zpath):
        try:
            zones = load_zone_map(zpath)
        except (OSError, ValueError, KeyError):
            zones = None
    live = WB.read_track_points(launch.sidecar) if launch.sidecar else {}
    return Session(defaults=defaults, launch=launch, tracker=tracker, sources=sources,
                   n_frames=n, fps=fps, frame_times=times,
                   markers=WB.trial_markers(_trial_rows(bundle), times),
                   zones=zones, live_track=live)


@dataclass(frozen=True)
class Overlay:
    """The view's overlay, fixed when made, so a clip saved on the player's
    worker draws exactly what the window showed when it started (ADR-0218)."""
    show: Dict[str, bool]
    tracker: Any
    zones: Optional[ZoneMap]
    live_track: Dict[int, WB.TrackPoint]
    run_track: Dict[int, WB.TrackPoint]

    def draw(self, disp, scale: float, idx: int, res, frame_shape) -> None:
        from tmaze.vision.tracker import draw_overlay
        sh = self.show
        draw_overlay(disp, scale, self.tracker, res, draw_landmarks=sh["landmarks"],
                     draw_roi=sh["roi"], draw_trail=sh["trail"] and res is not None,
                     draw_candidates=sh["candidates"])
        fh, fw = frame_shape[:2]
        if sh["zones"] and self.zones is not None:
            cur = None
            if res is not None and res.smoothed is not None:
                cur = self.zones.classify_point(res.smoothed[0] / fw, res.smoothed[1] / fh)
            draw_zone_overlay(disp, self.zones, current_zone=cur)
        live = self.live_track.get(idx)
        if sh["live"] and live is not None and live.x is not None:
            cv2.drawMarker(disp, (int(live.x * scale), int(live.y * scale)), LIVE_GHOST_BGR,
                           markerType=cv2.MARKER_CROSS, markerSize=18, thickness=2)
        run = self.run_track.get(idx)
        if sh["run"] and run is not None and run.x is not None:
            cv2.circle(disp, (int(run.x * scale), int(run.y * scale)), 9, RUN_GHOST_BGR, 2)
        if sh["mask"] and res is not None and res.mask is not None:
            _draw_mask_inset(disp, res)


def _draw_mask_inset(disp, res) -> None:
    m = cv2.cvtColor(res.mask, cv2.COLOR_GRAY2BGR)
    if res.chosen_contour is not None:
        cv2.drawContours(m, [res.chosen_contour], -1, (0, 255, 0), thickness=cv2.FILLED)
    dh, dw = disp.shape[:2]
    iw = max(1, int(dw * MASK_INSET_FRAC))
    ih = max(1, int(m.shape[0] * iw / max(1, m.shape[1])))
    if ih >= dh:
        return
    disp[0:ih, dw - iw:dw] = cv2.resize(m, (iw, ih), interpolation=cv2.INTER_NEAREST)
    cv2.rectangle(disp, (dw - iw, 0), (dw - 1, ih - 1), (200, 200, 200), 1)


def _describe(choice: Optional[OT.FileChoice]) -> str:
    if choice is None:
        return "none"
    if choice.path is None:
        return {OT.SRC_COMPUTED: "median of the video",
                OT.SRC_REGISTERED: "built here, not saved",
                WB.SRC_EDITED: "changed here, not saved"}.get(choice.source, choice.source)
    return f"{os.path.basename(choice.path)}  ({choice.source})"


def _is_text_widget(w) -> bool:
    return isinstance(w, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox, ttk.Spinbox,
                          ttk.Combobox, ttk.Scale, tk.Scale))


# ------------------------------------------------------------------- window
class OfflineWorkbench:
    def __init__(self, root: tk.Tk, session: Session, *, ratmodels_dir: str,
                 warmup: int = WB.DEFAULT_WARMUP_FRAMES):
        from tmaze.vision import video_player as VP
        self._VP = VP
        self.root = root
        self.s = session
        self.tracker = session.tracker
        self.video_dir = os.path.join(session.launch.bundle, "video")
        self.ratmodels_dir = ratmodels_dir
        self.player = VP.VideoPlayer(session.launch.video, self.tracker,
                                     n_frames=session.n_frames, fps=session.fps,
                                     output_dir=self.video_dir, warmup=warmup)
        L = session.launch
        self.files: Dict[str, Optional[OT.FileChoice]] = {
            "model": L.model, "blocker": L.blocker_model, "roi": L.roi,
            "landmarks": L.landmarks, "background": L.background}
        self.marks = WB.Marks()
        self.reg_rat: Optional[Tuple[int, int]] = None
        self.reg_blocker: Tuple[int, ...] = ()
        self._pending_reg: Tuple[Optional[str], Any] = (None, None)
        self.run_job = None
        self._run_start = 0
        self.run_track: Dict[int, WB.TrackPoint] = {}
        self._base_cache: Dict[Optional[str], Dict[str, Any]] = {}
        self._last_params = VP.current_params(self.tracker)
        self._last_idx = -2
        self._dirty = True
        self._afters: Dict[str, str] = {}     # pending root.after ids, cancelled on close
        self._photo = None
        self._closed = False

        root.title(f"Offline tracker workbench - {session.defaults.session_id}")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._refresh_files()
        self._refresh_settings()
        self._bind_keys()
        self._afters["poll"] = root.after(POLL_MS, self._poll)
        self._afters["params"] = root.after(PARAMS_CHECK_MS, self._check_params)

    # ================================================================ layout
    def _build(self) -> None:
        left = ttk.Frame(self.root, width=LEFT_W)
        right = ttk.Frame(self.root, width=RIGHT_W)
        centre = ttk.Frame(self.root)
        left.pack(side=tk.LEFT, fill=tk.Y)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        centre.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right.pack_propagate(False)

        opts = TrackerTabOptions(
            load_host_files=False, ask_before_saving=True, show_preview=False,
            hide=frozenset({HIDE_ENABLE, HIDE_GRAB_BG, HIDE_LED_BOARD, HIDE_TRACK_LOG}),
            file_dir=self.video_dir, model_dir=self.ratmodels_dir,
            on_event=self._on_tab_event)
        self.tab = TrackerControlView(left, self.tracker, camera=self.player, options=opts)
        self.tab.widget().pack(fill=tk.BOTH, expand=True)
        self.tab.set_active_paths(self._path("model"), self._path("blocker"))
        self.tab.refresh_from_tracker()

        self._build_centre(centre)
        nb = ttk.Notebook(right)
        nb.pack(fill=tk.BOTH, expand=True)
        for title, build in (("Files", self._build_files), ("Settings", self._build_settings),
                             ("Marks", self._build_marks), ("Run", self._build_run)):
            page = ttk.Frame(nb, padding=6)
            nb.add(page, text=title)
            build(page)
        self.status = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.status, wraplength=RIGHT_W - 10,
                  foreground="#b00").pack(side=tk.BOTTOM, fill=tk.X)

    # ---------------------------------------------------------------- centre
    def _build_centre(self, parent) -> None:
        bar = ttk.Frame(parent)
        bar.pack(side=tk.TOP, fill=tk.X)
        self.show = {}
        for key, text, on in (("roi", "ROI", True), ("trail", "Trail", True),
                              ("candidates", "Candidates", True),
                              ("landmarks", "Landmarks", True),
                              ("zones", "Zones", self.s.zones is not None),
                              ("mask", "Mask inset", False),
                              ("live", "Live track", bool(self.s.live_track)),
                              ("run", "Earlier run", True)):
            var = tk.BooleanVar(value=on)
            var.trace_add("write", lambda *_: self._mark_dirty())
            ttk.Checkbutton(bar, text=text, variable=var).pack(side=tk.LEFT, padx=3)
            self.show[key] = var

        # The transport is packed before the view so a short window shrinks the
        # video, not the controls.
        transport = ttk.Frame(parent)
        transport.pack(side=tk.BOTTOM, fill=tk.X)
        self._build_transport(transport)
        self.info = tk.StringVar()
        ttk.Label(parent, textvariable=self.info, anchor="w").pack(side=tk.BOTTOM, fill=tk.X)
        self.view_box = ttk.Frame(parent)
        self.view_box.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.view_box.pack_propagate(False)
        self.view = ttk.Label(self.view_box, anchor="center")
        self.view.pack(fill=tk.BOTH, expand=True)
        self.view_box.bind("<Configure>", lambda _e: self._mark_dirty())
        self.view.bind("<Button-1>", lambda _e: self.root.focus_set())

    def _build_transport(self, parent) -> None:
        n = max(1, self.s.n_frames)
        self.scrub_var = tk.DoubleVar(value=0)
        self.scrub = ttk.Scale(parent, from_=0, to=n - 1, variable=self.scrub_var)
        self.scrub.pack(side=tk.TOP, fill=tk.X, padx=4)
        self.scrub.bind("<ButtonRelease-1>",
                        lambda _e: self.player.seek(int(self.scrub_var.get())))
        self.strip = tk.Canvas(parent, height=MARKER_H, background="#202020",
                               highlightthickness=0)
        self.strip.pack(side=tk.TOP, fill=tk.X, padx=4)
        self.strip.bind("<Configure>", lambda _e: self._draw_strip())
        self.strip.bind("<Button-1>", self._strip_click)

        row = ttk.Frame(parent)
        row.pack(side=tk.TOP, fill=tk.X, pady=2)
        sec = max(1, int(round(self.s.fps)))
        for text, cmd in (("|<", lambda: self.player.seek(0)),
                          ("-1 s", lambda: self.player.step(-sec)),
                          ("-1", lambda: self.player.step(-1))):
            ttk.Button(row, text=text, width=5, command=cmd).pack(side=tk.LEFT)
        self.play_btn = ttk.Button(row, text="Play", width=6, command=self.player.toggle_play)
        self.play_btn.pack(side=tk.LEFT)
        for text, cmd in (("+1", lambda: self.player.step(1)),
                          ("+1 s", lambda: self.player.step(sec)),
                          (">|", lambda: self.player.seek(self.player.n_frames - 1))):
            ttk.Button(row, text=text, width=5, command=cmd).pack(side=tk.LEFT)
        ttk.Label(row, text="  frame").pack(side=tk.LEFT)
        self.frame_var = tk.StringVar(value="0")
        self.frame_entry = ttk.Entry(row, textvariable=self.frame_var, width=8)
        self.frame_entry.pack(side=tk.LEFT)
        self.frame_entry.bind("<Return>", lambda _e: self._goto_frame())
        self.time_var = tk.StringVar()
        ttk.Label(row, textvariable=self.time_var, width=18).pack(side=tk.LEFT, padx=4)
        ttk.Label(row, text="speed").pack(side=tk.LEFT)
        self.speed_var = tk.StringVar(value="1x")
        sp = ttk.Combobox(row, textvariable=self.speed_var, width=5, state="readonly",
                          values=[self._speed_text(v) for v in WB.SPEEDS])
        sp.pack(side=tk.LEFT)
        sp.bind("<<ComboboxSelected>>", lambda _e: self.player.set_speed(
            WB.SPEEDS[[self._speed_text(v) for v in WB.SPEEDS].index(self.speed_var.get())]))

        row2 = ttk.Frame(parent)
        row2.pack(side=tk.TOP, fill=tk.X, pady=2)
        ttk.Button(row2, text="A [", width=4, command=lambda: self.set_mark("a")).pack(side=tk.LEFT)
        ttk.Button(row2, text="] B", width=4, command=lambda: self.set_mark("b")).pack(side=tk.LEFT)
        ttk.Button(row2, text="Clear A-B", command=self.clear_ab).pack(side=tk.LEFT)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="Loop A-B", variable=self.loop_var,
                        command=self._push_loop).pack(side=tk.LEFT, padx=4)
        self.ab_var = tk.StringVar(value="A-B: -")
        ttk.Label(row2, textvariable=self.ab_var, width=18).pack(side=tk.LEFT)
        ttk.Separator(row2, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(row2, text="< trial", command=lambda: self.jump_trial(-1)).pack(side=tk.LEFT)
        ttk.Button(row2, text="trial >", command=lambda: self.jump_trial(1)).pack(side=tk.LEFT)
        self.trial_var = tk.StringVar()
        self.trial_entry = ttk.Entry(row2, textvariable=self.trial_var, width=5)
        self.trial_entry.pack(side=tk.LEFT, padx=(6, 0))
        self.trial_entry.bind("<Return>", lambda _e: self._goto_trial())
        ttk.Button(row2, text="Go", width=4, command=self._goto_trial).pack(side=tk.LEFT)
        for text, attr in (("choice", "choice"), ("reward", "reward"), ("end", "end")):
            ttk.Button(row2, text=text, width=7,
                       command=lambda a=attr: self.jump_event(a)).pack(side=tk.LEFT)

        row3 = ttk.Frame(parent)
        row3.pack(side=tk.TOP, fill=tk.X, pady=2)
        self.mode_var = tk.StringVar(value=self._VP.MODE_TRACK)
        for text, mode in (("Track", self._VP.MODE_TRACK),
                           ("View recorded only", self._VP.MODE_VIEW)):
            ttk.Radiobutton(row3, text=text, value=mode, variable=self.mode_var,
                            command=lambda: self.player.set_mode(self.mode_var.get())
                            ).pack(side=tk.LEFT)
        ttk.Label(row3, text="   warm-up after a jump (frames)").pack(side=tk.LEFT)
        self.warmup_var = tk.IntVar(value=self.player.warmup)
        wu = ttk.Spinbox(row3, from_=0, to=3000, increment=25, width=6,
                         textvariable=self.warmup_var, command=self._push_warmup)
        wu.pack(side=tk.LEFT)
        wu.bind("<Return>", lambda _e: self._push_warmup())
        ttk.Button(row3, text="Reset tracker", command=self._reset_tracker).pack(side=tk.LEFT, padx=6)
        ttk.Separator(row3, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        self.clip_btn = ttk.Button(row3, text="Save A-B as clip...", command=self._ask_save_clip)
        self.clip_btn.pack(side=tk.LEFT)
        ttk.Label(row3, text="at").pack(side=tk.LEFT, padx=(4, 0))
        self.clip_speed_var = tk.StringVar(value="1x")
        ttk.Combobox(row3, textvariable=self.clip_speed_var, width=5, state="readonly",
                     values=[self._speed_text(v) for v in WB.CLIP_SPEEDS]).pack(side=tk.LEFT)
        self.clip_cancel_btn = ttk.Button(row3, text="Cancel", state=tk.DISABLED,
                                          command=self.player.cancel_clip)
        self.clip_cancel_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.clip_var = tk.StringVar()
        ttk.Label(row3, textvariable=self.clip_var).pack(side=tk.LEFT, padx=4)

    @staticmethod
    def _speed_text(v: float) -> str:
        return "max" if v <= 0 else f"{v:g}x"

    # ----------------------------------------------------------------- files
    def _build_files(self, page) -> None:
        ttk.Label(page, wraplength=RIGHT_W - 30, justify="left", text=(
            "What the tracker is using now. Nothing is written until you Save as; "
            "the rig's host/ files and the bundle's own files are never changed.")
                  ).pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self.file_vars: Dict[str, tk.StringVar] = {}
        for kind in KINDS:
            box = ttk.LabelFrame(page, text=KIND_LABELS[kind], padding=4)
            box.pack(side=tk.TOP, fill=tk.X, pady=2)
            var = tk.StringVar()
            ttk.Label(box, textvariable=var, wraplength=RIGHT_W - 40).pack(side=tk.TOP, fill=tk.X)
            self.file_vars[kind] = var
            row = ttk.Frame(box)
            row.pack(side=tk.TOP, fill=tk.X)
            ttk.Button(row, text="Load...", command=lambda k=kind: self._ask_load(k)).pack(side=tk.LEFT)
            ttk.Button(row, text="Save as...", command=lambda k=kind: self._ask_save(k)).pack(side=tk.LEFT)
            ttk.Button(row, text="Session's", command=lambda k=kind: self.use_default(k)
                       ).pack(side=tk.LEFT)
            if kind == "background":
                ttk.Button(row, text="Median of video", command=self.use_median_background
                           ).pack(side=tk.LEFT)
            else:
                ttk.Button(row, text="None", command=lambda k=kind: self.clear_file(k)
                           ).pack(side=tk.LEFT)
        self.notes_var = tk.StringVar(value="\n".join(self.s.launch.notes))
        ttk.Label(page, textvariable=self.notes_var, wraplength=RIGHT_W - 30,
                  foreground="#555", justify="left").pack(side=tk.TOP, fill=tk.X, pady=6)

    def _path(self, kind: str) -> Optional[str]:
        c = self.files.get(kind)
        return c.path if c is not None else None

    def _default(self, kind: str) -> Optional[OT.FileChoice]:
        d = self.s.defaults
        return {"model": d.model, "blocker": d.blocker_model, "roi": d.roi,
                "landmarks": d.landmarks, "background": d.background}[kind]

    def _choice_for(self, kind: str, path: str) -> OT.FileChoice:
        d = self._default(kind)
        if d is not None and d.path and os.path.normcase(os.path.abspath(d.path)) \
                == os.path.normcase(os.path.abspath(path)):
            return d
        return OT.FileChoice(os.path.abspath(path), OT.SRC_OVERRIDE, sha256_file(path))

    def _set_file(self, kind: str, choice: Optional[OT.FileChoice]) -> None:
        self.files[kind] = choice
        if kind == "model" and (choice is None or choice.source != OT.SRC_REGISTERED
                                or choice.path is not None):
            self.reg_rat = None
        if kind == "blocker" and (choice is None or choice.source != OT.SRC_REGISTERED
                                  or choice.path is not None):
            self.reg_blocker = ()
        if kind in ("model", "blocker"):
            self.tab.set_active_paths(self._path("model"), self._path("blocker"))
        self._refresh_files()

    def _refresh_files(self) -> None:
        for kind in KINDS:
            self.file_vars[kind].set(_describe(self.files[kind]))

    def _initial_dir(self, kind: str) -> str:
        return self.ratmodels_dir if kind in ("model", "blocker") else self.video_dir

    @staticmethod
    def _filetypes(kind: str):
        if kind in ("model", "blocker"):
            return [("Model", "*.npz"), ("All files", "*.*")]
        if kind == "background":
            return [("PNG image", "*.png"), ("All files", "*.*")]
        return [("JSON", "*.json"), ("All files", "*.*")]

    def _ask_load(self, kind: str) -> None:
        path = filedialog.askopenfilename(title=f"Load {KIND_LABELS[kind].lower()}",
                                          initialdir=self._initial_dir(kind),
                                          filetypes=self._filetypes(kind))
        if path:
            self.load_file(kind, path)

    def _ask_save(self, kind: str) -> None:
        ext = {"model": ".npz", "blocker": ".npz", "background": ".png"}.get(kind, ".json")
        sid = self.s.defaults.session_id
        name = {"model": f"{sid}_ratmodel.npz", "blocker": f"{sid}_blockermodel.npz",
                "roi": "roi_polygon.json", "landmarks": "landmarks.json",
                "background": f"{sid}_background_workbench.png"}[kind]
        path = filedialog.asksaveasfilename(title=f"Save {KIND_LABELS[kind].lower()} as",
                                            initialdir=self._initial_dir(kind),
                                            initialfile=name, defaultextension=ext,
                                            filetypes=self._filetypes(kind))
        if path:
            self.save_file(kind, path)

    def load_file(self, kind: str, path: str) -> None:
        """Swap in a file (runs on the player's worker, then re-tracks)."""
        t = self.tracker
        if kind == "background":
            bg = cv2.imread(path, cv2.IMREAD_COLOR)
            if bg is None:
                self.status.set(f"cannot read background: {path}")
                return
            self.player.install_background(bg)
        else:
            fn = {"model": lambda: t.load_model(path, restore=True),
                  "blocker": lambda: t.load_blocker_model(path),
                  "roi": lambda: t.load_roi(path),
                  "landmarks": lambda: t.load_landmarks(path)}[kind]
            self.player.submit(f"load {kind}", fn)
        self._set_file(kind, self._choice_for(kind, path))

    def save_file(self, kind: str, path: str) -> bool:
        t = self.tracker
        try:
            if kind == "background":
                bg = t.get_background_image()
                ok = bg is not None and bool(cv2.imwrite(path, bg))
            else:
                ok = bool({"model": t.save_model, "blocker": t.save_blocker_model,
                           "roi": t.save_roi, "landmarks": t.save_landmarks}[kind](path))
        except Exception as e:
            self.status.set(f"save {kind}: {type(e).__name__}: {e}")
            return False
        if not ok:
            self.status.set(f"nothing to save for {KIND_LABELS[kind].lower()}")
            return False
        self._set_file(kind, self._choice_for(kind, path))
        return True

    def clear_file(self, kind: str) -> None:
        t = self.tracker
        fn = {"model": t.clear_model, "blocker": t.clear_blocker_model,
              "roi": t.clear_roi, "landmarks": t.clear_landmarks}[kind]
        self.player.submit(f"clear {kind}", fn)
        self._set_file(kind, None)

    def use_default(self, kind: str) -> None:
        d = self._default(kind)
        if d is None:
            self.clear_file(kind)
        elif d.path is None:
            self.use_median_background()
        else:
            self.load_file(kind, d.path)

    def use_median_background(self) -> None:
        from tmaze.vision import offline_tracker as OTR
        video, n, t = self.s.launch.video, self.s.n_frames, self.tracker
        self.player.submit("median background", lambda: OTR.install_background(
            t, OTR.median_background(video, n)))
        self._set_file("background", OT.FileChoice(None, OT.SRC_COMPUTED))

    def _on_tab_event(self, kind: str, action: str, path: Optional[str]) -> None:
        if action in ("load", "save") and path:
            self._set_file(kind, self._choice_for(kind, path))
        elif action == "clear":
            self._set_file(kind, None)
        elif action == "register":
            self._set_file(kind, OT.FileChoice(None, OT.SRC_REGISTERED))
        elif action == "edit":
            self._set_file(kind, OT.FileChoice(None, WB.SRC_EDITED))
        if self.player.playing():
            return
        # Capture / finish requests are consumed by the next processed frame;
        # re-tracking the warm-up would feed those frames into the registration.
        if action in ("capture", "register"):
            self.player.process_current()
        else:
            self._schedule_retrack()

    # -------------------------------------------------------------- settings
    def _build_settings(self, page) -> None:
        ttk.Label(page, wraplength=RIGHT_W - 30, justify="left", text=(
            "Every tracker setting. Layer = where the value comes from: snapshot "
            "(the session's own), model/default, or 'you' (changed here). "
            "Double-click a row to change it.")).pack(side=tk.TOP, fill=tk.X)
        box = ttk.Frame(page)
        box.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=4)
        self.table = ttk.Treeview(box, columns=("value", "layer"), show="tree headings",
                                  height=24)
        self.table.heading("#0", text="setting")
        self.table.heading("value", text="value")
        self.table.heading("layer", text="layer")
        self.table.column("#0", width=200)
        self.table.column("value", width=120)
        self.table.column("layer", width=100)
        self.table.tag_configure("you", foreground="#c05000")
        sb = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=sb.set)
        self.table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        self.table.bind("<Double-1>", self._edit_setting)
        row = ttk.Frame(page)
        row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(row, text="Load params file...", command=self._ask_load_params).pack(side=tk.LEFT)
        ttk.Button(row, text="Save params file...", command=self._ask_save_params).pack(side=tk.LEFT)
        ttk.Button(row, text="Reset to session", command=self.reset_settings).pack(side=tk.LEFT)

    def base_for(self, model_path: Optional[str]) -> Dict[str, Any]:
        if model_path not in self._base_cache:
            self._base_cache[model_path] = self._VP.base_params(model_path, self.s.defaults)
        return self._base_cache[model_path]

    def _table_base(self) -> Dict[str, Any]:
        return self.base_for(self._path("model") if self.files["model"] is not None else None)

    def _refresh_settings(self) -> None:
        current = self._VP.current_params(self.tracker)
        rows = WB.settings_table(current, self._table_base(), self.s.sources)
        existing = set(self.table.get_children())
        for name, value, layer in rows:
            vals = (WB.format_value(value), layer)
            tags = ("you",) if layer == WB.LAYER_YOU else ()
            if name in existing:
                self.table.item(name, values=vals, tags=tags)
            else:
                self.table.insert("", tk.END, iid=name, text=name, values=vals, tags=tags)

    def apply_settings(self, changes: Dict[str, Any]) -> None:
        """Validate ``changes`` the command line's way (known names, each value
        converted to the setting's type, as a params file is) and apply them."""
        current = self._VP.current_params(self.tracker)
        layer = replace(self.s.defaults, snapshot_params={}, file_params=dict(changes),
                        set_params=())
        params, _ = OT.merge_params(current, layer)
        self.tracker.update_params(**params)
        self.tab.refresh_from_tracker()

    def _edit_setting(self, event) -> None:
        name = self.table.identify_row(event.y)
        if not name:
            return
        value = self._VP.current_params(self.tracker)[name]
        raw = simpledialog.askstring("Change setting", f"{name} =",
                                     initialvalue=WB.format_value(value), parent=self.root)
        if raw is None:
            return
        try:
            self.apply_settings({name: raw.strip()})
        except ValueError as e:
            messagebox.showerror("Change setting", str(e), parent=self.root)

    def _ask_load_params(self) -> None:
        path = filedialog.askopenfilename(title="Load params file", initialdir=self.video_dir,
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.apply_settings(OT.load_params_file(path))
        except (OSError, ValueError) as e:
            messagebox.showerror("Load params file", str(e), parent=self.root)

    def _ask_save_params(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save params file", initialdir=self.video_dir,
            initialfile=f"{self.s.defaults.session_id}_tracker_params.json",
            defaultextension=".json", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.save_params(path)

    def save_params(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._VP.current_params(self.tracker), fh, indent=2, sort_keys=True)

    def reset_settings(self) -> None:
        self.tracker.update_params(**self._table_base())
        self.tab.refresh_from_tracker()

    # ----------------------------------------------------------------- marks
    def _build_marks(self, page) -> None:
        ttk.Label(page, wraplength=RIGHT_W - 30, justify="left", text=(
            "Build models from this video's own frames, like the command line's "
            "--register-frames / --blocker-frames. Build the blocker model first so "
            "the rat model is built with the blocker already subtracted.")
                  ).pack(side=tk.TOP, fill=tk.X)
        b = ttk.LabelFrame(page, text="Blocker model: frames with the blocker placed, "
                                      "nothing else moving", padding=4)
        b.pack(side=tk.TOP, fill=tk.X, pady=4)
        row = ttk.Frame(b)
        row.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(row, text="Add / remove this frame", command=self.toggle_blocker_frame
                   ).pack(side=tk.LEFT)
        ttk.Button(row, text="Build blocker model", command=self.build_blocker).pack(side=tk.LEFT)
        self.blocker_list = tk.Listbox(b, height=5)
        self.blocker_list.pack(side=tk.TOP, fill=tk.X, pady=2)
        self.blocker_list.bind("<Double-1>", self._blocker_list_seek)

        r = ttk.LabelFrame(page, text="Rat model: the A-B range (only the rat in the maze, "
                                      "ideally running, turning and rearing)", padding=4)
        r.pack(side=tk.TOP, fill=tk.X, pady=4)
        ttk.Button(r, text="Build rat model from A-B", command=self.build_rat).pack(side=tk.LEFT)
        self.reg_var = tk.StringVar()
        ttk.Label(page, textvariable=self.reg_var, wraplength=RIGHT_W - 30,
                  justify="left").pack(side=tk.TOP, fill=tk.X, pady=4)

        g = ttk.LabelFrame(page, text="Earlier offline run (magenta ghost)", padding=4)
        g.pack(side=tk.TOP, fill=tk.X, pady=4)
        self.run_tag_var = tk.StringVar()
        self.run_combo = ttk.Combobox(g, textvariable=self.run_tag_var, state="readonly",
                                      width=30)
        self.run_combo.pack(side=tk.LEFT)
        self.run_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_run_ghost(
            self._tag_from_label(self.run_tag_var.get())))
        self._refresh_run_list()
        self.dist_var = tk.StringVar()
        ttk.Label(page, textvariable=self.dist_var, justify="left").pack(side=tk.TOP, fill=tk.X)

    def set_mark(self, which: str) -> None:
        idx = max(0, self.player.shown().idx)
        self.marks = replace(self.marks, **{which: idx})
        self._marks_changed()

    def clear_ab(self) -> None:
        self.marks = replace(self.marks, a=None, b=None)
        self._marks_changed()

    def toggle_blocker_frame(self) -> None:
        self.marks = self.marks.toggle_blocker(max(0, self.player.shown().idx))
        self._marks_changed()

    def _marks_changed(self) -> None:
        ab = self.marks.ab()
        self.ab_var.set(f"A-B: {ab[0]}-{ab[1]}" if ab else
                        f"A-B: {self.marks.a if self.marks.a is not None else '-'}"
                        f"-{self.marks.b if self.marks.b is not None else '-'}")
        self.blocker_list.delete(0, tk.END)
        for f in self.marks.blocker_frames:
            self.blocker_list.insert(tk.END, str(f))
        self._push_loop()
        self._draw_strip()

    def _blocker_list_seek(self, _e) -> None:
        sel = self.blocker_list.curselection()
        if sel:
            self.player.seek(self.marks.blocker_frames[sel[0]])

    def _push_loop(self) -> None:
        self.player.set_loop(self.marks, self.loop_var.get())

    def build_blocker(self) -> None:
        frames = self.marks.blocker_frames
        if not frames:
            self.status.set("mark at least one blocker frame first")
            return
        self.player.register(blocker_frames=frames)
        self._pending_reg = ("blocker", frames)
        self.reg_var.set("building blocker model...")

    def build_rat(self) -> None:
        ab = self.marks.ab()
        if ab is None:
            self.status.set("set A and B around a stretch with only the rat in the maze")
            return
        self.player.register(rat_range=ab)
        self._pending_reg = ("rat", ab)
        self.reg_var.set("building rat model...")

    def _registration_done(self, info: Dict[str, Any]) -> None:
        kind, frames = self._pending_reg
        self._pending_reg = (None, None)
        lines = []
        if "blocker" in info:
            self._set_file("blocker", OT.FileChoice(None, OT.SRC_REGISTERED))
            self.reg_blocker = tuple(frames) if kind == "blocker" else ()
            lines.append(f"Blocker model: {info['blocker']['samples']} samples.")
        if "rat" in info:
            r = info["rat"]
            self._set_file("model", OT.FileChoice(None, OT.SRC_REGISTERED))
            self.reg_rat = tuple(frames) if kind == "rat" else None
            cov = "good coverage" if r["coverage_ok"] else "THIN coverage - use a longer range"
            lines.append(f"Rat model: {r['samples']} samples (target "
                         f"{r['target_samples']}), {cov}.")
        lines.append("Not saved yet: Files > Save as, or a Run saves it beside its output.")
        self.reg_var.set("\n".join(lines))

    # ---------------------------------------------------------------- ghosts
    @staticmethod
    def _run_label(tag: Optional[str]) -> str:
        return "(untagged run)" if tag is None else tag

    def _tag_from_label(self, label: str) -> Optional[str]:
        if label in ("", "(none)"):
            return ""
        return None if label == "(untagged run)" else label

    def _refresh_run_list(self) -> None:
        tags = WB.offline_runs(self.s.launch.bundle)
        self.run_combo.configure(values=["(none)"] + [self._run_label(t) for t in tags])
        if not self.run_tag_var.get():
            self.run_tag_var.set("(none)")

    def load_run_ghost(self, tag: Optional[str]) -> None:
        """Show an earlier offline run (``""`` = none)."""
        if tag == "":
            self.run_track = {}
        else:
            path = OT.output_paths(self.video_dir, self.s.defaults.session_id, tag)[0]
            try:
                self.run_track = WB.read_track_points(path)
            except OSError as e:
                self.status.set(f"cannot read run: {e}")
                self.run_track = {}
        self._mark_dirty()

    # ------------------------------------------------------------------- run
    def _build_run(self, page) -> None:
        ttk.Label(page, wraplength=RIGHT_W - 30, justify="left", text=(
            "Track with the current setup and write video/<id>_offline_track_<tag>"
            ".csv + .json in the bundle. Models built here are saved beside them. "
            "The copied command line gives the same rows.")).pack(side=tk.TOP, fill=tk.X)
        f = ttk.Frame(page)
        f.pack(side=tk.TOP, fill=tk.X, pady=4)
        ttk.Label(f, text="tag").grid(row=0, column=0, sticky="w")
        self.tag_var = tk.StringVar(value="workbench")
        ttk.Entry(f, textvariable=self.tag_var, width=20).grid(row=0, column=1, sticky="w")
        self.range_var = tk.StringVar(value="all")
        ttk.Radiobutton(f, text="whole video", value="all", variable=self.range_var
                        ).grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(f, text="A-B range only", value="ab", variable=self.range_var
                        ).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="warm-up frames (range)").grid(row=3, column=0, sticky="w")
        self.run_warmup_var = tk.IntVar(value=WB.DEFAULT_WARMUP_FRAMES)
        ttk.Spinbox(f, from_=0, to=5000, increment=50, width=7,
                    textvariable=self.run_warmup_var).grid(row=3, column=1, sticky="w")
        self.overwrite_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="replace an existing run with this tag",
                        variable=self.overwrite_var).grid(row=4, column=0, columnspan=2,
                                                          sticky="w")
        row = ttk.Frame(page)
        row.pack(side=tk.TOP, fill=tk.X)
        self.run_btn = ttk.Button(row, text="Run", command=self.start_run)
        self.run_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(row, text="Cancel", state=tk.DISABLED,
                                     command=lambda: self.run_job and self.run_job.cancel())
        self.cancel_btn.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(page, maximum=max(1, self.s.n_frames))
        self.progress.pack(side=tk.TOP, fill=tk.X, pady=4)
        self.run_status = tk.StringVar()
        ttk.Label(page, textvariable=self.run_status, wraplength=RIGHT_W - 30,
                  justify="left").pack(side=tk.TOP, fill=tk.X)
        ttk.Button(page, text="Copy command line", command=self.copy_command
                   ).pack(side=tk.TOP, anchor="w", pady=(10, 2))
        self.cmd_text = tk.Text(page, height=9, wrap="word")
        self.cmd_text.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _range(self) -> Tuple[int, Optional[int], int]:
        if self.range_var.get() == "ab":
            ab = self.marks.ab()
            if ab is None:
                raise ValueError("set A and B first (or choose whole video)")
            return ab[0], ab[1], max(0, int(self.run_warmup_var.get()))
        return 0, None, 0

    def workbench_files(self) -> WB.WorkbenchFiles:
        f = self.files
        return WB.WorkbenchFiles(model=f["model"], blocker_model=f["blocker"], roi=f["roi"],
                                 landmarks=f["landmarks"], background=f["background"])

    def command(self) -> Tuple[List[str], List[str]]:
        """``(argv, warnings)`` of the equivalent offline_track command."""
        tag = self.tag_var.get().strip() or None
        start, end, warm = self._range()
        files = self.workbench_files()
        m = files.model
        base_model = m.path if m is not None and m.path else (
            self.s.defaults.model.path if m is not None and self.s.defaults.model else None)
        sets = WB.set_args(self.base_for(base_model), self._VP.current_params(self.tracker))
        reg = OT.RegistrationRequest(rat_frames=self.reg_rat, blocker_frames=self.reg_blocker)
        return WB.cli_args(self.s.defaults, files, sets, tag=tag, start_frame=start,
                           end_frame=end, warmup_frames=warm, registration=reg)

    def copy_command(self) -> None:
        try:
            argv, warnings = self.command()
        except ValueError as e:
            self.status.set(str(e))
            return
        text = WB.format_command(argv)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.cmd_text.delete("1.0", tk.END)
        self.cmd_text.insert(tk.END, text + "\n")
        for w in warnings:
            self.cmd_text.insert(tk.END, f"\nWARNING: {w}")
        self.run_status.set("Command copied to the clipboard.")

    def start_run(self) -> Optional[str]:
        """Start a run; returns an error message instead (also shown) if it can't."""
        if self.run_job is not None and not self.run_job.done():
            return None
        try:
            tag = OT.validate_tag(self.tag_var.get().strip() or None)
            start, end, warm = self._range()
            OT.frame_range(start, end, warm)
            csv_path, json_path = OT.output_paths(self.video_dir, self.s.defaults.session_id, tag)
            OT.check_overwrite((csv_path, json_path), self.overwrite_var.get())
            settings, _ = self._VP.prepare_run(self.s.defaults, self.workbench_files(),
                                               self.tracker, out_dir=self.video_dir, tag=tag)
        except (OSError, ValueError, RuntimeError) as e:
            self.run_status.set(str(e))
            return str(e)
        for kind, key in (("model", "model"), ("blocker", "blocker_model")):
            choice = getattr(settings, key)
            if self.files[kind] is not None and self.files[kind].path is None and choice:
                self._set_file(kind, choice)
        self.progress.configure(maximum=max(1, (end if end is not None else self.s.n_frames - 1)
                                            - start + 1))
        self.progress["value"] = 0
        self._run_start = start
        self.run_job = self._VP.RunJob(settings, csv_path=csv_path, json_path=json_path,
                                       tag=tag, start_frame=start, end_frame=end,
                                       warmup_frames=warm)
        self.run_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.run_status.set(f"running -> {os.path.basename(csv_path)}")
        return None

    def _poll_run(self) -> None:
        job = self.run_job
        if job is None:
            return
        idx, _ = job.progress
        self.progress["value"] = max(0, idx - self._run_start)
        if not job.done():
            return
        self.run_job = None
        self.run_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        if job.cancelled:
            self.run_status.set("Cancelled; nothing written.")
        elif job.error:
            self.run_status.set(f"Run failed: {job.error}")
        else:
            s = job.summary or {}
            fr = ", ".join(f"{k} {v:.0%}" for k, v in s.get("state_fractions", {}).items())
            self.run_status.set(f"Done: {s.get('frames_written', 0)} frames in "
                                f"{s.get('elapsed_s', 0):.0f} s ({fr}).\n{job.csv_path}")
            self._refresh_run_list()

    # ============================================================ navigation
    def _goto_frame(self) -> None:
        try:
            self.player.seek(int(self.frame_var.get()))
        except ValueError:
            pass
        self.root.focus_set()

    def _goto_trial(self) -> None:
        try:
            f = WB.trial_frame(self.s.markers, int(self.trial_var.get()))
        except ValueError:
            f = None
        self.root.focus_set()
        if f is None:
            self.status.set(f"no trial {self.trial_var.get()!r} in this video")
            return
        self.player.seek(f)

    def jump_trial(self, delta: int) -> None:
        if not self.s.markers:
            return
        cur = max(0, self.player.shown().idx)
        starts = [m.start for m in self.s.markers]
        if delta > 0:
            nxt = [s for s in starts if s > cur]
            target = nxt[0] if nxt else None
        else:
            prev = [s for s in starts if s < cur]
            target = prev[-1] if prev else None
        if target is not None:
            self.player.seek(target)

    def jump_event(self, attr: str) -> None:
        m = WB.trial_at(self.s.markers, max(0, self.player.shown().idx))
        f = getattr(m, attr, None) if m is not None else None
        if f is not None:
            self.player.seek(f)

    def _push_warmup(self) -> None:
        try:
            self.player.set_warmup(int(self.warmup_var.get()))
        except (ValueError, tk.TclError):
            pass

    # ----------------------------------------------------------------- clips
    def _clip_speed(self) -> float:
        texts = [self._speed_text(v) for v in WB.CLIP_SPEEDS]
        return WB.CLIP_SPEEDS[texts.index(self.clip_speed_var.get())]

    def _ask_save_clip(self) -> None:
        ab = self.marks.ab()
        if ab is None:
            self.status.set("set A and B around the stretch to save first")
            return
        path = filedialog.asksaveasfilename(
            title="Save A-B as clip", initialdir=self.video_dir,
            initialfile=WB.clip_name(self.s.defaults.session_id, *ab, self._clip_speed()),
            defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4")])
        if path:
            self.save_clip(path)

    def save_clip(self, path: str) -> Optional[str]:
        """Save the A-B range at the chosen speed, with the overlay as it is
        shown now; returns an error message instead (also shown) if it can't."""
        ab = self.marks.ab()
        try:
            if ab is None:
                raise ValueError("set A and B around the stretch to save first")
            if self.clip_cancel_btn.instate(["!disabled"]):
                raise ValueError("a clip is already being saved")
            plan = WB.clip_plan(*ab, fps=self.player.fps, speed=self._clip_speed(),
                                warmup=self.player.warmup)
        except ValueError as e:
            self.status.set(str(e))
            return str(e)
        overlay = self._overlay()
        self.player.save_clip(path, plan, lambda pic, idx, res:
                              overlay.draw(pic, 1.0, idx, res, pic.shape))
        self.clip_btn.configure(state=tk.DISABLED)
        self.clip_cancel_btn.configure(state=tk.NORMAL)
        self.clip_var.set("saving clip...")
        return None

    def _poll_clip(self) -> None:
        if self.clip_cancel_btn.instate(["disabled"]):
            return
        done = self.player.take_job_result("clip")
        if done is None:
            n, total = self.player.clip_progress
            self.clip_var.set(f"saving clip: {n} / {total} frames")
            return
        self.clip_btn.configure(state=tk.NORMAL)
        self.clip_cancel_btn.configure(state=tk.DISABLED)
        if done.get("cancelled"):
            self.clip_var.set("clip cancelled; nothing saved")
        elif done.get("error"):
            self.clip_var.set("")
            self.status.set(f"clip failed: {done['error']}")
        else:
            self.clip_var.set(f"saved {done['frames']} frames: "
                              f"{os.path.basename(done['path'])}")

    def _reset_tracker(self) -> None:
        self.player.reset_tracker()
        if not self.player.playing():
            self.player.process_current()

    def _strip_click(self, event) -> None:
        w = max(1, self.strip.winfo_width())
        self.player.seek(int(event.x / w * max(1, self.player.n_frames - 1)))

    def _draw_strip(self) -> None:
        c = self.strip
        c.delete("all")
        w = max(1, c.winfo_width())
        n = max(1, self.player.n_frames - 1)

        def x(f):
            return f / n * w

        ab = self.marks.ab()
        if ab:
            c.create_rectangle(x(ab[0]), 0, x(ab[1]), MARKER_H, fill="#2a4a7a", width=0)
        for m in self.s.markers:
            color = OUTCOME_COLORS.get(m.outcome or "", OTHER_TRIAL_COLOR)
            c.create_line(x(m.start), 4, x(m.start), MARKER_H - 4, fill=color, width=2)
        for f in self.marks.blocker_frames:
            c.create_line(x(f), 0, x(f), MARKER_H, fill="#ff9900", width=1)
        c.create_line(0, 0, 0, MARKER_H, fill="white", width=2, tags="cursor")

    def _bind_keys(self) -> None:
        sec = max(1, int(round(self.s.fps)))
        keys = {"<space>": self.player.toggle_play,
                "<Left>": lambda: self.player.step(-1),
                "<Right>": lambda: self.player.step(1),
                "<Shift-Left>": lambda: self.player.step(-sec),
                "<Shift-Right>": lambda: self.player.step(sec),
                "<bracketleft>": lambda: self.set_mark("a"),
                "<bracketright>": lambda: self.set_mark("b"),
                "<t>": lambda: self.trial_entry.focus_set()}
        for seq, fn in keys.items():
            self.root.bind_all(seq, lambda e, fn=fn: None if _is_text_widget(e.widget)
                               else (fn(), "break")[1])

    # ============================================================== drawing
    def _mark_dirty(self) -> None:
        self._dirty = True

    def _view_width(self, frame) -> int:
        h, w = frame.shape[:2]
        bw = max(320, self.view_box.winfo_width() - 4)
        bh = max(240, self.view_box.winfo_height() - 4)
        return int(min(bw, bh * w / max(1, h)))

    def _overlay(self) -> "Overlay":
        """What to draw, read from the toggles now (Tk thread only)."""
        return Overlay(show={k: v.get() for k, v in self.show.items()}, tracker=self.tracker,
                       zones=self.s.zones, live_track=self.s.live_track,
                       run_track=self.run_track)

    def _draw(self, disp, scale: float) -> None:
        shown = self.player.shown()
        self._overlay().draw(disp, scale, shown.idx, shown.result, shown.frame.shape)

    def _frame_time(self, idx: int) -> float:
        t = self.s.frame_times
        if t and 0 <= idx < len(t):
            return t[idx] - t[0]
        return idx / max(1e-6, self.player.fps)

    @staticmethod
    def _clock(sec: float) -> str:
        m, s = divmod(max(0.0, sec), 60)
        return f"{int(m):02d}:{s:04.1f}"

    def _update_readouts(self, shown) -> None:
        idx = shown.idx
        self.scrub_var.set(idx)
        if self.root.focus_get() is not self.frame_entry:
            self.frame_var.set(str(idx))
        total = self._frame_time(self.player.n_frames - 1)
        self.time_var.set(f"{self._clock(self._frame_time(idx))} / {self._clock(total)}")
        w = max(1, self.strip.winfo_width())
        x = idx / max(1, self.player.n_frames - 1) * w
        self.strip.coords("cursor", x, 0, x, MARKER_H)
        parts = []
        m = WB.trial_at(self.s.markers, idx)
        if m is not None:
            parts.append(f"trial {m.trial}: {m.outcome or '-'}"
                         + (f", cue {m.choice_cue}" if m.choice_cue else "")
                         + (f", chose {m.chosen_arm}" if m.chosen_arm else ""))
        res = shown.result
        if res is not None:
            parts.append(f"tracker: {res.state}")
        self.info.set("   |   ".join(parts))
        here = None
        if res is not None and res.smoothed is not None:
            here = WB.TrackPoint(res.state, res.smoothed[0], res.smoothed[1])
        d_live = WB.distance(here, self.s.live_track.get(idx))
        d_run = WB.distance(here, self.run_track.get(idx))
        fmt = lambda d: "-" if d is None else f"{d:.1f} px"  # noqa: E731
        self.dist_var.set(f"distance to live track: {fmt(d_live)}\n"
                          f"distance to earlier run: {fmt(d_run)}")

    # ================================================================ loops
    def _poll(self) -> None:
        if self._closed:
            return
        try:
            p = self.player
            err = p.take_error()
            if err:
                self.status.set(err)
            info = p.take_job_result("register")
            if info is not None:
                self._registration_done(info)
            busy = p.busy()
            self.play_btn.configure(text="Pause" if p.playing() else "Play")
            if busy:
                self.info.set(f"working: {busy}...")
            shown = p.shown()
            if shown.frame is not None and (shown.idx != self._last_idx or self._dirty):
                self._last_idx, self._dirty = shown.idx, False
                self._photo = render_bgr_to_label(self.view, shown.frame,
                                                  self._view_width(shown.frame), draw=self._draw,
                                                  copy_unscaled=True)
                self._update_readouts(shown)
            self._poll_run()
            self._poll_clip()
        finally:
            self._afters["poll"] = self.root.after(POLL_MS, self._poll)

    def _check_params(self) -> None:
        if self._closed:
            return
        try:
            cur = self._VP.current_params(self.tracker)
            if cur != self._last_params:
                self._last_params = cur
                self.tab.refresh_from_tracker()
                self._refresh_settings()
                if not self.player.playing():
                    self._schedule_retrack()
        finally:
            self._afters["params"] = self.root.after(PARAMS_CHECK_MS, self._check_params)

    def _schedule_retrack(self) -> None:
        old = self._afters.pop("retrack", None)
        if old is not None:
            self.root.after_cancel(old)

        def go():
            self._afters.pop("retrack", None)
            self.player.retrack()
        self._afters["retrack"] = self.root.after(RETRACK_DELAY_MS, go)

    def close(self) -> None:
        self._closed = True
        for after_id in self._afters.values():
            self.root.after_cancel(after_id)
        if self.run_job is not None:
            self.run_job.cancel()
        self.player.cancel_clip()
        self.tab.close()
        self.player.close()
        self.root.destroy()


# ------------------------------------------------------------------- launch
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tmaze.ui.offline_workbench",
        description="Tune the rat tracker on a recorded session video.")
    p.add_argument("bundle", nargs="?", help="session bundle folder (default: pick one)")
    p.add_argument("--model", help="rat appearance model .npz")
    b = p.add_mutually_exclusive_group()
    b.add_argument("--blocker-model", help="blocker model .npz")
    b.add_argument("--no-blocker", action="store_true", help="start without a blocker model")
    p.add_argument("--roi", help="ROI polygon .json")
    p.add_argument("--landmarks", help="landmarks .json")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--background", help="empty-rig background .png")
    g.add_argument("--background-from-video", action="store_true",
                   help="compute a median background from the video")
    p.add_argument("--params-file", help="JSON of settings to start from")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="change one setting at start (repeatable)")
    p.add_argument("--warmup", type=int, default=WB.DEFAULT_WARMUP_FRAMES,
                   help="frames re-tracked before a jumped-to frame")
    return p


def _overrides(args: argparse.Namespace) -> OT.Overrides:
    return OT.Overrides(model=args.model, blocker_model=args.blocker_model,
                        no_blocker=args.no_blocker, roi=args.roi, landmarks=args.landmarks,
                        background=args.background,
                        background_from_video=args.background_from_video,
                        params_file=args.params_file, sets=tuple(args.set))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    missing = missing_dependency("numpy", "opencv-python", "pillow")
    if missing:
        print(f"error: missing dependency {missing}", file=sys.stderr)
        return 2
    from tmaze.tools.offline_track import ratmodels_dir
    root = tk.Tk()
    bundle = args.bundle
    if not bundle:
        root.withdraw()
        bundle = filedialog.askdirectory(title="Pick a session bundle folder",
                                         initialdir=str(phase3_recordings_root()))
        if not bundle:
            root.destroy()
            return 1
    if not OT.is_bundle(bundle):
        messagebox.showerror("Offline workbench",
                             f"Not a session bundle (no video/<id>.mp4|.mkv):\n{bundle}")
        root.destroy()
        return 2
    print(f"loading {bundle} (a median background takes a few seconds)...", flush=True)
    try:
        session = load_session(bundle, _overrides(args), ratmodels_dir=ratmodels_dir())
    except (OSError, ValueError, RuntimeError) as e:
        messagebox.showerror("Offline workbench", str(e))
        root.destroy()
        return 2
    root.deiconify()
    root.geometry("1900x1020")
    OfflineWorkbench(root, session, ratmodels_dir=ratmodels_dir(), warmup=args.warmup)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
