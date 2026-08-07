"""
records_manager.py - desktop GUI for the animal/session records (thin shell).

One window to keep the lab's *records* without hand-editing CSVs or workbooks:

- **Daily entry** - add/edit a daily husbandry row (weight, baseline, auto
  % baseline with a <90% flag, water on/off, supplemental water, notes). Saving
  upserts the row into ``daily_log.csv`` (the source of truth) by
  ``(animal_id, date)`` - it never rewrites the whole file, so a row another
  surface added survives.
- **Annotations** - type the qualitative "common procedures" + notes for a
  recorded session; saving upserts ``session_annotations.csv`` by ``session_id``.
- **Roster** - add/remove rats and edit their aliases; saving validates and
  writes ``animals.json`` (the identity source of truth).
- **Dashboard** - a read-only welfare view (per rat: % baseline today + <90% flag
  + grams-to-90% deficit; a not-logged/not-weighed-today alarm; water intake by
  source; and a 14-day weight trend). Signals come from the pure ``dashboard``
  core.

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
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

from tmaze.core.records import animals, dashboard, daily_log, session_annotations
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


def _today_iso() -> str:
    return dt.date.today().isoformat()


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
        self.registry_path = os.path.join(
            build_session_registry.DEFAULT_RECORDINGS_ROOT,
            build_session_registry.REGISTRY_FILENAME)

        self._busy = False
        self._load_data()
        self._build_ui()
        self._refresh_all()

    # ---------------------------------------------------------------- data
    def _load_data(self) -> None:
        """(Re)read every source of truth from disk into memory."""
        self.roster = animals.load_records(self.animals_path)  # [{id, aliases}]
        self.animal_ids = [r["id"] for r in self.roster] or ["600", "601", "602"]
        self.daily_rows = daily_log.load_rows(self.daily_path)
        self.daily = daily_log.DailyLog(self.daily_rows)
        self.annot_rows = session_annotations.load_rows(self.annot_path)
        self.registry_rows = _read_csv_skip_comments(self.registry_path)

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
        self._build_annotations_tab()
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

        # Animal (dropdown) + Date.
        ttk.Label(form, text="Animal").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        self.d_animal = ttk.Combobox(form, textvariable=self.d_vars["animal_id"],
                                     values=self.animal_ids, width=10)
        self.d_animal.grid(row=0, column=1, sticky="w")
        self.d_animal.bind("<<ComboboxSelected>>", lambda _e: self._prefill_baseline())

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
        table = ttk.Frame(tab)
        table.pack(side="top", fill="both", expand=True, pady=(8, 0))
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

    def _save_daily(self) -> None:
        aid = self.d_vars["animal_id"].get().strip()
        date = self.d_vars["date"].get().strip()
        if not aid or not date:
            messagebox.showwarning("Missing key", "Animal and Date are required.")
            return
        pct = daily_log.compute_pct_baseline(self.d_vars["weight_g"].get(),
                                             self.d_vars["baseline_g"].get())
        edit = {f: self.d_vars[f].get().strip() for f in daily_log.FIELDS}
        edit["pct_baseline"] = "" if pct is None else str(pct)
        try:
            merged = daily_log.upsert_rows(daily_log.load_rows(self.daily_path),
                                           [edit])
            daily_log.write_rows(merged, self.daily_path)
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return
        self._set_status(f"Saved {aid} {date} to daily_log.csv.")
        self._load_data()
        self._refresh_daily_table()
        self._refresh_dashboard()

    def _clear_daily(self) -> None:
        for f in daily_log.FIELDS:
            self.d_vars[f].set("")
        self.d_vars["date"].set(_today_iso())
        self._update_pct()

    def _load_selected_daily(self, _event=None) -> None:
        sel = self.d_tree.selection()
        if not sel:
            return
        vals = self.d_tree.item(sel[0], "values")
        for f, v in zip(daily_log.FIELDS, vals):
            self.d_vars[f].set("" if v is None else str(v))
        self._update_pct()

    def _refresh_daily_table(self) -> None:
        self.d_tree.delete(*self.d_tree.get_children())
        rows = sorted(self.daily_rows,
                      key=lambda r: (r.get("date", ""), r.get("animal_id", "")),
                      reverse=True)
        for r in rows:
            self.d_tree.insert("", "end",
                               values=[r.get(f, "") for f in daily_log.FIELDS])

    # ---- Annotations tab --------------------------------------------------
    def _build_annotations_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Annotations")

        top = ttk.Frame(tab)
        top.pack(side="top", fill="x")
        ttk.Label(top, text="Session").pack(side="left")
        self.a_session = ttk.Combobox(top, width=44, state="readonly")
        self.a_session.pack(side="left", padx=(4, 0))
        self.a_session.bind("<<ComboboxSelected>>", self._load_selected_annotation)
        ttk.Button(top, text="Save annotation",
                   command=self._save_annotation).pack(side="left", padx=(8, 0))

        body = ttk.Frame(tab)
        body.pack(side="top", fill="both", expand=True, pady=(8, 0))
        ttk.Label(body, text="Common procedures across days").grid(
            row=0, column=0, sticky="w")
        self.a_proc = tk.Text(body, height=4, width=90, wrap="word")
        self.a_proc.grid(row=1, column=0, sticky="we", pady=(0, 8))
        ttk.Label(body, text="Notes").grid(row=2, column=0, sticky="w")
        self.a_notes = tk.Text(body, height=12, width=90, wrap="word")
        self.a_notes.grid(row=3, column=0, sticky="nswe")
        body.rowconfigure(3, weight=1)
        body.columnconfigure(0, weight=1)

        self.a_hint = ttk.Label(
            tab, foreground="#555",
            text=("Sessions come from session_registry.csv. If it's empty, run "
                  "Generate files after recording sessions."))
        self.a_hint.pack(side="bottom", fill="x")

    def _annotation_session_ids(self) -> List[str]:
        """Session ids to annotate: registry sessions first, then any annotated
        session not in the registry (so old notes remain reachable)."""
        reg = [r.get("session_id", "").strip() for r in self.registry_rows
               if r.get("session_id")]
        extra = [r.get("session_id", "").strip() for r in self.annot_rows
                 if r.get("session_id") and r.get("session_id") not in reg]
        return reg + extra

    def _refresh_annotations(self) -> None:
        ids = self._annotation_session_ids()
        self.a_session["values"] = ids
        if ids and not self.a_session.get():
            self.a_session.set(ids[0])
            self._load_selected_annotation()

    def _load_selected_annotation(self, _event=None) -> None:
        sid = self.a_session.get().strip()
        row = session_annotations.SessionAnnotations(self.annot_rows).for_session(sid)
        self.a_proc.delete("1.0", "end")
        self.a_notes.delete("1.0", "end")
        if row:
            self.a_proc.insert("1.0", row.get("procedures", ""))
            self.a_notes.insert("1.0", row.get("notes", ""))

    def _save_annotation(self) -> None:
        sid = self.a_session.get().strip()
        if not sid:
            messagebox.showwarning("No session", "Pick a session first.")
            return
        reg = {r.get("session_id", "").strip(): r for r in self.registry_rows}
        meta = reg.get(sid, {})
        edit = {
            "session_id": sid,
            "animal": meta.get("animal", ""),
            "date": meta.get("date", ""),
            "procedures": self.a_proc.get("1.0", "end").strip(),
            "notes": self.a_notes.get("1.0", "end").strip(),
        }
        try:
            merged = session_annotations.upsert_rows(
                session_annotations.load_rows(self.annot_path), [edit])
            session_annotations.write_rows(merged, self.annot_path)
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return
        self._set_status(f"Saved annotation for {sid}.")
        self._load_data()

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

        self.dash_body = ttk.Frame(tab)
        self.dash_body.pack(side="top", fill="both", expand=True, pady=(8, 0))

    def _refresh_dashboard(self) -> None:
        for w in self.dash_body.winfo_children():
            w.destroy()
        date = self.dash_date.get().strip() or _today_iso()
        missing = dashboard.not_logged_today(self.daily, self.animal_ids, date)
        for aid in self.animal_ids:
            self._dashboard_card(self.dash_body, aid, date, missing.get(aid))

    def _dashboard_card(self, parent, aid: str, date: str,
                        missing: Optional[str]) -> None:
        card = ttk.LabelFrame(parent, text=f"Rat {aid}", padding=8)
        card.pack(side="top", fill="x", pady=4)

        status = dashboard.weight_status(self.daily, aid, date)
        if missing:
            line = f"ALARM: {missing} today ({date})"
            colour = "#9C0006"
        elif status["pct_baseline"] is None:
            line = "no weight recorded today"
            colour = "#9C0006"
        else:
            pct = status["pct_baseline"] * 100
            flag = " <90% FLAG" if status["below_threshold"] else ""
            deficit = status["grams_to_threshold"] or 0.0
            need = f"  (needs +{deficit:.1f} g to reach 90%)" if deficit else ""
            line = f"% baseline: {pct:.1f}%{flag}{need}"
            colour = "#9C0006" if status["below_threshold"] else "#1a7f37"
        ttk.Label(card, text=line, foreground=colour).pack(side="top", anchor="w")

        # Water intake by source (totals only, no target flag).
        drow = self.daily.for_date(aid, date)
        day_reg = dashboard.registry_rows_for(self.registry_rows, aid, date)
        wb = dashboard.water_breakdown(dashboard.collect_water(drow, day_reg))
        if wb["sources"]:
            parts = " + ".join(f"{n} {ml:g}" for n, ml in wb["sources"])
            water = f"water today: {parts} = {wb['total_ml']:g} mL"
        else:
            water = "water today: none recorded"
        ttk.Label(card, text=water).pack(side="top", anchor="w")

        # 14-day weight trend: a compact sparkline + first/last labels.
        trend = dashboard.weight_trend(self.daily, aid, date, days=14)
        self._sparkline(card, trend)

    def _sparkline(self, parent, trend: List[dict]) -> None:
        row = ttk.Frame(parent)
        row.pack(side="top", fill="x", pady=(4, 0))
        ttk.Label(row, text="14-day weight:").pack(side="left")
        weights = [t["weight_g"] for t in trend if t.get("weight_g") is not None]
        if len(weights) < 2:
            ttk.Label(row, text="  (not enough data)",
                      foreground="#555").pack(side="left")
            return
        w, h, pad = 220, 34, 4
        cv = tk.Canvas(row, width=w, height=h, highlightthickness=0)
        cv.pack(side="left", padx=6)
        lo, hi = min(weights), max(weights)
        span = (hi - lo) or 1.0
        n = len(weights)
        pts = []
        for i, wt in enumerate(weights):
            x = pad + i * (w - 2 * pad) / (n - 1)
            y = h - pad - (wt - lo) / span * (h - 2 * pad)
            pts.extend((x, y))
        cv.create_line(*pts, fill="#1f77b4", width=2)
        ttk.Label(row, text=f"{trend[0]['date']}: {weights[0]:g} g  ->  "
                            f"{trend[-1]['date']}: {weights[-1]:g} g",
                  foreground="#555").pack(side="left")

    # ---------------------------------------------------------------- refresh
    def _refresh_all(self) -> None:
        self._refresh_daily_table()
        self._clear_daily()
        self._refresh_annotations()
        self._refresh_roster()
        self._refresh_dashboard()

    # ---------------------------------------------------------------- actions
    def _on_reload(self) -> None:
        self._load_data()
        self.d_animal["values"] = self.animal_ids
        self._refresh_all()
        self._set_status("Reloaded from disk.")

    def _on_generate(self) -> None:
        self._run_async("Generating files...", self._do_generate)

    def _on_import(self) -> None:
        self._run_async("Importing from Excel...", self._do_import)

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
        """Rebuild session_registry.csv by calling the tool's main() directly."""
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                build_session_registry.main([])
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
