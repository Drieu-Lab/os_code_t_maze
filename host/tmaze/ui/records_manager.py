"""
records_manager.py - desktop GUI for the animal/session records (thin shell).

One window to keep the lab's *records* without hand-editing CSVs or workbooks:

- **Daily entry** - add/edit a daily husbandry row (weight, baseline, auto
  % baseline with a <90% flag, water on/off, supplemental water, notes). Saving
  upserts the row into ``daily_log.csv`` (the source of truth) by
  ``(animal_id, date)`` - it never rewrites the whole file, so a row another
  surface added survives.
- **Sessions** - browse the recorded sessions (from ``session_registry.csv``),
  read each one's metrics + rendered summary, and type the qualitative "common
  procedures" + notes for it; saving upserts ``session_annotations.csv`` by
  ``session_id`` (ADR-0089).
- **Roster** - add/remove rats and edit their aliases; saving validates and
  writes ``animals.json`` (the identity source of truth).
- **Dashboard** - a read-only welfare view: a compact per-rat status strip
  (% baseline today + <90% flag + grams-to-90% deficit; a not-logged/not-weighed
  alarm; water intake by source) above a single combined 14-day % baseline trend
  chart that overlays every rat (per-rat show/hide toggles, one shared 90% line).
  The optional water-mL overlay draws only when exactly one rat is soloed. Signals
  come from the pure ``dashboard`` core.

Two one-click actions tie the CSV sources of truth to the human-readable
artifacts, both non-destructive (the plan's reversible dual-surface model,
ADR-0085):

- **Generate files** - rebuild the session registry (in-process) then the two
  workbooks (``daily_log.xlsx`` regenerated as a CSV mirror, ``session_training_log.xlsx``)
  by running the resources-repo builders.
- **Import from Excel** - fold hand-edits made directly in the workbooks back into
  the CSVs (``build_daily_log.py --export``, ``build_session_training_log.py
  --import-annotations``), so the Excel files stay a working fallback for
  remote/no-GUI record-keeping.

This module is the SHELL: all data logic is in the pure cores
(``tmaze.core.records.daily_log / session_annotations / animals / dashboard``);
here we only render Tk widgets, run the builders (subprocess / in-process), and
degrade gracefully when the sibling resources repo isn't present.

Run (conda env drieu_t_maze):
    python -m tmaze.ui.records_manager
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import io
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from tmaze.core.records import (
    RecordsFileError,
    animals,
    dashboard,
    daily_log,
    session_annotations,
    session_ignore,
)
from tmaze.core.session import session_detail
from tmaze.paths import host_dir, phase3_recordings_root
from tmaze.tools import build_session_registry

# The four daily-entry water/measurement fields the form binds to (the rest of
# daily_log.FIELDS are animal_id/date/pct_baseline/water_on/off/notes).
_ENTRY_LABELS = {
    "weight_g": "Weight (g)",
    "baseline_g": "Baseline (g)",
    "water_on": "H2O ON",
    "water_off": "H2O OFF",
    "water_ml": "Water (mL)",
    "supp_water": "Supp. water",
}

_BUILDER_TIMEOUT_S = 180

# Small GUI-pref file (a machine path, so git-ignored): remembers the recordings
# folder the operator picked so it survives restarts. Precedence for the scan
# root is this pref, else tmaze.paths.phase3_recordings_root() (which itself
# honours the TMAZE_RECORDINGS_DIR env var, else the machine default; ADR-0088).
_PREFS_PATH = os.path.join(str(host_dir()), "records_manager.json")

# Stable per-rat colours for the combined dashboard trend (matplotlib tab10);
# assigned by the rat's position in the roster so a given rat keeps its colour.
_RAT_PALETTE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f")


def _today_iso() -> str:
    return dt.date.today().isoformat()


def _is_iso_date(value: str) -> bool:
    """True if ``value`` is a real ``YYYY-MM-DD`` date (the record key format).

    Guards against mis-keyed rows / silent dashboard misses from typos like
    ``2026-8-1`` or ``08/11/2026`` (#14)."""
    try:
        dt.date.fromisoformat(str(value).strip())
        return True
    except (ValueError, TypeError):
        return False


def _read_csv_skip_comments(path: str) -> List[Dict[str, str]]:
    """Rows of a `# schema_version`-prefixed CSV as dicts; [] if absent/unreadable."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    except OSError:
        return []
    return list(csv.DictReader(lines))


class RecordsManager:
    """The records-manager window: a notebook of tabs over the record cores."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("T-maze Records Manager")
        self.root.geometry("1000x680")

        # Resolved on-disk locations (all best-effort; the cores tolerate absence).
        self.daily_path = daily_log.default_daily_log_path()
        self.annot_path = session_annotations.default_annotations_path()
        self.animals_path = animals.animals_json_path()
        self.records_dir = os.path.dirname(self.daily_path)
        # Where the rig's session bundles are SCANNED from (operator-pickable,
        # persisted). The registry CSV is READ from a fixed spot regardless of the
        # scan root (host/data/phase3_recordings; see docs/decisions/0088).
        self.recordings_root = self._load_recordings_root()
        self.registry_path = os.path.join(
            build_session_registry.REGISTRY_OUTPUT_DIR,
            build_session_registry.REGISTRY_FILENAME)

        self._busy = False
        # Sessions tab selection state (session_id of the selected row + the
        # per-Treeview-item registry rows behind it).
        self.current_session_id = ""
        self._session_row_by_item: Dict[str, Dict[str, str]] = {}
        # Unsaved-edit tracking (#3): the Daily form marks itself dirty on any
        # user edit (suspended during programmatic fills); the annotation editor
        # uses the Text widgets' own modified flag. Reload/Generate/Import/close
        # and switching sessions confirm before discarding.
        self._daily_dirty = False
        self._suspend_dirty = False
        self._suspend_session_select = False
        self._load_data()
        self._build_ui()
        self._refresh_all()
        # Guard the window-close button too (unsaved edits / a running build).
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- data
    def _load_data(self) -> None:
        """(Re)read every source of truth from disk into memory."""
        self.roster = animals.load_records(self.animals_path)  # [{id, aliases}]
        self.animal_ids = [r["id"] for r in self.roster] or ["600", "601", "602"]
        self.daily_rows = daily_log.load_rows(self.daily_path)
        self.daily = daily_log.DailyLog(self.daily_rows)
        self.annot_rows = session_annotations.load_rows(self.annot_path)
        self.registry_rows = _read_csv_skip_comments(self.registry_path)
        # Non-real sessions to hide from the list (test runs / stray folders kept
        # on disk; ADR-0097). Applied to both registry and annotation-only rows so
        # a stale registry or a leftover annotation can't resurface them.
        self.ignored_sessions = session_ignore.load_ignored()

    # ---------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(side="top", fill="x")
        ttk.Button(bar, text="Generate files",
                   command=self._on_generate).pack(side="left")
        ttk.Button(bar, text="Import from Excel",
                   command=self._on_import).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="Reload",
                   command=self._on_reload).pack(side="left", padx=(6, 0))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status_var,
                  foreground="#555").pack(side="right")

        # Recordings folder: the root "Generate files" scans for session bundles.
        # Shown + pickable so the operator can point it at the rig's recordings
        # without editing code; the choice persists (see docs/decisions/0088).
        recbar = ttk.Frame(self.root, padding=(8, 0))
        recbar.pack(side="top", fill="x")
        ttk.Label(recbar, text="Recordings:").pack(side="left")
        self.rec_root_var = tk.StringVar(value=self.recordings_root)
        ttk.Label(recbar, textvariable=self.rec_root_var,
                  foreground="#555").pack(side="left", padx=(4, 6))
        ttk.Button(recbar, text="Browse...",
                   command=self._on_pick_recordings).pack(side="left")

        if not os.path.isdir(self.records_dir):
            warn = ttk.Label(
                self.root, foreground="#9C0006",
                text=("Resources repo not found at "
                      f"{self.records_dir} - saves will still write there, but "
                      "Generate/Import need the builders. Set "
                      "OS_CODE_T_MAZE_RESOURCES if it lives elsewhere."))
            warn.pack(side="top", fill="x", padx=8)

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(side="top", fill="both", expand=True, padx=8, pady=6)
        self._build_daily_tab()
        self._build_sessions_tab()
        self._build_roster_tab()
        self._build_dashboard_tab()

    # ---- Daily entry tab --------------------------------------------------
    def _build_daily_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Daily entry")

        form = ttk.LabelFrame(tab, text="Daily husbandry row", padding=8)
        form.pack(side="top", fill="x")

        self.d_vars: Dict[str, tk.StringVar] = {
            f: tk.StringVar() for f in daily_log.FIELDS}
        # Any user edit to a field marks the form dirty (#3); programmatic fills
        # wrap themselves in _suspend_dirty_tracking so they don't trip this.
        for _f in daily_log.FIELDS:
            self.d_vars[_f].trace_add("write", self._mark_daily_dirty)

        # Animal (dropdown) + Date.
        ttk.Label(form, text="Animal").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        self.d_animal = ttk.Combobox(form, textvariable=self.d_vars["animal_id"],
                                     values=self.animal_ids, width=10)
        self.d_animal.grid(row=0, column=1, sticky="w")
        self.d_animal.bind("<<ComboboxSelected>>", self._on_form_animal)

        ttk.Label(form, text="Date").grid(row=0, column=2, sticky="e", padx=4)
        self.d_vars["date"].set(_today_iso())
        ttk.Entry(form, textvariable=self.d_vars["date"], width=12).grid(
            row=0, column=3, sticky="w")

        # Weight / baseline / % baseline (auto).
        for col, field in enumerate(("weight_g", "baseline_g")):
            ttk.Label(form, text=_ENTRY_LABELS[field]).grid(
                row=1, column=col * 2, sticky="e", padx=4, pady=3)
            e = ttk.Entry(form, textvariable=self.d_vars[field], width=12)
            e.grid(row=1, column=col * 2 + 1, sticky="w")
            self.d_vars[field].trace_add("write", lambda *_a: self._update_pct())
        ttk.Label(form, text="% baseline").grid(row=1, column=4, sticky="e", padx=4)
        self.d_pct = ttk.Label(form, text="-", width=14)
        self.d_pct.grid(row=1, column=5, sticky="w")

        # Water + notes.
        ttk.Label(form, text=_ENTRY_LABELS["water_on"]).grid(row=2, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(form, textvariable=self.d_vars["water_on"], width=12).grid(row=2, column=1, sticky="w")
        ttk.Label(form, text=_ENTRY_LABELS["water_off"]).grid(row=2, column=2, sticky="e", padx=4)
        ttk.Entry(form, textvariable=self.d_vars["water_off"], width=12).grid(row=2, column=3, sticky="w")
        ttk.Label(form, text=_ENTRY_LABELS["water_ml"]).grid(row=2, column=4, sticky="e", padx=4)
        ttk.Entry(form, textvariable=self.d_vars["water_ml"], width=12).grid(row=2, column=5, sticky="w")

        ttk.Label(form, text=_ENTRY_LABELS["supp_water"]).grid(row=3, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(form, textvariable=self.d_vars["supp_water"], width=12).grid(row=3, column=1, sticky="w")
        ttk.Label(form, text="Notes").grid(row=3, column=2, sticky="e", padx=4)
        ttk.Entry(form, textvariable=self.d_vars["notes"], width=48).grid(
            row=3, column=3, columnspan=3, sticky="we")

        btns = ttk.Frame(form)
        btns.grid(row=4, column=0, columnspan=6, sticky="w", pady=(8, 0))
        ttk.Button(btns, text="Save row", command=self._save_daily).pack(side="left")
        ttk.Button(btns, text="Clear form", command=self._clear_daily).pack(
            side="left", padx=(6, 0))

        # Recent rows (most recent first); double-click loads a row for editing.
        # A "Show rat" filter narrows the table to one animal so per-rat trends
        # are easy to read; it follows the animal picked in the form above.
        filt = ttk.Frame(tab)
        filt.pack(side="top", fill="x", pady=(8, 0))
        ttk.Label(filt, text="Show rat").pack(side="left")
        self.d_filter = tk.StringVar(value="All")
        self.d_filter_box = ttk.Combobox(
            filt, textvariable=self.d_filter, values=["All"] + self.animal_ids,
            state="readonly", width=10)
        self.d_filter_box.pack(side="left", padx=(4, 0))
        self.d_filter_box.bind("<<ComboboxSelected>>",
                               lambda _e: self._refresh_daily_table())

        table = ttk.Frame(tab)
        table.pack(side="top", fill="both", expand=True, pady=(4, 0))
        cols = list(daily_log.FIELDS)
        self.d_tree = ttk.Treeview(table, columns=cols, show="headings", height=12)
        for c in cols:
            self.d_tree.heading(c, text=c)
            self.d_tree.column(c, width=90, anchor="center")
        self.d_tree.column("notes", width=220, anchor="w")
        vsb = ttk.Scrollbar(table, orient="vertical", command=self.d_tree.yview)
        self.d_tree.configure(yscrollcommand=vsb.set)
        self.d_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.d_tree.bind("<Double-1>", self._load_selected_daily)

    def _on_form_animal(self, _event=None) -> None:
        """Picking an animal in the form prefills its baseline and narrows the
        recent-rows table to that rat (single-rat focus during data entry)."""
        self._prefill_baseline()
        aid = self.d_vars["animal_id"].get().strip()
        if aid:
            self.d_filter.set(aid)
            self._refresh_daily_table()

    def _prefill_baseline(self) -> None:
        """When picking an animal, prefill baseline from its most recent row."""
        aid = self.d_vars["animal_id"].get().strip()
        if not aid or self.d_vars["baseline_g"].get().strip():
            return
        latest = self.daily.latest_for(aid)
        if latest and latest.get("baseline_g"):
            self.d_vars["baseline_g"].set(latest["baseline_g"])

    def _update_pct(self) -> None:
        pct = daily_log.compute_pct_baseline(self.d_vars["weight_g"].get(),
                                             self.d_vars["baseline_g"].get())
        if pct is None:
            self.d_pct.config(text="-", foreground="#000")
        else:
            flag = pct < dashboard.BELOW_THRESHOLD
            self.d_pct.config(text=f"{pct * 100:.1f}%" + (" <90%!" if flag else ""),
                              foreground="#9C0006" if flag else "#1a7f37")

    # ---- unsaved-edit tracking (#3) --------------------------------------
    def _mark_daily_dirty(self, *_args) -> None:
        if not self._suspend_dirty:
            self._daily_dirty = True

    @contextlib.contextmanager
    def _suspend_dirty_tracking(self):
        """Programmatic form fills shouldn't count as user edits."""
        prev = self._suspend_dirty
        self._suspend_dirty = True
        try:
            yield
        finally:
            self._suspend_dirty = prev

    def _annotation_dirty(self) -> bool:
        """True if the annotation editor has unsaved text (Text.edit_modified)."""
        try:
            return bool(self.a_proc.edit_modified()
                        or self.a_notes.edit_modified())
        except (AttributeError, tk.TclError):
            return False

    def _has_unsaved(self) -> bool:
        return self._daily_dirty or self._annotation_dirty()

    def _confirm_discard(self, what: str) -> bool:
        """Ask before an action that would throw away unsaved edits."""
        return messagebox.askyesno(
            "Discard unsaved changes?",
            f"You have unsaved changes: {what}\n\nContinue and discard them?",
            default="no")

    def _save_daily(self) -> None:
        aid = self.d_vars["animal_id"].get().strip()
        date = self.d_vars["date"].get().strip()
        if not aid or not date:
            messagebox.showwarning("Missing key", "Animal and Date are required.")
            return
        if not _is_iso_date(date):
            messagebox.showwarning(
                "Bad date", f"Date '{date}' must be in YYYY-MM-DD form "
                "(e.g. {}).".format(_today_iso()))
            return
        pct = daily_log.compute_pct_baseline(self.d_vars["weight_g"].get(),
                                             self.d_vars["baseline_g"].get())
        edit = {f: self.d_vars[f].get().strip() for f in daily_log.FIELDS}
        edit["pct_baseline"] = "" if pct is None else str(pct)
        try:
            # Read-for-write so a present-but-unreadable CSV aborts the save
            # rather than being overwritten with just this row (#12).
            existing = daily_log.load_rows_for_write(self.daily_path)
            merged = daily_log.upsert_rows(existing, [edit])
            daily_log.write_rows(merged, self.daily_path)
        except RecordsFileError as e:
            messagebox.showerror("Save refused", str(e))
            return
        except (OSError, ValueError) as e:
            messagebox.showerror("Save failed", str(e))
            return
        self._set_status(f"Saved {aid} {date} to daily_log.csv.")
        self._daily_dirty = False
        self._load_data()
        self._refresh_daily_table()
        self._refresh_dashboard()

    def _clear_daily(self) -> None:
        with self._suspend_dirty_tracking():
            for f in daily_log.FIELDS:
                self.d_vars[f].set("")
            self.d_vars["date"].set(_today_iso())
            self._update_pct()
        self._daily_dirty = False

    def _load_selected_daily(self, _event=None) -> None:
        sel = self.d_tree.selection()
        if not sel:
            return
        vals = self.d_tree.item(sel[0], "values")
        # Loading a row into the form is not itself an edit; it becomes dirty
        # only if the operator then changes a field (#3).
        with self._suspend_dirty_tracking():
            for f, v in zip(daily_log.FIELDS, vals):
                self.d_vars[f].set("" if v is None else str(v))
            self._update_pct()
        self._daily_dirty = False

    def _sync_filter_values(self) -> None:
        """Keep the Show-rat filter's options in step with the roster; if the
        currently selected rat is gone, fall back to All."""
        self.d_filter_box["values"] = ["All"] + self.animal_ids
        if self.d_filter.get().strip() not in ("All", *self.animal_ids):
            self.d_filter.set("All")

    def _refresh_daily_table(self) -> None:
        self.d_tree.delete(*self.d_tree.get_children())
        sel = self.d_filter.get().strip()
        rows = [r for r in self.daily_rows
                if sel in ("", "All") or r.get("animal_id", "") == sel]
        rows = sorted(rows,
                      key=lambda r: (r.get("date", ""), r.get("animal_id", "")),
                      reverse=True)
        for r in rows:
            self.d_tree.insert("", "end",
                               values=[r.get(f, "") for f in daily_log.FIELDS])

    # ---- Sessions tab (browse recordings + view logs + annotate) ---------
    def _build_sessions_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Sessions")

        # Top: a rat filter + the session list (mirrors the Daily tab's table).
        filt = ttk.Frame(tab)
        filt.pack(side="top", fill="x")
        ttk.Label(filt, text="Show rat").pack(side="left")
        self.s_filter = tk.StringVar(value="All")
        self.s_filter_box = ttk.Combobox(
            filt, textvariable=self.s_filter, values=["All"] + self.animal_ids,
            state="readonly", width=10)
        self.s_filter_box.pack(side="left", padx=(4, 0))
        self.s_filter_box.bind("<<ComboboxSelected>>",
                               lambda _e: self._refresh_sessions())

        listwrap = ttk.Frame(tab)
        listwrap.pack(side="top", fill="x", pady=(4, 0))
        cols = session_detail.list_columns() + ["annot"]
        heads = {"date": "Date", "time": "Time", "animal": "Animal",
                 "duration": "Duration", "trials": "Trials", "accuracy": "Acc",
                 "task_ml": "Task mL", "manual_ml": "Man mL", "stage": "Stage",
                 "annot": "Annot"}
        widths = {"date": 92, "time": 54, "animal": 60, "duration": 74,
                  "trials": 74, "accuracy": 50, "task_ml": 64, "manual_ml": 64,
                  "stage": 96, "annot": 54}
        self.s_tree = ttk.Treeview(listwrap, columns=cols, show="headings",
                                   height=9)
        for c in cols:
            self.s_tree.heading(c, text=heads.get(c, c))
            self.s_tree.column(c, width=widths.get(c, 80),
                               anchor="w" if c == "stage" else "center")
        vsb = ttk.Scrollbar(listwrap, orient="vertical",
                            command=self.s_tree.yview)
        self.s_tree.configure(yscrollcommand=vsb.set)
        self.s_tree.pack(side="left", fill="x", expand=True)
        vsb.pack(side="right", fill="y")
        self.s_tree.bind("<<TreeviewSelect>>", self._on_session_select)

        # Bottom: left = read-only session log, right = the annotation editor.
        split = ttk.PanedWindow(tab, orient="horizontal")
        split.pack(side="top", fill="both", expand=True, pady=(8, 0))

        left = ttk.LabelFrame(split, text="Session log (read-only)", padding=6)
        # A compact metrics block folded from the registry row (works for every
        # session, even old bundles without a rendered summary).
        self.s_metrics = tk.Text(left, height=8, wrap="none",
                                 background="#f4f4f4", relief="flat")
        self.s_metrics.pack(side="top", fill="x")
        self.s_metrics.configure(state="disabled")
        ttk.Separator(left, orient="horizontal").pack(side="top", fill="x",
                                                       pady=4)
        # The bundle's rendered summary + operator-notes timeline (when present).
        logwrap = ttk.Frame(left)
        logwrap.pack(side="top", fill="both", expand=True)
        self.s_log = tk.Text(logwrap, wrap="word")
        lvsb = ttk.Scrollbar(logwrap, orient="vertical",
                             command=self.s_log.yview)
        self.s_log.configure(yscrollcommand=lvsb.set, state="disabled")
        self.s_log.pack(side="left", fill="both", expand=True)
        lvsb.pack(side="right", fill="y")
        split.add(left, weight=3)

        right = ttk.LabelFrame(split, text="Your annotations", padding=6)
        ttk.Label(right, text="Common procedures across days").pack(
            side="top", anchor="w")
        self.a_proc = tk.Text(right, height=4, wrap="word")
        self.a_proc.pack(side="top", fill="x", pady=(0, 6))
        ttk.Label(right, text="Notes").pack(side="top", anchor="w")
        self.a_notes = tk.Text(right, height=10, wrap="word")
        self.a_notes.pack(side="top", fill="both", expand=True)
        ttk.Button(right, text="Save annotation",
                   command=self._save_annotation).pack(side="top", anchor="e",
                                                        pady=(6, 0))
        split.add(right, weight=2)

        self.a_hint = ttk.Label(
            tab, foreground="#555",
            text=("Sessions come from session_registry.csv. If it's empty, run "
                  "Generate files after recording sessions."))
        self.a_hint.pack(side="bottom", fill="x")

    def _sync_sessions_filter(self) -> None:
        """Keep the Show-rat filter's options in step with the roster; if the
        selected rat is gone, fall back to All."""
        self.s_filter_box["values"] = ["All"] + self.animal_ids
        if self.s_filter.get().strip() not in ("All", *self.animal_ids):
            self.s_filter.set("All")

    def _session_rows_filtered(self) -> List[Dict[str, str]]:
        """Registry rows for the list, newest-first, narrowed by the rat filter;
        plus any annotated session absent from the registry (as a minimal
        synthetic row) so old notes stay reachable."""
        ignored = self.ignored_sessions
        reg_ids = {r.get("session_id", "").strip() for r in self.registry_rows
                   if r.get("session_id")}
        rows: List[Dict[str, str]] = [
            r for r in self.registry_rows
            if r.get("session_id", "").strip() not in ignored]
        for r in self.annot_rows:
            sid = r.get("session_id", "").strip()
            if sid and sid not in reg_ids and sid not in ignored:
                rows.append({"session_id": sid, "animal": r.get("animal", ""),
                             "date": r.get("date", "")})
        sel = self.s_filter.get().strip()
        if sel not in ("", "All"):
            rows = [r for r in rows if str(r.get("animal", "")).strip() == sel]
        rows.sort(key=lambda r: (str(r.get("date", "")),
                                 str(r.get("session_id", ""))), reverse=True)
        return rows

    def _refresh_sessions(self) -> None:
        self._sync_sessions_filter()
        prev = self.current_session_id
        self.s_tree.delete(*self.s_tree.get_children())
        self._session_row_by_item = {}
        # "Annot = yes" means the session has *content* (procedures or notes),
        # not merely a row in the CSV - an all-blank row isn't an annotation (#5).
        annotated = {
            r.get("session_id", "").strip() for r in self.annot_rows
            if r.get("session_id")
            and ((r.get("procedures") or "").strip()
                 or (r.get("notes") or "").strip())}
        reselect = None
        for r in self._session_rows_filtered():
            sid = str(r.get("session_id", "")).strip()
            vals = list(session_detail.list_row(r)) + [
                "yes" if sid in annotated else ""]
            iid = self.s_tree.insert("", "end", values=vals)
            self._session_row_by_item[iid] = r
            if sid and sid == prev:
                reselect = iid
        children = self.s_tree.get_children()
        # Selecting programmatically doesn't reliably raise <<TreeviewSelect>>,
        # so populate the detail panel explicitly. Use _show_session (not the
        # _on_session_select guard) so a programmatic refresh never prompts (#3).
        if reselect:
            self.s_tree.selection_set(reselect)
            self.s_tree.see(reselect)
            self._show_session(self._session_row_by_item.get(reselect, {}))
        elif children:
            self.s_tree.selection_set(children[0])
            self._show_session(self._session_row_by_item.get(children[0], {}))
        else:
            self._clear_session_detail()

    def _item_for_session(self, sid: str) -> Optional[str]:
        """The Treeview item id currently showing ``sid`` (or None)."""
        for iid, row in self._session_row_by_item.items():
            if str(row.get("session_id", "")).strip() == sid:
                return iid
        return None

    def _on_session_select(self, _event=None) -> None:
        """User picked a session row: guard an unsaved annotation on the row we're
        leaving, then show the new one (#3)."""
        if self._suspend_session_select:
            return
        sel = self.s_tree.selection()
        if not sel:
            return
        row = self._session_row_by_item.get(sel[0], {})
        new_sid = str(row.get("session_id", "")).strip()
        if (self.current_session_id and new_sid != self.current_session_id
                and self._annotation_dirty()
                and not self._confirm_discard(
                    f"the annotation for {self.current_session_id}")):
            # Put the selection back on the session being edited, without
            # re-triggering this guard.
            prev = self._item_for_session(self.current_session_id)
            if prev:
                self._suspend_session_select = True
                try:
                    self.s_tree.selection_set(prev)
                    self.s_tree.see(prev)
                finally:
                    self._suspend_session_select = False
            return
        self._show_session(row)

    def _show_session(self, row: Dict[str, str]) -> None:
        """Populate the read-only panels + annotation editor for ``row``."""
        self.current_session_id = str(row.get("session_id", "")).strip()
        self._set_readonly(self.s_metrics,
                           "\n".join(session_detail.metrics_lines(row)))
        text = session_detail.summary_text(
            str(row.get("bundle", "")).strip() or None)
        if not text:
            text = ("No rendered summary for this session.\n\n"
                    "Older bundles (recorded before the summary/ folder was "
                    "written) show only the metrics above. Newer sessions include "
                    "the full behavioral summary and the operator-notes timeline "
                    "here.")
        self._set_readonly(self.s_log, text)
        self._load_annotation(self.current_session_id)

    @staticmethod
    def _set_readonly(widget: tk.Text, text: str) -> None:
        """Replace a read-only Text widget's contents (toggling state around it)."""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _clear_session_detail(self) -> None:
        self.current_session_id = ""
        self._set_readonly(self.s_metrics, "")
        self._set_readonly(self.s_log, "Select a session to view its log.")
        self.a_proc.delete("1.0", "end")
        self.a_notes.delete("1.0", "end")
        self._reset_annotation_modified()

    def _load_annotation(self, sid: str) -> None:
        row = session_annotations.SessionAnnotations(
            self.annot_rows).for_session(sid)
        self.a_proc.delete("1.0", "end")
        self.a_notes.delete("1.0", "end")
        if row:
            self.a_proc.insert("1.0", row.get("procedures", ""))
            self.a_notes.insert("1.0", row.get("notes", ""))
        # A freshly loaded editor is clean; edits after this mark it dirty (#3).
        self._reset_annotation_modified()

    def _reset_annotation_modified(self) -> None:
        for widget in (getattr(self, "a_proc", None), getattr(self, "a_notes", None)):
            try:
                widget.edit_modified(False)
            except (AttributeError, tk.TclError):
                pass

    def _save_annotation(self) -> None:
        sid = self.current_session_id.strip()
        if not sid:
            messagebox.showwarning("No session", "Pick a session first.")
            return
        reg = {r.get("session_id", "").strip(): r for r in self.registry_rows}
        meta = reg.get(sid, {})
        # Denormalised animal/date: prefer the registry, but for an orphan
        # session (annotated, not in the registry) fall back to the existing
        # annotation row so a re-save doesn't blank those fields (#1).
        prior = session_annotations.SessionAnnotations(
            self.annot_rows).for_session(sid) or {}
        animal = str(meta.get("animal") or "").strip() or prior.get("animal", "")
        date = str(meta.get("date") or "").strip() or prior.get("date", "")
        edit = {
            "session_id": sid,
            "animal": animal,
            "date": date,
            "procedures": self.a_proc.get("1.0", "end").strip(),
            "notes": self.a_notes.get("1.0", "end").strip(),
        }
        try:
            # Read-for-write so a present-but-unreadable CSV aborts the save
            # rather than being overwritten with just this row (#12).
            existing = session_annotations.load_rows_for_write(self.annot_path)
            merged = session_annotations.upsert_rows(existing, [edit])
            session_annotations.write_rows(merged, self.annot_path)
        except RecordsFileError as e:
            messagebox.showerror("Save refused", str(e))
            return
        except (OSError, ValueError) as e:
            messagebox.showerror("Save failed", str(e))
            return
        self._set_status(f"Saved annotation for {sid}.")
        self._reset_annotation_modified()
        self._load_data()
        self._refresh_sessions()

    # ---- Roster tab -------------------------------------------------------
    def _build_roster_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Roster")

        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Rats (id  |  aliases)").pack(side="top", anchor="w")
        self.r_tree = ttk.Treeview(left, columns=("id", "aliases"),
                                   show="headings", height=14)
        self.r_tree.heading("id", text="id")
        self.r_tree.heading("aliases", text="aliases (comma-separated)")
        self.r_tree.column("id", width=80, anchor="center")
        self.r_tree.column("aliases", width=260, anchor="w")
        self.r_tree.pack(side="top", fill="both", expand=True)
        self.r_tree.bind("<<TreeviewSelect>>", self._load_selected_roster)

        right = ttk.LabelFrame(tab, text="Add / edit rat", padding=8)
        right.pack(side="right", fill="y", padx=(8, 0))
        ttk.Label(right, text="id").grid(row=0, column=0, sticky="e", pady=3)
        self.r_id = tk.StringVar()
        ttk.Entry(right, textvariable=self.r_id, width=14).grid(row=0, column=1)
        ttk.Label(right, text="aliases").grid(row=1, column=0, sticky="e", pady=3)
        self.r_aliases = tk.StringVar()
        ttk.Entry(right, textvariable=self.r_aliases, width=20).grid(row=1, column=1)
        ttk.Button(right, text="Add / update",
                   command=self._roster_add).grid(row=2, column=0, columnspan=2,
                                                   sticky="we", pady=(6, 2))
        ttk.Button(right, text="Remove selected",
                   command=self._roster_remove).grid(row=3, column=0, columnspan=2,
                                                      sticky="we")
        ttk.Separator(right).grid(row=4, column=0, columnspan=2, sticky="we", pady=8)
        ttk.Button(right, text="Save roster -> animals.json",
                   command=self._roster_save).grid(row=5, column=0, columnspan=2,
                                                    sticky="we")
        ttk.Label(right, foreground="#555", wraplength=180,
                  text=("Edits stay in this window until you Save. Save validates "
                        "ids/aliases before writing.")).grid(
            row=6, column=0, columnspan=2, sticky="we", pady=(8, 0))

    def _refresh_roster(self) -> None:
        self.r_tree.delete(*self.r_tree.get_children())
        for rec in self.roster:
            self.r_tree.insert("", "end",
                               values=(rec["id"], ", ".join(rec.get("aliases", []))))

    def _load_selected_roster(self, _event=None) -> None:
        sel = self.r_tree.selection()
        if not sel:
            return
        rid, aliases = self.r_tree.item(sel[0], "values")
        self.r_id.set(rid)
        self.r_aliases.set(aliases)

    def _roster_add(self) -> None:
        rid = self.r_id.get().strip()
        if not rid:
            messagebox.showwarning("Missing id", "Enter a rat id.")
            return
        aliases = [a.strip() for a in self.r_aliases.get().split(",") if a.strip()]
        self.roster = [r for r in self.roster if r["id"] != rid]
        self.roster.append({"id": rid, "aliases": aliases})
        self.roster.sort(key=lambda r: r["id"])
        self._refresh_roster()
        self._set_status(f"{rid} staged - Save roster to write animals.json.")

    def _roster_remove(self) -> None:
        sel = self.r_tree.selection()
        if not sel:
            return
        rid = self.r_tree.item(sel[0], "values")[0]
        self.roster = [r for r in self.roster if r["id"] != rid]
        self._refresh_roster()
        self._set_status(f"{rid} staged for removal - Save roster to apply.")

    def _roster_save(self) -> None:
        try:
            # Refuse to overwrite a present-but-unreadable animals.json (a save
            # on top of a corrupt file would wipe the roster; #12). Absent is
            # fine - write_records will create it.
            animals.load_records_for_write(self.animals_path)
        except RecordsFileError as e:
            messagebox.showerror("Save refused", str(e))
            return
        try:
            cleaned = animals.write_records(self.roster, self.animals_path)
        except ValueError as e:
            messagebox.showerror("Invalid roster", str(e))
            return
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.roster = cleaned
        self._set_status("Saved roster to animals.json.")
        self._load_data()
        self._refresh_roster()
        self.d_animal["values"] = self.animal_ids
        self._sync_filter_values()
        self._refresh_daily_table()
        # A roster change also affects the Sessions rat filter and the Dashboard
        # per-rat strip/toggles, so refresh those too (#2). (Not _refresh_all -
        # that would clear an in-progress Daily form.)
        self._refresh_sessions()
        self._refresh_dashboard()

    # ---- Dashboard tab ----------------------------------------------------
    def _build_dashboard_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Dashboard")
        head = ttk.Frame(tab)
        head.pack(side="top", fill="x")
        ttk.Label(head, text="As of").pack(side="left")
        self.dash_date = tk.StringVar(value=_today_iso())
        ttk.Entry(head, textvariable=self.dash_date, width=12).pack(side="left", padx=4)
        ttk.Button(head, text="Refresh", command=self._refresh_dashboard).pack(side="left")
        # Optional water overlay: off by default (the water records still have
        # gaps/mislabels, so it isn't informative yet - see docs/decisions/0087)
        # and only meaningful for a single rat, so it draws when exactly one rat
        # is toggled on (bars for several rats at once would be unreadable).
        self.dash_show_water = tk.BooleanVar(value=False)
        ttk.Checkbutton(head, text="Show water trace (single rat only)",
                        variable=self.dash_show_water,
                        command=self._refresh_dashboard).pack(side="left", padx=(12, 0))

        # Per-rat show/hide toggles + a compact status line each, above one shared
        # combined trend chart. The BooleanVars persist across refreshes so a rat
        # stays hidden until the operator re-enables it.
        self.dash_rat_show: Dict[str, tk.BooleanVar] = {}
        self.dash_strip = ttk.Frame(tab)
        self.dash_strip.pack(side="top", fill="x", pady=(8, 0))
        self.dash_body = ttk.Frame(tab)
        self.dash_body.pack(side="top", fill="both", expand=True, pady=(8, 0))
        self._dash_canvases: List = []

    def _rat_color(self, aid: str) -> str:
        """Stable trace colour for a rat, keyed on its roster position."""
        try:
            idx = self.animal_ids.index(aid)
        except ValueError:
            idx = abs(hash(aid))
        return _RAT_PALETTE[idx % len(_RAT_PALETTE)]

    def _sync_dash_toggles(self) -> None:
        """Ensure every current rat has a show/hide var (default on) and drop
        vars for rats no longer in the roster."""
        for aid in self.animal_ids:
            self.dash_rat_show.setdefault(aid, tk.BooleanVar(value=True))
        for aid in list(self.dash_rat_show):
            if aid not in self.animal_ids:
                del self.dash_rat_show[aid]

    def _refresh_dashboard(self) -> None:
        date = self.dash_date.get().strip() or _today_iso()
        if not _is_iso_date(date):
            # Keep the last good view rather than reading against a mis-typed
            # date (which would silently show every rat as "not logged"); #14.
            self._set_status(f"Dashboard date '{date}' must be YYYY-MM-DD.")
            return
        self._sync_dash_toggles()
        for w in (*self.dash_strip.winfo_children(),
                  *self.dash_body.winfo_children()):
            w.destroy()
        # Drop the previous chart canvas reference (its Tk widget was just
        # destroyed); the combined chart repopulates this per refresh.
        self._dash_canvases = []
        missing = dashboard.not_logged_today(self.daily, self.animal_ids, date)
        for aid in self.animal_ids:
            self._dashboard_status_row(self.dash_strip, aid, date,
                                       missing.get(aid))
        self._trend_chart_combined(self.dash_body, date)

    def _dashboard_status_row(self, parent, aid: str, date: str,
                              missing: Optional[str]) -> None:
        """One compact row per rat: a show/hide toggle + colour swatch (matching
        its trace) followed by today's welfare status and water intake."""
        row = ttk.Frame(parent)
        row.pack(side="top", fill="x", anchor="w")
        colour = self._rat_color(aid)
        ttk.Checkbutton(row, variable=self.dash_rat_show[aid],
                        command=self._refresh_dashboard).pack(side="left")
        tk.Label(row, text="  ", background=colour).pack(side="left", padx=(0, 4))
        ttk.Label(row, text=f"Rat {aid}", width=9).pack(side="left")

        status = dashboard.weight_status(self.daily, aid, date)
        if missing:
            line = f"ALARM: {missing} today ({date})"
            fg = "#9C0006"
        elif status["pct_baseline"] is None:
            line = "no weight recorded today"
            fg = "#9C0006"
        else:
            pct = status["pct_baseline"] * 100
            flag = " <90% FLAG" if status["below_threshold"] else ""
            deficit = status["grams_to_threshold"] or 0.0
            need = f" (needs +{deficit:.1f} g to reach 90%)" if deficit else ""
            line = f"% baseline: {pct:.1f}%{flag}{need}"
            fg = "#9C0006" if status["below_threshold"] else "#1a7f37"
        ttk.Label(row, text=line, foreground=fg).pack(side="left")

        # Water intake by source (totals only, no target flag).
        drow = self.daily.for_date(aid, date)
        day_reg = dashboard.registry_rows_for(self.registry_rows, aid, date)
        wb = dashboard.water_breakdown(dashboard.collect_water(drow, day_reg))
        if wb["sources"]:
            parts = " + ".join(f"{n} {ml:g}" for n, ml in wb["sources"])
            water = f"   water: {parts} = {wb['total_ml']:g} mL"
        else:
            water = "   water: none recorded"
        ttk.Label(row, text=water, foreground="#555").pack(side="left")

    def _enabled_dash_rats(self) -> List[str]:
        return [aid for aid in self.animal_ids
                if self.dash_rat_show.get(aid) and self.dash_rat_show[aid].get()]

    def _trend_chart_combined(self, parent, date: str) -> None:
        """One shared chart overlaying each enabled rat's 14-day % baseline line
        (per-rat colour, markers, red where <90%, endpoint label) over a single
        dashed 90% line. The optional water-mL overlay draws only when exactly one
        rat is enabled (see docs/decisions/0087). Falls back to text if matplotlib
        is unavailable."""
        enabled = self._enabled_dash_rats()
        series = {}
        for aid in enabled:
            trend = dashboard.weight_trend(self.daily, aid, date, days=14)
            pts = [(t["date"], t["pct_baseline"] * 100.0) for t in trend
                   if t.get("pct_baseline") is not None]
            if pts:
                series[aid] = pts
        if not series:
            msg = ("no rats selected" if not enabled
                   else "not enough data to plot")
            ttk.Label(parent, text=f"14-day % baseline:  ({msg})",
                      foreground="#555").pack(side="top", anchor="w", pady=(4, 0))
            return
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
        except ImportError:
            self._trend_text_fallback(parent, series)
            return

        thr = dashboard.BELOW_THRESHOLD * 100.0
        # Shared x-axis: the union of dates across the enabled rats, oldest first.
        all_dates = sorted({d for pts in series.values() for d, _ in pts})
        pos = {d: i for i, d in enumerate(all_dates)}
        n = len(all_dates)

        fig = Figure(figsize=(6.4, 3.0), dpi=100)
        fig.subplots_adjust(left=0.10, right=0.88, top=0.93, bottom=0.14)
        ax = fig.add_subplot(111)

        # Water overlay only makes sense for one rat (twin-axis bars for several
        # would be unreadable), so require an exact solo selection.
        if self.dash_show_water.get() and len(enabled) == 1:
            aid = enabled[0]
            wt = dashboard.water_ml_trend(self.daily, self.registry_rows, aid,
                                          date, days=14)
            wmap = {r["date"]: r["total_ml"] for r in wt}
            bars = [wmap.get(d, 0.0) for d in all_dates]
            if any(bars):
                axw = ax.twinx()
                axw.bar(range(n), bars, color="#7fb3d5", alpha=0.30, width=0.6,
                        zorder=0)
                axw.set_ylabel("water mL", fontsize=8, color="#4a789c")
                axw.tick_params(axis="y", labelsize=7, colors="#4a789c")
                axw.set_ylim(0, max(bars) * 1.25 or 1.0)

        ax.axhline(thr, color="#9C0006", lw=1.0, ls="--", alpha=0.7, zorder=1)
        all_y = [y for pts in series.values() for _, y in pts]
        for aid, pts in series.items():
            colour = self._rat_color(aid)
            xs = [pos[d] for d, _ in pts]
            ys = [y for _, y in pts]
            ax.plot(xs, ys, color=colour, lw=1.6, marker="o", ms=3.5,
                    label=f"Rat {aid}", zorder=2)
            below = [(x, y) for x, y in zip(xs, ys) if y < thr]
            if below:
                ax.scatter([x for x, _ in below], [y for _, y in below],
                           s=22, color="#9C0006", zorder=4)
            ax.annotate(f"{ys[-1]:.0f}%", (xs[-1], ys[-1]),
                        textcoords="offset points", xytext=(4, 2), fontsize=7,
                        color=colour)

        lo = min(min(all_y), thr) - 2
        hi = max(max(all_y), 100.0) + 2
        ax.set_ylim(lo, hi)
        ax.set_ylabel("% baseline", fontsize=8)
        ax.tick_params(axis="y", labelsize=7)
        ticks = sorted({0, n // 2, n - 1}) if n > 1 else [0]
        ax.set_xticks(ticks)
        ax.set_xticklabels([all_dates[i][5:] for i in ticks], fontsize=7)
        ax.set_xlim(-0.5, max(n - 0.5, 0.5))
        ax.grid(True, axis="y", alpha=0.15)
        if len(series) > 1:
            ax.legend(fontsize=7, loc="lower left", framealpha=0.6)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True,
                                    pady=(4, 0))
        # Keep a reference so the canvas isn't garbage-collected before redraw.
        self._dash_canvases.append(canvas)

    def _trend_text_fallback(self, parent, series: Dict[str, list]) -> None:
        """Text summary used only when matplotlib can't be imported."""
        ttk.Label(parent, text="14-day % baseline (matplotlib unavailable):",
                  foreground="#555").pack(side="top", anchor="w", pady=(4, 0))
        for aid, pts in series.items():
            first_d, first_y = pts[0]
            last_d, last_y = pts[-1]
            ttk.Label(parent, foreground=self._rat_color(aid),
                      text=f"Rat {aid}:  {first_d} {first_y:.0f}%  ->  "
                           f"{last_d} {last_y:.0f}%").pack(side="top", anchor="w")

    # ---------------------------------------------------------------- refresh
    def _refresh_all(self) -> None:
        self._refresh_daily_table()
        self._clear_daily()
        self._refresh_sessions()
        self._refresh_roster()
        self._refresh_dashboard()

    # ------------------------------------------------------- recordings root
    def _load_recordings_root(self) -> str:
        """The recordings folder to scan: the persisted pref if present, else the
        shared default (env-overridable) from tmaze.paths (ADR-0088)."""
        try:
            with open(_PREFS_PATH, "r", encoding="utf-8") as fh:
                pref = json.load(fh).get("recordings_root", "")
        except (OSError, ValueError):
            pref = ""
        return pref.strip() or str(phase3_recordings_root())

    def _save_recordings_root(self, path: str) -> None:
        """Persist the chosen recordings folder (best-effort)."""
        try:
            with open(_PREFS_PATH, "w", encoding="utf-8") as fh:
                json.dump({"recordings_root": path}, fh, indent=2)
        except OSError:
            pass

    def _on_pick_recordings(self) -> None:
        start = self.recordings_root if os.path.isdir(self.recordings_root) else None
        chosen = filedialog.askdirectory(
            title="Select the Phase 3 recordings folder to scan",
            initialdir=start)
        if not chosen:
            return
        self.recordings_root = os.path.normpath(chosen)
        self.rec_root_var.set(self.recordings_root)
        self._save_recordings_root(self.recordings_root)
        self._set_status(f"Recordings folder set to {self.recordings_root}. "
                         "Click Generate files to re-scan.")

    # ---------------------------------------------------------------- actions
    def _on_reload(self) -> None:
        if self._has_unsaved() and not self._confirm_discard(
                "reloading will replace the form/editor contents"):
            return
        self._load_data()
        self.d_animal["values"] = self.animal_ids
        self._sync_filter_values()
        self._refresh_all()
        self._set_status("Reloaded from disk.")

    def _on_generate(self) -> None:
        if self._has_unsaved() and not self._confirm_discard(
                "Generate refreshes every view from disk"):
            return
        self._run_async("Generating files...", self._do_generate)

    def _on_import(self) -> None:
        if self._has_unsaved() and not self._confirm_discard(
                "Import refreshes every view from disk"):
            return
        self._run_async("Importing from Excel...", self._do_import)

    def _on_close(self) -> None:
        """Window-close guard: warn on a running build or unsaved edits (#3)."""
        if self._busy:
            if not messagebox.askyesno(
                    "Quit?", "A build is still running. Quit anyway?",
                    default="no"):
                return
        elif self._has_unsaved() and not messagebox.askyesno(
                "Quit?", "You have unsaved changes. Quit anyway?",
                default="no"):
            return
        self.root.destroy()

    def _do_generate(self) -> str:
        log = [self._build_registry_inprocess(),
               self._run_script("build_daily_log.py", ["--rebuild"]),
               self._run_script("build_session_training_log.py", [])]
        return "\n".join(log)

    def _do_import(self) -> str:
        log = [self._run_script("build_daily_log.py", ["--export"]),
               self._run_script("build_session_training_log.py",
                                ["--import-annotations"])]
        return "\n".join(log)

    def _build_registry_inprocess(self) -> str:
        """Rebuild session_registry.csv by calling the tool's main() directly,
        scanning the operator-chosen recordings root (the CSV still lands in the
        tool's fixed output dir; see docs/decisions/0088)."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                build_session_registry.main(["--root", self.recordings_root])
            return "[session registry] " + (buf.getvalue().strip() or "rebuilt")
        except SystemExit as e:  # the tool raises SystemExit on a missing root
            return f"[session registry] {e}"
        except Exception as e:  # noqa: BLE001 - surface any builder error in the log
            return f"[session registry] FAILED: {e}"

    def _run_script(self, name: str, args: List[str]) -> str:
        """Run a resources-repo builder as a subprocess; return a one-line result."""
        script = os.path.join(self.records_dir, name)
        if not os.path.exists(script):
            return f"[{name}] not found ({script})"
        try:
            proc = subprocess.run([sys.executable, script, *args],
                                  cwd=self.records_dir, capture_output=True,
                                  text=True, timeout=_BUILDER_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as e:
            return f"[{name}] FAILED: {e}"
        out = (proc.stdout or "").strip().replace("\n", " | ")
        err = (proc.stderr or "").strip().replace("\n", " | ")
        tag = "OK" if proc.returncode == 0 else f"rc={proc.returncode}"
        # A builder needs openpyxl; if this env lacks it, point at the fix rather
        # than leaving a raw ModuleNotFoundError traceback (see docs/decisions/0085).
        if proc.returncode != 0 and "No module named 'openpyxl'" in err:
            err += "  (install it in this env: pip install openpyxl)"
        return f"[{name} {tag}] {out}{('  ERR: ' + err) if err else ''}"

    def _run_async(self, busy_msg: str, work) -> None:
        """Run ``work`` (returns a log string) off the UI thread, then report."""
        if self._busy:
            return
        self._busy = True
        self._set_status(busy_msg)

        def worker():
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                result = f"FAILED: {e}"
            self.root.after(0, lambda: self._async_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _async_done(self, result: str) -> None:
        self._busy = False
        self._load_data()
        self.d_animal["values"] = self.animal_ids
        self._refresh_all()
        self._set_status("Done.")
        messagebox.showinfo("Result", result)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)


def main() -> int:
    root = tk.Tk()
    RecordsManager(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
