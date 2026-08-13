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
def _g(row: Dict[str, Any], key: str) -> str:
    """A cell shown in the block: the trimmed value, or ``-`` when blank."""
    return _s(row, key) or "-"


def tag_tally(row: Dict[str, Any]) -> str:
    """The session's note-tag counts as ``name x n, ...`` (intervention history),
    read from the registry row's wide ``tag_<name>`` columns (0/blank skipped);
    ``-`` when the session carries no tags. The tidy ``session_registry_tags.csv``
    is the source of truth; these wide columns are its convenience view
    (ADR-0081)."""
    tallies: List[str] = []
    for key in sorted(row):
        if not str(key).startswith("tag_"):
            continue
        n = _num(_s(row, key))
        if n and n > 0:
            tallies.append(f"{str(key)[4:]} x {int(n)}")
    return ", ".join(tallies) if tallies else "-"


# Each selectable metric is one labelled line. ``id`` is the stable key persisted
# in the GUI's prefs; ``label`` names it in the picker; ``fn`` renders the line
# for a registry row. Order here is the display order. Grouped so each line stays
# a useful at-a-glance unit (mirrors the summary's sections) while still being
# individually show/hideable (docs/decisions/0089; the selectable UI is FW-work).
_METRICS: List[Tuple[str, str, Any]] = [
    ("session", "Session id",
     lambda r: f"Session:  {_g(r, 'session_id')}"),
    ("identity", "Animal / date / time",
     lambda r: f"Animal:   {_g(r, 'animal')}    Date: {_g(r, 'date')}"
               f"    Time: {session_time(r)}"),
    ("duration", "Duration / stage",
     lambda r: f"Duration: {_duration(_s(r, 'duration_s'))}    "
               f"Stage: {_g(r, 'stage')} ({_g(r, 'stage_source')})"),
    ("weight", "Weight / baseline",
     lambda r: f"Weight:   {_g(r, 'weight_g')} g   baseline {_g(r, 'baseline_g')}"
               f" g   ({_pct(_s(r, 'pct_baseline'))} baseline)"),
    ("hours_since_water", "Hours since water",
     lambda r: f"Hrs since water: {_g(r, 'hours_since_water')}"),
    ("trials", "Trials / accuracy",
     lambda r: f"Trials:   {_g(r, 'completed')}/{_g(r, 'trials_total')} completed"
               f"   correct {_g(r, 'correct')} / incorrect {_g(r, 'incorrect')}"
               f"   acc {_pct(_s(r, 'accuracy_cum'))}"),
    ("side_bias", "Side bias / stem compliance",
     lambda r: f"Side bias: L {_pct(_s(r, 'side_bias_left'))} / "
               f"R {_pct(_s(r, 'side_bias_right'))}   "
               f"stem-compliance {_pct(_s(r, 'stem_compliance'))}"),
    ("water", "Water (task / manual / supp)",
     lambda r: f"Water:    task {_g(r, 'task_reward_ml')} mL   "
               f"manual {_g(r, 'manual_reward_ml')} mL   supp {_g(r, 'supp_water')}"),
    ("trainer", "Trainer / reward",
     lambda r: f"Trainer:  {_g(r, 'trainer')}   Reward: {_g(r, 'reward_type')}"),
    ("tags", "Note tags (tally)",
     lambda r: f"Tags:     {tag_tally(r)}"),
]

_METRIC_IDS: Tuple[str, ...] = tuple(mid for mid, _label, _fn in _METRICS)
_METRIC_FNS = {mid: fn for mid, _label, fn in _METRICS}


def available_metrics() -> List[Tuple[str, str]]:
    """``[(id, label)]`` for every selectable metric, in display order (for a
    picker UI)."""
    return [(mid, label) for mid, label, _fn in _METRICS]


def default_metric_ids() -> List[str]:
    """The default selection: every metric, in display order."""
    return list(_METRIC_IDS)


def metrics_lines(row: Dict[str, Any],
                  selected: Optional[List[str]] = None) -> List[str]:
    """A compact, labelled metrics block for one registry row (blanks tolerated,
    shown as ``-``).

    ``selected`` is an iterable of metric ids to include; unknown ids are ignored
    and the result always follows the catalog's display order (not the caller's).
    ``None`` means "all metrics" (the default block), preserving the original
    behaviour for callers that don't choose.
    """
    if selected is None:
        ids: List[str] = list(_METRIC_IDS)
    else:
        chosen = set(selected)
        ids = [mid for mid in _METRIC_IDS if mid in chosen]
    return [_METRIC_FNS[mid](row) for mid in ids]


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
