"""
session_detail.py - present one session for the Records Manager "Sessions" tab
(pure core).

The Sessions browser needs two read-only views of a single session:

- a compact metrics BLOCK folded from that session's ``session_registry.csv`` row
  (already-parsed columns: husbandry, behaviour, the four water channels, sticky
  metadata), and
- the bundle's ready-made human LOG at ``summary/session_summary_*.txt`` (metrics
  + a timestamped operator-notes timeline), when the bundle has one.

Plus the curated column set for the session LIST (one row per session). This core
only selects/formats values that already exist - it recomputes no stats and does
no raw-CSV/video parsing. Registry cells are strings (the CSV round-trips them);
rates/fractions are 0..1 and pct_baseline is a fraction, so we render them as
percentages for humans. All reads are best-effort. See docs/decisions/0089.

Pure stdlib (``glob`` / ``os``), no Tk. Headless tests: python -m unittest
test_session_detail.
"""

from __future__ import annotations

import glob
import os
from typing import Any, Dict, List, Optional, Tuple


# ------------------------------------------------------------ the rendered log
def summary_text(bundle_dir: Optional[str]) -> Optional[str]:
    """The bundle's rendered ``summary/session_summary_*.txt`` (the first match,
    sorted), or None when the bundle path is missing/unreadable or the session
    has no ``summary/`` folder (older, pre-ADR-0071 bundles). Best-effort."""
    if not bundle_dir:
        return None
    matches = sorted(glob.glob(os.path.join(bundle_dir, "summary",
                                            "session_summary_*.txt")))
    if not matches:
        return None
    try:
        with open(matches[0], "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


# ------------------------------------------------------------------- helpers
def _s(row: Dict[str, Any], key: str) -> str:
    """A trimmed string cell ("" when absent)."""
    v = row.get(key)
    return "" if v is None else str(v).strip()


def _num(v: str) -> Optional[float]:
    """Parse a CSV cell to float, or None when blank/non-numeric."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _duration(seconds: str) -> str:
    """Seconds (a CSV float string) -> ``H:MM:SS``, or ``-`` when absent.

    Mirrors session_summary._duration so the block and the .txt agree."""
    n = _num(seconds)
    if n is None:
        return "-"
    total = int(max(0.0, n))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _pct(frac: str) -> str:
    """A 0..1 fraction (CSV string) -> ``NN%``, or ``-`` when absent."""
    n = _num(frac)
    return "-" if n is None else f"{n * 100:.0f}%"


def session_time(row: Dict[str, Any]) -> str:
    """The session's clock time ``HH:MM`` from ``start_iso`` (``...THH:MM:SS``),
    falling back to the ``_HHMMSS`` field of ``<animal>_<date>_<HHMMSS>`` ids,
    else ``-``."""
    iso = _s(row, "start_iso")
    if "T" in iso and len(iso.split("T", 1)[1]) >= 5:
        return iso.split("T", 1)[1][:5]
    sid = _s(row, "session_id")
    parts = sid.rsplit("_", 1)
    tail = parts[1] if len(parts) == 2 else ""
    if len(tail) >= 4 and tail[:6].isdigit():
        return f"{tail[0:2]}:{tail[2:4]}"
    return "-"


# ------------------------------------------------------------- metrics block
def metrics_lines(row: Dict[str, Any]) -> List[str]:
    """A compact, labelled metrics block for one registry row (blanks tolerated,
    shown as ``-``). Groups mirror the summary's sections at a glance so the block
    is useful even for older bundles that have no ``.txt`` log."""
    def g(key: str) -> str:
        return _s(row, key) or "-"

    weight = g("weight_g")
    baseline = g("baseline_g")
    pctb = _pct(_s(row, "pct_baseline"))
    left = _pct(_s(row, "side_bias_left"))
    right = _pct(_s(row, "side_bias_right"))
    supp = g("supp_water")

    return [
        f"Session:  {g('session_id')}",
        f"Animal:   {g('animal')}    Date: {g('date')}    Time: {session_time(row)}",
        f"Duration: {_duration(_s(row, 'duration_s'))}    Stage: {g('stage')}"
        f" ({g('stage_source')})",
        f"Weight:   {weight} g   baseline {baseline} g   ({pctb} baseline)",
        f"Trials:   {g('completed')}/{g('trials_total')} completed   "
        f"correct {g('correct')} / incorrect {g('incorrect')}   "
        f"acc {_pct(_s(row, 'accuracy_cum'))}",
        f"Side bias: L {left} / R {right}   "
        f"stem-compliance {_pct(_s(row, 'stem_compliance'))}",
        f"Water:    task {g('task_reward_ml')} mL   "
        f"manual {g('manual_reward_ml')} mL   supp {supp}",
        f"Trainer:  {g('trainer')}   Reward: {g('reward_type')}",
    ]


# ----------------------------------------------------------------- list view
# The curated Sessions-list columns (registry-derived). The GUI may add its own
# marker columns (e.g. whether an annotation exists) around these.
_LIST_COLUMNS: Tuple[str, ...] = (
    "date", "time", "animal", "duration",
    "trials", "accuracy", "task_ml", "manual_ml", "stage",
)


def list_columns() -> List[str]:
    """Column ids for the Sessions Treeview (registry-derived, in order)."""
    return list(_LIST_COLUMNS)


def list_row(row: Dict[str, Any]) -> Tuple[str, ...]:
    """One session as the curated Treeview cell values (aligned to
    :func:`list_columns`)."""
    def g(key: str) -> str:
        return _s(row, key) or "-"

    completed = _s(row, "completed")
    total = _s(row, "trials_total")
    trials = f"{completed}/{total}" if (completed or total) else "-"
    return (
        g("date"),
        session_time(row),
        g("animal"),
        _duration(_s(row, "duration_s")),
        trials,
        _pct(_s(row, "accuracy_cum")),
        g("task_reward_ml"),
        g("manual_reward_ml"),
        g("stage"),
    )
