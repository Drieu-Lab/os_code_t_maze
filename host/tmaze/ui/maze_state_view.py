"""
maze_state_view.py - "Maze state" tab for experiment_control.py.

The Tk shell around the shared maze-state pipeline (maze_pipeline, which drives
ONE pure maze_state.MazeStateEstimator on the camera tracking worker at frame
rate; see ADR-0041). This tab is the OWNER/editor of that pipeline: its zone-map
edits and param sliders reconfigure the single estimator every tab consumes. For
display it shows a live zone / substate / direction / loop readout from the
pipeline's freshest snapshot, and for the state-timeline CSV it drains the full
RatState stream (every frame, so no transition is missed). It also hosts a
still-frame, multi-region polygon ZONE EDITOR (mirroring the ROI / landmark
editors in tracker_view.py) that writes zones.json.

Why a separate tab (not folded into Tracking): the estimator is a *consumer* of
the tracker (like the rules engine, ADR-0023), not a tracking tuner; giving it
its own tab leaves room for the state/direction/loop readout + the state log
without crowding the dense Tracking tab. See docs/decisions/0025.

Heavy deps (numpy, OpenCV, Pillow) come from gui_deps so importing this module
never breaks experiment_control.py; the tab surfaces any missing dep in its UI.
"""

from __future__ import annotations

import csv
import os
import time
import tkinter as tk
from dataclasses import replace
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from tmaze.ui.gui_deps import missing_dependency
from tmaze.ui.preview import PreviewLoop, safe_after
from tmaze.ui.still_frame_editor import _StillFrameEditorBase
from tmaze.ui.tooltips import tip
from tmaze.ui.zone_overlay import ZONE_HEX, WELL_HEX, AXIS_HEX, draw_zone_overlay

from tmaze.core import protocol
from tmaze.paths import host_dir
from tmaze.core.maze_state import (
    EDITABLE_ZONES, IN_WELL, ZONES_PATH, MazeStateParams, Zone,
    ZoneMap, load_zone_map, save_zone_map, state_row, state_timeline_header,
)

# Shared tracking overlay (ROI/landmarks/path/centroid) + the per-kind landmark
# colours, guarded so a missing tracker core doesn't break this module's import.
try:
    from tmaze.vision.tracker import (  # noqa: E402
        draw_overlay as _draw_overlay,
        LANDMARK_COLORS as _LANDMARK_COLORS,
        _LANDMARK_DEFAULT_COLOR as _LANDMARK_DEFAULT,
    )
except Exception:  # pragma: no cover
    _draw_overlay = None  # type: ignore
    _LANDMARK_COLORS = {}  # type: ignore
    _LANDMARK_DEFAULT = (255, 255, 255)  # type: ignore

try:
    from tmaze.ui.camera_view import DEFAULT_OUTPUT_DIR as _RECORDINGS_DIR  # noqa: E402
except Exception:  # pragma: no cover
    _RECORDINGS_DIR = "recordings"


# ================================== CONFIG ===================================
_HERE = str(host_dir())   # host/ - "Load zone map" dialog opens here (ADR-0083)
TRACK_LOG_SUBDIR = "track_logs"   # subfolder of the recordings dir, shared with tracker_view

POLL_INTERVAL_MS = 150       # readout / estimator / state-log refresh (~ tracker UI rate)
PREVIEW_INTERVAL_MS = 66     # ~15 fps live preview (only while the tab is visible)
PREVIEW_MAX_W = 640          # live preview width
# EDITOR_MAX_W (still-frame editor display width) is shared via still_frame_editor.

# Per-zone draw colours + the OpenCV zone-overlay drawing live in zone_overlay.py
# (shared with the Rig view camera preview); imported above. ZONE_HEX/WELL_HEX/
# AXIS_HEX double as Tk-canvas colours for the zone editor below.


def _bgr_to_hex(bgr) -> str:
    b, g, r = int(bgr[0]), int(bgr[1]), int(bgr[2])
    return f"#{r:02x}{g:02x}{b:02x}"


def _landmark_hex(kind: str) -> str:
    return _bgr_to_hex(_LANDMARK_COLORS.get(kind, _LANDMARK_DEFAULT))


# Editor targets: the six zone polygons, the well region, and the two stem-axis
# points. (key, kind, label). kind: "zone" | "well" | "point".
def _editor_targets():
    targets = [(name, "zone", f"{name} (zone)") for name in EDITABLE_ZONES]
    targets.append(("__well__", "well", "WELL (in_well region, inside HUB)"))
    targets.append(("__stem_base__", "point", "STEM base point (HUB/well end)"))
    targets.append(("__stem_tip__", "point", "STEM tip point (junction end)"))
    return targets


class MazeStateControlView:
    """Tk module: zone editor + live discrete-maze-state readout + state log."""

    def __init__(self, parent: tk.Misc, tracker=None, camera=None, pipeline=None):
        self._parent = parent
        self._tracker = tracker
        self._camera = camera
        # Shared maze-state pipeline (ADR-0041): this tab OWNS its zone map +
        # params (editing them here reconfigures the one estimator all tabs
        # consume) and drains its RatState stream for the state-timeline log.
        self._pipeline = pipeline
        self._sub = None
        self.frame = ttk.Frame(parent)
        self._animal = ""
        self._log_file = None
        self._log_writer = None
        self._closed = False
        self._last_state = None

        missing = missing_dependency("numpy", "opencv-python", "pillow")
        if tracker is None or pipeline is None or missing:
            why = ("the tracker core is unavailable"
                   if tracker is None or pipeline is None
                   else f"missing dependency:\n{missing}")
            ttk.Label(self.frame, padding=24, anchor="center", justify="center",
                      text=f"Maze state tab unavailable - {why}.").pack(
                fill=tk.BOTH, expand=True)
            return

        self._sub = pipeline.register()
        self._build_ui()
        self._sync_params()
        self._set_zone_status()
        self._schedule_poll()
        self._preview_loop = PreviewLoop(
            self.frame, self._preview, PREVIEW_INTERVAL_MS, PREVIEW_MAX_W,
            source=lambda: self._camera.get_latest_frame() if self._camera else None,
            draw=self._draw_preview, is_closed=lambda: self._closed)
        self._preview_loop.start()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, info: protocol.LineInfo) -> None:
        """Learn the animal id from the serial stream (for the log filename).
        Reads the pre-parsed protocol.LineInfo (docs/decisions/0056)."""
        if info.animal:
            self._animal = info.animal

    def close(self) -> None:
        self._closed = True
        self._stop_log()
        if self._sub is not None:
            self._sub.close()

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = dict(padx=4, pady=2)
        content = ttk.Frame(self.frame)
        content.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        controls = ttk.Frame(content)
        controls.pack(side=tk.LEFT, fill=tk.Y, anchor="n")
        viewcol = ttk.Frame(content)
        viewcol.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._preview = ttk.Label(viewcol, anchor="center")
        self._preview.pack(side=tk.TOP, fill=tk.BOTH, expand=True, **pad)

        # Zone map editor + persistence.
        zone_box = ttk.LabelFrame(controls, text="Zone map")
        zone_box.pack(side=tk.TOP, fill=tk.X, **pad)
        define_btn = ttk.Button(zone_box, text="Define zones...",
                                command=self._define_zones)
        define_btn.pack(side=tk.LEFT, padx=2, pady=4)
        tip(define_btn, "maze.define_zones")
        save_btn = ttk.Button(zone_box, text="Save zones", command=self._save_zones)
        save_btn.pack(side=tk.LEFT, padx=2)
        tip(save_btn, "maze.save_zones")
        load_btn = ttk.Button(zone_box, text="Load zones", command=self._load_zones)
        load_btn.pack(side=tk.LEFT, padx=2)
        tip(load_btn, "maze.load_zones")
        self._zone_status = tk.StringVar(value="zones: none")
        ttk.Label(zone_box, textvariable=self._zone_status).pack(side=tk.LEFT, padx=6)

        # Params (min confidence + min dwell). Tuning knobs for the estimator.
        params = ttk.LabelFrame(controls, text="State estimation")
        params.pack(side=tk.TOP, fill=tk.X, **pad)
        self._min_conf = tk.DoubleVar(value=self._pipeline.params.min_confidence)
        self._min_dwell = tk.IntVar(value=self._pipeline.params.min_dwell_ticks)
        crow = ttk.Frame(params)
        crow.pack(side=tk.TOP, fill=tk.X, pady=1)
        ttk.Label(crow, text="Min confidence", width=14, anchor="w").pack(side=tk.LEFT)
        cscale = ttk.Scale(crow, from_=0.0, to=1.0, variable=self._min_conf,
                           command=lambda _=None: self._sync_params())
        cscale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        tip(cscale, "maze.min_confidence")
        drow = ttk.Frame(params)
        drow.pack(side=tk.TOP, fill=tk.X, pady=1)
        ttk.Label(drow, text="Min dwell (frames)", width=14, anchor="w").pack(side=tk.LEFT)
        dspin = ttk.Spinbox(drow, from_=1, to=30, width=5, textvariable=self._min_dwell,
                            command=self._sync_params)
        dspin.pack(side=tk.LEFT, padx=4)
        tip(dspin, "maze.min_dwell")

        # Live readout.
        read = ttk.LabelFrame(controls, text="Current state")
        read.pack(side=tk.TOP, fill=tk.X, **pad)
        self._readout = tk.StringVar(value="state: -")
        rlbl = ttk.Label(read, textvariable=self._readout, anchor="w", justify="left")
        rlbl.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)
        tip(rlbl, "maze.readout")

        # State-timeline log.
        logrow = ttk.Frame(controls)
        logrow.pack(side=tk.TOP, fill=tk.X, **pad)
        self._log_btn = ttk.Button(logrow, text="Start state log",
                                   command=self._toggle_log)
        self._log_btn.pack(side=tk.LEFT, padx=2)
        tip(self._log_btn, "maze.log")
        self._log_status = tk.StringVar(value="log: off")
        ttk.Label(logrow, textvariable=self._log_status).pack(side=tk.LEFT, padx=6)

    # --------------------------------------------------------------- params
    def _sync_params(self) -> None:
        try:
            self._pipeline.set_params(MazeStateParams(
                min_confidence=float(self._min_conf.get()),
                min_dwell_ticks=max(1, int(self._min_dwell.get())),
            ))
        except (tk.TclError, ValueError):
            pass

    # ----------------------------------------------------------- zone I/O
    def _set_zone_status(self) -> None:
        zm = self._pipeline.zone_map
        n = len(zm.zones)
        extras = []
        if zm.well is not None:
            extras.append("well")
        if zm.stem_base is not None and zm.stem_tip is not None:
            extras.append("axis")
        suffix = (" + " + "/".join(extras)) if extras else ""
        self._zone_status.set(f"zones: {n}{suffix}" if n else "zones: none")

    def _define_zones(self) -> None:
        if self._camera is None:
            messagebox.showinfo("Define zones", "Camera is unavailable.")
            return
        frame = self._camera.get_latest_frame()
        if frame is None:
            messagebox.showinfo("Define zones", "No camera frame yet.")
            return
        # Overlay the reward-well / body-sensor landmarks (from the Tracking tab,
        # landmarks.json; ADR-0018) so zones can be aligned to them.
        landmarks = []
        try:
            landmarks = self._tracker.get_landmarks()
        except Exception:
            landmarks = []
        _ZoneEditor(self.frame, frame, self._pipeline.zone_map,
                    on_commit=self._on_zones_committed, landmarks=landmarks)

    def _on_zones_committed(self, zone_map: ZoneMap) -> None:
        self._pipeline.set_zone_map(zone_map)
        try:
            save_zone_map(zone_map, ZONES_PATH)
        except Exception as e:
            messagebox.showerror("Save zones", f"{type(e).__name__}: {e}")
        self._set_zone_status()

    def _save_zones(self) -> None:
        zm = self._pipeline.zone_map
        if not zm.valid():
            messagebox.showinfo("Save zones", "No zones to save - define them first.")
            return
        try:
            save_zone_map(zm, ZONES_PATH)
            self._zone_status.set(f"zones: saved {os.path.basename(ZONES_PATH)}")
        except Exception as e:
            messagebox.showerror("Save zones", f"{type(e).__name__}: {e}")

    def _load_zones(self) -> None:
        path = filedialog.askopenfilename(
            title="Load zone map", initialdir=_HERE,
            filetypes=[("Zone map", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            self._pipeline.set_zone_map(load_zone_map(path))
            self._set_zone_status()
        except Exception as e:
            messagebox.showerror("Load zones", f"{type(e).__name__}: {e}")

    # ----------------------------------------------------- state-timeline log
    def _toggle_log(self) -> None:
        if self._log_file is not None:
            self._stop_log()
        else:
            self._start_log()

    def _log_dir(self) -> str:
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
                log_dir, f"{animal}_{time.strftime('%Y%m%d_%H%M%S')}_mazestate.csv")
            self._log_file = open(path, "w", newline="")
            self._log_writer = csv.writer(self._log_file)
            self._log_writer.writerow(state_timeline_header())
            self._log_btn.configure(text="Stop state log")
            self._log_status.set(f"log: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("State log", f"{type(e).__name__}: {e}")
            self._log_file = self._log_writer = None

    def _stop_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
        self._log_file = self._log_writer = None
        try:
            self._log_btn.configure(text="Start state log")
            self._log_status.set("log: off")
        except tk.TclError:
            pass

    # --------------------------------------------------------- poll / preview
    def _tracking_running(self) -> bool:
        return self._camera is not None and self._camera.tracking_enabled()

    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._poll()
        finally:
            safe_after(self.frame, POLL_INTERVAL_MS, self._schedule_poll)

    def _poll(self) -> None:
        """Render the freshest shared state; drain the stream for the state log.

        The shared estimator (maze_pipeline) runs on the camera worker at frame
        rate, so display uses the latest snapshot while the state-timeline log
        drains EVERY RatState since last poll (frame-rate rows, no missed edges).
        """
        if not self._tracking_running():
            if self._sub is not None:
                self._sub.drain()   # discard so a later log doesn't dump a backlog
            self._readout.set("state: (tracking disabled)")
            self._last_state = None
            return

        bundles = self._sub.drain() if self._sub is not None else []
        snap = self._pipeline.snapshot()
        s = snap.state if snap is not None else None
        self._last_state = s
        if s is None:
            self._readout.set("state: -  (waiting for frames)")
        else:
            parts = [f"zone: {s.zone}"]
            if s.substate:
                parts.append(f"({s.substate})")
            if not s.valid:
                parts.append("[invalid]")
            line1 = "  ".join(parts)
            bits = []
            if s.direction:
                bits.append(s.direction)
            if s.stem_progress is not None:
                bits.append(f"t={s.stem_progress:.2f}")
            bits.append(f"conf={s.confidence:.2f}")
            line2 = "  ".join(bits)
            line3 = ""
            if s.entered or s.exited:
                line3 = f"entered={s.entered or '-'}  exited={s.exited or '-'}"
            if s.loop_completed:
                line3 += f"   LOOP {s.loop_side}"
            self._readout.set("\n".join(x for x in (line1, line2, line3) if x))

        if self._log_writer is not None:
            for b in bundles:
                try:
                    # Log wall-clock time (the shared state's `now` is monotonic,
                    # the engine clock); the bundle carries the wall stamp.
                    self._log_writer.writerow(state_row(replace(b.state,
                                                                now=b.wall_ts)))
                except Exception:
                    pass

    def _draw_preview(self, disp, scale) -> None:
        # Base tracker overlay (ROI/landmarks/path/centroid), then the zones.
        result = self._tracker.snapshot_result() if self._tracking_running() else None
        if _draw_overlay is not None:
            _draw_overlay(disp, scale, self._tracker, result)
        self._draw_zones(disp)

    def _draw_zones(self, disp) -> None:
        """Draw the zone polygons + well + stem axis on the (scaled) preview."""
        s = self._last_state
        cur = s.zone if s is not None else None
        in_well = s.substate == IN_WELL if s is not None else False
        draw_zone_overlay(disp, self._pipeline.zone_map, cur, in_well=in_well)


class _ZoneEditor(_StillFrameEditorBase):
    """Modal still-frame, multi-region polygon editor for the maze zone map.

    Right list = editable targets (the six zones, the well region, the two
    stem-axis points). Select a target, then click the image:
      - zone / well targets: L-click adds polygon vertices; "Finish region" (or
        right-click / 'r') commits the current polygon to the selected target.
      - stem base / tip: L-click places the single point immediately.
    "z" undoes the last in-progress vertex; "Clear selected" removes a target's
    geometry. Enter / Save commits the whole map (normalized 0..1) via on_commit;
    Esc / Cancel aborts. Mirrors tracker_view._RoiEditor / _LandmarkEditor.

    Any reward-well / body-sensor landmarks (from the Tracking tab, ADR-0018) are
    drawn as reference diamonds so zone polygons can be aligned to the sensors.
    """

    def __init__(self, parent, frame_bgr, existing: ZoneMap, on_commit,
                 landmarks=None):
        super().__init__(
            parent, frame_bgr,
            "Define zones - select a target, L-click to draw; "
            "r/right-click finish region, z undo, Enter save, Esc cancel")
        self._on_commit = on_commit
        self._landmarks = landmarks or []

        self._targets = _editor_targets()
        # Committed geometry, keyed by target key: polygon -> list of norm verts;
        # point -> (nx, ny). Seed from the existing zone map.
        self._geoms: dict = {}
        for z in existing.zones:
            self._geoms[z.name] = [(float(x), float(y)) for x, y in z.polygon]
        if existing.well is not None:
            self._geoms["__well__"] = [(float(x), float(y)) for x, y in existing.well]
        if existing.stem_base is not None:
            self._geoms["__stem_base__"] = existing.stem_base
        if existing.stem_tip is not None:
            self._geoms["__stem_tip__"] = existing.stem_tip
        self._current_pts = []   # in-progress polygon, display px

        body = ttk.Frame(self._top)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        side = ttk.Frame(body)
        side.pack(side=tk.RIGHT, fill=tk.Y, padx=4, pady=4)
        ttk.Label(side, text="Targets (select, then click image):").pack(
            side=tk.TOP, anchor="w")
        self._listbox = tk.Listbox(side, width=30, height=12, exportselection=False)
        self._listbox.pack(side=tk.TOP, fill=tk.Y, expand=True)
        ttk.Button(side, text="Finish region", command=self._finish_region).pack(
            side=tk.TOP, fill=tk.X, pady=(4, 0))
        ttk.Button(side, text="Undo vertex", command=self._undo).pack(
            side=tk.TOP, fill=tk.X)
        ttk.Button(side, text="Clear selected", command=self._clear_selected).pack(
            side=tk.TOP, fill=tk.X)
        if self._landmarks:
            ttk.Label(side, wraplength=200, justify="left", foreground="#888888",
                      text=(f"\u25c6 {len(self._landmarks)} landmark(s) shown "
                            "(reward wells / body sensors) - align the zone "
                            "boundaries to them.")).pack(
                side=tk.TOP, anchor="w", pady=(6, 0))

        self._canvas = self._make_canvas(body)
        self._canvas.pack(side=tk.LEFT)

        bar = ttk.Frame(self._top)
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Save", command=self._commit).pack(
            side=tk.LEFT, padx=4, pady=4)
        ttk.Button(bar, text="Cancel", command=self._cancel).pack(side=tk.LEFT, padx=4)

        self._canvas.bind("<Button-1>", self._click)
        self._canvas.bind("<Button-3>", lambda _e: self._finish_region())
        self._top.bind("<Return>", lambda _e: self._commit())
        self._top.bind("<Escape>", lambda _e: self._cancel())
        self._top.bind("r", lambda _e: self._finish_region())
        self._top.bind("z", lambda _e: self._undo())
        self._top.grab_set()

        self._refresh_list(select=0)
        self._redraw()

    # ------------------------------------------------------------- helpers
    def _selected(self):
        sel = self._listbox.curselection()
        if not sel:
            return None
        return self._targets[int(sel[0])]

    def _kind(self, target) -> str:
        return target[1] if target else ""

    def _refresh_list(self, select: int = -1) -> None:
        self._listbox.delete(0, tk.END)
        for key, kind, label in self._targets:
            g = self._geoms.get(key)
            if kind == "point":
                suffix = "  (set)" if g is not None else "  (unset)"
            else:
                suffix = f"  ({len(g)} pts)" if g else "  (empty)"
            self._listbox.insert(tk.END, label + suffix)
        if 0 <= select < len(self._targets):
            self._listbox.selection_clear(0, tk.END)
            self._listbox.selection_set(select)
            self._listbox.see(select)

    def _redraw(self) -> None:
        self._canvas.delete("ov")
        # Reference landmarks first (kept beneath the editable geometry).
        self._draw_landmarks()
        # Committed geometry for every target.
        for key, kind, _label in self._targets:
            g = self._geoms.get(key)
            if not g:
                continue
            color = self._target_color(key)
            if kind == "point":
                x, y = g[0] * self._dw, g[1] * self._dh
                self._marker(x, y, color)
            else:
                self._polygon(g, color, closed=True)
        # In-progress polygon.
        for i, (x, y) in enumerate(self._current_pts):
            self._canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill="yellow",
                                     outline="black", tags="ov")
            if i > 0:
                px, py = self._current_pts[i - 1]
                self._canvas.create_line(px, py, x, y, fill="yellow", tags="ov")

    def _draw_landmarks(self) -> None:
        """Draw reward-well / body-sensor landmarks as reference diamonds."""
        r = 6
        for lm in self._landmarks:
            try:
                x = float(lm["x"]) * self._dw
                y = float(lm["y"]) * self._dh
            except (KeyError, TypeError, ValueError):
                continue
            color = _landmark_hex(lm.get("kind", "custom"))
            self._canvas.create_polygon(
                x, y - r, x + r, y, x, y + r, x - r, y,
                outline=color, fill="", width=2, tags="ov")
            label = lm.get("label", "")
            if label:
                self._canvas.create_text(x + r + 2, y - r - 2, text=label,
                                         fill=color, anchor="sw", tags="ov")

    def _target_color(self, key: str) -> str:
        if key == "__well__":
            return WELL_HEX
        if key in ("__stem_base__", "__stem_tip__"):
            return AXIS_HEX
        return ZONE_HEX.get(key, "#ffffff")

    def _marker(self, x, y, color) -> None:
        r = 5
        self._canvas.create_line(x - r, y, x + r, y, fill=color, width=2, tags="ov")
        self._canvas.create_line(x, y - r, x, y + r, fill=color, width=2, tags="ov")

    def _polygon(self, norm_verts, color, closed) -> None:
        pts = [(x * self._dw, y * self._dh) for x, y in norm_verts]
        for i in range(1, len(pts)):
            self._canvas.create_line(*pts[i - 1], *pts[i], fill=color, tags="ov")
        if closed and len(pts) >= 3:
            self._canvas.create_line(*pts[-1], *pts[0], fill=color, dash=(4, 2),
                                     tags="ov")

    # --------------------------------------------------------------- actions
    def _click(self, event) -> None:
        target = self._selected()
        if target is None:
            messagebox.showinfo("Define zones", "Select a target in the list first.")
            return
        if self._kind(target) == "point":
            key = target[0]
            self._geoms[key] = (max(0.0, min(1.0, event.x / self._dw)),
                                max(0.0, min(1.0, event.y / self._dh)))
            self._refresh_list(select=self._listbox.curselection()[0])
            self._redraw()
        else:
            self._current_pts.append((event.x, event.y))
            self._redraw()

    def _finish_region(self) -> None:
        target = self._selected()
        if target is None or self._kind(target) == "point":
            return
        if len(self._current_pts) < 3:
            messagebox.showinfo("Define zones", "Need at least 3 vertices.")
            return
        key = target[0]
        self._geoms[key] = [(x / self._dw, y / self._dh)
                            for x, y in self._current_pts]
        self._current_pts = []
        self._refresh_list(select=self._listbox.curselection()[0])
        self._redraw()

    def _undo(self) -> None:
        if self._current_pts:
            self._current_pts.pop()
            self._redraw()

    def _clear_selected(self) -> None:
        target = self._selected()
        if target is None:
            return
        self._geoms.pop(target[0], None)
        self._current_pts = []
        self._refresh_list(select=self._listbox.curselection()[0])
        self._redraw()

    def _commit(self) -> None:
        zones = []
        for name in EDITABLE_ZONES:
            g = self._geoms.get(name)
            if g and len(g) >= 3:
                zones.append(Zone(name, tuple((float(x), float(y)) for x, y in g)))
        well_g = self._geoms.get("__well__")
        well = tuple((float(x), float(y)) for x, y in well_g) \
            if well_g and len(well_g) >= 3 else None
        base = self._geoms.get("__stem_base__")
        tip_p = self._geoms.get("__stem_tip__")
        zone_map = ZoneMap(zones=tuple(zones), well=well,
                           stem_base=base, stem_tip=tip_p)
        self._on_commit(zone_map)
        self._close()

    def _cancel(self) -> None:
        self._close()
