"""
phase3_log_reader.py - reconstruct a Phase 3 snapshot from a recorded CSV (pure).

The live Metrics tab (docs/decisions/0114) plots the current session's rolling
traces from the published ``Phase3StateSnapshot``. To overlay a PAST session as a
faded reference line (docs/decisions/0117), we need to feed a prior session's data
through the SAME ``phase3_traces.build_traces`` core - so the reference line is
computed identically to the live one. This module reads one recorded
``phase3_*.csv`` (the file ``phase3_log.render_csv`` wrote) back into a
``Phase3StateSnapshot`` that ``build_traces`` can consume, plus a small
``SessionConfig`` describing the run for the config-mismatch caption.

The CSV carries everything needed: the ``#`` header comment holds the cue->side
mapping (``high_tone_is_left``), tone frequencies, seed, and generator; each data
row maps 1:1 back onto a ``Phase3RowView`` field (see ``phase3_log.COLUMNS``).
Return-cue usage and probe presence aren't in the header of older files, so they
are DERIVED from the rows (any played return cue / any ``probe=1`` trial) - the
same signal is derivable from a live snapshot's rows, so the mismatch compare
stays symmetric (see ``session_config_from_snapshot``).

Pure stdlib (``csv`` / ``io``); the only impurity is the best-effort file read in
:func:`snapshot_from_csv`. Headless tests: python -m unittest test_phase3_log_reader.
"""

from __future__ import annotations

import csv
import glob
import io
import os
from dataclasses import dataclass, fields
from typing import Dict, Iterable, List, Optional, Tuple

from tmaze.core.phase3.phase3_log import Phase3RowView, Phase3StateSnapshot
from tmaze.core.records import session_ignore
from tmaze.core.session import session_registry

# The data columns (everything on a row except the derived ``trial`` index), read
# off the public row view so this reader can't drift from the live schema.
_DATA_FIELDS = tuple(f.name for f in fields(Phase3RowView) if f.name != "trial")

# Row cells that stay a plain (possibly empty) string; every other column is an
# Optional[str] where an empty cell means "unrecorded" (None), matching the live
# ``_Row`` defaults so a reconstructed row behaves identically in build_traces.
_PLANNED_STR = ("correct_arm", "choice_cue", "return_cue_left", "return_cue_right")

# Return-cue "played" cells that mean NO real return tone sounded (so the session
# did not actually use return cues on that trial).
_NO_RETURN_CUE = (None, "", "NONE")


@dataclass(frozen=True)
class SessionConfig:
    """The handful of run settings the Metrics tab compares a reference session
    against the current one on (the config-mismatch caption; docs/decisions/0117).

    ``uses_return_cue`` and ``has_probe`` are DERIVED from the rows (not the
    header), so the same function builds a comparable config from a live snapshot.
    """
    high_tone_is_left: Optional[bool]
    generator: str
    high_tone_hz: Optional[int]
    low_tone_hz: Optional[int]
    uses_return_cue: bool
    has_probe: bool


# --------------------------------------------------------------- header parsing
def _parse_header(text: str) -> Dict[str, str]:
    """Fold the leading ``#`` comment lines into a flat ``key=value`` dict.

    Mirrors ``analysis.bundle_reader.parse_header_comments`` but stdlib-only (the
    analysis version imports pandas): every whitespace-separated ``key=value``
    token across the ``#`` lines becomes an entry (last write wins); ``# note:``
    prose is skipped; the first non-comment line ends the scan.
    """
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            break
        body = line.lstrip("#").strip()
        if body.lower().startswith("note:"):
            continue
        for tok in body.split():
            if "=" in tok:
                key, val = tok.split("=", 1)
                if key:
                    out[key] = val
    return out


def _to_bool(val: Optional[str]) -> Optional[bool]:
    if val is None:
        return None
    return val.strip().lower() in ("true", "1", "yes")


def _to_int(val: Optional[str]) -> Optional[int]:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- row mapping
def _row_view(record: Dict[str, str], trial: int) -> Phase3RowView:
    """Map one CSV record (column-name -> cell) back onto a ``Phase3RowView``.

    Empty cells become ``None`` (the live "unrecorded" value) except the planned
    cue columns, which stay strings; unknown columns are ignored and missing ones
    default (so an older file lacking e.g. ``probe`` reconstructs cleanly).
    """
    values: Dict[str, object] = {"trial": trial}
    for name in _DATA_FIELDS:
        cell = record.get(name)
        if cell is None or cell == "":
            values[name] = "" if name in _PLANNED_STR else None
        else:
            values[name] = cell
    return Phase3RowView(**values)  # type: ignore[arg-type]


def parse_csv_text(text: str) -> Tuple[Phase3StateSnapshot, SessionConfig]:
    """Reconstruct a ``(Phase3StateSnapshot, SessionConfig)`` from CSV text.

    The snapshot carries the header's cue mapping / tones / generator / seed and
    every data row as a ``Phase3RowView``; ``current_index``/``total`` point past
    the last row and ``armed`` is False (a finished, replayed session). It holds
    exactly what ``build_traces`` reads.
    """
    header = _parse_header(text)

    # Data rows: skip the '#' comment lines, then read the column-name header row.
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    rows: List[Phase3RowView] = []
    reader = csv.DictReader(io.StringIO(body))
    for i, record in enumerate(reader, start=1):
        trial = _to_int(record.get("trial")) or i
        rows.append(_row_view(record, trial))

    high_left = _to_bool(header.get("high_tone_is_left"))
    snapshot = Phase3StateSnapshot(
        rows=tuple(rows),
        current_index=len(rows),
        total=len(rows),
        armed=False,
        seed=_to_int(header.get("seed")) or 0,
        # build_traces treats a missing mapping as high=left (its own default).
        high_tone_is_left=True if high_left is None else high_left,
        high_tone_hz=_to_int(header.get("high_tone_hz")) or 0,
        low_tone_hz=_to_int(header.get("low_tone_hz")) or 0,
        generator=header.get("generator", "") or "",
    )
    return snapshot, session_config_from_snapshot(snapshot)


def snapshot_from_csv(path: str) -> Optional[Tuple[Phase3StateSnapshot, SessionConfig]]:
    """Read a recorded ``phase3_*.csv`` into ``(snapshot, SessionConfig)``, or
    None if the file can't be read. Best-effort file read; parsing is pure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    return parse_csv_text(text)


# --------------------------------------------------------------- config compare
# --------------------------------------------------------------- discovery
@dataclass(frozen=True)
class SessionRef:
    """A recorded past session available as a reference overlay: its id, animal,
    ISO date, a human ``label`` for the dropdown (``<animal> · <date> <time>``),
    and the path to its trial CSV. Lightweight - the CSV is only parsed when the
    operator actually picks it."""
    session_id: str
    animal: str
    date: str
    label: str
    csv_path: str


def _parse_session_id(session_id: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """``<animal>_<YYYYMMDD>_<HHMMSS>`` -> (animal, ISO date, ``HH:MM``), else
    (None, None, None). Mirrors ``session_registry._animal_date_from_id`` (the
    animal itself may contain ``_``, so split from the right)."""
    parts = session_id.rsplit("_", 2)
    if (len(parts) == 3 and len(parts[1]) == 8 and parts[1].isdigit()
            and len(parts[2]) >= 4 and parts[2][:4].isdigit()):
        ymd, hms = parts[1], parts[2]
        return (parts[0], f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}",
                f"{hms[0:2]}:{hms[2:4]}")
    return None, None, None


def _first_trial_csv(bundle_dir: str) -> Optional[str]:
    """The bundle's per-trial CSV (the first non-``_excursions`` file in
    ``task/``), or None. Matches ``bundle_reader._task_trial_csvs`` without the
    pandas dependency."""
    found = sorted(glob.glob(os.path.join(bundle_dir, "task", "*.csv")))
    for p in found:
        if not p.endswith("_excursions.csv"):
            return p
    return None


def discover_sessions(animal: str,
                      roots: Optional[Iterable[str]] = None,
                      ignore: Optional[Iterable[str]] = None) -> List[SessionRef]:
    """The given animal's real recorded sessions, newest-first, as ``SessionRef``s.

    Reuses ``session_registry.scan_roots`` over ``paths.phase3_recordings_roots()``
    (the primary folder + any archives; docs/decisions/0113) so a split history is
    fully discovered, and honours the ``session_ignore`` list (ADR-0097) so test /
    junk bundles never appear. Only real session BUNDLES are considered, so manual
    ``host/data/`` runs (not bundles) are naturally excluded. A blank ``animal``,
    or a bundle without a trial CSV, yields nothing for that entry. Best-effort:
    unreadable roots simply contribute no sessions.
    """
    want = str(animal or "").strip()
    if not want:
        return []
    if roots is None:
        from tmaze.paths import phase3_recordings_roots
        roots = [str(p) for p in phase3_recordings_roots()]
    skip = set(ignore) if ignore is not None else session_ignore.load_ignored()

    refs: List[SessionRef] = []
    for bundle in session_registry.scan_roots(roots):
        sid = os.path.basename(os.path.normpath(bundle))
        if sid in skip:
            continue
        a, d, hm = _parse_session_id(sid)
        if (a or "") != want:
            continue
        csv_path = _first_trial_csv(bundle)
        if not csv_path:
            continue
        # Prefix the animal id so the operator can visually confirm the ghost is
        # animal-matched (the list is already filtered to ``want``, so this is a
        # constant across entries - shown for reassurance, not disambiguation).
        label = f"{a} \u00b7 {d} {hm}" if d and hm else sid
        refs.append(SessionRef(session_id=sid, animal=a or want,
                               date=d or "", label=label, csv_path=csv_path))
    # session_id embeds a sortable timestamp; reverse gives newest-first.
    refs.sort(key=lambda r: r.session_id, reverse=True)
    return refs


# --------------------------------------------------------------- config compare
def session_config_from_snapshot(snapshot: Phase3StateSnapshot) -> SessionConfig:
    """Derive the comparable ``SessionConfig`` from a snapshot (live OR replayed).

    The cue mapping / generator / tones come straight off the snapshot; return-cue
    usage and probe presence are read from the rows so the SAME derivation applies
    to a live session and a reconstructed one (a symmetric compare). Note that on a
    freshly-armed live session (no completed trials yet) both derived flags read
    False until trials accumulate - the caption is informational, not a gate.
    """
    rows = snapshot.rows
    uses_return_cue = any(r.return_cue_played not in _NO_RETURN_CUE for r in rows)
    has_probe = any(r.probe == "1" for r in rows)
    return SessionConfig(
        high_tone_is_left=snapshot.high_tone_is_left,
        generator=snapshot.generator or "",
        high_tone_hz=snapshot.high_tone_hz or None,
        low_tone_hz=snapshot.low_tone_hz or None,
        uses_return_cue=uses_return_cue,
        has_probe=has_probe,
    )


def _cue_map(cfg: SessionConfig) -> str:
    if cfg.high_tone_is_left is None:
        return "?"
    return "HIGH=L" if cfg.high_tone_is_left else "HIGH=R"


def _tones(cfg: SessionConfig) -> str:
    hi = cfg.high_tone_hz if cfg.high_tone_hz else "?"
    lo = cfg.low_tone_hz if cfg.low_tone_hz else "?"
    return f"{hi}/{lo}Hz"


def describe_config_mismatch(current: SessionConfig,
                             reference: SessionConfig) -> List[str]:
    """Human-readable list of the run settings on which ``reference`` differs from
    ``current`` (empty when they match), for the Metrics tab's mismatch caption
    (docs/decisions/0117). Each entry reads ``current→reference`` so the operator
    can see how the ghost's conditions depart from the live run: cue->side mapping,
    generator, tone frequencies, return-cue usage, and probe presence.
    """
    diffs: List[str] = []
    if current.high_tone_is_left != reference.high_tone_is_left:
        diffs.append(f"cue map {_cue_map(current)}→{_cue_map(reference)}")
    if (current.generator or "") != (reference.generator or ""):
        diffs.append(f"generator {current.generator or '?'}→"
                     f"{reference.generator or '?'}")
    if (current.high_tone_hz, current.low_tone_hz) != (
            reference.high_tone_hz, reference.low_tone_hz):
        diffs.append(f"tones {_tones(current)}→{_tones(reference)}")
    if current.uses_return_cue != reference.uses_return_cue:
        diffs.append("return cue "
                     f"{'on' if current.uses_return_cue else 'off'}→"
                     f"{'on' if reference.uses_return_cue else 'off'}")
    if current.has_probe != reference.has_probe:
        diffs.append("probe "
                     f"{'present' if current.has_probe else 'absent'}→"
                     f"{'present' if reference.has_probe else 'absent'}")
    return diffs
