"""
rules_view.py - "Rules" tab for experiment_control.py.

The thin GUI/host layer over the pure rules engine (rules_engine.py). It is the
SINGLE actuation authority: it loads rules.json + the cm<->px scale calibration
(rig_scale.json), polls the shared tracker, feeds lick lines to the engine, and
EXECUTES the returned action intents (send a reward command, write a rule-event
row). The engine itself never sends or logs. See docs/decisions/0023.

Module contract (like the other tabs):
    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None   handle each serial line (mode + lick edges)
    close() -> None            finalize the rule-event log

Safety (this actuates the reward hardware):
  - Disabled by default; explicit "Arm rules" checkbox.
  - The engine is only enabled (evaluated) while ALL hold: armed, a valid scale
    calibration exists, tracking is running, and the firmware is in Free Roam
    (so the firmware's own Full-task reward logic stays the sole authority in
    that mode). When the gate is closed the engine does not run, so no state
    advances and nothing fires.
  - Per-rule cooldown / per-visit count / global rate backstop live in the
    engine; every fire is logged (and also flows to the GUI reward tally via the
    firmware REWARD,...,MANUAL line).

Calibration UI: measure the real distance between pairs of already-placed
landmarks; the global scale is the mean cm/px over all segments, with the
coefficient of variation surfaced so a poor single-scale fit (suggesting a
homography upgrade) is visible. The proximity radius is drawn on the preview so
R can be verified visually while tuning.

Heavy deps (numpy, OpenCV, Pillow) are imported defensively so importing this
module never breaks experiment_control.py.
"""

from __future__ import annotations

import csv
import json
import os
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from tooltips import tip

try:
    import numpy as np  # type: ignore
    _NUMPY_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    np = None  # type: ignore
    _NUMPY_ERR = f"{type(e).__name__}: {e}"

try:
    import cv2  # type: ignore
    _CV2_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    cv2 = None  # type: ignore
    _CV2_ERR = f"{type(e).__name__}: {e}"

try:
    from PIL import Image, ImageTk  # type: ignore
    _PIL_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    Image = None  # type: ignore
    ImageTk = None  # type: ignore
    _PIL_ERR = f"{type(e).__name__}: {e}"

try:
    from tracker import draw_overlay as _draw_overlay  # noqa: E402
except Exception:  # pragma: no cover
    _draw_overlay = None  # type: ignore

from rules_engine import (
    FireReward, LogEvent, RulesEngine, Scale, load_rules_config,
    resolve_landmark,
)

# Recordings folder default (mirrors tracker_view): rule-event logs live in a
# subfolder of whatever recordings folder the camera is actually using.
try:
    from camera_view import DEFAULT_OUTPUT_DIR as _RECORDINGS_DIR  # noqa: E402
except Exception:  # pragma: no cover
    _RECORDINGS_DIR = "recordings"


# ================================== CONFIG ===================================
_HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(_HERE, "rules.json")       # versioned example config
SCALE_PATH = os.path.join(_HERE, "rig_scale.json")   # per-rig scale (gitignored)
RULE_LOG_SUBDIR = "track_logs"                        # shared with track logs

POLL_INTERVAL_MS = 100        # engine tick + status refresh
PREVIEW_INTERVAL_MS = 66      # ~15 fps live preview (only while the tab is visible)
PREVIEW_MAX_W = 640

# Free Roam is the only firmware mode we actuate in (see module docstring).
FREEROAM_TOKENS = ("FREEROAM", "FREE")


class RulesControlView:
    """Tk module: the Rules tab - controls + executor for the rules engine."""

    def __init__(self, parent: tk.Misc, tracker=None, camera=None, send=None):
        self._parent = parent
        self._tracker = tracker
        self._camera = camera
        self._send = send                # callable(str) -> None (RigClient.send)
        self.frame = ttk.Frame(parent)
        self._photo_preview = None
        self._mode = ""                  # last firmware MODE token
        self._engine: Optional[RulesEngine] = None
        self._scale = Scale()
        self._measurements: list = []    # list of measurement dicts
        self._landmark_options: dict = {}  # combobox label -> landmark dict
        self._log_file = None
        self._log_writer = None
        self._log_idx = 0
        self._closed = False

        missing = self._missing_dependency()
        if tracker is None or missing:
            why = ("the tracker core is unavailable"
                   if tracker is None else f"missing dependency:\n{missing}")
            ttk.Label(self.frame, padding=24, anchor="center", justify="center",
                      text=f"Rules tab unavailable - {why}.").pack(
                fill=tk.BOTH, expand=True)
            return

        self._load_rules()
        self._load_scale()
        self._build_ui()
        self._refresh_landmark_options()
        self._render_measurements()
        self._schedule_poll()
        self._schedule_preview()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, line: str) -> None:
        """Track firmware mode and feed lick edges to the engine."""
        fields = line.split(",")
        if len(fields) >= 3 and fields[1] == "MODE":
            self._mode = fields[2].strip().upper()
            return
        if self._engine is None:
            return
        # Feed lick lines; only acts when the gate is open (set on the engine).
        if len(fields) == 4 and fields[1] == "L":
            self._engine.enabled = self._gate_open()
            if self._engine.enabled:
                actions = self._engine.event(line, time.monotonic())
                self._execute(actions)

    def close(self) -> None:
        self._closed = True
        self._stop_log()

    # ---------------------------------------------------------- dependencies
    @staticmethod
    def _missing_dependency() -> Optional[str]:
        problems = []
        if np is None:
            problems.append(f"numpy ({_NUMPY_ERR})")
        if cv2 is None:
            problems.append(f"opencv-python ({_CV2_ERR})")
        if Image is None or ImageTk is None:
            problems.append(f"pillow ({_PIL_ERR})")
        return "\n".join(problems) if problems else None

    # -------------------------------------------------------------- loading
    def _load_rules(self) -> None:
        try:
            config = load_rules_config(RULES_PATH)
            self._engine = RulesEngine.from_config(config)
            self._load_error = None
        except Exception as e:
            # Fall back to an empty engine so the tab still loads.
            from rules_engine import EngineDefaults
            self._engine = RulesEngine([], EngineDefaults())
            self._load_error = f"{type(e).__name__}: {e}"
        # The engine is always OFF until the user arms it (and the gate opens).
        self._engine.enabled = False

    def _load_scale(self) -> None:
        if not os.path.exists(SCALE_PATH):
            return
        try:
            with open(SCALE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._scale = Scale.from_dict(data)
            self._measurements = list(data.get("measurements", []))
        except Exception:
            self._scale = Scale()
            self._measurements = []

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

        # Arm + reload.
        top = ttk.LabelFrame(controls, text="Engine (actuates reward - off by default)")
        top.pack(side=tk.TOP, fill=tk.X, **pad)
        self._enable_var = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(top, text="Arm rules", variable=self._enable_var,
                             command=self._on_enable)
        cb.pack(side=tk.LEFT, padx=4, pady=4)
        tip(cb, "rules.enable")
        reload_btn = ttk.Button(top, text="Reload rules.json", command=self._reload_rules)
        reload_btn.pack(side=tk.LEFT, padx=4)
        tip(reload_btn, "rules.reload")

        self._status_var = tk.StringVar(value="rules: idle")
        status_lbl = ttk.Label(controls, textvariable=self._status_var,
                               anchor="w", justify="left", wraplength=300)
        status_lbl.pack(side=tk.TOP, fill=tk.X, **pad)
        tip(status_lbl, "rules.status")

        self._gate_var = tk.StringVar(value="")
        ttk.Label(controls, textvariable=self._gate_var, anchor="w",
                  justify="left", wraplength=300,
                  foreground="#a05000").pack(side=tk.TOP, fill=tk.X, **pad)

        # Scale calibration.
        cal = ttk.LabelFrame(controls, text="Measure scale (cm per px)")
        cal.pack(side=tk.TOP, fill=tk.X, **pad)
        row1 = ttk.Frame(cal)
        row1.pack(side=tk.TOP, fill=tk.X, pady=1)
        ttk.Label(row1, text="A:", width=2).pack(side=tk.LEFT)
        self._cmb_a = ttk.Combobox(row1, width=16, state="readonly")
        self._cmb_a.pack(side=tk.LEFT, padx=2)
        tip(self._cmb_a, "rules.measure.a")
        ttk.Label(row1, text="B:", width=2).pack(side=tk.LEFT)
        self._cmb_b = ttk.Combobox(row1, width=16, state="readonly")
        self._cmb_b.pack(side=tk.LEFT, padx=2)
        tip(self._cmb_b, "rules.measure.b")
        row2 = ttk.Frame(cal)
        row2.pack(side=tk.TOP, fill=tk.X, pady=1)
        ttk.Label(row2, text="Known cm:").pack(side=tk.LEFT, padx=(0, 2))
        self._cm_var = tk.StringVar(value="")
        cm_entry = ttk.Entry(row2, width=8, textvariable=self._cm_var)
        cm_entry.pack(side=tk.LEFT, padx=2)
        tip(cm_entry, "rules.measure.cm")
        add_btn = ttk.Button(row2, text="Add", command=self._add_measurement)
        add_btn.pack(side=tk.LEFT, padx=2)
        tip(add_btn, "rules.measure.add")
        self._meas_list = tk.Listbox(cal, height=4)
        self._meas_list.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        self._stats_var = tk.StringVar(value="scale: (none)")
        stats_lbl = ttk.Label(cal, textvariable=self._stats_var, anchor="w")
        stats_lbl.pack(side=tk.TOP, fill=tk.X, padx=2)
        tip(stats_lbl, "rules.measure.stats")
        calbtns = ttk.Frame(cal)
        calbtns.pack(side=tk.TOP, fill=tk.X, pady=2)
        clear_btn = ttk.Button(calbtns, text="Clear", command=self._clear_measurements)
        clear_btn.pack(side=tk.LEFT, padx=2)
        tip(clear_btn, "rules.measure.clear")
        save_btn = ttk.Button(calbtns, text="Save scale", command=self._save_scale)
        save_btn.pack(side=tk.LEFT, padx=2)
        tip(save_btn, "rules.measure.save")

        self._log_status = tk.StringVar(value="log: off")
        log_lbl = ttk.Label(controls, textvariable=self._log_status, anchor="w")
        log_lbl.pack(side=tk.TOP, fill=tk.X, **pad)
        tip(log_lbl, "rules.log")

    # ---------------------------------------------------------- enable/reload
    def _on_enable(self) -> None:
        if self._enable_var.get() and not self._scale.valid():
            messagebox.showinfo(
                "Arm rules",
                "No scale calibration yet. Measure the cm/px scale (below) so "
                "the engine can convert rule radii to pixels.")
            self._enable_var.set(False)
            return
        if self._enable_var.get() and self._load_error:
            messagebox.showwarning(
                "Arm rules",
                f"rules.json failed to load ({self._load_error}); no rules are "
                "active.")

    def _reload_rules(self) -> None:
        was_armed = self._enable_var.get()
        self._enable_var.set(False)
        self._load_rules()
        if self._load_error:
            messagebox.showerror("Reload rules", self._load_error)
        else:
            messagebox.showinfo(
                "Reload rules",
                f"Loaded {len(self._engine.rules)} rule(s) from rules.json.")
        if was_armed:
            messagebox.showinfo("Reload rules", "Re-arm the engine when ready.")

    # ------------------------------------------------------------- gating
    def _tracking_running(self) -> bool:
        try:
            return self._camera is not None and self._camera.tracking_enabled()
        except Exception:
            return False

    def _mode_is_freeroam(self) -> bool:
        return self._mode in FREEROAM_TOKENS

    def _gate_open(self) -> bool:
        """All conditions that must hold before the engine may actuate."""
        return bool(self._enable_var.get()
                    and self._engine is not None
                    and self._scale.valid()
                    and self._tracking_running()
                    and self._mode_is_freeroam())

    def _gate_reason(self) -> str:
        if not self._enable_var.get():
            return ""
        if self._engine is None or not self._engine.rules:
            return "gated: no rules loaded"
        if not self._scale.valid():
            return "gated: no scale calibration"
        if not self._tracking_running():
            return "gated: tracking is not running"
        if not self._mode_is_freeroam():
            return f"gated: firmware not in Free Roam (mode={self._mode or '?'})"
        return "ARMED: firing enabled"

    # ------------------------------------------------------------- executor
    def _execute(self, actions) -> None:
        for a in actions:
            if isinstance(a, FireReward):
                if self._send is not None:
                    # Bare reward-spout fire at the port (sol=1 = reward). The
                    # firmware logs REWARD,<port>,MANUAL,<ms> so the GUI tally
                    # already counts it. See PROTOCOL.md / ADR-0007.
                    self._send(f"R{a.port}1,{a.ms}")
            elif isinstance(a, LogEvent):
                self._log_event(a)

    # ----------------------------------------------------- rule-event log
    def _log_dir(self) -> str:
        base = _RECORDINGS_DIR
        if self._camera is not None:
            try:
                base = self._camera.get_output_dir()
            except Exception:
                pass
        return os.path.join(base, RULE_LOG_SUBDIR)

    def _ensure_log(self) -> None:
        if self._log_writer is not None:
            return
        try:
            log_dir = self._log_dir()
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(
                log_dir, f"rules_{time.strftime('%Y%m%d_%H%M%S')}.csv")
            self._log_file = open(path, "w", newline="")
            self._log_writer = csv.writer(self._log_file)
            self._log_writer.writerow(
                ["idx", "host_unix_s", "firmware_mode", "event", "rule_id",
                 "port", "pulse_ms", "detail"])
            self._log_idx = 0
            self._log_status.set(f"log: {os.path.basename(path)}")
        except Exception as e:
            self._log_file = self._log_writer = None
            self._log_status.set(f"log: error ({type(e).__name__})")

    def _log_event(self, ev: LogEvent) -> None:
        self._ensure_log()
        if self._log_writer is None:
            return
        detail = dict(ev.detail or {})
        port = detail.pop("port", "")
        pulse = detail.pop("pulse_ms", "")
        try:
            self._log_writer.writerow([
                self._log_idx, f"{time.time():.6f}", self._mode, ev.name,
                ev.rule_id, port, pulse,
                json.dumps(detail) if detail else ""])
            self._log_file.flush()
            self._log_idx += 1
        except Exception:
            pass

    def _stop_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
        self._log_file = self._log_writer = None

    # --------------------------------------------------- scale calibration
    def _frame_size(self):
        """Current full-frame (w, h) from the tracker result or camera frame."""
        try:
            res = self._tracker.snapshot_result()
            if res is not None and res.frame_size and res.frame_size[0]:
                return tuple(res.frame_size)
        except Exception:
            pass
        if self._camera is not None:
            try:
                frame = self._camera.get_latest_frame()
                if frame is not None:
                    h, w = frame.shape[:2]
                    return (w, h)
            except Exception:
                pass
        return None

    def _refresh_landmark_options(self) -> None:
        self._landmark_options = {}
        try:
            landmarks = self._tracker.get_landmarks()
        except Exception:
            landmarks = []
        labels = []
        for lm in landmarks:
            label = lm.get("label") or f"{lm.get('kind')}{lm.get('index')}"
            self._landmark_options[label] = lm
            labels.append(label)
        try:
            self._cmb_a["values"] = labels
            self._cmb_b["values"] = labels
        except Exception:
            pass

    def _add_measurement(self) -> None:
        self._refresh_landmark_options()
        la = self._cmb_a.get()
        lb = self._cmb_b.get()
        if not la or not lb or la == lb:
            messagebox.showinfo("Add measurement",
                                "Pick two different placed landmarks.")
            return
        try:
            known_cm = float(self._cm_var.get())
            if known_cm <= 0:
                raise ValueError
        except ValueError:
            messagebox.showinfo("Add measurement", "Enter a positive cm value.")
            return
        fs = self._frame_size()
        if fs is None:
            messagebox.showinfo("Add measurement",
                                "No camera frame yet (need the frame size).")
            return
        a = self._landmark_options[la]
        b = self._landmark_options[lb]
        w, h = fs
        px = ((float(a["x"]) - float(b["x"])) * w) ** 2 \
            + ((float(a["y"]) - float(b["y"])) * h) ** 2
        px = px ** 0.5
        if px <= 0:
            messagebox.showinfo("Add measurement",
                                "The two landmarks are at the same pixel.")
            return
        self._measurements.append({
            "a": {"kind": a.get("kind"), "index": a.get("index")},
            "b": {"kind": b.get("kind"), "index": b.get("index")},
            "a_label": la, "b_label": lb,
            "ax": float(a["x"]), "ay": float(a["y"]),
            "bx": float(b["x"]), "by": float(b["y"]),
            "known_cm": known_cm, "px": px, "cm_per_px": known_cm / px,
            "frame_size": [w, h],
        })
        self._cm_var.set("")
        self._recompute_scale()
        self._render_measurements()

    def _clear_measurements(self) -> None:
        self._measurements = []
        self._recompute_scale()
        self._render_measurements()

    def _recompute_scale(self) -> None:
        vals = [m["cm_per_px"] for m in self._measurements if m.get("cm_per_px")]
        # Preserve an existing homography (an accuracy upgrade layered on top).
        H = self._scale.homography if isinstance(self._scale, Scale) else None
        if not vals:
            self._scale = Scale(cm_per_px=None, homography=H)
            return
        mean = sum(vals) / len(vals)
        self._scale = Scale(cm_per_px=mean, homography=H)

    def _scale_stats(self):
        vals = [m["cm_per_px"] for m in self._measurements if m.get("cm_per_px")]
        n = len(vals)
        if n == 0:
            return None
        mean = sum(vals) / n
        if n >= 2:
            var = sum((v - mean) ** 2 for v in vals) / (n - 1)
            std = var ** 0.5
        else:
            std = 0.0
        cv = (std / mean) if mean else 0.0
        return {"mean": mean, "std": std, "cv": cv, "n": n}

    def _render_measurements(self) -> None:
        try:
            self._meas_list.delete(0, tk.END)
        except Exception:
            return
        for m in self._measurements:
            self._meas_list.insert(
                tk.END,
                f"{m.get('a_label', '?')}-{m.get('b_label', '?')}: "
                f"{m['known_cm']:.1f} cm / {m['px']:.0f} px = "
                f"{m['cm_per_px']:.4f} cm/px")
        stats = self._scale_stats()
        if stats is None:
            self._stats_var.set("scale: (none - add a measurement)")
        else:
            self._stats_var.set(
                f"scale: {stats['mean']:.4f} cm/px  "
                f"(CV {stats['cv'] * 100:.1f}%, n={stats['n']})")

    def _save_scale(self) -> None:
        stats = self._scale_stats()
        if stats is None:
            messagebox.showinfo("Save scale", "Add at least one measurement.")
            return
        fs = self._frame_size()
        data = {
            "version": 1,
            "frame_size": list(fs) if fs else None,
            "cm_per_px": stats["mean"],
            "measurements": self._measurements,
            "stats": stats,
            "homography": self._scale.homography,
        }
        try:
            with open(SCALE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo(
                "Save scale",
                f"Saved {os.path.basename(SCALE_PATH)} "
                f"({stats['mean']:.4f} cm/px, CV {stats['cv'] * 100:.1f}%).")
        except Exception as e:
            messagebox.showerror("Save scale", f"{type(e).__name__}: {e}")

    # --------------------------------------------------------- poll + preview
    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._poll()
        finally:
            try:
                self.frame.after(POLL_INTERVAL_MS, self._schedule_poll)
            except tk.TclError:
                pass

    def _poll(self) -> None:
        if self._engine is None:
            return
        gate = self._gate_open()
        self._engine.enabled = gate
        if gate:
            try:
                res = self._tracker.snapshot_result()
                landmarks = self._tracker.get_landmarks()
                actions = self._engine.tick(res, landmarks, self._scale,
                                            time.monotonic())
                self._execute(actions)
            except Exception:
                pass
        # Status line: per-rule state + gate reason.
        parts = []
        for st in self._engine.status():
            parts.append(f"{st['id']}: {st['state']} "
                         f"(top-ups {st['topups']}/{st['max_topups']}, "
                         f"fires {st['fires']})")
        self._status_var.set("  |  ".join(parts) if parts else "rules: none loaded")
        self._gate_var.set(self._gate_reason())

    def _schedule_preview(self) -> None:
        if self._closed:
            return
        try:
            self._render_preview()
        finally:
            try:
                self.frame.after(PREVIEW_INTERVAL_MS, self._schedule_preview)
            except tk.TclError:
                pass

    def _render_preview(self) -> None:
        if self._camera is None:
            return
        try:
            if not self._preview.winfo_viewable():
                return
        except tk.TclError:
            return
        frame = self._camera.get_latest_frame()
        if frame is None:
            return
        try:
            h, w = frame.shape[:2]
            scale = min(1.0, PREVIEW_MAX_W / float(w)) if w else 1.0
            if scale < 1.0:
                disp = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                                  interpolation=cv2.INTER_AREA)
            else:
                disp = frame.copy()
            result = self._tracker.snapshot_result() if self._tracking_running() else None
            if _draw_overlay is not None:
                _draw_overlay(disp, scale, self._tracker, result)
            self._draw_zones(disp, scale, (w, h))
            rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
            self._photo_preview = ImageTk.PhotoImage(Image.fromarray(rgb))
            self._preview.configure(image=self._photo_preview)
        except Exception:
            pass

    def _draw_zones(self, img, scale: float, frame_size) -> None:
        """Draw each enabled rule's proximity circle (cm -> px) on the preview."""
        if self._engine is None or not self._scale.valid():
            return
        try:
            landmarks = self._tracker.get_landmarks()
        except Exception:
            return
        for rule in self._engine.rules:
            if not getattr(rule, "enabled", True):
                continue
            ref = getattr(rule, "landmark", None)
            radius_cm = getattr(rule, "radius_cm", None)
            if ref is None or radius_cm is None:
                continue
            lm_px = resolve_landmark(landmarks, ref, frame_size)
            r_px_full = self._scale.cm_to_px(radius_cm)
            if lm_px is None or r_px_full is None:
                continue
            cx, cy = int(lm_px[0] * scale), int(lm_px[1] * scale)
            r = int(r_px_full * scale)
            primed = getattr(rule, "state", "") == "PRIMED"
            color = (0, 215, 255) if primed else (0, 165, 255)  # amber/orange
            cv2.circle(img, (cx, cy), max(1, r), color, 2)
            cv2.putText(img, f"{getattr(rule, 'id', '')} R={radius_cm:g}cm",
                        (cx + 6, cy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color,
                        1, cv2.LINE_AA)
