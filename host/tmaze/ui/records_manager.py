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
- **Operator + change log** (ADR-0155) - the toolbar's Operator dropdown (from
  the tracked ``host/operators.json``) must be set before any save/import, and
  every write appends who-changed-which-cell lines to the append-only
  ``records/animal/records_changes.csv`` beside the records files.

This module is the SHELL: all data logic is in the pure cores
(``tmaze.core.records.daily_log / session_annotations / animals / dashboard``);
here we only render Tk widgets, run the builders (subprocess / in-process), and
degrade gracefully when the sibling resources repo isn't present.

Run (conda env drieu_t_maze):
    python -m tmaze.ui.records_manager
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional

from tmaze.core.records import (
    RecordsFileError,
    animals,
    dashboard,
    daily_log,
    file_mtime,
    operators,
    read_commented_csv,
    records_audit,
    records_audit_ack,
    records_changelog,
    records_git,
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

# A small starter vocabulary of common per-session procedures for the Sessions
# annotation checklist (Phase B). Ticking one adds it as a line in the free-text
# "Common procedures" box; the operator can still type anything else. This is a
# stub the lab can refine (e.g. to match the ACUC protocol's procedure names).
_COMMON_PROCEDURES = (
    "Water restriction",
    "Weighed",
    "Handling / habituation",
    "Behavior training",
    "Recording",
    "Surgery / implant",
    "Health check",
    "Supplemental water",
)

# Small GUI-pref file (a machine path, so git-ignored): remembers the recordings
# folder the operator picked so it survives restarts. Precedence for the scan
# root is this pref, else tmaze.paths.phase3_recordings_root() (which itself
# honours the TMAZE_RECORDINGS_DIR env var, else the machine default; ADR-0088).
_PREFS_PATH = os.path.join(str(host_dir()), "records_manager.json")

# Stable per-rat colours for the combined dashboard trend (matplotlib tab10);
# assigned by the rat's position in the roster so a given rat keeps its colour.
_RAT_PALETTE = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
                "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f")

# Dashboard trend window bounds: a sane default span and a cap so a mistyped
# (or huge) From/To range can't try to draw thousands of days at once.
_DASH_DEFAULT_SPAN_DAYS = 14
_DASH_MAX_SPAN_DAYS = 180

# The Daily-tab table columns: (source id, heading, width, anchor). Most ids map
# straight to a daily_log field; "day" and "wt_delta" are DERIVED (weekday label
# and day-over-day weight change) - display-only, computed from the pure core.
_DAILY_TREE_COLUMNS = (
    ("animal_id", "Animal", 68, "center"),
    ("date", "Date", 90, "center"),
    ("day", "Day", 46, "center"),
    ("weight_g", "Weight", 64, "center"),
    ("wt_delta", "\u0394 g", 54, "center"),      # day-over-day change
    ("baseline_g", "Baseline", 66, "center"),
    ("pct_baseline", "% base", 62, "center"),
    ("water_on", "Water on", 76, "center"),
    ("water_off", "Water off", 76, "center"),
    ("water_ml", "Water mL", 70, "center"),
    ("supp_water", "Supp", 60, "center"),
    ("water_regime", "Regime", 78, "center"),
    ("water_method", "Method", 100, "center"),
    ("notes", "Notes", 200, "w"),
)

# How many days back (ending today) the Daily table flags as missing-entry gaps
# for a single selected rat. Bounded so old history doesn't fill with placeholders.
_DAILY_MISSING_LOOKBACK_DAYS = 14

# Sentinel in the "Mark span" dialog's regime/method dropdowns meaning "don't
# touch this field over the span" (so a bulk regime mark doesn't clear methods).
_SPAN_LEAVE = "(leave unchanged)"


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
    """Rows of a `# schema_version`-prefixed CSV as dicts; [] if absent/unreadable.

    Uses the shared records reader so only the LEADING ``#`` lines count as
    comments (a ``#`` inside a quoted cell is data; ADR-0155)."""
    try:
        return read_commented_csv(path).rows
    except (OSError, ValueError):
        return []


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
        # The append-only per-field change log every save writes to, and the
        # operator list feeding the toolbar dropdown (ADR-0155).
        self.changelog_path = records_changelog.default_changelog_path()
        self.operators = operators.load_operators()
        self.records_dir = os.path.dirname(self.daily_path)
        # The generated daily workbook (hand-editable Entry fallback). Its mtime
        # vs the last sync we performed drives the Import-before-Generate warning
        # + the staleness banner (Phase B).
        self.daily_xlsx_path = os.path.join(self.records_dir, "daily_log.xlsx")
        # Where the rig's session bundles are SCANNED from (operator-pickable,
        # persisted). The registry CSV is READ from a fixed spot regardless of the
        # scan root (host/data/phase3_recordings; see docs/decisions/0088).
        self.recordings_root = self._load_recordings_root()
        # Extra read-only ARCHIVE roots (older sessions moved off the rig disk;
        # docs/decisions/0113). Scanned in addition to the primary root; the rig
        # still writes only to the primary. Persisted in records_manager.json.
        self.recordings_archives = self._load_recordings_archives()
        self.registry_path = os.path.join(
            build_session_registry.REGISTRY_OUTPUT_DIR,
            build_session_registry.REGISTRY_FILENAME)

        self._busy = False
        # Sessions tab selection state (session_id of the selected row + the
        # per-Treeview-item registry rows behind it).
        self.current_session_id = ""
        self._session_row_by_item: Dict[str, Dict[str, str]] = {}
        # Daily table: maps each Treeview item to its source row (real rows) or
        # to its missing-entry placeholder, so a double-click loads the row or
        # opens a new entry for the gap.
        self._daily_row_by_iid: Dict[str, Dict[str, str]] = {}
        self._daily_missing_by_iid: Dict[str, Dict[str, str]] = {}
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
        """(Re)read every source of truth from disk into memory.

        Also remembers each file's modification time as loaded. Every save
        passes it to the core writer, which refuses to write if the file has
        changed on disk since (another surface saved in between); the GUI then
        offers to reload and re-apply the edit (ADR-0155)."""
        self._mtimes: Dict[str, Optional[int]] = {
            "daily": file_mtime(self.daily_path),
            "annot": file_mtime(self.annot_path),
            "animals": file_mtime(self.animals_path),
        }
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
        ttk.Button(bar, text="Integrity check",
                   command=self._integrity_check).pack(side="left", padx=(6, 0))
        # Operator: WHO is editing. Required before any save/import; every
        # change is logged under this name (ADR-0155). Fed by the tracked
        # host/operators.json; the last choice is remembered per machine.
        ttk.Label(bar, text="Operator:").pack(side="left", padx=(14, 2))
        remembered = str(self._load_prefs().get("operator", "")).strip()
        self.operator_var = tk.StringVar(
            value=remembered if remembered in self.operators else "")
        self.operator_box = ttk.Combobox(
            bar, textvariable=self.operator_var, values=self.operators,
            state="readonly", width=8)
        self.operator_box.pack(side="left")
        self.operator_box.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._save_prefs(operator=self.operator_var.get()))
        if not self.operators:
            ttk.Label(bar, text="(none in host/operators.json)",
                      foreground="#9C0006").pack(side="left", padx=(4, 0))
        # Commit records: put the four records files into the resources repo's
        # git history (only those files; never pushes; ADR-0155). The label
        # beside it says how many logged edits are not yet committed.
        ttk.Button(bar, text="Commit records",
                   command=self._on_commit_records).pack(side="left",
                                                          padx=(14, 0))
        self.git_var = tk.StringVar(value="")
        self.git_label = ttk.Label(bar, textvariable=self.git_var,
                                   foreground="#555")
        self.git_label.pack(side="left", padx=(6, 0))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status_var,
                  foreground="#555").pack(side="right")

        # Recordings folder: the root "Generate files" scans for session bundles.
        # Shown + pickable so the operator can point it at the rig's recordings
        # without editing code; the choice persists (see docs/decisions/0088).
        recbar = ttk.Frame(self.root, padding=(8, 0))
        recbar.pack(side="top", fill="x")
        ttk.Label(recbar, text="Recordings:").pack(side="left")
        self.rec_root_var = tk.StringVar(value=self._recordings_summary())
        ttk.Label(recbar, textvariable=self.rec_root_var,
                  foreground="#555").pack(side="left", padx=(4, 6))
        ttk.Button(recbar, text="Browse...",
                   command=self._on_pick_recordings).pack(side="left")
        # Manage the read-only archive roots (scanned in addition to the primary;
        # docs/decisions/0113).
        ttk.Button(recbar, text="Archives...",
                   command=self._on_manage_archives).pack(side="left", padx=(6, 0))

        # Staleness banner: how many sessions are indexed + when the registry was
        # last built, and whether the daily Excel workbook has edits not yet
        # imported to the CSV (Phase B). Updated on every refresh.
        self.banner_var = tk.StringVar(value="")
        self.banner = ttk.Label(self.root, textvariable=self.banner_var,
                                padding=(8, 2))
        self.banner.pack(side="top", fill="x")

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
        # A user edit to a DATA field marks the form dirty (#3); programmatic
        # fills wrap themselves in _suspend_dirty_tracking so they don't trip
        # this. The key/navigation fields (animal_id, date) and the derived
        # pct_baseline are deliberately NOT tracked: picking an animal or a date
        # is browsing (the Animal dropdown also filters the table below), not
        # editing, so it must not raise a spurious "unsaved changes" warning.
        for _f in daily_log.FIELDS:
            if _f in ("animal_id", "date", "pct_baseline"):
                continue
            self.d_vars[_f].trace_add("write", self._mark_daily_dirty)

        # Animal (dropdown) + Date.
        ttk.Label(form, text="Animal").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        # Read-only: the roster (animals.json) is the source of truth for ids,
        # so a typo can't mint an off-roster husbandry row here - add the animal
        # on the Roster tab first (ADR-0075/0155).
        self.d_animal = ttk.Combobox(form, textvariable=self.d_vars["animal_id"],
                                     values=self.animal_ids, width=10,
                                     state="readonly")
        self.d_animal.grid(row=0, column=1, sticky="w")
        self.d_animal.bind("<<ComboboxSelected>>", self._on_form_animal)
        # The animal the form's baseline was last prefilled for, so re-selecting
        # the same animal doesn't clobber a baseline the operator just typed
        # while switching to a DIFFERENT animal does re-prefill it.
        self._last_form_animal = ""

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

        # Water regime (ADR-0100): restriction default vs ad-lib (off-restriction,
        # e.g. weekends). Water method (ADR-0104) is orthogonal: HOW the day's
        # (restricted) water was delivered. The span button bulk-marks a range.
        ttk.Label(form, text="Water regime").grid(row=4, column=0, sticky="e",
                                                  padx=4, pady=3)
        self.d_regime = ttk.Combobox(
            form, textvariable=self.d_vars["water_regime"],
            values=["restriction", "ad_lib"], state="readonly", width=12)
        self.d_regime.grid(row=4, column=1, sticky="w")
        ttk.Label(form, text="Water method").grid(row=4, column=2, sticky="e",
                                                  padx=4)
        self.d_method = ttk.Combobox(
            form, textvariable=self.d_vars["water_method"],
            values=[""] + list(daily_log.WATER_METHODS), state="readonly",
            width=14)
        self.d_method.grid(row=4, column=3, sticky="w")
        ttk.Button(form, text="Mark span...",
                   command=self._mark_span).grid(
            row=4, column=4, columnspan=2, sticky="w", padx=4)

        btns = ttk.Frame(form)
        btns.grid(row=5, column=0, columnspan=6, sticky="w", pady=(8, 0))
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

        # A one-line legend for the row colours, so the tints are self-explaining.
        legend = ttk.Frame(tab)
        legend.pack(side="top", fill="x", pady=(4, 0))
        ttk.Label(legend, text="Rows:", foreground="#555").pack(side="left")
        for text, bg, fg in (("weekend", "#f2f2f2", "#000"),
                             ("ad-lib", "#fde7d0", "#000"),
                             ("<90%", "#ffffff", "#9C0006"),
                             ("missing entry", "#fde0e0", "#9a6700")):
            tk.Label(legend, text=f" {text} ", background=bg, foreground=fg).pack(
                side="left", padx=(6, 0))

        table = ttk.Frame(tab)
        table.pack(side="top", fill="both", expand=True, pady=(4, 0))
        cols = [c[0] for c in _DAILY_TREE_COLUMNS]
        self.d_tree = ttk.Treeview(table, columns=cols, show="headings",
                                   height=12)
        for cid, label, width, anchor in _DAILY_TREE_COLUMNS:
            self.d_tree.heading(cid, text=label)
            self.d_tree.column(cid, width=width, anchor=anchor)
        # Row tints (ADR-0103): weekend/ad-lib set the background (ad-lib wins if
        # both), <90% sets a red foreground (independent of background), and a
        # missing-entry placeholder row gets its own pink row.
        self.d_tree.tag_configure("weekend", background="#f2f2f2")
        self.d_tree.tag_configure("adlib", background="#fde7d0")
        self.d_tree.tag_configure("below", foreground="#9C0006")
        self.d_tree.tag_configure("missing", background="#fde0e0",
                                  foreground="#9a6700")
        vsb = ttk.Scrollbar(table, orient="vertical", command=self.d_tree.yview)
        self.d_tree.configure(yscrollcommand=vsb.set)
        self.d_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.d_tree.bind("<Double-1>", self._load_selected_daily)

    def _on_form_animal(self, _event=None) -> None:
        """Picking a DIFFERENT animal starts a fresh entry for it: the per-day
        data fields (weight, water, notes) are cleared - before this, the
        previous rat's weight silently carried over and could be saved under
        the new rat - and its baseline / water method are prefilled (ADR-0155).
        If the form holds unsaved edits the switch asks first and, if declined,
        snaps the dropdown back. The table narrows to the picked rat.
        Re-selecting the SAME animal changes nothing."""
        aid = self.d_vars["animal_id"].get().strip()
        if aid != self._last_form_animal:
            if self._daily_dirty and not messagebox.askyesno(
                    "Discard unsaved changes?",
                    "You have unsaved changes:\n\n" + self._unsaved_summary()
                    + f"\n\nSwitch to rat {aid} and discard them?",
                    default="no"):
                with self._suspend_dirty_tracking():
                    self.d_vars["animal_id"].set(self._last_form_animal)
                return
            self._start_new_daily(aid, self.d_vars["date"].get().strip())
        if aid:
            self.d_filter.set(aid)
            self._refresh_daily_table()

    # Per-day data fields: everything except the key (animal, date), the
    # prefilled baseline, and the derived pct.
    _DAY_FIELDS = ("weight_g", "water_on", "water_off", "water_ml",
                   "supp_water", "notes")

    def _start_new_daily(self, aid: str, date: str) -> None:
        """Put the form into "new entry for (aid, date)" state: day fields blank,
        baseline + water method prefilled from the animal's history, regime at
        the restriction default (a weekend's ad-lib must not leak into Monday).
        Not a user edit, so the form is clean afterwards."""
        with self._suspend_dirty_tracking():
            for f in self._DAY_FIELDS:
                self.d_vars[f].set("")
            self.d_vars["animal_id"].set(aid)
            self.d_vars["date"].set(date or _today_iso())
            self.d_vars["water_regime"].set("restriction")
            latest_m = self.daily.latest_for(aid, date or None,
                                             having="water_method") if aid else None
            self.d_vars["water_method"].set(
                daily_log.normalize_method((latest_m or {}).get("water_method")))
            self._update_pct()
        self._last_form_animal = aid
        self._prefill_baseline(force=True)
        self._daily_dirty = False

    def _prefill_baseline(self, force: bool = False) -> None:
        """Prefill baseline from the animal's most recent row (on/before the
        form's date). Without ``force`` an already-filled baseline is left alone;
        with it (an animal change) the field is REPLACED with the selected
        animal's latest baseline, or cleared when that animal has no known
        baseline - so a stale value from the previous animal never lingers."""
        aid = self.d_vars["animal_id"].get().strip()
        if not aid:
            return
        if not force and self.d_vars["baseline_g"].get().strip():
            return
        # The latest row that actually HAS a baseline (a newer row with the cell
        # left blank must not wipe the prefill; ADR-0155).
        latest = self.daily.latest_for(
            aid, self.d_vars["date"].get().strip() or None,
            having="baseline_g")
        baseline = str((latest or {}).get("baseline_g", "")) if latest else ""
        if force or baseline:
            # Auto-prefilling the baseline on an animal change is not a user
            # edit, so don't let it mark the form dirty (#3).
            with self._suspend_dirty_tracking():
                self.d_vars["baseline_g"].set(baseline)

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

    def _unsaved_summary(self) -> str:
        """A human list of what's actually unsaved, so a discard/quit prompt can
        name it instead of a vague "you have unsaved changes" (#3)."""
        parts: List[str] = []
        if self._daily_dirty:
            aid = self.d_vars["animal_id"].get().strip() or "?"
            date = self.d_vars["date"].get().strip() or "?"
            parts.append(f"- Daily entry form (rat {aid}, {date})")
        if self._annotation_dirty():
            sid = self.current_session_id or "?"
            parts.append(f"- Annotation for session {sid}")
        return "\n".join(parts)

    def _confirm_discard(self, what: str) -> bool:
        """Ask before an action that would throw away unsaved edits."""
        return messagebox.askyesno(
            "Discard unsaved changes?",
            f"You have unsaved changes: {what}\n\nContinue and discard them?",
            default="no")

    # ---- operator + change log (ADR-0155) --------------------------------
    def _require_operator(self) -> Optional[str]:
        """The chosen operator, or None after telling the user why nothing was
        saved. Every write path calls this first so no edit is ever logged
        anonymously."""
        op = self.operator_var.get().strip()
        if op and op in self.operators:
            return op
        if not self.operators:
            messagebox.showwarning(
                "No operators configured",
                "host/operators.json lists nobody, so edits can't be "
                "attributed. Add your initials to its \"operators\" list "
                "(and commit it), then restart the Records Manager.")
        else:
            messagebox.showwarning(
                "Choose an operator",
                "Pick your name in the Operator box (toolbar) before saving, "
                "so the change log records who made this edit.")
        return None

    def _log_changes(self, changes: List[records_changelog.Change],
                     operator: str, source: str, file_name: str) -> int:
        """Append ``changes`` to records_changes.csv; a failure here must not
        undo the save that already happened, so it is reported, not raised."""
        try:
            return records_changelog.append(changes, operator, source,
                                            file_name, path=self.changelog_path)
        except OSError as e:
            messagebox.showwarning(
                "Change log not written",
                f"The edit was saved, but could not be logged to "
                f"{self.changelog_path}:\n{e}")
            return 0

    @staticmethod
    def _roster_as_rows(records: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Flatten roster records to string rows for the change-log diff."""
        return [{"id": str(r.get("id", "")),
                 "aliases": "; ".join(str(a) for a in (r.get("aliases") or []))}
                for r in records]

    # ---- guarded writes (ADR-0155) ---------------------------------------
    def _guarded_write(self, which: str, path: str, write_fn) -> bool:
        """Run ``write_fn(expect_mtime)`` - a read-merge-write against ``path`` -
        with the modification time captured at load. If the core refuses
        because the file changed on disk meanwhile, offer to reload and re-apply
        the same edit on top of the current contents (``write_fn`` re-reads the
        file, so the edit lands on fresh rows). Returns True if written; every
        refusal/failure is reported to the operator here."""
        try:
            write_fn(self._mtimes.get(which))
            return True
        except RecordsFileError as e:
            if file_mtime(path) == self._mtimes.get(which):
                messagebox.showerror("Save refused", str(e))  # not staleness
                return False
            if not messagebox.askyesno(
                    "File changed on disk",
                    f"{e}\n\nReload the current contents now and re-apply "
                    "this edit on top of them? (Your edit wins for the rows it "
                    "touches; everything else comes from the file.)",
                    default="yes"):
                return False
            self._load_data()
            try:
                write_fn(self._mtimes.get(which))
                return True
            except RecordsFileError as e2:
                messagebox.showerror("Save refused", str(e2))
                return False
            except (OSError, ValueError) as e2:
                messagebox.showerror("Save failed", str(e2))
                return False
        except (OSError, ValueError) as e:
            messagebox.showerror("Save failed", str(e))
            return False

    def _save_daily(self) -> None:
        operator = self._require_operator()
        if operator is None:
            return
        edit = {f: self.d_vars[f].get().strip() for f in daily_log.FIELDS}
        # Hard validation (blocks the save): required key + ISO date + numeric
        # weight/baseline/water mL. Shared with the Excel --export path so both
        # surfaces reject the same garbage (Phase B; #14).
        errors = daily_log.validate_entry(edit)
        if errors:
            messagebox.showwarning(
                "Can't save this row",
                "Please fix:\n  - " + "\n  - ".join(errors))
            return
        aid, date = edit["animal_id"], edit["date"]
        # Soft, warn-but-allow identity check (keeps ADR-0075's policy): an id not
        # on the roster is allowed but confirmed, so a typo doesn't silently mint
        # an off-roster husbandry row. Only when the roster actually loaded.
        reg = animals.AnimalRegistry(self.roster)
        if reg.available and not reg.is_known(aid) and not messagebox.askyesno(
                "Unknown animal",
                f"'{aid}' isn't in the roster (animals.json).\n"
                "Save this row anyway?", default="no"):
            return
        pct = daily_log.compute_pct_baseline(edit["weight_g"], edit["baseline_g"])
        edit["pct_baseline"] = "" if pct is None else str(pct)
        # Store the canonical regime ("ad_lib" or "" for restriction) so the
        # column stays blank except on ad-lib days (ADR-0100), and the canonical
        # water method ("" when unspecified/unknown; ADR-0104).
        edit["water_regime"] = daily_log.normalize_regime(edit["water_regime"])
        edit["water_method"] = daily_log.normalize_method(edit["water_method"])

        changes: List[records_changelog.Change] = []

        def _write(expect_mtime) -> None:
            # Read-for-write so a present-but-unreadable/mis-headed CSV aborts
            # the save rather than being overwritten with just this row (#12).
            existing = daily_log.load_rows_for_write(self.daily_path)
            merged = daily_log.upsert_rows(existing, [edit])
            daily_log.write_rows(merged, self.daily_path,
                                 expect_mtime=expect_mtime)
            changes[:] = records_changelog.diff_rows(
                existing, merged, daily_log.KEY_FIELDS, daily_log.FIELDS)

        if not self._guarded_write("daily", self.daily_path, _write):
            return
        n = self._log_changes(changes, operator, "records_manager",
                              "daily_log.csv")
        self._set_status(f"Saved {aid} {date} to daily_log.csv "
                         f"({n} change{'s' if n != 1 else ''} logged).")
        self._daily_dirty = False
        self._load_data()
        self._refresh_daily_table()
        self._refresh_dashboard()
        self._refresh_git_label()

    def _clear_daily(self) -> None:
        with self._suspend_dirty_tracking():
            for f in daily_log.FIELDS:
                self.d_vars[f].set("")
            self.d_vars["date"].set(_today_iso())
            # Show the default regime rather than a blank dropdown.
            self.d_vars["water_regime"].set("restriction")
            self._update_pct()
        self._last_form_animal = ""
        self._daily_dirty = False

    def _load_selected_daily(self, _event=None) -> None:
        sel = self.d_tree.selection()
        if not sel:
            return
        if self._daily_dirty and not messagebox.askyesno(
                "Discard unsaved changes?",
                "You have unsaved changes:\n\n" + self._unsaved_summary()
                + "\n\nLoad the selected row and discard them?", default="no"):
            return
        row = self._daily_row_by_iid.get(sel[0])
        if row is None:
            # A missing-entry placeholder: open a NEW entry for that rat + day
            # (the gap is exactly what the operator wants to fill).
            gap = self._daily_missing_by_iid.get(sel[0])
            if gap is not None:
                self._start_new_daily(gap["animal_id"], gap["date"])
            return
        # Loading a row into the form is not itself an edit; it becomes dirty
        # only if the operator then changes a field (#3).
        with self._suspend_dirty_tracking():
            for f in daily_log.FIELDS:
                self.d_vars[f].set(str(row.get(f, "")))
            # A blank regime cell means the restriction default; show it as such.
            if not self.d_vars["water_regime"].get().strip():
                self.d_vars["water_regime"].set("restriction")
            self._update_pct()
        # Loading sets the animal directly (no combobox event); track it so a
        # later re-pick of the same animal won't overwrite the loaded baseline.
        self._last_form_animal = str(row.get("animal_id", "")).strip()
        self._daily_dirty = False

    def _mark_span(self) -> None:
        """Bulk-mark a date span for one animal: set the water REGIME and/or the
        water METHOD across every day in the range at once, writing one row per
        day (ADR-0100/0104). Each of regime/method can be left unchanged, so the
        same dialog handles "mark this weekend ad-lib" (regime=ad_lib, method
        left) and "this training block was task-earned" (regime left,
        method=task_earned). Optional on/off times land on the boundary days
        (water_on on the first, water_off on the last) - the within-day ad-lib
        window. Existing rows are upserted (weight etc. preserved)."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Mark span")
        dlg.transient(self.root)
        dlg.grab_set()
        default_animal = (self.d_vars["animal_id"].get().strip()
                          or (self.animal_ids[0] if self.animal_ids else ""))
        v_animal = tk.StringVar(value=default_animal)
        v_start = tk.StringVar(value=_today_iso())
        v_start_t = tk.StringVar()
        v_end = tk.StringVar(value=_today_iso())
        v_end_t = tk.StringVar()
        # Regime defaults to ad_lib (the common "mark a weekend" case); method
        # defaults to leave-unchanged so it isn't clobbered unless chosen.
        v_regime = tk.StringVar(value=daily_log.REGIME_AD_LIB)
        v_method = tk.StringVar(value=_SPAN_LEAVE)

        r = 0
        ttk.Label(dlg, text="Animal").grid(row=r, column=0, sticky="e",
                                           padx=6, pady=3)
        ttk.Combobox(dlg, textvariable=v_animal, values=self.animal_ids,
                     width=16).grid(row=r, column=1, sticky="w", padx=6)
        for label, var in (("Start date (YYYY-MM-DD)", v_start),
                           ("  on-time (optional, e.g. 5pm)", v_start_t),
                           ("End date (YYYY-MM-DD)", v_end),
                           ("  off-time (optional, e.g. 2pm)", v_end_t)):
            r += 1
            ttk.Label(dlg, text=label).grid(row=r, column=0, sticky="e",
                                            padx=6, pady=3)
            ttk.Entry(dlg, textvariable=var, width=18).grid(
                row=r, column=1, sticky="w", padx=6)
        r += 1
        ttk.Label(dlg, text="Set regime").grid(row=r, column=0, sticky="e",
                                               padx=6, pady=3)
        ttk.Combobox(dlg, textvariable=v_regime, state="readonly", width=16,
                     values=[_SPAN_LEAVE, "restriction", "ad_lib"]).grid(
            row=r, column=1, sticky="w", padx=6)
        r += 1
        ttk.Label(dlg, text="Set method").grid(row=r, column=0, sticky="e",
                                               padx=6, pady=3)
        ttk.Combobox(dlg, textvariable=v_method, state="readonly", width=16,
                     values=[_SPAN_LEAVE] + list(daily_log.WATER_METHODS)).grid(
            row=r, column=1, sticky="w", padx=6)
        result: Dict[str, str] = {}

        def _ok():
            result.update(animal=v_animal.get().strip(),
                          start=v_start.get().strip(),
                          start_t=v_start_t.get().strip(),
                          end=v_end.get().strip(), end_t=v_end_t.get().strip(),
                          regime=v_regime.get(), method=v_method.get())
            dlg.destroy()

        btns = ttk.Frame(dlg)
        btns.grid(row=r + 1, column=0, columnspan=2, pady=(8, 6))
        ttk.Button(btns, text="Apply", command=_ok).pack(side="left")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side="left", padx=(6, 0))
        dlg.wait_window()
        if not result:
            return
        self._apply_span(result)

    def _apply_span(self, spec: Dict[str, str]) -> None:
        operator = self._require_operator()
        if operator is None:
            return
        aid = spec["animal"]
        start, end = spec["start"], spec["end"]
        if not aid:
            messagebox.showwarning("Missing animal", "Pick an animal.")
            return
        if not _is_iso_date(start) or not _is_iso_date(end):
            messagebox.showwarning(
                "Bad date", "Start and End must both be YYYY-MM-DD.")
            return
        d0 = dt.date.fromisoformat(start)
        d1 = dt.date.fromisoformat(end)
        if d1 < d0:
            messagebox.showwarning("Bad range", "End date is before Start date.")
            return
        if (d1 - d0).days > 31:
            messagebox.showwarning("Range too long",
                                   "Span is over 31 days - mark a shorter range.")
            return
        set_regime = spec.get("regime", _SPAN_LEAVE) != _SPAN_LEAVE
        set_method = spec.get("method", _SPAN_LEAVE) != _SPAN_LEAVE
        if not set_regime and not set_method:
            messagebox.showwarning(
                "Nothing to set",
                "Choose a regime and/or a method to apply over the span.")
            return
        regime_val = daily_log.normalize_regime(spec.get("regime", ""))
        method_val = daily_log.normalize_method(spec.get("method", ""))
        n = (d1 - d0).days + 1

        def _write(expect_mtime) -> None:
            # Build the per-day edits from the rows AS READ NOW, not from the
            # in-memory table: a span only touches regime/method/boundary times,
            # so every other cell (weight, notes...) must come from the file's
            # current row or a concurrent edit would be reverted (ADR-0155).
            existing = daily_log.load_rows_for_write(self.daily_path)
            fresh = daily_log.DailyLog(existing)
            edits: List[Dict[str, str]] = []
            for i in range(n):
                day = (d0 + dt.timedelta(days=i)).isoformat()
                row = dict(fresh.for_date(aid, day) or
                           {"animal_id": aid, "date": day})
                row["animal_id"], row["date"] = aid, day
                if set_regime:
                    row["water_regime"] = regime_val
                if set_method:
                    row["water_method"] = method_val
                if i == 0 and spec.get("start_t"):
                    row["water_on"] = spec["start_t"]
                if i == n - 1 and spec.get("end_t"):
                    row["water_off"] = spec["end_t"]
                edits.append(row)
            merged = daily_log.upsert_rows(existing, edits)
            daily_log.write_rows(merged, self.daily_path,
                                 expect_mtime=expect_mtime)
            changes[:] = records_changelog.diff_rows(
                existing, merged, daily_log.KEY_FIELDS, daily_log.FIELDS)

        changes: List[records_changelog.Change] = []
        if not self._guarded_write("daily", self.daily_path, _write):
            return
        self._log_changes(changes, operator, "records_manager span",
                          "daily_log.csv")
        what = []
        if set_regime:
            what.append(f"regime={regime_val or 'restriction'}")
        if set_method:
            what.append(f"method={method_val or 'unspecified'}")
        self._set_status(f"Marked {n} day(s) for {aid} ({start} \u2192 {end}): "
                         + ", ".join(what) + ".")
        self._load_data()
        self._refresh_daily_table()
        self._refresh_dashboard()
        self._refresh_status_banner()

    def _sync_filter_values(self) -> None:
        """Keep the Show-rat filter's options in step with the roster; if the
        currently selected rat is gone, fall back to All."""
        self.d_filter_box["values"] = ["All"] + self.animal_ids
        if self.d_filter.get().strip() not in ("All", *self.animal_ids):
            self.d_filter.set("All")

    def _refresh_daily_table(self) -> None:
        self.d_tree.delete(*self.d_tree.get_children())
        # Map each Treeview item back to its source row so editing loads the real
        # record (the table now shows derived columns, so positional zip is out).
        self._daily_row_by_iid: Dict[str, Dict[str, str]] = {}
        self._daily_missing_by_iid: Dict[str, Dict[str, str]] = {}
        sel = self.d_filter.get().strip()
        rows = [dict(r) for r in self.daily_rows
                if sel in ("", "All") or r.get("animal_id", "") == sel]
        # Missing-entry gaps only read for a single selected rat (per-rat, over a
        # bounded recent window); interleaved as placeholder rows.
        if sel not in ("", "All"):
            have = {r.get("date", "") for r in rows}
            end = _today_iso()
            start = (dt.date.today()
                     - dt.timedelta(days=_DAILY_MISSING_LOOKBACK_DAYS - 1)
                     ).isoformat()
            for md in dashboard.missing_days(self.daily, sel, start, end):
                if md not in have:
                    rows.append({"animal_id": sel, "date": md, "_missing": True})
        rows = sorted(rows,
                      key=lambda r: (r.get("date", ""), r.get("animal_id", "")),
                      reverse=True)
        for r in rows:
            values = [self._daily_cell(r, cid) for cid, *_ in _DAILY_TREE_COLUMNS]
            iid = self.d_tree.insert("", "end", values=values,
                                     tags=self._daily_row_tags(r))
            if r.get("_missing"):
                self._daily_missing_by_iid[iid] = r
            else:
                self._daily_row_by_iid[iid] = r

    def _daily_cell(self, row: Dict[str, str], col_id: str) -> str:
        """The display string for one table cell - a raw daily_log field, or a
        derived value for the "day"/"wt_delta"/"pct_baseline" columns."""
        if row.get("_missing"):
            if col_id == "day":
                return dashboard.weekday_label(row.get("date", ""))
            if col_id == "notes":
                return "\u2014 no entry logged \u2014"
            if col_id in ("animal_id", "date"):
                return row.get(col_id, "")
            return ""
        if col_id == "day":
            return dashboard.weekday_label(row.get("date", ""))
        if col_id == "wt_delta":
            dv = dashboard.weight_delta(self.daily, row.get("animal_id", ""),
                                        row.get("date", ""))
            return "" if dv is None else f"{dv:+.1f}"
        if col_id == "pct_baseline":
            pct = daily_log.compute_pct_baseline(row.get("weight_g", ""),
                                                 row.get("baseline_g", ""))
            if pct is None:
                raw = str(row.get("pct_baseline", "")).strip()
                try:
                    pct = float(raw)
                except ValueError:
                    return ""
            return f"{pct * 100:.1f}%"
        return str(row.get(col_id, ""))

    def _daily_row_tags(self, row: Dict[str, str]) -> tuple:
        """Tint tags for a row: weekend/ad-lib background, <90% red foreground,
        or the missing-entry placeholder tint (see docs/decisions/0103)."""
        if row.get("_missing"):
            return ("missing",)
        tags: List[str] = []
        try:
            if dt.date.fromisoformat(row.get("date", "")).weekday() >= 5:
                tags.append("weekend")
        except ValueError:
            pass
        if daily_log.is_ad_lib(row):     # after weekend so ad-lib bg wins
            tags.append("adlib")
        pct = daily_log.compute_pct_baseline(row.get("weight_g", ""),
                                             row.get("baseline_g", ""))
        if pct is not None and pct < dashboard.BELOW_THRESHOLD:
            tags.append("below")
        return tuple(tags)

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
        # Which metric lines to show in the block (persisted per machine); the
        # operator picks via "Choose metrics..." (default: all).
        raw_metrics = self._load_prefs().get("sessions_metrics")
        self._metric_ids = (session_detail.default_metric_ids()
                            if raw_metrics is None
                            else self._sanitize_metric_ids(raw_metrics))
        self._current_session_row: Optional[Dict[str, str]] = None
        mrow = ttk.Frame(left)
        mrow.pack(side="top", fill="x")
        ttk.Button(mrow, text="Choose metrics...",
                   command=self._choose_metrics).pack(side="right")
        self._open_bundle_btn = ttk.Button(
            mrow, text="Open bundle folder", command=self._open_bundle_folder,
            state="disabled")
        self._open_bundle_btn.pack(side="left")
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
        # Quick-tick checklist (stub vocabulary): ticking adds/removes a line in
        # the free-text box below; the operator can still type anything else.
        checks = ttk.Frame(right)
        checks.pack(side="top", fill="x", pady=(0, 3))
        self.proc_vars: Dict[str, tk.BooleanVar] = {}
        for i, label in enumerate(_COMMON_PROCEDURES):
            var = tk.BooleanVar(value=False)
            self.proc_vars[label] = var
            ttk.Checkbutton(
                checks, text=label, variable=var,
                command=lambda l=label: self._on_proc_toggle(l)).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 10))
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
        self._update_sessions_hint(len(children))
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

    def _update_sessions_hint(self, shown: int) -> None:
        """Explicit empty-state vs a normal count line under the session list."""
        if shown == 0:
            filt = self.s_filter.get().strip()
            if self.registry_rows and filt not in ("", "All"):
                self.a_hint.configure(
                    text=f"No sessions for rat {filt}. Clear the filter, or "
                         "record + Generate.", foreground="#555")
            else:
                self.a_hint.configure(
                    text=("No sessions indexed yet. Scanning: "
                          f"{self._recordings_summary()}. Record sessions there, "
                          "then click 'Generate files'."),
                    foreground="#9C0006")
        else:
            self.a_hint.configure(
                text=(f"{shown} session{'' if shown == 1 else 's'} from "
                      f"session_registry.csv (built {self._registry_built_str()})."),
                foreground="#555")

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

    def _sanitize_metric_ids(self, ids: Any) -> List[str]:
        """Keep only known metric ids, in the catalog's display order. A saved
        selection that predates a metric-catalog change stays valid."""
        if not isinstance(ids, list):
            return session_detail.default_metric_ids()
        chosen = set(ids)
        return [mid for mid, _label in session_detail.available_metrics()
                if mid in chosen]

    def _render_metrics(self, row: Optional[Dict[str, str]]) -> None:
        """Draw the metrics block for ``row`` using the current selection."""
        lines = session_detail.metrics_lines(row or {}, self._metric_ids)
        self._set_readonly(self.s_metrics, "\n".join(lines))

    def _choose_metrics(self) -> None:
        """Pick which metric lines the session block shows (persisted)."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose session metrics")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(dlg, text="Show these metrics in the session block:").pack(
            side="top", anchor="w", padx=8, pady=(8, 4))
        body = ttk.Frame(dlg)
        body.pack(side="top", fill="both", expand=True, padx=8)
        current = set(self._metric_ids)
        vars_by_id: Dict[str, tk.BooleanVar] = {}
        for mid, label in session_detail.available_metrics():
            var = tk.BooleanVar(value=(mid in current))
            vars_by_id[mid] = var
            ttk.Checkbutton(body, text=label, variable=var).pack(
                side="top", anchor="w")
        applied = {"ok": False}

        def _ok():
            applied["ok"] = True
            dlg.destroy()

        def _set_all(value: bool):
            for var in vars_by_id.values():
                var.set(value)

        btns = ttk.Frame(dlg)
        btns.pack(side="top", fill="x", padx=8, pady=8)
        ttk.Button(btns, text="Apply", command=_ok).pack(side="left")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(
            side="left", padx=(6, 0))
        ttk.Button(btns, text="All", command=lambda: _set_all(True)).pack(
            side="right")
        ttk.Button(btns, text="None", command=lambda: _set_all(False)).pack(
            side="right", padx=(0, 6))
        dlg.wait_window()
        if not applied["ok"]:
            return
        self._metric_ids = [mid for mid, _label in session_detail.available_metrics()
                            if vars_by_id[mid].get()]
        self._save_prefs(sessions_metrics=self._metric_ids)
        self._render_metrics(self._current_session_row)

    def _show_session(self, row: Dict[str, str]) -> None:
        """Populate the read-only panels + annotation editor for ``row``."""
        self.current_session_id = str(row.get("session_id", "")).strip()
        self._current_session_row = row
        self._render_metrics(row)
        self._update_bundle_button(row)
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

    def _update_bundle_button(self, row: Optional[Dict[str, str]]) -> None:
        """Enable 'Open bundle folder' only when the row points at a folder that
        exists on disk (annotation-only / stale rows have no openable bundle)."""
        path = str((row or {}).get("bundle", "") or "").strip()
        ok = bool(path) and os.path.isdir(path)
        self._open_bundle_btn.configure(state=("normal" if ok else "disabled"))

    def _open_bundle_folder(self) -> None:
        """Open the selected session's bundle folder in the OS file browser."""
        row = self._current_session_row or {}
        path = str(row.get("bundle", "") or "").strip()
        if not path or not os.path.isdir(path):
            messagebox.showinfo(
                "Open bundle folder",
                "This session has no bundle folder on disk to open.")
            return
        try:
            self._open_in_file_browser(path)
        except OSError as exc:
            messagebox.showerror(
                "Open bundle folder", f"Couldn't open the folder:\n{exc}")

    @staticmethod
    def _open_in_file_browser(path: str) -> None:
        """Reveal ``path`` in the platform's file browser (Explorer/Finder/xdg)."""
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)

    # ------------------------------------------------------------- integrity
    def _integrity_check(self) -> None:
        """Show the read-only cross-record integrity report (bug #260810;
        ADR-0097): typo'd animal ids, sessions with no husbandry row, water-time
        cells holding a volume/note, an unknown water_method, and start/stop
        churn. Uses the records currently in memory and changes nothing - the
        fixes are the operator's. Findings acknowledged in
        host/records_audit_ack.json are dismissed from the active list but shown
        as a dismissed count (ADR-0105)."""
        registry = animals.load_registry(self.animals_path)
        triples = records_audit.session_triples(
            self.registry_rows, self.annot_rows, self.ignored_sessions)
        kept = [r for r in self.registry_rows
                if str(r.get("session_id", "")).strip()
                not in self.ignored_sessions]
        acknowledged = records_audit_ack.load_acked_keys()
        sections = records_audit.build_report(
            triples, self.daily_rows, kept, self.daily, registry, acknowledged)
        total = records_audit.total_flagged(sections)
        self._show_integrity_dialog(sections, total, len(triples))

    def _show_integrity_dialog(
            self, sections: "List[records_audit.AuditSection]",
            total: int, n_sessions: int) -> None:
        """Interactive report: active findings grouped by section in a tree the
        operator can select and ACKNOWLEDGE (dismiss). Acknowledging writes the
        finding's stable key to host/records_audit_ack.json so it stays hidden
        next run; "Manage dismissed..." restores. Read-only w.r.t. the records
        themselves - only the acknowledgement store is written (ADR-0105)."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Records integrity")
        dlg.transient(self.root)
        acked_n = records_audit.total_acknowledged(sections)
        header = ("No active problems found." if total == 0
                  else f"{total} item(s) flagged.")
        if acked_n:
            header += f"  ({acked_n} dismissed and hidden.)"
        ttk.Label(
            dlg,
            text=(f"{header}  Checked {n_sessions} session(s). "
                  "Read-only report - the records are not changed."),
            foreground=("#2e7d32" if total == 0 else "#9C0006")).pack(
            side="top", anchor="w", padx=8, pady=(8, 4))

        wrap = ttk.Frame(dlg)
        wrap.pack(side="top", fill="both", expand=True, padx=8)
        tree = ttk.Treeview(wrap, show="tree", height=22, selectmode="browse")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.column("#0", width=680, stretch=True)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # iid -> finding key, so a selected leaf can be acknowledged.
        iid_to_key: Dict[str, str] = {}
        for s in sections:
            if s.skipped:
                label = f"{s.title}  -  ({s.note})"
            elif not s.items:
                extra = (f"  ({s.acknowledged_count} dismissed)"
                         if s.acknowledged_count else "")
                label = f"{s.title}  -  none{extra}"
            else:
                extra = (f", {s.acknowledged_count} dismissed"
                         if s.acknowledged_count else "")
                label = f"{s.title}  ({s.count}{extra})"
            parent = tree.insert("", "end", text=label, open=True)
            for finding in s.findings:
                leaf = tree.insert(parent, "end", text=finding.text)
                iid_to_key[leaf] = finding.key
            if s.fix and s.items:
                tree.insert(parent, "end", text=f"Fix: {s.fix}")

        btns = ttk.Frame(dlg)
        btns.pack(side="bottom", fill="x", padx=8, pady=8)

        def acknowledge_selected() -> None:
            sel = tree.selection()
            key = iid_to_key.get(sel[0]) if sel else None
            if not key:
                messagebox.showinfo(
                    "Acknowledge finding",
                    "Select a single flagged item (not a section heading) to "
                    "dismiss it from future checks.", parent=dlg)
                return
            reason = simpledialog.askstring(
                "Acknowledge finding",
                "Why is this finding OK to dismiss? (optional note)\n\n"
                f"{tree.item(sel[0], 'text')}", parent=dlg)
            if reason is None:      # cancelled
                return
            records_audit_ack.acknowledge(key, reason)
            dlg.destroy()
            self._integrity_check()     # reopen, refreshed

        ttk.Button(btns, text="Acknowledge selected...",
                   command=acknowledge_selected).pack(side="left")
        ttk.Button(btns, text="Manage dismissed...",
                   command=lambda: self._manage_acknowledged(dlg)).pack(
            side="left", padx=(6, 0))
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side="right")

    def _manage_acknowledged(self, parent: tk.Toplevel) -> None:
        """List the acknowledged (dismissed) findings and let the operator RESTORE
        one (un-acknowledge it, bringing it back into the active report)."""
        acked = records_audit_ack.load_ack()
        dlg = tk.Toplevel(parent)
        dlg.title("Dismissed findings")
        dlg.transient(parent)
        if not acked:
            ttk.Label(dlg, text="No findings have been dismissed.").pack(
                padx=12, pady=12)
            ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=(0, 10))
            return
        ttk.Label(
            dlg, text="Dismissed findings hidden from the integrity report. "
            "Restore one to bring it back.").pack(
            side="top", anchor="w", padx=8, pady=(8, 4))
        wrap = ttk.Frame(dlg)
        wrap.pack(side="top", fill="both", expand=True, padx=8)
        cols = ("finding", "reason", "date")
        tree = ttk.Treeview(wrap, columns=cols, show="headings", height=14,
                            selectmode="browse")
        for col, txt, w in (("finding", "Finding (key)", 380),
                            ("reason", "Reason", 220), ("date", "Date", 90)):
            tree.heading(col, text=txt)
            tree.column(col, width=w, stretch=(col != "date"))
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        iid_to_key: Dict[str, str] = {}
        for key in sorted(acked):
            meta = acked[key]
            iid = tree.insert("", "end", values=(
                key, meta.get("reason", ""), meta.get("date", "")))
            iid_to_key[iid] = key

        def restore_selected() -> None:
            sel = tree.selection()
            key = iid_to_key.get(sel[0]) if sel else None
            if not key:
                return
            records_audit_ack.unacknowledge(key)
            dlg.destroy()
            parent.destroy()
            self._integrity_check()     # reopen the report, refreshed

        btns = ttk.Frame(dlg)
        btns.pack(side="bottom", fill="x", padx=8, pady=8)
        ttk.Button(btns, text="Restore selected",
                   command=restore_selected).pack(side="left")
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side="right")

    def _clear_session_detail(self) -> None:
        self.current_session_id = ""
        self._current_session_row = None
        self._update_bundle_button(None)
        self._set_readonly(self.s_metrics, "")
        self._set_readonly(self.s_log, "Select a session to view its log.")
        self.a_proc.delete("1.0", "end")
        self.a_notes.delete("1.0", "end")
        self._sync_proc_checks()
        self._reset_annotation_modified()

    def _load_annotation(self, sid: str) -> None:
        row = session_annotations.SessionAnnotations(
            self.annot_rows).for_session(sid)
        self.a_proc.delete("1.0", "end")
        self.a_notes.delete("1.0", "end")
        if row:
            self.a_proc.insert("1.0", row.get("procedures", ""))
            self.a_notes.insert("1.0", row.get("notes", ""))
        # Reflect the loaded procedures in the checklist (var.set doesn't fire the
        # Checkbutton command, so this doesn't rewrite the text).
        self._sync_proc_checks()
        # A freshly loaded editor is clean; edits after this mark it dirty (#3).
        self._reset_annotation_modified()

    # ---- procedures checklist (Phase B) ----------------------------------
    def _proc_items(self) -> List[str]:
        """The procedures box as a list of non-blank lines (one procedure each)."""
        return [ln.strip()
                for ln in self.a_proc.get("1.0", "end").splitlines()
                if ln.strip()]

    def _set_proc_items(self, items: List[str]) -> None:
        self.a_proc.delete("1.0", "end")
        self.a_proc.insert("1.0", "\n".join(items))

    def _on_proc_toggle(self, label: str) -> None:
        """Ticking a checklist item adds/removes it as a line in the free-text
        procedures box (case-insensitive; the operator's other lines are kept)."""
        items = self._proc_items()
        has = any(i.lower() == label.lower() for i in items)
        if self.proc_vars[label].get() and not has:
            items.append(label)
        elif not self.proc_vars[label].get() and has:
            items = [i for i in items if i.lower() != label.lower()]
        self._set_proc_items(items)

    def _sync_proc_checks(self) -> None:
        """Tick the boxes whose label appears in the procedures text (no rewrite)."""
        present = {i.lower() for i in self._proc_items()}
        for label, var in self.proc_vars.items():
            var.set(label.lower() in present)

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
        operator = self._require_operator()
        if operator is None:
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
        changes: List[records_changelog.Change] = []

        def _write(expect_mtime) -> None:
            # Read-for-write so a present-but-unreadable/mis-headed CSV aborts
            # the save rather than being overwritten with just this row (#12).
            existing = session_annotations.load_rows_for_write(self.annot_path)
            merged = session_annotations.upsert_rows(existing, [edit])
            session_annotations.write_rows(merged, self.annot_path,
                                           expect_mtime=expect_mtime)
            changes[:] = records_changelog.diff_rows(
                existing, merged, (session_annotations.KEY_FIELD,),
                session_annotations.FIELDS)

        if not self._guarded_write("annot", self.annot_path, _write):
            return
        n = self._log_changes(changes, operator, "records_manager",
                              "session_annotations.csv")
        self._set_status(f"Saved annotation for {sid} "
                         f"({n} change{'s' if n != 1 else ''} logged).")
        self._reset_annotation_modified()
        self._load_data()
        self._refresh_sessions()
        self._refresh_git_label()

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
        operator = self._require_operator()
        if operator is None:
            return
        try:
            # Refuse to overwrite a present-but-unreadable animals.json (a save
            # on top of a corrupt file would wipe the roster; #12). Absent is
            # fine - write_records will create it.
            before = animals.load_records_for_write(self.animals_path)
        except RecordsFileError as e:
            messagebox.showerror("Save refused", str(e))
            return
        try:
            cleaned = animals.write_records(
                self.roster, self.animals_path,
                expect_mtime=self._mtimes.get("animals"))
        except RecordsFileError as e:
            # The roster is a whole-file replace of the staged in-memory list,
            # so a file changed on disk can't be merged automatically: reload
            # (toolbar) and redo the roster edits (ADR-0155).
            messagebox.showerror(
                "Save refused",
                f"{e}\n\nUse Reload, then redo your roster edits.")
            return
        except ValueError as e:
            messagebox.showerror("Invalid roster", str(e))
            return
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return
        self.roster = cleaned
        n = self._log_changes(
            records_changelog.diff_rows(self._roster_as_rows(before),
                                        self._roster_as_rows(cleaned), ("id",)),
            operator, "records_manager", "animals.json")
        self._set_status(f"Saved roster to animals.json "
                         f"({n} change{'s' if n != 1 else ''} logged).")
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
        self._refresh_git_label()

    # ---- Dashboard tab ----------------------------------------------------
    def _build_dashboard_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Dashboard")
        head = ttk.Frame(tab)
        head.pack(side="top", fill="x")
        # Explicit From/To date range for the trend. The To date doubles as the
        # "as of" anchor for each rat's status row. The window length (not the
        # absolute dates) is what persists: on open we re-anchor To to today so
        # the dashboard never opens on a stale week (see docs/decisions).
        span = self._dash_span_pref()
        today = dt.date.today()
        self.dash_from = tk.StringVar(
            value=(today - dt.timedelta(days=span - 1)).isoformat())
        self.dash_to = tk.StringVar(value=today.isoformat())
        ttk.Label(head, text="From").pack(side="left")
        ttk.Entry(head, textvariable=self.dash_from, width=12).pack(
            side="left", padx=4)
        ttk.Label(head, text="To").pack(side="left")
        ttk.Entry(head, textvariable=self.dash_to, width=12).pack(
            side="left", padx=4)
        ttk.Button(head, text="Refresh",
                   command=self._refresh_dashboard).pack(side="left")
        # Quick-fill: last N days ending today (a convenience over the raw range).
        for n in (7, 14, 28):
            ttk.Button(head, text=f"{n}d", width=4,
                       command=lambda days=n: self._dash_quick_fill(days)).pack(
                side="left", padx=(4, 0))
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

    def _dash_span_pref(self) -> int:
        """Persisted trend-window length in days (clamped to [1, cap]); default
        14. Only the length persists - _build re-anchors To to today so we never
        reopen on a stale week."""
        try:
            span = int(self._load_prefs().get("dash_span_days",
                                              _DASH_DEFAULT_SPAN_DAYS))
        except (TypeError, ValueError):
            span = _DASH_DEFAULT_SPAN_DAYS
        return max(1, min(span, _DASH_MAX_SPAN_DAYS))

    def _dash_quick_fill(self, days: int) -> None:
        """Set the range to the last ``days`` ending today, then refresh."""
        today = dt.date.today()
        self.dash_from.set((today - dt.timedelta(days=days - 1)).isoformat())
        self.dash_to.set(today.isoformat())
        self._refresh_dashboard()

    def _dash_window(self) -> Optional[tuple]:
        """Validated ``(from_iso, to_iso)`` from the entries, or None (with a
        status message) if either isn't a date, From > To, or the span exceeds
        the cap. Persists the span length on success."""
        start = self.dash_from.get().strip()
        end = self.dash_to.get().strip()
        for label, value in (("From", start), ("To", end)):
            if not _is_iso_date(value):
                self._set_status(f"Dashboard {label} '{value}' must be "
                                 "YYYY-MM-DD.")
                return None
        d0 = dt.date.fromisoformat(start)
        d1 = dt.date.fromisoformat(end)
        if d0 > d1:
            self._set_status("Dashboard From date must be on or before To.")
            return None
        span = (d1 - d0).days + 1
        if span > _DASH_MAX_SPAN_DAYS:
            self._set_status(f"Dashboard range {span} days exceeds the "
                             f"{_DASH_MAX_SPAN_DAYS}-day cap; narrow it.")
            return None
        self._save_prefs(dash_span_days=span)
        return start, end

    def _refresh_dashboard(self) -> None:
        window = self._dash_window()
        if window is None:
            # Keep the last good view rather than reading against a mis-typed
            # range (which would silently show every rat as "not logged"); #14.
            return
        start, end = window
        self._sync_dash_toggles()
        for w in (*self.dash_strip.winfo_children(),
                  *self.dash_body.winfo_children()):
            w.destroy()
        # Drop the previous chart canvas reference (its Tk widget was just
        # destroyed); the combined chart repopulates this per refresh.
        self._dash_canvases = []
        # The To date is the "as of" anchor for each rat's current status.
        missing = dashboard.not_logged_today(self.daily, self.animal_ids, end)
        for aid in self.animal_ids:
            self._dashboard_status_row(self.dash_strip, aid, end,
                                       missing.get(aid))
        self._trend_chart_combined(self.dash_body, start, end)

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
        pct_val = status["pct_baseline"]
        if missing and not status["ad_lib"]:
            line = f"ALARM: {missing} today ({date})"
            fg = "#9C0006"
        elif status["ad_lib"]:
            # Off-restriction day: no not-weighed alarm; show the weight if it
            # happens to be recorded (ADR-0100).
            if pct_val is None:
                line = "ad-lib (off restriction)"
            else:
                flag = " <90% FLAG" if status["below_threshold"] else ""
                line = f"ad-lib \u00b7 % baseline: {pct_val * 100:.1f}%{flag}"
            fg = "#9C0006" if status["below_threshold"] else "#2451b3"
        elif pct_val is None:
            line = "no weight recorded today"
            fg = "#9C0006"
        else:
            pct = pct_val * 100
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

    def _trend_chart_combined(self, parent, start: str, end: str) -> None:
        """One shared chart overlaying each enabled rat's % baseline line over the
        ``[start, end]`` window on a TRUE date x-axis (so the 7-day rhythm reads
        as even spacing, gaps show as gaps). Two shading layers sit behind the
        lines: a faint calendar-weekend reference and solid ad-lib bands where
        the data marks off-restriction (union across enabled rats; ADR-0100).
        Per-rat colour, markers, red where <90%, endpoint label, one dashed 90%
        line. The optional water-mL overlay draws only for a solo rat (see
        docs/decisions/0087). Falls back to text if matplotlib is unavailable."""
        enabled = self._enabled_dash_rats()
        series = {}
        for aid in enabled:
            trend = dashboard.weight_trend(self.daily, aid, end,
                                           start_date=start)
            pts = [(t["date"], t["pct_baseline"] * 100.0) for t in trend
                   if t.get("pct_baseline") is not None]
            if pts:
                series[aid] = pts
        if not series:
            msg = ("no rats selected" if not enabled
                   else "not enough data to plot")
            ttk.Label(parent, text=f"% baseline {start} to {end}:  ({msg})",
                      foreground="#555").pack(side="top", anchor="w", pady=(4, 0))
            return
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            import matplotlib.dates as mdates
        except ImportError:
            self._trend_text_fallback(parent, series)
            return

        thr = dashboard.BELOW_THRESHOLD * 100.0
        all_dates = sorted({d for pts in series.values() for d, _ in pts})

        fig = Figure(figsize=(6.4, 3.0), dpi=100)
        fig.subplots_adjust(left=0.10, right=0.88, top=0.93, bottom=0.16)
        ax = fig.add_subplot(111)
        ax.xaxis_date()

        def _num(d: dt.date) -> float:
            return mdates.date2num(d)

        # Two-layer shading behind everything (bands centred on their days, so
        # extend half a day each side). Only the first band of each kind carries
        # a legend label to avoid duplicate legend entries.
        first = True
        for s, e in dashboard.weekend_spans(start, end):
            ax.axvspan(_num(s) - 0.5, _num(e) + 0.5, color="#000000",
                       alpha=0.05, lw=0, zorder=0,
                       label="weekend" if first else None)
            first = False
        first = True
        for s, e in dashboard.ad_lib_spans(self.daily, enabled, start, end):
            ax.axvspan(_num(s) - 0.5, _num(e) + 0.5, color="#ff7f0e",
                       alpha=0.15, lw=0, zorder=0,
                       label="ad-lib" if first else None)
            first = False

        # Water overlay only makes sense for one rat (twin-axis bars for several
        # would be unreadable), so require an exact solo selection.
        if self.dash_show_water.get() and len(enabled) == 1:
            aid = enabled[0]
            wt = dashboard.water_ml_trend(self.daily, self.registry_rows, aid,
                                          end, start_date=start)
            wmap = {r["date"]: r["total_ml"] for r in wt}
            xs = [_num(dt.date.fromisoformat(d)) for d in all_dates]
            bars = [wmap.get(d, 0.0) for d in all_dates]
            if any(bars):
                axw = ax.twinx()
                axw.bar(xs, bars, color="#7fb3d5", alpha=0.30, width=0.6,
                        zorder=0)
                axw.set_ylabel("water mL", fontsize=8, color="#4a789c")
                axw.tick_params(axis="y", labelsize=7, colors="#4a789c")
                axw.set_ylim(0, max(bars) * 1.25 or 1.0)

        ax.axhline(thr, color="#9C0006", lw=1.0, ls="--", alpha=0.7, zorder=1)
        all_y = [y for pts in series.values() for _, y in pts]
        for aid, pts in series.items():
            colour = self._rat_color(aid)
            xs = [dt.date.fromisoformat(d) for d, _ in pts]
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
        # Honour the requested window (not just the data range) and pad half a
        # day each side so end markers/bands aren't clipped.
        ax.set_xlim(_num(dt.date.fromisoformat(start)) - 0.5,
                    _num(dt.date.fromisoformat(end)) + 0.5)
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        for lbl in ax.get_xticklabels():
            lbl.set_fontsize(7)
        ax.grid(True, axis="y", alpha=0.15)
        ax.legend(fontsize=7, loc="lower left", framealpha=0.6, ncol=2)

        # Caption so the shading is self-explanatory without a manual.
        ttk.Label(parent,
                  text="Shaded: grey = calendar weekend, orange = ad-lib "
                       "(off-restriction) days.",
                  foreground="#777").pack(side="bottom", anchor="w")

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
    def _registry_built_str(self) -> str:
        """When session_registry.csv was last written, human-readable."""
        m = self._file_mtime(self.registry_path)
        if m is None:
            return "never"
        return dt.datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M")

    def _refresh_status_banner(self) -> None:
        """Update the top staleness strip (session count + build time + whether
        the daily Excel workbook holds edits not yet imported)."""
        n = len(self.registry_rows)
        reg = (f"Registry: {n} session{'' if n == 1 else 's'} "
               f"\u00b7 built {self._registry_built_str()}")
        if self._excel_has_unsynced_edits():
            self.banner_var.set(
                reg + "   |   Daily Excel edited since last sync \u2014 run "
                "Import before Generate, or those edits are overwritten.")
            self.banner.configure(foreground="#9a6700")
        else:
            self.banner_var.set(reg + "   |   Daily Excel in sync")
            self.banner.configure(foreground="#555")
        self._refresh_git_label()

    # ---- Commit records (ADR-0155) ---------------------------------------
    def _git_run(self) -> records_git.GitRun:
        """A git runner bound to the resources repo (the folder the records
        files live in)."""
        return records_git.git_runner(str(animals.resources_root()))

    def _records_git_status(self) -> records_git.RecordsStatus:
        return records_git.records_status(
            self._git_run(), records_changelog.load_entries(self.changelog_path))

    def _refresh_git_label(self) -> None:
        """The toolbar's 'N records edits not yet committed' label."""
        st = self._records_git_status()
        if not st.ok:
            self.git_var.set("(git unavailable)")
            self.git_label.configure(foreground="#9a6700")
            return
        n = len(st.pending)
        if n:
            self.git_var.set(f"{n} records edit{'' if n == 1 else 's'} "
                             "not yet committed")
            self.git_label.configure(foreground="#9a6700")
        elif st.records_changed:
            self.git_var.set("records files changed, not yet committed")
            self.git_label.configure(foreground="#9a6700")
        else:
            self.git_var.set("records committed")
            self.git_label.configure(foreground="#555")

    def _on_commit_records(self) -> None:
        """Stage only the records files and commit them to the resources repo,
        after the operator has seen (and may edit) the generated message."""
        if self._busy:
            messagebox.showinfo("Busy", "Wait for the running build to finish.")
            return
        if self._has_unsaved() and not messagebox.askyesno(
                "Unsaved edits",
                "You have unsaved edits - they are not on disk, so they will "
                "NOT be in this commit:\n\n" + self._unsaved_summary()
                + "\n\nCommit what is already saved anyway?", default="no"):
            return
        st = self._records_git_status()
        why = records_git.refusal(st)
        if why:
            (messagebox.showinfo if why.startswith("Nothing")
             else messagebox.showwarning)("Commit records", why)
            self._refresh_git_label()
            return
        message = records_git.build_message(st.pending, st.records_changed)
        self._commit_dialog(st, message)

    def _commit_dialog(self, st: records_git.RecordsStatus,
                       message: str) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("Commit records")
        dlg.transient(self.root)
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        files = ", ".join(os.path.basename(p) for p in st.records_changed)
        ttk.Label(frm, text=f"Files to commit: {files}\n"
                            "Only these are staged; nothing is pushed. "
                            "Edit the message if you like, then Commit.",
                  justify="left").pack(anchor="w")
        txt = tk.Text(frm, width=76, height=14, wrap="word")
        txt.pack(fill="both", expand=True, pady=(6, 6))
        txt.insert("1.0", message)
        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        def _do_commit() -> None:
            msg = txt.get("1.0", "end").strip() + "\n"
            if not msg.strip():
                messagebox.showwarning("Commit records",
                                       "The commit message is empty.")
                return
            # Re-query right before committing so a file staged in between
            # (another window, a terminal) is caught by the same refusal.
            fresh = self._records_git_status()
            result = records_git.commit_records(self._git_run(), fresh, msg)
            dlg.destroy()
            if result.ok:
                messagebox.showinfo("Commit records", result.summary)
                self._set_status(result.summary)
            else:
                messagebox.showerror("Commit records", result.summary)
            self._refresh_git_label()

        ttk.Button(btns, text="Commit", command=_do_commit).pack(side="right")
        ttk.Button(btns, text="Cancel",
                   command=dlg.destroy).pack(side="right", padx=(0, 6))
        dlg.grab_set()
        txt.focus_set()

    def _refresh_all(self) -> None:
        self._refresh_daily_table()
        self._clear_daily()
        self._refresh_sessions()
        self._refresh_roster()
        self._refresh_dashboard()
        self._refresh_status_banner()

    # ------------------------------------------------------- prefs / mtimes
    def _load_prefs(self) -> dict:
        """The small machine-local GUI-pref dict (best-effort; {} if absent)."""
        try:
            with open(_PREFS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_prefs(self, **updates) -> None:
        """Merge ``updates`` into the pref file, preserving the other keys."""
        prefs = self._load_prefs()
        prefs.update(updates)
        try:
            with open(_PREFS_PATH, "w", encoding="utf-8") as fh:
                json.dump(prefs, fh, indent=2)
        except OSError:
            pass

    @staticmethod
    def _file_mtime(path: str) -> Optional[float]:
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _load_recordings_root(self) -> str:
        """The recordings folder to scan: the persisted pref if present, else the
        shared default (env-overridable) from tmaze.paths (ADR-0088)."""
        return (self._load_prefs().get("recordings_root", "").strip()
                or str(phase3_recordings_root()))

    def _save_recordings_root(self, path: str) -> None:
        """Persist the chosen recordings folder (best-effort)."""
        self._save_prefs(recordings_root=path)

    def _load_recordings_archives(self) -> List[str]:
        """The persisted list of read-only ARCHIVE recordings roots (folders
        older sessions were moved to; docs/decisions/0113). [] when unset. Only
        well-formed string entries are kept, order preserved."""
        raw = self._load_prefs().get("recordings_archives", [])
        if not isinstance(raw, list):
            return []
        return [os.path.normpath(p.strip()) for p in raw
                if isinstance(p, str) and p.strip()]

    def _save_recordings_archives(self, paths: List[str]) -> None:
        """Persist the archive-root list (best-effort)."""
        self._save_prefs(recordings_archives=paths)

    def _all_scan_roots(self) -> List[str]:
        """Every root the registry build should scan: the primary first, then the
        archives, order-preserving de-duplicated (docs/decisions/0113)."""
        seen: set = set()
        out: List[str] = []
        for p in [self.recordings_root, *self.recordings_archives]:
            key = os.path.normcase(os.path.normpath(p))
            if key not in seen:
                seen.add(key)
                out.append(p)
        return out

    def _recordings_summary(self) -> str:
        """The recordings-bar label: the primary root, plus a "(+N archived)"
        suffix when archive roots are configured."""
        n = len(self.recordings_archives)
        if not n:
            return self.recordings_root
        return f"{self.recordings_root}   (+{n} archived)"

    def _record_xlsx_sync(self) -> None:
        """Remember the daily workbook's mtime right after we (re)built it from
        the CSV, so a later hand-edit in Excel shows up as an out-of-sync edit."""
        m = self._file_mtime(self.daily_xlsx_path)
        if m is not None:
            self._save_prefs(daily_xlsx_synced_mtime=m)

    def _excel_has_unsynced_edits(self) -> bool:
        """True if daily_log.xlsx changed since the last Generate/Import we ran -
        i.e. the operator likely edited the Entry sheet in Excel and hasn't
        Imported it yet, so a Generate (--rebuild) would overwrite those edits.

        Conservative: if we've never synced it, don't cry wolf (return False)."""
        m = self._file_mtime(self.daily_xlsx_path)
        if m is None:
            return False
        synced = self._load_prefs().get("daily_xlsx_synced_mtime")
        if synced is None:
            return False
        # 1s slack absorbs filesystem mtime granularity.
        return m > float(synced) + 1.0

    def _on_pick_recordings(self) -> None:
        start = self.recordings_root if os.path.isdir(self.recordings_root) else None
        chosen = filedialog.askdirectory(
            title="Select the Phase 3 recordings folder to scan",
            initialdir=start)
        if not chosen:
            return
        self.recordings_root = os.path.normpath(chosen)
        self.rec_root_var.set(self._recordings_summary())
        self._save_recordings_root(self.recordings_root)
        self._set_status(f"Recordings folder set to {self.recordings_root}. "
                         "Click Generate files to re-scan.")

    def _on_manage_archives(self) -> None:
        """Add/remove read-only ARCHIVE recordings roots (older sessions moved off
        the rig disk; docs/decisions/0113). These are scanned in ADDITION to the
        primary folder on the next Generate; the rig still writes only to the
        primary. Changes persist immediately."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Archive recordings folders")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(
            dlg, wraplength=460, justify="left",
            text=("Extra, read-only folders where older sessions were moved when "
                  "the rig disk filled. They are scanned in addition to the "
                  "primary recordings folder when you click 'Generate files'. "
                  "Point each at a 'phase3_recordings' folder.")).pack(
            side="top", anchor="w", padx=10, pady=(10, 6))

        wrap = ttk.Frame(dlg)
        wrap.pack(side="top", fill="both", expand=True, padx=10)
        lb = tk.Listbox(wrap, height=6, width=64)
        for p in self.recordings_archives:
            lb.insert("end", p)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=vsb.set)
        lb.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def _add() -> None:
            chosen = filedialog.askdirectory(
                title="Select an archive phase3_recordings folder", parent=dlg)
            if not chosen:
                return
            norm = os.path.normpath(chosen)
            existing = {os.path.normcase(lb.get(i)) for i in range(lb.size())}
            if os.path.normcase(norm) in existing:
                return
            lb.insert("end", norm)

        def _remove() -> None:
            for i in reversed(lb.curselection()):
                lb.delete(i)

        btns = ttk.Frame(dlg)
        btns.pack(side="top", fill="x", padx=10, pady=(6, 0))
        ttk.Button(btns, text="Add folder...", command=_add).pack(side="left")
        ttk.Button(btns, text="Remove selected", command=_remove).pack(
            side="left", padx=(6, 0))

        def _save() -> None:
            paths = [lb.get(i) for i in range(lb.size())]
            self.recordings_archives = paths
            self._save_recordings_archives(paths)
            self.rec_root_var.set(self._recordings_summary())
            n = len(paths)
            self._set_status(
                f"{n} archive folder{'' if n == 1 else 's'} set. "
                "Click Generate files to re-scan.")
            dlg.destroy()

        foot = ttk.Frame(dlg)
        foot.pack(side="bottom", fill="x", padx=10, pady=10)
        ttk.Button(foot, text="Save", command=_save).pack(side="left")
        ttk.Button(foot, text="Cancel", command=dlg.destroy).pack(
            side="left", padx=(6, 0))

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
        # Generate's --rebuild regenerates the Entry sheet from the CSV, so any
        # hand-edits made in Excel since the last sync would be lost. Warn first
        # (Phase B / B4).
        if self._excel_has_unsynced_edits() and not messagebox.askyesno(
                "Excel edits not imported",
                "The daily Excel workbook has changed since the last sync.\n\n"
                "Generate rebuilds the Entry sheet from daily_log.csv and will "
                "OVERWRITE those Excel edits. To keep them, click No, then "
                "'Import from Excel' first.\n\nGenerate anyway?", default="no"):
            return
        self._run_async("Generating files...", self._do_generate)

    def _on_import(self) -> None:
        if self._has_unsaved() and not self._confirm_discard(
                "Import refreshes every view from disk"):
            return
        operator = self._require_operator()
        if operator is None:
            return
        self._run_async("Importing from Excel...",
                        lambda: self._do_import(operator))

    def _on_close(self) -> None:
        """Window-close guard: warn on a running build or unsaved edits (#3)."""
        if self._busy:
            if not messagebox.askyesno(
                    "Quit?", "A build is still running. Quit anyway?",
                    default="no"):
                return
        elif self._has_unsaved() and not messagebox.askyesno(
                "Quit?",
                "You have unsaved changes:\n\n" + self._unsaved_summary()
                + "\n\nQuit anyway?", default="no"):
            return
        self.root.destroy()

    def _do_generate(self) -> str:
        log = [self._build_registry_inprocess(),
               self._run_script("build_daily_log.py", ["--rebuild"]),
               self._run_script("build_session_training_log.py", [])]
        return "\n".join(log)

    def _do_import(self, operator: str) -> str:
        """Worker: run the two importing builders under ``operator``. They log
        what they changed to records_changes.csv themselves (ADR-0155), so the
        GUI does not diff here - one writer, one log entry per change."""
        log = [self._run_script("build_daily_log.py",
                                ["--export", "--operator", operator]),
               self._run_script("build_session_training_log.py",
                                ["--import-annotations", "--operator",
                                 operator])]
        return "\n".join(log)

    def _build_registry_inprocess(self) -> str:
        """Rebuild session_registry.csv by calling the tool's main() directly,
        scanning the operator-chosen recordings root plus any archive roots (the
        CSV still lands in the tool's fixed output dir; see docs/decisions/0088,
        0113)."""
        buf = io.StringIO()
        root_args: List[str] = []
        for r in self._all_scan_roots():
            root_args += ["--root", r]
        try:
            with contextlib.redirect_stdout(buf):
                build_session_registry.main(root_args)
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
        # A successful Generate/Import leaves the workbook regenerated from the
        # CSV, so record it as the new in-sync point (unless the builder failed,
        # to avoid muting a real "Excel edited" warning). See _excel_has_unsynced.
        if "FAILED" not in result and "rc=" not in result:
            self._record_xlsx_sync()
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
