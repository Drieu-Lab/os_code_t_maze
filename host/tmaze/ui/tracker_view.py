"""
tracker_view.py - "Tracking" tab for experiment_control.py.

A Tk control surface for the shared markerless tracker (tracker.RatTracker). The
heavy lifting lives in tracker.py and runs on the camera grab thread (see
camera_view.py); this tab only *controls* the tracker and *reads* its latest
result, following the same module contract as the other tabs:

    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None  to handle each serial line (learns the animal id)
    close() -> None           to finalize any standalone track log

What it offers:
  - Enable tracking (drives CameraView.enable_tracking; off by default).
  - Grab background (empty-rig reference) and a live state/confidence readout.
  - Register the rat: accumulate the colour+size+shape appearance model over a
    window of frames; optional online enrichment while tracking.
  - Tuning sliders (threshold, min area, blur, gate, accept score, coast frames).
  - Optional binary-mask view (stacked under the live preview) with the
    currently tracked blob filled green.
  - Save/Load the appearance model (.npz, per animal) and the maze ROI polygon
    (host/roi_polygon.json), the latter drawn back on the camera preview.
  - Define the ROI by clicking a captured still frame (precise, and the moving
    rat doesn't interfere).
  - Start/stop a standalone track CSV (independent of video recording).

Heavy deps (numpy, OpenCV, Pillow) are imported defensively so importing this
module never breaks experiment_control.py.
"""

from __future__ import annotations

import csv
import json
import os
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, FrozenSet, Optional

from tmaze.core import protocol
from tmaze.paths import host_dir
from tmaze.ui.gui_deps import Image, ImageTk, cv2, missing_dependency, np
from tmaze.ui.preview import PreviewLoop, safe_after
from tmaze.ui.still_frame_editor import _StillFrameEditorBase
from tmaze.ui.tooltips import tip
from tmaze.vision.tracker import TRACK_COLUMNS, track_row

# Shared tracking-overlay drawing (so this preview matches the camera tab's).
try:
    from tmaze.vision.tracker import draw_overlay as _draw_overlay  # noqa: E402
except Exception:  # pragma: no cover
    _draw_overlay = None  # type: ignore

# Canonical fixed landmark set (reward wells + body-sensor beams). Guarded so a
# missing tracker core doesn't break this module's import.
try:
    from tmaze.vision.tracker import FIXED_LANDMARKS as _FIXED_LANDMARKS  # noqa: E402
except Exception:  # pragma: no cover
    _FIXED_LANDMARKS = ()  # type: ignore

# Parameter groups persisted with a saved model (see ADR-0027): which settings
# get restored on load, and which of those are the correctness-critical shaping
# knobs (a proc_max_w change means the background must be re-grabbed).
try:
    from tmaze.vision.tracker import (MODEL_REAPPLY_FIELDS as _MODEL_REAPPLY_FIELDS,  # noqa: E402
                         MODEL_SHAPING_FIELDS as _MODEL_SHAPING_FIELDS)
except Exception:  # pragma: no cover
    _MODEL_REAPPLY_FIELDS = ()  # type: ignore
    _MODEL_SHAPING_FIELDS = ()  # type: ignore

# Recordings folder default (camera_view guards its heavy imports, so this is
# safe even without pypylon/OpenCV). Track logs live in a subfolder of whatever
# recordings folder the camera is actually using.
try:
    from tmaze.ui.camera_view import DEFAULT_OUTPUT_DIR as _RECORDINGS_DIR  # noqa: E402
except Exception:  # pragma: no cover
    _RECORDINGS_DIR = "recordings"


# ================================== CONFIG ===================================
_HERE = str(host_dir())   # host/ - the per-rig config lives beside the package (ADR-0083)
ROI_PATH = os.path.join(_HERE, "roi_polygon.json")   # shared with the prototype
# Operator-drawn LED board search region (host/led_board_search.json), an INPUT to
# `sync_fit --map` (ADR-0195). Same normalized polygon_norm schema as the tracker ROI.
LED_BOARD_SEARCH_PATH = os.path.join(_HERE, "led_board_search.json")
LANDMARKS_PATH = os.path.join(_HERE, "landmarks.json")  # reward-well/body-sensor points
MODEL_DIR = os.path.join(_HERE, "ratmodels")          # appearance models (.npz)
TRACK_LOG_SUBDIR = "track_logs"   # subfolder of the recordings dir for track CSVs

# Control groups a host can hide (TrackerTabOptions.hide).
HIDE_ENABLE = "enable"          # Enable tracking checkbox
HIDE_GRAB_BG = "grab_bg"        # Grab background button
HIDE_LED_BOARD = "led_board"    # LED board search region row
HIDE_TRACK_LOG = "track_log"    # standalone track log row


@dataclass(frozen=True)
class TrackerTabOptions:
    """How the tab loads and saves its files. The defaults are the live rig's
    behaviour; the offline workbench hosts the tab with options that never read
    or write the rig's host/ files (see docs/decisions/0213).

    ``ask_before_saving``: Save ROI / landmarks go through a Save-as dialog
    (opening in ``file_dir``), defining landmarks no longer saves them, and
    Clear landmarks no longer deletes a file.

    ``on_event(kind, action, path)`` is called after the operator changes a
    file-backed setting: kind is ``model`` / ``blocker`` / ``roi`` /
    ``landmarks`` / ``background``; action is ``load`` / ``save`` (with the
    path), or ``edit`` / ``register`` / ``clear`` / ``capture`` (path None).
    """
    load_host_files: bool = True
    ask_before_saving: bool = False
    hide: FrozenSet[str] = frozenset()
    show_preview: bool = True
    file_dir: Optional[str] = None      # ROI / landmarks / background dialogs
    model_dir: str = MODEL_DIR          # model + blocker model dialogs
    on_event: Optional[Callable[[str, str, Optional[str]], None]] = None

POLL_INTERVAL_MS = 150       # readout / mask / standalone-log refresh
PREVIEW_INTERVAL_MS = 66     # ~15 fps live preview (only while the tab is visible)
PREVIEW_MAX_W = 640          # live preview width on this tab (right column)
MASK_VIEW_W = 420            # colorized mask width, stacked under the preview
# EDITOR_MAX_W (still-frame editor display width) is shared via still_frame_editor.

# Tuning sliders: (label, param-name, lo, hi, is_float, scale-to-param).
_SLIDERS = (
    ("Threshold", "thresh", 0, 255, False, 1.0),
    ("Min area", "min_area", 0, 8000, False, 1.0),
    ("Blur", "blur", 1, 31, False, 1.0),
    ("Gate (px)", "gate_radius", 0, 500, False, 1.0),
    ("Cluster gap (px)", "cluster_gap", 0, 100, False, 1.0),  # 0 = off (ADR-0037)
    ("Accept", "accept_score", 0, 100, False, 0.01),   # slider 0..100 -> 0..1
    ("Coast (frames)", "coast_max_frames", 1, 200, False, 1.0),
)

# Appearance-model tolerance knobs (live at scoring time; see ADR-0014). Shown in
# a collapsible "Model tolerances" section so the main panel stays uncluttered.
_TOL_SLIDERS = (
    ("Size tol (k)", "area_gate_k", 1.0, 6.0, True, 1.0),       # exp(muA +/- k*sigmaA)
    ("Appearance bias", "appearance_bias", 0.0, 1.0, True, 1.0),  # appearance vs distance
    ("Shape tol", "form_tol", 0.2, 5.0, True, 1.0),            # Gaussian softness
    ("Color gate", "hist_min", 0.0, 1.0, True, 1.0),           # min hue/sat correlation (0=off)
    ("Cluster color gate", "cluster_hist_min", 0.0, 1.0, True, 1.0),  # merge membership (ADR-0037)
)


class TrackerControlView:
    """Tk module: controls + live readout for the shared RatTracker."""

    def __init__(self, parent: tk.Misc, tracker=None, camera=None,
                 options: Optional[TrackerTabOptions] = None):
        self._parent = parent
        self._tracker = tracker
        self._camera = camera
        self._opts = options or TrackerTabOptions()
        self.frame = ttk.Frame(parent)
        self._animal = ""
        self._photo_mask = None       # keep ref so Tk doesn't GC the mask image
        self._log_file = None
        self._log_writer = None
        self._log_idx = 0
        self._sliders = {}            # param-name -> (tk var, scale-factor)
        self._slider_labels = {}      # param-name -> value Label (for refresh)
        # Path of the appearance model last loaded/saved (None if the live model
        # came from Register rat and was never saved). Read by the Session panel
        # to snapshot the active model into the session config (docs/decisions/0072).
        self._model_path: Optional[str] = None
        self._blocker_path: Optional[str] = None   # same, for the blocker model
        self._closed = False

        missing = self._missing_dependency()
        if tracker is None or missing:
            why = ("the tracker core is unavailable"
                   if tracker is None else f"missing dependency:\n{missing}")
            ttk.Label(self.frame, padding=24, anchor="center", justify="center",
                      text=f"Tracking tab unavailable - {why}.").pack(
                fill=tk.BOTH, expand=True)
            return

        self._build_ui()
        # Reuse a previously drawn ROI (also written by the prototype), if any.
        if self._opts.load_host_files and os.path.exists(ROI_PATH):
            try:
                self._tracker.load_roi(ROI_PATH)
                self._roi_status.set(f"ROI: loaded {os.path.basename(ROI_PATH)}")
            except Exception:
                pass
        # Reuse previously marked landmarks (reward wells + body sensors), if any.
        if self._opts.load_host_files and os.path.exists(LANDMARKS_PATH):
            try:
                self._tracker.load_landmarks(LANDMARKS_PATH)
            except Exception:
                pass
        self._set_landmark_status()
        self._push_params()
        self._schedule_poll()
        if self._opts.show_preview:
            self._preview_loop = PreviewLoop(
                self.frame, self._preview, PREVIEW_INTERVAL_MS, PREVIEW_MAX_W,
                source=lambda: self._camera.get_latest_frame() if self._camera else None,
                draw=self._draw_preview, is_closed=lambda: self._closed)
            self._preview_loop.start()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, info: protocol.LineInfo) -> None:
        """Learn the animal id from the serial stream (for model filenames).
        Reads the pre-parsed protocol.LineInfo (docs/decisions/0056)."""
        if info.animal is not None:
            self._animal = info.animal
            if self._tracker is not None:
                self._tracker.set_animal(self._animal)

    def close(self) -> None:
        self._closed = True
        self._stop_log()

    # ---------------------------------------------------------- dependencies
    @staticmethod
    def _missing_dependency() -> Optional[str]:
        return missing_dependency("numpy", "opencv-python", "pillow")

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = dict(padx=4, pady=2)

        # Two columns: controls on the left, live preview filling the space on
        # the right. The preview mirrors the camera tab and only renders while
        # this tab is visible, so it costs nothing on the Rig view tab.
        content = ttk.Frame(self.frame)
        content.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        controls = ttk.Frame(content)
        controls.pack(side=tk.LEFT, fill=tk.Y, anchor="n")
        # Right column: live camera preview on top, colorized mask stacked below.
        hide = self._opts.hide
        viewcol = ttk.Frame(content)
        if self._opts.show_preview:
            viewcol.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._preview = ttk.Label(viewcol, anchor="center")
        self._preview.pack(side=tk.TOP, fill=tk.BOTH, expand=True, **pad)
        self._mask_label = ttk.Label(viewcol, anchor="center")
        self._mask_label.pack(side=tk.TOP, anchor="center", **pad)

        # Enable + background.
        top = ttk.Frame(controls)
        top.pack(side=tk.TOP, fill=tk.X, **pad)
        self._enable_var = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(top, text="Enable tracking", variable=self._enable_var,
                             command=self._on_enable)
        if HIDE_ENABLE not in hide:
            cb.pack(side=tk.LEFT)
        tip(cb, "tracker.enable")
        if self._camera is None:
            cb.configure(state=tk.DISABLED)
            self._enable_var.set(False)
        grab_bg_btn = ttk.Button(top, text="Grab background", command=self._grab_bg)
        if HIDE_GRAB_BG not in hide:
            grab_bg_btn.pack(side=tk.LEFT, padx=(12, 2))
        tip(grab_bg_btn, "tracker.grab_bg")
        save_bg_btn = ttk.Button(top, text="Save background...",
                                 command=self._save_background)
        save_bg_btn.pack(side=tk.LEFT, padx=2)
        tip(save_bg_btn, "tracker.save_bg")
        self._bg_status = tk.StringVar(value="reference: none")
        ttk.Label(top, textvariable=self._bg_status).pack(side=tk.LEFT, padx=4)

        # Registration + enrichment.
        reg = ttk.LabelFrame(controls, text="Appearance model")
        reg.pack(side=tk.TOP, fill=tk.X, **pad)
        self._reg_btn = ttk.Button(reg, text="Register rat", command=self._toggle_register)
        self._reg_btn.pack(side=tk.LEFT, padx=4, pady=4)
        tip(self._reg_btn, "tracker.register")
        cancel_btn = ttk.Button(reg, text="Cancel", command=self._cancel_register)
        cancel_btn.pack(side=tk.LEFT, padx=2)
        tip(cancel_btn, "tracker.cancel")
        self._enrich_var = tk.BooleanVar(value=bool(self._tracker.get_params().adapt_enrich))
        enrich_cb = ttk.Checkbutton(reg, text="Enrich while tracking",
                                    variable=self._enrich_var,
                                    command=self._push_params)
        enrich_cb.pack(side=tk.LEFT, padx=(12, 2))
        tip(enrich_cb, "tracker.enrich")
        self._model_status = tk.StringVar(value="model: none")
        ttk.Label(reg, textvariable=self._model_status).pack(side=tk.LEFT, padx=6)

        # Model + ROI persistence.
        persist = ttk.Frame(controls)
        persist.pack(side=tk.TOP, fill=tk.X, **pad)
        save_model_btn = ttk.Button(persist, text="Save model...",
                                    command=self._save_model)
        save_model_btn.pack(side=tk.LEFT, padx=2)
        tip(save_model_btn, "tracker.save_model")
        load_model_btn = ttk.Button(persist, text="Load model...",
                                    command=self._load_model)
        load_model_btn.pack(side=tk.LEFT, padx=2)
        tip(load_model_btn, "tracker.load_model")
        clear_model_btn = ttk.Button(persist, text="Clear model",
                                     command=self._clear_model)
        clear_model_btn.pack(side=tk.LEFT, padx=2)
        tip(clear_model_btn, "tracker.clear_model")

        # Blocker (orange-taped obstacle): on-demand still captures across
        # placements; tracked in parallel and subtracted from the rat. ADR-0034.
        blk = ttk.LabelFrame(controls, text="Blocker (orange tape)")
        blk.pack(side=tk.TOP, fill=tk.X, **pad)
        cap_btn = ttk.Button(blk, text="Capture sample", command=self._capture_blocker)
        cap_btn.pack(side=tk.LEFT, padx=4, pady=4)
        tip(cap_btn, "tracker.blocker_capture")
        finish_btn = ttk.Button(blk, text="Finish", command=self._finish_blocker)
        finish_btn.pack(side=tk.LEFT, padx=2)
        tip(finish_btn, "tracker.blocker_finish")
        cancelb_btn = ttk.Button(blk, text="Cancel", command=self._cancel_blocker)
        cancelb_btn.pack(side=tk.LEFT, padx=2)
        tip(cancelb_btn, "tracker.blocker_cancel")
        clearb_btn = ttk.Button(blk, text="Clear", command=self._clear_blocker)
        clearb_btn.pack(side=tk.LEFT, padx=2)
        tip(clearb_btn, "tracker.blocker_clear")
        saveb_btn = ttk.Button(blk, text="Save...", command=self._save_blocker)
        saveb_btn.pack(side=tk.LEFT, padx=2)
        tip(saveb_btn, "tracker.blocker_save")
        loadb_btn = ttk.Button(blk, text="Load...", command=self._load_blocker)
        loadb_btn.pack(side=tk.LEFT, padx=2)
        tip(loadb_btn, "tracker.blocker_load")
        self._blocker_status = tk.StringVar(value="blocker: none")
        ttk.Label(blk, textvariable=self._blocker_status).pack(side=tk.LEFT, padx=6)

        region = ttk.Frame(controls)
        region.pack(side=tk.TOP, fill=tk.X, **pad)
        define_region_btn = ttk.Button(region, text="Define region...",
                                       command=self._define_region)
        define_region_btn.pack(side=tk.LEFT, padx=2)
        tip(define_region_btn, "tracker.define_region")
        clear_region_btn = ttk.Button(region, text="Clear region",
                                      command=self._clear_region)
        clear_region_btn.pack(side=tk.LEFT, padx=2)
        tip(clear_region_btn, "tracker.clear_region")
        save_roi_btn = ttk.Button(
            region, text=("Save ROI..." if self._opts.ask_before_saving else "Save ROI"),
            command=self._save_roi)
        save_roi_btn.pack(side=tk.LEFT, padx=2)
        tip(save_roi_btn, "tracker.save_roi")
        load_roi_btn = ttk.Button(region, text="Load ROI", command=self._load_roi)
        load_roi_btn.pack(side=tk.LEFT, padx=2)
        tip(load_roi_btn, "tracker.load_roi")
        self._roi_status = tk.StringVar(value="ROI: none")
        ttk.Label(region, textvariable=self._roi_status).pack(side=tk.LEFT, padx=6)

        # LED board search region (host/led_board_search.json): a polygon around the
        # whole indicator board, an input to `sync_fit --map` (ADR-0195). Separate
        # from the tracker ROI above; drawn with the same still-frame polygon editor.
        ledboard = ttk.Frame(controls)
        if HIDE_LED_BOARD not in hide:
            ledboard.pack(side=tk.TOP, fill=tk.X, **pad)
        define_ledbrd_btn = ttk.Button(ledboard, text="Define LED board region...",
                                       command=self._define_led_board_region)
        define_ledbrd_btn.pack(side=tk.LEFT, padx=2)
        tip(define_ledbrd_btn, "tracker.define_led_board_region")
        self._led_board_status = tk.StringVar(
            value=("LED board: saved" if os.path.isfile(LED_BOARD_SEARCH_PATH)
                   else "LED board: none"))
        ttk.Label(ledboard, textvariable=self._led_board_status).pack(side=tk.LEFT, padx=6)

        # Landmark annotations: reward wells + body-sensor points (and any custom
        # points) marked on a still frame, drawn back on the camera preview.
        marks = ttk.Frame(controls)
        marks.pack(side=tk.TOP, fill=tk.X, **pad)
        define_lm_btn = ttk.Button(marks, text="Define landmarks...",
                                   command=self._define_landmarks)
        define_lm_btn.pack(side=tk.LEFT, padx=2)
        tip(define_lm_btn, "tracker.define_landmarks")
        clear_lm_btn = ttk.Button(marks, text="Clear landmarks",
                                  command=self._clear_landmarks)
        clear_lm_btn.pack(side=tk.LEFT, padx=2)
        tip(clear_lm_btn, "tracker.clear_landmarks")
        if self._opts.ask_before_saving:
            save_lm_btn = ttk.Button(marks, text="Save landmarks...",
                                     command=self._save_landmarks_as)
            save_lm_btn.pack(side=tk.LEFT, padx=2)
            tip(save_lm_btn, "tracker.save_landmarks")
        self._landmark_status = tk.StringVar(value="landmarks: none")
        ttk.Label(marks, textvariable=self._landmark_status).pack(side=tk.LEFT, padx=6)

        # Tuning sliders.
        tune = ttk.LabelFrame(controls, text="Tuning")
        tune.pack(side=tk.TOP, fill=tk.X, **pad)
        params = self._tracker.get_params()
        for label, name, lo, hi, is_float, factor in _SLIDERS:
            init = getattr(params, name) / factor if factor != 1.0 else getattr(params, name)
            self._add_slider(tune, label, name, lo, hi, is_float, factor, init)

        # Appearance-model tolerances (collapsed by default to save height).
        tol = self._add_collapsible(controls, "Model tolerances", expanded=False)
        for label, name, lo, hi, is_float, factor in _TOL_SLIDERS:
            init = getattr(params, name) / factor if factor != 1.0 else getattr(params, name)
            self._add_slider(tol, label, name, lo, hi, is_float, factor, init)

        # Mask preview + readout.
        bottom = ttk.Frame(controls)
        bottom.pack(side=tk.TOP, fill=tk.BOTH, expand=True, **pad)
        self._show_mask_var = tk.BooleanVar(value=False)
        show_mask_cb = ttk.Checkbutton(
            bottom, text="Show mask (tracked blob in green)",
            variable=self._show_mask_var, command=self._on_show_mask)
        if self._opts.show_preview:
            show_mask_cb.pack(side=tk.TOP, anchor="w")
        tip(show_mask_cb, "tracker.show_mask")
        self._readout = tk.StringVar(value="state: -")
        readout_lbl = ttk.Label(bottom, textvariable=self._readout, anchor="w")
        readout_lbl.pack(side=tk.TOP, fill=tk.X)
        tip(readout_lbl, "tracker.readout")

        # Standalone track log.
        logrow = ttk.Frame(controls)
        if HIDE_TRACK_LOG not in hide:
            logrow.pack(side=tk.TOP, fill=tk.X, **pad)
        self._log_btn = ttk.Button(logrow, text="Start track log", command=self._toggle_log)
        self._log_btn.pack(side=tk.LEFT, padx=2)
        tip(self._log_btn, "tracker.log")
        self._log_status = tk.StringVar(value="log: off")
        ttk.Label(logrow, textvariable=self._log_status).pack(side=tk.LEFT, padx=6)

    def _add_collapsible(self, parent, title, expanded=False):
        """A simple expand/collapse section; returns the inner frame for content."""
        holder = ttk.Frame(parent)
        holder.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)
        inner = ttk.Frame(holder)
        state = {"open": bool(expanded)}
        btn_text = tk.StringVar()

        def render():
            btn_text.set(("\u25be " if state["open"] else "\u25b8 ") + title)
            if state["open"]:
                inner.pack(side=tk.TOP, fill=tk.X)
            else:
                inner.pack_forget()

        def toggle():
            state["open"] = not state["open"]
            render()

        ttk.Button(holder, textvariable=btn_text, command=toggle).pack(
            side=tk.TOP, fill=tk.X)
        render()
        return inner

    def _add_slider(self, parent, label, name, lo, hi, is_float, factor, init) -> None:
        row = ttk.Frame(parent)
        row.pack(side=tk.TOP, fill=tk.X, pady=1)
        ttk.Label(row, text=label, width=14, anchor="w").pack(side=tk.LEFT)
        var = tk.DoubleVar(value=init)
        val = ttk.Label(row, width=7, anchor="e")

        def on_change(_=None):
            v = var.get()
            val.configure(text=(f"{v:.2f}" if is_float else f"{int(round(v))}"))
            self._push_params()

        scale = ttk.Scale(row, from_=lo, to=hi, variable=var, command=on_change)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        val.pack(side=tk.LEFT)
        tip(scale, f"tracker.{name}")
        self._sliders[name] = (var, factor)
        self._slider_labels[name] = (val, is_float)
        on_change()

    def _refresh_controls(self, params) -> None:
        """Re-read tracker params into the slider vars/labels (e.g. after a load
        restores the model's saved settings). Does not push params back."""
        for name, (var, factor) in self._sliders.items():
            raw = getattr(params, name)
            var.set(raw / factor if factor != 1.0 else raw)
            entry = self._slider_labels.get(name)
            if entry is not None:
                lbl, is_float = entry
                v = var.get()
                lbl.configure(text=(f"{v:.2f}" if is_float else f"{int(round(v))}"))

    # --------------------------------------------------------------- actions
    def _push_params(self) -> None:
        if self._tracker is None:
            return
        kwargs = {}
        for name, (var, factor) in self._sliders.items():
            v = var.get() * factor
            if name in ("thresh", "blur", "coast_max_frames"):
                v = int(round(v))
            kwargs[name] = v
        kwargs["adapt_enrich"] = bool(self._enrich_var.get())
        try:
            self._tracker.update_params(**kwargs)
        except Exception:
            pass

    def _on_enable(self) -> None:
        on = bool(self._enable_var.get())
        if self._camera is not None:
            self._camera.enable_tracking(on)

    def _grab_bg(self) -> None:
        self._tracker.grab_background()
        self._bg_status.set("reference: requested")

    def _save_background(self) -> None:
        """Save the full-res colour empty-rig frame for offline tuning (ADR-0020)."""
        bg = self._tracker.get_background_image()
        if bg is None:
            messagebox.showinfo("Save background",
                                "No background yet - grab a background first.")
            return
        initialdir = self._opts.file_dir or _HERE
        if self._camera is not None and self._opts.file_dir is None:
            try:
                initialdir = self._camera.get_output_dir()
            except Exception:
                pass
        try:
            os.makedirs(initialdir, exist_ok=True)
        except OSError:
            initialdir = _HERE
        animal = self._animal or "animal"
        default = f"{animal}_{time.strftime('%Y%m%d_%H%M%S')}_background.png"
        path = filedialog.asksaveasfilename(
            title="Save background image", initialdir=initialdir,
            initialfile=default, defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")])
        if not path:
            return
        try:
            if cv2.imwrite(path, bg):
                self._bg_status.set(f"reference: saved {os.path.basename(path)}")
                self._notify("background", "save", path)
            else:
                messagebox.showerror("Save background", "OpenCV failed to write the image.")
        except Exception as e:
            messagebox.showerror("Save background", f"{type(e).__name__}: {e}")

    def _notify(self, kind: str, action: str, path: Optional[str] = None) -> None:
        if self._opts.on_event is not None:
            self._opts.on_event(kind, action, path)

    def _toggle_register(self) -> None:
        if self._tracker.is_registering():
            self._tracker.finalize_registration()
            self._notify("model", "register")
        elif not self._tracker.has_reference():
            messagebox.showinfo("Register rat",
                                "Grab a background first, then register the rat.")
        else:
            if not self.tracking_running():
                messagebox.showinfo(
                    "Register rat",
                    "Enable tracking (and have the camera running) so the tracker "
                    "sees frames during registration.")
                return
            self._tracker.start_registration()

    def _cancel_register(self) -> None:
        self._tracker.cancel_registration()

    def tracking_running(self) -> bool:
        return self._camera is not None and self._camera.tracking_enabled()

    # ----------------------------------------------------------- model I/O
    def _save_model(self) -> None:
        if not self._tracker.has_model():
            messagebox.showinfo("Save model", "No model yet - register the rat first.")
            return
        os.makedirs(self._opts.model_dir, exist_ok=True)
        animal = self._animal or "animal"
        default = f"{animal}_ratmodel.npz"
        path = filedialog.asksaveasfilename(
            title="Save appearance model", initialdir=self._opts.model_dir,
            initialfile=default, defaultextension=".npz",
            filetypes=[("RatModel", "*.npz"), ("All files", "*.*")])
        if not path:
            return
        try:
            self._tracker.save_model(path)
            self._model_path = path
            self._model_status.set(f"model: saved {os.path.basename(path)}")
            self._notify("model", "save", path)
        except Exception as e:
            messagebox.showerror("Save model", f"{type(e).__name__}: {e}")

    def _load_model(self) -> None:
        path = filedialog.askopenfilename(
            title="Load appearance model", initialdir=self._opts.model_dir,
            filetypes=[("RatModel", "*.npz"), ("All files", "*.*")])
        if not path:
            return
        try:
            before = self._tracker.get_params()
            self._tracker.load_model(path)
            self._model_path = path
            m = self._tracker.get_model()
            self._model_status.set(
                f"model: {os.path.basename(path)} (n={m.n}, "
                f"{'cov' if m.coverage_ok else 'thin'}"
                f"{', ' + m.animal if m.animal else ''})")
            # load_model restored the model's saved settings; sync the controls.
            after = self._tracker.get_params()
            self._refresh_controls(after)
            self._notify("model", "load", path)
            messagebox.showinfo("Load model",
                                self._load_model_message(m, before, after))
        except Exception as e:
            messagebox.showerror("Load model", f"{type(e).__name__}: {e}")

    @staticmethod
    def _load_model_message(model, before, after) -> str:
        """Human summary of what a model load restored (see ADR-0027)."""
        if getattr(model, "acq_params", None) is None:
            return ("Model loaded. It has no stored settings (older model), so "
                    "your current tuning was kept - verify proc_max_w matches. "
                    "Grab a fresh background for this session.")
        changed = []
        for name in _MODEL_REAPPLY_FIELDS:
            old, new = getattr(before, name), getattr(after, name)
            if old != new:
                changed.append(f"  - {name}: {old} -> {new}")
        lines = ["Model loaded; its saved settings were restored."]
        if changed:
            lines.append("")
            lines.append("Changed to match the model:")
            lines.extend(changed)
        else:
            lines.append("(Current settings already matched the model.)")
        lines.append("")
        regrab = any(getattr(before, f) != getattr(after, f)
                     for f in _MODEL_SHAPING_FIELDS)
        if regrab:
            lines.append("Detection settings changed - grab a fresh background.")
        else:
            lines.append("Remember to grab a fresh background for this session "
                         "(lighting drifts day to day).")
        return "\n".join(lines)

    def _clear_model(self) -> None:
        self._tracker.clear_model()
        self._model_path = None
        self._model_status.set("model: none")
        self._notify("model", "clear")

    def active_model_path(self) -> Optional[str]:
        """Path of the appearance model currently loaded/saved (``.npz``), or None.

        Read by the Session panel to copy the active model into the session's
        config snapshot (docs/decisions/0072)."""
        return self._model_path

    def active_blocker_model_path(self) -> Optional[str]:
        """Path of the blocker model currently loaded/saved, or None (none, or
        built from captures and not saved)."""
        return self._blocker_path

    def set_active_paths(self, model_path: Optional[str],
                         blocker_path: Optional[str]) -> None:
        """Record which files a host loaded into the tracker itself."""
        self._model_path = model_path
        self._blocker_path = blocker_path

    def refresh_from_tracker(self) -> None:
        """Re-read sliders + the enrich checkbox after a host changed the
        tracker's settings directly. Does not push settings back."""
        if self._tracker is None or not self._sliders:
            return
        params = self._tracker.get_params()
        self._refresh_controls(params)
        self._enrich_var.set(bool(params.adapt_enrich))

    # ----------------------------------------------------------- blocker I/O
    def _capture_blocker(self) -> None:
        if not self._tracker.has_reference():
            messagebox.showinfo(
                "Blocker", "Grab a background of the EMPTY rig first, then place "
                "the blocker, withdraw your hand, and capture.")
            return
        if not self.tracking_running():
            messagebox.showinfo(
                "Blocker", "Enable tracking (with the camera running) so the "
                "tracker sees the frame to sample.")
            return
        self._tracker.capture_blocker_sample()
        self._notify("blocker", "capture")

    def _finish_blocker(self) -> None:
        registering, n = self._tracker.blocker_registration_status()
        if not registering or n <= 0:
            messagebox.showinfo(
                "Blocker", "Capture at least one sample (at a few placements) first.")
            return
        self._tracker.finalize_blocker_model()
        self._blocker_path = None
        self._notify("blocker", "register")

    def _cancel_blocker(self) -> None:
        self._tracker.cancel_blocker_registration()

    def _clear_blocker(self) -> None:
        self._tracker.clear_blocker_model()
        self._blocker_path = None
        self._blocker_status.set("blocker: none")
        self._notify("blocker", "clear")

    def _save_blocker(self) -> None:
        if not self._tracker.has_blocker_model():
            messagebox.showinfo("Save blocker",
                                "No blocker model yet - capture samples and Finish first.")
            return
        os.makedirs(self._opts.model_dir, exist_ok=True)
        animal = self._animal or "animal"
        default = f"{animal}_blockermodel.npz"
        path = filedialog.asksaveasfilename(
            title="Save blocker model", initialdir=self._opts.model_dir,
            initialfile=default, defaultextension=".npz",
            filetypes=[("Blocker model", "*.npz"), ("All files", "*.*")])
        if not path:
            return
        try:
            self._tracker.save_blocker_model(path)
            self._blocker_path = path
            self._blocker_status.set(f"blocker: saved {os.path.basename(path)}")
            self._notify("blocker", "save", path)
        except Exception as e:
            messagebox.showerror("Save blocker", f"{type(e).__name__}: {e}")

    def _load_blocker(self) -> None:
        path = filedialog.askopenfilename(
            title="Load blocker model", initialdir=self._opts.model_dir,
            filetypes=[("Blocker model", "*.npz"), ("All files", "*.*")])
        if not path:
            return
        try:
            self._tracker.load_blocker_model(path)
            self._blocker_path = path
            self._notify("blocker", "load", path)
        except Exception as e:
            messagebox.showerror("Load blocker", f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------- ROI I/O
    def _ask_save_json(self, title: str, initialfile: str) -> Optional[str]:
        return filedialog.asksaveasfilename(
            title=title, initialdir=self._opts.file_dir or _HERE,
            initialfile=initialfile, defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")]) or None

    def _save_roi(self) -> None:
        if not self._tracker.get_roi_polygon():
            messagebox.showinfo("Save ROI", "No ROI to save - define a region first.")
            return
        path = ROI_PATH
        if self._opts.ask_before_saving:
            path = self._ask_save_json("Save ROI polygon", "roi_polygon.json")
            if path is None:
                return
        if self._tracker.save_roi(path):
            self._roi_status.set(f"ROI: saved {os.path.basename(path)}")
            self._notify("roi", "save", path)
        else:
            messagebox.showinfo("Save ROI", "No ROI to save - define a region first.")

    def _load_roi(self) -> None:
        path = filedialog.askopenfilename(
            title="Load ROI polygon", initialdir=self._opts.file_dir or _HERE,
            filetypes=[("ROI JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            if self._tracker.load_roi(path):
                self._roi_status.set(f"ROI: loaded {os.path.basename(path)}")
                self._notify("roi", "load", path)
            else:
                messagebox.showerror("Load ROI", "File has fewer than 3 vertices.")
        except Exception as e:
            messagebox.showerror("Load ROI", f"{type(e).__name__}: {e}")

    def _clear_region(self) -> None:
        self._tracker.clear_roi()
        self._roi_status.set("ROI: none")
        self._notify("roi", "clear")

    def _define_region(self) -> None:
        if self._camera is None:
            messagebox.showinfo("Define region", "Camera is unavailable.")
            return
        frame = self._camera.get_latest_frame()
        if frame is None:
            messagebox.showinfo("Define region", "No camera frame yet.")
            return
        _RoiEditor(self.frame, frame, on_commit=self._on_roi_committed)

    def _on_roi_committed(self, norm_verts) -> None:
        self._tracker.set_roi_polygon(norm_verts)
        self._roi_status.set(f"ROI: set ({len(norm_verts)} pts)")
        self._notify("roi", "edit")

    # ----------------------------------------------- LED board search region I/O
    def _define_led_board_region(self) -> None:
        """Draw the board search polygon (ADR-0195) on a still and save it to
        host/led_board_search.json for sync_fit --map. Reuses the ROI editor."""
        if self._camera is None:
            messagebox.showinfo("LED board region", "Camera is unavailable.")
            return
        frame = self._camera.get_latest_frame()
        if frame is None:
            messagebox.showinfo("LED board region", "No camera frame yet.")
            return
        _RoiEditor(self.frame, frame, on_commit=self._on_led_board_committed)

    def _on_led_board_committed(self, norm_verts) -> None:
        if not norm_verts or len(norm_verts) < 3:
            return
        try:
            with open(LED_BOARD_SEARCH_PATH, "w", encoding="utf-8") as f:
                json.dump({"polygon_norm": [[float(x), float(y)] for x, y in norm_verts]},
                          f, indent=2)
        except OSError as e:
            messagebox.showerror("LED board region", f"Could not save: {e}")
            return
        self._led_board_status.set(
            f"LED board: saved ({len(norm_verts)} pts)")

    # --------------------------------------------------------- landmark I/O
    def _set_landmark_status(self) -> None:
        placed = len(self._tracker.get_landmarks())
        fixed = len(_FIXED_LANDMARKS)
        if placed == 0:
            self._landmark_status.set("landmarks: none")
        else:
            self._landmark_status.set(f"landmarks: {placed} placed (of {fixed} fixed)")

    def _define_landmarks(self) -> None:
        if self._camera is None:
            messagebox.showinfo("Define landmarks", "Camera is unavailable.")
            return
        frame = self._camera.get_latest_frame()
        if frame is None:
            messagebox.showinfo("Define landmarks", "No camera frame yet.")
            return
        _LandmarkEditor(self.frame, frame, _FIXED_LANDMARKS,
                        self._tracker.get_landmarks(),
                        on_commit=self._on_landmarks_committed)

    def _on_landmarks_committed(self, landmarks) -> None:
        self._tracker.set_landmarks(landmarks)
        if not self._opts.ask_before_saving:
            self._tracker.save_landmarks(LANDMARKS_PATH)
        self._set_landmark_status()
        self._notify("landmarks", "edit")

    def _save_landmarks_as(self) -> None:
        if not self._tracker.get_landmarks():
            messagebox.showinfo("Save landmarks", "No landmarks placed yet.")
            return
        path = self._ask_save_json("Save landmarks", "landmarks.json")
        if path is None:
            return
        try:
            self._tracker.save_landmarks(path)
            self._landmark_status.set(f"landmarks: saved {os.path.basename(path)}")
            self._notify("landmarks", "save", path)
        except Exception as e:
            messagebox.showerror("Save landmarks", f"{type(e).__name__}: {e}")

    def _clear_landmarks(self) -> None:
        self._tracker.clear_landmarks()
        if not self._opts.ask_before_saving:
            try:
                if os.path.exists(LANDMARKS_PATH):
                    os.remove(LANDMARKS_PATH)
            except OSError:
                pass
        self._set_landmark_status()
        self._notify("landmarks", "clear")

    # ----------------------------------------------------- standalone log
    def _toggle_log(self) -> None:
        if self._log_file is not None:
            self._stop_log()
        else:
            self._start_log()

    def _log_dir(self) -> str:
        """track_logs subfolder of the camera's current recordings folder."""
        base = _RECORDINGS_DIR
        if self._camera is not None:
            try:
                base = self._camera.get_output_dir()
            except Exception:
                pass
        return os.path.join(base, TRACK_LOG_SUBDIR)

    def _start_log(self) -> None:
        try:
            log_dir = self._log_dir()
            os.makedirs(log_dir, exist_ok=True)
            animal = self._animal or "animal"
            path = os.path.join(
                log_dir, f"{animal}_{time.strftime('%Y%m%d_%H%M%S')}_track.csv")
            self._log_file = open(path, "w", newline="")
            self._log_writer = csv.writer(self._log_file)
            self._log_writer.writerow(
                ["sample_idx", "host_unix_s"] + list(TRACK_COLUMNS))
            self._log_idx = 0
            self._log_btn.configure(text="Stop track log")
            self._log_status.set(f"log: {os.path.basename(path)} (UI-rate)")
        except Exception as e:
            messagebox.showerror("Track log", f"{type(e).__name__}: {e}")
            self._log_file = self._log_writer = None

    def _stop_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
        self._log_file = self._log_writer = None
        try:
            self._log_btn.configure(text="Start track log")
            self._log_status.set("log: off")
        except tk.TclError:
            pass

    # --------------------------------------------------------- poll loop
    def _on_show_mask(self) -> None:
        if not self._show_mask_var.get():
            self._mask_label.configure(image="")
            self._photo_mask = None

    def _draw_preview(self, disp, scale: float) -> None:
        """Overlay for the shared preview loop: tracker overlay + optional mask.

        The mask render needs the same per-frame ``result`` as the overlay, so it
        lives here (it targets the separate mask label, so drawing it during the
        preview draw callback doesn't affect the preview image)."""
        result = self._tracker.snapshot_result() if self.tracking_running() else None
        if _draw_overlay is not None:
            _draw_overlay(disp, scale, self._tracker, result)
        if self._show_mask_var.get() and result is not None and result.mask is not None:
            self._render_mask(result.mask, result.chosen_contour)

    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._poll()
        finally:
            safe_after(self.frame, POLL_INTERVAL_MS, self._schedule_poll)

    def _poll(self) -> None:
        tr = self._tracker
        if tr is None:
            return
        self._bg_status.set("reference: set" if tr.has_reference() else "reference: none")

        registering, acc, target = tr.registration_status()
        self._reg_btn.configure(text=("Finalize register" if registering else "Register rat"))

        result = tr.snapshot_result()
        if registering:
            self._model_status.set(f"REGISTERING  acc={acc}/{target}")
        elif tr.has_model():
            m = tr.get_model()
            cov = "cov" if m.coverage_ok else "thin"
            self._model_status.set(f"model: set  n={m.n}  {cov}")
        else:
            self._model_status.set("model: none")

        b_reg, b_n = tr.blocker_registration_status()
        if b_reg:
            self._blocker_status.set(f"blocker: REGISTERING  samples={b_n}")
        elif tr.has_blocker_model():
            rng = tr.get_blocker_orange_range()
            if rng is not None:
                lo, hi = rng
                self._blocker_status.set(f"blocker: set  orange H {lo[0]}-{hi[0]}")
            else:
                self._blocker_status.set("blocker: set")
        else:
            self._blocker_status.set("blocker: none")

        if not self.tracking_running():
            self._readout.set("state: (tracking disabled)")
        elif result is not None:
            self._readout.set(
                f"state: {result.state}   conf: {result.confidence:.2f}   "
                f"area: {int(result.area)}   elong: {result.elongation:.2f}   "
                f"sol: {result.solidity:.2f}")
        else:
            self._readout.set("state: -")

        if self._log_writer is not None and result is not None:
            self._write_log_row(result)

    def _render_mask(self, mask, contour=None) -> None:
        """Show the binary mask (white blobs) with the tracked blob filled green.

        `mask` and `contour` are both at processing resolution, so the contour
        aligns with the mask pixels before any display scaling.
        """
        try:
            bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            if contour is not None:
                cv2.drawContours(bgr, [contour], -1, (0, 255, 0), thickness=cv2.FILLED)
            h, w = bgr.shape[:2]
            scale = MASK_VIEW_W / float(w) if w else 1.0
            dw, dh = max(1, int(w * scale)), max(1, int(h * scale))
            bgr = cv2.resize(bgr, (dw, dh), interpolation=cv2.INTER_NEAREST)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            self._photo_mask = ImageTk.PhotoImage(Image.fromarray(rgb))
            self._mask_label.configure(image=self._photo_mask)
        except Exception:
            pass

    def _write_log_row(self, result) -> None:
        try:
            self._log_writer.writerow(
                [self._log_idx, f"{time.time():.6f}"] + list(track_row(result)))
            self._log_idx += 1
        except Exception:
            pass


class _RoiEditor(_StillFrameEditorBase):
    """Modal still-frame polygon editor (precise clicking; rat won't interfere).

    Left-click adds a vertex, right-click / Enter / "Save" commits (>=3 verts),
    "z" undoes, Esc / "Cancel" aborts. Display coords are normalized (0..1) on
    commit so the polygon survives any later scaling.
    """

    def __init__(self, parent, frame_bgr, on_commit):
        super().__init__(
            parent, frame_bgr,
            "Define region - L-click add, R-click/Enter commit, z undo, Esc cancel")
        self._on_commit = on_commit
        self._pts = []

        self._canvas = self._make_canvas(self._top)
        self._canvas.pack()

        bar = ttk.Frame(self._top)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Save", command=self._commit).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(bar, text="Undo", command=self._undo).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=4)

        self._canvas.bind("<Button-1>", self._add)
        self._canvas.bind("<Button-3>", lambda _e: self._commit())
        self._top.bind("<Return>", lambda _e: self._commit())
        self._top.bind("<Escape>", lambda _e: self._cancel())
        self._top.bind("z", lambda _e: self._undo())
        self._top.grab_set()

    def _add(self, event) -> None:
        self._pts.append((event.x, event.y))
        self._redraw()

    def _undo(self) -> None:
        if self._pts:
            self._pts.pop()
            self._redraw()

    def _redraw(self) -> None:
        self._canvas.delete("ov")
        for i, (x, y) in enumerate(self._pts):
            self._canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="yellow",
                                     outline="black", tags="ov")
            if i > 0:
                px, py = self._pts[i - 1]
                self._canvas.create_line(px, py, x, y, fill="yellow", tags="ov")
        if len(self._pts) >= 3:
            x0, y0 = self._pts[0]
            xn, yn = self._pts[-1]
            self._canvas.create_line(xn, yn, x0, y0, fill="yellow", dash=(4, 2),
                                     tags="ov")

    def _commit(self) -> None:
        if len(self._pts) < 3:
            messagebox.showinfo("Define region", "Need at least 3 points.")
            return
        norm = [(x / self._dw, y / self._dh) for x, y in self._pts]
        self._on_commit(norm)
        self._close()

    def _cancel(self) -> None:
        self._close()


class _LandmarkEditor(_StillFrameEditorBase):
    """Modal still-frame editor for single-point landmark annotations.

    Shows the captured frame with a side list of landmarks: the fixed named set
    (reward wells + body-sensor beams) pre-seeded, plus any existing custom
    points. Select a landmark to "arm" it, then left-click the image to place it
    (auto-advancing to the next unplaced). "Add custom point..." appends a named
    point; "Clear selected"/"Clear all" remove placements. On Save the placed
    landmarks (normalized 0..1) are handed back via on_commit. See ADR-0018.
    """

    def __init__(self, parent, frame_bgr, fixed_spec, existing, on_commit):
        super().__init__(
            parent, frame_bgr,
            "Define landmarks - select one, click to place; "
            "Enter/Save commit, Esc cancel")
        self._on_commit = on_commit

        # Working list of entries: {kind, index, label, x, y(or None)}. Seed the
        # fixed set, pre-filling coords from any matching existing landmark, then
        # append existing custom points not covered by the fixed set.
        existing = existing or []

        def find_existing(kind, index):
            for lm in existing:
                if lm.get("kind") == kind and lm.get("index") == index:
                    return lm
            return None

        self._items = []
        for kind, index, label in fixed_spec:
            match = find_existing(kind, index)
            self._items.append({
                "kind": kind, "index": index, "label": label,
                "x": (match["x"] if match else None),
                "y": (match["y"] if match else None),
            })
        covered = {(k, i) for k, i, _ in fixed_spec}
        for lm in existing:
            if (lm.get("kind"), lm.get("index")) in covered:
                continue
            self._items.append({
                "kind": lm.get("kind", "custom"), "index": lm.get("index"),
                "label": lm.get("label", "custom"),
                "x": lm.get("x"), "y": lm.get("y"),
            })

        body = ttk.Frame(self._top)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Right: the list of landmarks.
        side = ttk.Frame(body)
        side.pack(side=tk.RIGHT, fill=tk.Y, padx=4, pady=4)
        ttk.Label(side, text="Landmarks (select, then click image):").pack(
            side=tk.TOP, anchor="w")
        self._listbox = tk.Listbox(side, width=28, height=18,
                                   exportselection=False)
        self._listbox.pack(side=tk.TOP, fill=tk.Y, expand=True)
        self._listbox.bind("<<ListboxSelect>>", lambda _e: None)
        ttk.Button(side, text="Add custom point...",
                   command=self._add_custom).pack(side=tk.TOP, fill=tk.X, pady=(4, 0))
        ttk.Button(side, text="Clear selected",
                   command=self._clear_selected).pack(side=tk.TOP, fill=tk.X)
        ttk.Button(side, text="Clear all",
                   command=self._clear_all).pack(side=tk.TOP, fill=tk.X)

        # Left: the image canvas.
        self._canvas = self._make_canvas(body)
        self._canvas.pack(side=tk.LEFT)

        bar = ttk.Frame(self._top)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Save", command=self._commit).pack(
            side=tk.LEFT, padx=4, pady=4)
        ttk.Button(bar, text="Cancel", command=self._cancel).pack(
            side=tk.LEFT, padx=4)

        self._canvas.bind("<Button-1>", self._place)
        self._top.bind("<Return>", lambda _e: self._commit())
        self._top.bind("<Escape>", lambda _e: self._cancel())
        self._top.grab_set()

        self._refresh_list(select=self._first_unplaced())
        self._redraw()

    # ------------------------------------------------------------- helpers
    def _first_unplaced(self) -> int:
        for i, it in enumerate(self._items):
            if it["x"] is None:
                return i
        return 0 if self._items else -1

    def _selected_index(self) -> int:
        sel = self._listbox.curselection()
        return int(sel[0]) if sel else -1

    def _refresh_list(self, select: int = -1) -> None:
        self._listbox.delete(0, tk.END)
        for it in self._items:
            if it["x"] is None:
                suffix = "  (unplaced)"
            else:
                suffix = f"  ({it['x']:.2f}, {it['y']:.2f})"
            self._listbox.insert(tk.END, it["label"] + suffix)
        if 0 <= select < len(self._items):
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(select)
            self._listbox.see(select)

    def _redraw(self) -> None:
        self._canvas.delete("ov")
        sel = self._selected_index()
        for i, it in enumerate(self._items):
            if it["x"] is None:
                continue
            x, y = it["x"] * self._dw, it["y"] * self._dh
            color = "#00ff00" if it["kind"] == "well" else (
                "#ff66dd" if it["kind"] == "sensor" else "#ffaa22")
            r = 5
            outline = "red" if i == sel else "black"
            self._canvas.create_line(x - r, y, x + r, y, fill=color, width=2,
                                     tags="ov")
            self._canvas.create_line(x, y - r, x, y + r, fill=color, width=2,
                                     tags="ov")
            self._canvas.create_oval(x - r, y - r, x + r, y + r, outline=outline,
                                     tags="ov")
            self._canvas.create_text(x + 8, y - 8, text=it["label"], anchor="w",
                                     fill=color, tags="ov",
                                     font=("TkDefaultFont", 7))

    def _place(self, event) -> None:
        idx = self._selected_index()
        if idx < 0:
            messagebox.showinfo("Define landmarks",
                                "Select a landmark in the list first.")
            return
        self._items[idx]["x"] = max(0.0, min(1.0, event.x / self._dw))
        self._items[idx]["y"] = max(0.0, min(1.0, event.y / self._dh))
        nxt = self._first_unplaced()
        self._refresh_list(select=(nxt if nxt >= 0 else idx))
        self._redraw()

    def _add_custom(self) -> None:
        name = simpledialog.askstring("Add custom point", "Label:",
                                      parent=self._top)
        if not name:
            return
        self._items.append({"kind": "custom", "index": None,
                            "label": name.strip() or "custom",
                            "x": None, "y": None})
        self._refresh_list(select=len(self._items) - 1)
        self._redraw()

    def _clear_selected(self) -> None:
        idx = self._selected_index()
        if idx < 0:
            return
        it = self._items[idx]
        if it["kind"] == "custom":
            # Custom points are removed entirely; fixed ones just unplaced.
            del self._items[idx]
            idx = min(idx, len(self._items) - 1)
        else:
            it["x"] = it["y"] = None
        self._refresh_list(select=idx)
        self._redraw()

    def _clear_all(self) -> None:
        kept = []
        for it in self._items:
            if it["kind"] != "custom":
                it["x"] = it["y"] = None
                kept.append(it)
        self._items = kept
        self._refresh_list(select=self._first_unplaced())
        self._redraw()

    def _commit(self) -> None:
        placed = [
            {"kind": it["kind"], "index": it["index"], "label": it["label"],
             "x": it["x"], "y": it["y"]}
            for it in self._items if it["x"] is not None and it["y"] is not None
        ]
        self._on_commit(placed)
        self._close()

    def _cancel(self) -> None:
        self._close()
