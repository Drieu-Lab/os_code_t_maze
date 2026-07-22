"""
rules_view.py - "Rules" tab for experiment_control.py.

The thin GUI/host layer over the pure rules engine (rules_engine.py). It is the
SINGLE actuation authority: it loads rules.json + the cm<->px scale calibration
(rig_scale.json), DRAINS the shared maze-state pipeline (maze_pipeline; ADR-0041)
folding each RatState through its own TaskEvents + engine, feeds lick lines to the
engine, and EXECUTES the returned action intents (send a reward command, write a
rule-event row). The engine itself never sends or logs. Actuation still fires on
the Tk thread here (moving it onto the worker callback is Phase 2b). See
docs/decisions/0023.

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

import protocol
from gui_deps import cv2, missing_dependency
from preview import render_bgr_to_label, safe_after
from tooltips import tip

try:
    from tracker import draw_overlay as _draw_overlay  # noqa: E402
except Exception:  # pragma: no cover
    _draw_overlay = None  # type: ignore

from rules_engine import (
    DEBIAS_CREDENTIALS, NONSTEM_CREDENTIALS, RULE_EDIT_FIELDS,
    RULE_FIELD_CHOICES, EngineDefaults, FireReward, LogEvent, RulesEngine, Scale,
    coerce_rule_field, load_rules_config, resolve_landmark, save_rules_config,
    set_rule_debias, validate_profile_rules,
)

# The reward-shaping phases (1/2a/2b) all use this one template; the editor's
# debias sub-form and conditional field enabling key off it. See ADR-0031.
WELL_TEMPLATE = "well_reward_with_excursion"

# Task-event layer (pure stdlib; safe to import). Zone / loop rule templates need
# the task-event context it produces from the shared pipeline's RatState stream.
# The maze-state estimator itself is no longer built here - it is the one shared
# estimator on the tracking worker (maze_pipeline; ADR-0041). See ADR-0026.
try:
    from task_events import TaskEvents
    _MAZE_STATE_IMPORT_ERROR = None
except Exception as e:  # pragma: no cover
    TaskEvents = None  # type: ignore
    _MAZE_STATE_IMPORT_ERROR = f"{type(e).__name__}: {e}"

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

    def __init__(self, parent: tk.Misc, tracker=None, camera=None, pipeline=None,
                 send=None):
        self._parent = parent
        self._tracker = tracker
        self._camera = camera
        # Shared maze-state pipeline (ADR-0041): the one estimator on the tracking
        # worker. This tab drains its RatState stream and folds each state through
        # its OWN TaskEvents + engine (per-consumer, still on the Tk thread).
        self._pipeline = pipeline
        self._sub = pipeline.register() if pipeline is not None else None
        self._send = send                # callable(str) -> None (RigClient.send)
        self.frame = ttk.Frame(parent)
        self._photo_preview = None
        self._mode = ""                  # last firmware MODE token
        self._engine: Optional[RulesEngine] = None
        self._scale = Scale()
        self._measurements: list = []    # list of measurement dicts
        self._landmark_options: dict = {}  # combobox label -> landmark dict
        # Profiles (reward-shaping phases) + task-event pipeline. See ADR-0026.
        self._config: dict = {}
        self._profiles: dict = {}        # name -> profile dict
        self._active_profile: str = ""
        self._defaults = None            # engine defaults from the loaded config
        self._task_events = None
        self._gate_was_open = False      # gate rising-edge detector (resets state)
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

        self._load_zone_map()
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
        mode = protocol.mode_name(line)
        if mode is not None:
            self._mode = mode.upper()
            return
        if self._engine is None:
            return
        # Feed lick lines; only acts when the gate is open (set on the engine).
        if protocol.lick_edge(line) is not None:
            self._engine.enabled = self._gate_open()
            if self._engine.enabled:
                actions = self._engine.event(line, time.monotonic())
                self._execute(actions)

    def close(self) -> None:
        self._closed = True
        self._stop_log()
        if self._sub is not None:
            self._sub.close()

    # ---------------------------------------------------------- dependencies
    @staticmethod
    def _missing_dependency() -> Optional[str]:
        return missing_dependency("numpy", "opencv-python", "pillow")

    # -------------------------------------------------------------- loading
    def _load_zone_map(self) -> None:
        """Build this tab's per-consumer task-event layer over the shared pipeline.

        The zone map + estimator are now shared (owned by the Maze state tab via
        maze_pipeline; ADR-0041); this tab only needs its own TaskEvents to fold
        the shared RatState stream into reward-rule credentials.
        """
        self._zone_map_error = None
        if TaskEvents is None or self._pipeline is None:
            self._task_events = None
            self._zone_map_error = _MAZE_STATE_IMPORT_ERROR or "maze_state unavailable"
            return
        try:
            self._task_events = TaskEvents()
        except Exception as e:
            self._task_events = None
            self._zone_map_error = f"{type(e).__name__}: {e}"

    def _reset_state_pipeline(self) -> None:
        """Reset only THIS tab's task-event history so an arming session starts
        fresh. The shared estimator is global and is never reset by a consumer
        (its RatState.history is global physical position; ADR-0041)."""
        try:
            if self._task_events is not None:
                self._task_events.reset()
        except Exception:
            pass

    def _load_rules(self) -> None:
        """Load rules.json. Supports either a flat top-level ``rules`` list or a
        ``profiles`` map (reward-shaping phases); the active profile builds the
        engine. See ADR-0026.
        """
        try:
            self._config = load_rules_config(RULES_PATH)
            self._defaults = EngineDefaults.from_dict(self._config.get("defaults"))
            self._profiles = dict(self._config.get("profiles") or {})
            if self._profiles:
                # Pick the configured active profile (or the first defined).
                want = self._config.get("active_profile")
                if want not in self._profiles:
                    want = next(iter(self._profiles))
                self._active_profile = want
            else:
                self._active_profile = ""
            self._build_engine_for_active()
            self._load_error = None
        except Exception as e:
            # Fall back to an empty engine so the tab still loads.
            self._engine = RulesEngine([], EngineDefaults())
            self._profiles = {}
            self._active_profile = ""
            self._load_error = f"{type(e).__name__}: {e}"
        # The engine is always OFF until the user arms it (and the gate opens).
        self._engine.enabled = False

    def _build_engine_for_active(self) -> None:
        """(Re)build the engine from the active profile (or top-level rules)."""
        if self._profiles:
            prof = self._profiles.get(self._active_profile, {})
            rules = prof.get("rules", [])
            defaults = EngineDefaults.from_dict(
                prof.get("defaults") or self._config.get("defaults"))
        else:
            rules = self._config.get("rules", [])
            defaults = EngineDefaults.from_dict(self._config.get("defaults"))
        cfg = {"version": self._config.get("version", 1),
               "enabled": False, "defaults": defaults.__dict__, "rules": rules}
        self._engine = RulesEngine.from_config(cfg)
        self._engine.enabled = False
        self._reset_state_pipeline()
        self._sync_nonstem_widget()
        self._rebuild_editor()

    # ------------------------------------------------- non-stem reward fade
    def _choice_rules(self) -> list:
        """Active rules whose credit fades with the non-stem probability."""
        if self._engine is None:
            return []
        return [r for r in self._engine.rules
                if getattr(r, "credential", None) == "choice"
                and hasattr(r, "nonstem_reward_prob")]

    def _current_nonstem_prob(self) -> float:
        rs = self._choice_rules()
        return float(rs[0].nonstem_reward_prob) if rs else 1.0

    def _sync_nonstem_widget(self) -> None:
        """Reflect the active profile: enable only when a 'choice' rule is
        active, and show its current p."""
        spin = getattr(self, "_nonstem_spin", None)
        if spin is None:
            return
        rs = self._choice_rules()
        try:
            if rs:
                self._nonstem_var.set(round(float(rs[0].nonstem_reward_prob), 3))
                spin.configure(state="normal")
            else:
                spin.configure(state="disabled")
        except Exception:
            pass

    def _apply_nonstem_prob(self) -> None:
        rs = self._choice_rules()
        if not rs:
            return
        try:
            p = max(0.0, min(1.0, float(self._nonstem_var.get())))
        except (tk.TclError, ValueError):
            messagebox.showinfo("Non-stem reward prob", "Enter a number 0.0-1.0.")
            return
        for r in rs:
            r.nonstem_reward_prob = p
            self._log_event(LogEvent("nonstem_prob_set", r.id, {"p": round(p, 3)}))
        self._nonstem_var.set(round(p, 3))

    # ------------------------------------------------- rules.json field editor
    def _active_rule_dicts(self) -> list:
        """The rule DICTS of the active profile (or the flat list).

        These are the live objects inside ``self._config``; editing them in
        place and calling ``save_rules_config`` writes the change back while
        leaving the ``_schema`` block and every untouched key intact.
        """
        if self._profiles:
            prof = self._profiles.get(self._active_profile, {})
            return list(prof.get("rules", []))
        return list(self._config.get("rules", []))

    def _rebuild_editor(self) -> None:
        """(Re)build the field editor for the active profile's rules."""
        body = getattr(self, "_editor_body", None)
        if body is None:      # called during __init__ before the UI is built
            return
        for child in body.winfo_children():
            child.destroy()
        self._editor_rows = []
        rules = self._active_rule_dicts()
        if not rules:
            ttk.Label(body, text="(no editable rules in this phase)").pack(
                side=tk.TOP, anchor="w", padx=4, pady=2)
            return
        for rule in rules:
            self._build_rule_editor(body, rule)

    def _build_rule_editor(self, parent: tk.Misc, rule: dict) -> None:
        template = rule.get("template", "")
        box = ttk.LabelFrame(parent, text=f"{rule.get('id', '?')}  [{template}]")
        box.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        row: dict = {"rule": rule, "template": template, "vars": {},
                     "widgets": {}, "kinds": {}, "debias": None}
        grid = ttk.Frame(box)
        grid.pack(side=tk.TOP, fill=tk.X, padx=2, pady=2)
        for r, (key, kind) in enumerate(RULE_EDIT_FIELDS.get(template, [])):
            row["kinds"][key] = kind
            ttk.Label(grid, text=key + ":").grid(
                row=r, column=0, sticky="w", padx=(2, 4), pady=1)
            if kind == "bool":
                var: tk.Variable = tk.BooleanVar(value=bool(rule.get(key, True)))
                w: tk.Widget = ttk.Checkbutton(grid, variable=var)
            elif key in RULE_FIELD_CHOICES:
                choices = RULE_FIELD_CHOICES[key]
                var = tk.StringVar(value=str(rule.get(key, choices[0])))
                w = ttk.Combobox(grid, width=16, state="readonly",
                                 values=choices, textvariable=var)
                if key == "credential":
                    w.bind("<<ComboboxSelected>>",
                           lambda _e, rw=row: self._sync_editor_enable(rw))
            else:
                default = rule.get(key, "")
                var = tk.StringVar(value=("" if default == "" else str(default)))
                w = ttk.Entry(grid, width=10, textvariable=var)
            w.grid(row=r, column=1, sticky="w", padx=2, pady=1)
            tip(w, f"rules.editor.field.{key}")
            row["vars"][key] = var
            row["widgets"][key] = w
        if template == WELL_TEMPLATE:
            self._build_debias_editor(box, rule, row)
        self._editor_rows.append(row)
        self._sync_editor_enable(row)

    def _build_debias_editor(self, box: tk.Misc, rule: dict, row: dict) -> None:
        """The optional anti-bias sub-form (well_reward only)."""
        db = rule.get("debias") or {}
        mode = str(db.get("mode")) if db else "none"
        if mode not in ("none", "fixed", "uniform"):
            mode = "none"
        frame = ttk.Frame(box)
        frame.pack(side=tk.TOP, fill=tk.X, padx=2, pady=(0, 2))
        ttk.Label(frame, text="debias:").pack(side=tk.LEFT, padx=(2, 4))
        mvar = tk.StringVar(value=mode)
        mcmb = ttk.Combobox(frame, width=8, state="readonly",
                            values=["none", "fixed", "uniform"], textvariable=mvar)
        mcmb.pack(side=tk.LEFT, padx=2)
        mcmb.bind("<<ComboboxSelected>>",
                  lambda _e, rw=row: self._sync_editor_enable(rw))
        widgets = {"mode": mcmb}
        variables = {"mode": mvar}
        for label, key, default in (("n", "n", 3), ("min", "min", 3),
                                    ("max", "max", 5)):
            ttk.Label(frame, text=f"{label}:").pack(side=tk.LEFT)
            v = tk.StringVar(value=str(db.get(key, default)))
            e = ttk.Entry(frame, width=4, textvariable=v)
            e.pack(side=tk.LEFT, padx=2)
            widgets[key] = e
            variables[key] = v
        tip(frame, "rules.editor.debias")
        row["debias"] = {"vars": variables, "widgets": widgets}

    def _sync_editor_enable(self, row: dict) -> None:
        """Gray out well fields that don't apply to the selected credential."""
        if row.get("template") != WELL_TEMPLATE:
            return
        cred_var = row["vars"].get("credential")
        cred = cred_var.get() if cred_var is not None else ""
        nonstem = row["widgets"].get("nonstem_reward_prob")
        if nonstem is not None:
            nonstem.configure(
                state=("normal" if cred in NONSTEM_CREDENTIALS else "disabled"))
        db = row.get("debias")
        if db:
            applies = cred in DEBIAS_CREDENTIALS
            mode = db["vars"]["mode"].get()
            db["widgets"]["mode"].configure(
                state=("readonly" if applies else "disabled"))
            for key in ("n", "min", "max"):
                on = applies and ((key == "n" and mode == "fixed")
                                  or (key in ("min", "max") and mode == "uniform"))
                db["widgets"][key].configure(state=("normal" if on else "disabled"))

    def _save_rules(self) -> None:
        """Coerce the editor fields into rules.json, validate, and write."""
        rows = getattr(self, "_editor_rows", [])
        if not rows:
            messagebox.showinfo("Save rules", "No editable rules in this phase.")
            return
        try:
            for row in rows:
                rule = row["rule"]
                for key, var in row["vars"].items():
                    kind = row["kinds"][key]
                    raw = var.get()
                    if kind in ("int", "float") and str(raw).strip() == "":
                        continue      # leave a blank optional numeric unset
                    rule[key] = coerce_rule_field(kind, raw)
                db = row.get("debias")
                if db is not None:
                    set_rule_debias(
                        rule, db["vars"]["mode"].get(),
                        n=(db["vars"]["n"].get() or 3),
                        lo=(db["vars"]["min"].get() or 3),
                        hi=(db["vars"]["max"].get() or 5))
        except (ValueError, TypeError) as e:
            messagebox.showerror("Save rules", f"Invalid field value: {e}")
            return
        rules = self._active_rule_dicts()
        if self._profiles:
            defaults = (self._profiles.get(self._active_profile, {}).get("defaults")
                        or self._config.get("defaults"))
        else:
            defaults = self._config.get("defaults")
        try:
            validate_profile_rules(rules, defaults)
        except Exception as e:
            messagebox.showerror(
                "Save rules",
                f"Edits are invalid, nothing was saved:\n{type(e).__name__}: {e}")
            return
        if self._active_profile:
            # Persist the current phase selection alongside the field edits.
            self._config["active_profile"] = self._active_profile
        try:
            save_rules_config(RULES_PATH, self._config)
        except Exception as e:
            messagebox.showerror("Save rules", f"{type(e).__name__}: {e}")
            return
        # Rebuild from the saved config and disarm (re-arm deliberately).
        self._enable_var.set(False)
        self._build_engine_for_active()
        self._engine.enabled = False
        messagebox.showinfo(
            "Save rules",
            f"Saved {len(rules)} rule(s) to rules.json "
            f"(phase: {self._active_profile or 'flat'}). Re-arm when ready.")

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

        # Reward-shaping phase selector (only when rules.json defines profiles).
        if self._profiles:
            prow = ttk.Frame(controls)
            prow.pack(side=tk.TOP, fill=tk.X, **pad)
            ttk.Label(prow, text="Phase:").pack(side=tk.LEFT, padx=(4, 2))
            self._profile_labels = {
                (p.get("label") or name): name
                for name, p in self._profiles.items()}
            self._profile_var = tk.StringVar()
            self._profile_cmb = ttk.Combobox(
                prow, width=28, state="readonly",
                values=list(self._profile_labels.keys()),
                textvariable=self._profile_var)
            # Preselect the active profile's label.
            for label, name in self._profile_labels.items():
                if name == self._active_profile:
                    self._profile_var.set(label)
                    break
            self._profile_cmb.pack(side=tk.LEFT, padx=2)
            self._profile_cmb.bind("<<ComboboxSelected>>", self._on_profile_change)
            tip(self._profile_cmb, "rules.profile")

        # Non-stem reward probability (the P1 -> 2a fade; see ADR-0028). Set it
        # per session, lowering it across days from 1.0 (Phase 1) to 0.0 (= 2a).
        shaping = ttk.LabelFrame(controls, text="Shaping (Phase 1 -> 2a fade)")
        shaping.pack(side=tk.TOP, fill=tk.X, **pad)
        ttk.Label(shaping, text="Non-stem reward prob:").pack(side=tk.LEFT, padx=(4, 2))
        self._nonstem_var = tk.DoubleVar(value=self._current_nonstem_prob())
        self._nonstem_spin = ttk.Spinbox(
            shaping, from_=0.0, to=1.0, increment=0.05, width=6,
            textvariable=self._nonstem_var, command=self._apply_nonstem_prob)
        self._nonstem_spin.pack(side=tk.LEFT, padx=2)
        tip(self._nonstem_spin, "rules.nonstem")
        apply_p = ttk.Button(shaping, text="Apply", command=self._apply_nonstem_prob)
        apply_p.pack(side=tk.LEFT, padx=2)
        tip(apply_p, "rules.nonstem")
        self._sync_nonstem_widget()

        # Per-rule field editor: tune the active phase's rule(s) and write the
        # changes back to rules.json. See docs/decisions/0031.
        editor = ttk.LabelFrame(controls, text="Rules editor (active phase)")
        editor.pack(side=tk.TOP, fill=tk.X, **pad)
        self._editor_body = ttk.Frame(editor)
        self._editor_body.pack(side=tk.TOP, fill=tk.X)
        save_rules_btn = ttk.Button(editor, text="Save to rules.json",
                                    command=self._save_rules)
        save_rules_btn.pack(side=tk.TOP, anchor="w", padx=4, pady=(2, 4))
        tip(save_rules_btn, "rules.editor.save")
        self._editor_rows: list = []
        self._rebuild_editor()

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
    def _on_profile_change(self, _evt=None) -> None:
        """Switch the active reward-shaping phase and rebuild the engine."""
        label = self._profile_var.get()
        name = getattr(self, "_profile_labels", {}).get(label)
        if not name or name == self._active_profile:
            return
        # Disarm on any phase change (the experimenter re-arms deliberately).
        self._enable_var.set(False)
        self._active_profile = name
        self._build_engine_for_active()
        self._engine.enabled = False

    def _on_enable(self) -> None:
        if not self._enable_var.get():
            return
        if self._needs_scale() and not self._scale.valid():
            messagebox.showinfo(
                "Arm rules",
                "No scale calibration yet. Measure the cm/px scale (below) so "
                "the engine can convert rule radii to pixels.")
            self._enable_var.set(False)
            return
        if self._needs_task() and not self._task_ready():
            messagebox.showinfo(
                "Arm rules",
                "This phase needs a maze zone map (zones.json) and the maze-state "
                "estimator. Define the zones in the Tracking tab first.")
            self._enable_var.set(False)
            return
        if self._load_error:
            messagebox.showwarning(
                "Arm rules",
                f"rules.json failed to load ({self._load_error}); no rules are "
                "active.")

    def _reload_rules(self) -> None:
        was_armed = self._enable_var.get()
        self._enable_var.set(False)
        self._load_zone_map()
        self._load_rules()
        # Refresh the phase dropdown to match the reloaded file.
        if getattr(self, "_profile_cmb", None) is not None:
            self._profile_labels = {
                (p.get("label") or name): name
                for name, p in self._profiles.items()}
            self._profile_cmb["values"] = list(self._profile_labels.keys())
            for label, name in self._profile_labels.items():
                if name == self._active_profile:
                    self._profile_var.set(label)
                    break
        if self._load_error:
            messagebox.showerror("Reload rules", self._load_error)
        else:
            profile = f" (phase: {self._active_profile})" if self._active_profile else ""
            messagebox.showinfo(
                "Reload rules",
                f"Loaded {len(self._engine.rules)} rule(s) from rules.json{profile}.")
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

    def _needs_scale(self) -> bool:
        """True if any active rule measures cm distances (needs a valid Scale)."""
        if self._engine is None:
            return False
        return any(getattr(r, "needs_scale", False) for r in self._engine.rules)

    def _needs_task(self) -> bool:
        """True if any active rule needs the zone/task-event context."""
        if self._engine is None:
            return False
        return any(getattr(r, "needs_task", False) for r in self._engine.rules)

    def _task_ready(self) -> bool:
        return bool(self._pipeline is not None and self._task_events is not None
                    and self._pipeline.zones_valid())

    def _gate_open(self) -> bool:
        """All conditions that must hold before the engine may actuate."""
        if not (self._enable_var.get() and self._engine is not None
                and self._engine.rules and self._tracking_running()
                and self._mode_is_freeroam()):
            return False
        if self._needs_scale() and not self._scale.valid():
            return False
        if self._needs_task() and not self._task_ready():
            return False
        return True

    def _gate_reason(self) -> str:
        if not self._enable_var.get():
            return ""
        if self._engine is None or not self._engine.rules:
            return "gated: no rules loaded"
        if self._needs_scale() and not self._scale.valid():
            return "gated: no scale calibration"
        if self._needs_task() and not self._task_ready():
            return "gated: no maze zone map (zones.json)"
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
            safe_after(self.frame, POLL_INTERVAL_MS, self._schedule_poll)

    def _poll(self) -> None:
        if self._engine is None:
            return
        gate = self._gate_open()
        self._engine.enabled = gate
        # On the gate's rising edge, drop any stale zone/loop history so an
        # arming session starts from a clean maze state.
        if gate and not self._gate_was_open:
            self._reset_state_pipeline()
        self._gate_was_open = gate
        if gate:
            try:
                landmarks = self._tracker.get_landmarks()
                bundles = self._sub.drain() if self._sub is not None else []
                if bundles:
                    # Fold EVERY shared RatState since last poll: the choice/loop
                    # edges are one-frame flags, so folding only the latest would
                    # drop a trial (and its reward) that landed between polls. The
                    # engine ticks per bundle (frame rate) but still on the Tk
                    # thread, batched here - actuation moves to the worker in 2b.
                    for b in bundles:
                        task = None
                        if self._needs_task() and self._task_events is not None:
                            task = self._task_events.update(b.state)
                        actions = self._engine.tick(
                            b.track_result, landmarks, self._scale, b.mono_ts,
                            task=task)
                        self._execute(actions)
                elif not self._needs_task():
                    # Proximity-only rules have no edges to miss, so a single tick
                    # on the freshest result each poll suffices (and works even
                    # with no pipeline, e.g. legacy landmark-radius rules).
                    res = self._tracker.snapshot_result()
                    actions = self._engine.tick(res, landmarks, self._scale,
                                                time.monotonic(), task=None)
                    self._execute(actions)
            except Exception:
                pass
        # Status line: per-rule state + gate reason.
        parts = []
        for st in self._engine.status():
            armed = " ARMED" if st.get("armed") else ""
            cred = f" [{st['credential']}]" if st.get("credential") else ""
            parts.append(f"{st['id']}{cred}: {st['state']}{armed} "
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
            safe_after(self.frame, PREVIEW_INTERVAL_MS, self._schedule_preview)

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
        h, w = frame.shape[:2]
        result = self._tracker.snapshot_result() if self._tracking_running() else None

        def draw(disp, scale: float) -> None:
            if _draw_overlay is not None:
                _draw_overlay(disp, scale, self._tracker, result)
            self._draw_zones(disp, scale, (w, h))

        photo = render_bgr_to_label(self._preview, frame, PREVIEW_MAX_W, draw=draw)
        if photo is not None:
            self._photo_preview = photo

    def _draw_zone_polys(self, img, scale: float, frame_size) -> None:
        """Outline the maze zones + well on the preview (task/loop phases)."""
        zm = self._pipeline.zone_map if self._pipeline is not None else None
        if zm is None or not zm.valid():
            return
        w, h = frame_size

        def to_px(pt):
            return (int(pt[0] * w * scale), int(pt[1] * h * scale))

        for z in zm.zones:
            poly = z.polygon
            if len(poly) < 2:
                continue
            for i in range(len(poly)):
                cv2.line(img, to_px(poly[i]), to_px(poly[(i + 1) % len(poly)]),
                         (120, 180, 120), 1, cv2.LINE_AA)
            cv2.putText(img, z.name, to_px(poly[0]), cv2.FONT_HERSHEY_SIMPLEX,
                        0.35, (120, 200, 120), 1, cv2.LINE_AA)
        if zm.well and len(zm.well) >= 2:
            for i in range(len(zm.well)):
                cv2.line(img, to_px(zm.well[i]), to_px(zm.well[(i + 1) % len(zm.well)]),
                         (0, 215, 255), 2, cv2.LINE_AA)

    def _draw_zones(self, img, scale: float, frame_size) -> None:
        """Draw the maze zones (task phases) + each legacy rule's proximity circle."""
        if self._needs_task():
            self._draw_zone_polys(img, scale, frame_size)
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
