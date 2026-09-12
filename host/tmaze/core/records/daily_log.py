"""
daily_log.py - read the resources repo's daily husbandry table (pure core).

The resources repo owns the machine-readable daily husbandry table
`records/animal/daily_log.csv` (the source of truth defined in
docs/decisions/0076): one row per `(animal_id, date)` with weight, baseline,
% baseline, water on/off, water mL, supplemental water, and free-text notes,
behind a leading `# schema_version=N` comment line.

This core is the *code-repo* reader/writer for that file. It resolves the
resources repo via the shared sibling-path resolver (``animals.resources_root``,
ADR-0075), reads the CSV with stdlib ``csv`` (no new dependency), and answers the
two lookups the rig needs: the row for a given `(animal, date)` (WS2 prefill / WS4
exact join) and the most recent row for an animal on or before a date (to recover
a baseline when today's row hasn't been entered yet).

It also *writes* the table for the Records Manager GUI: :func:`write_rows`
serialises the same on-disk shape the resources ``build_daily_log.py`` writer uses
(a ``# schema_version`` comment line, then the header + one deduped, sorted row per
``(animal_id, date)``), and :func:`upsert_rows` merges GUI edits into the existing
rows by that key so a save never drops a row another surface (the Excel Entry
sheet) added. Writes are atomic (temp file + ``os.replace``). The CSV stays the
single source of truth; the Excel workbook is a reconciled, hand-editable fallback
(the plan's dual-surface model).

Every read is best-effort: a missing/renamed resources checkout or a malformed
file yields an empty table rather than raising, so Session Start still works
without the husbandry data (it just falls back to a Start dialog - see the
controller).

Pure stdlib (``csv`` / ``os``), no Tk/serial, headlessly testable.

Headless tests: python -m unittest test_daily_log.
"""

from __future__ import annotations

import csv
import datetime as _dt
import os
from typing import Dict, List, Optional

from tmaze.core.records import (
    NO_MTIME_CHECK,
    RecordsFileError,
    animals,
    check_header,
    check_unchanged,
    read_commented_csv,
)

# Column order mirrors the resources builder (records/animal/build_daily_log.py).
# ``water_regime`` (schema v2, ADR-0100), ``water_method`` (schema v3,
# ADR-0104) and the two supplemental-water cells (schema v4, ADR-0160) are
# appended LAST, in that order, so older readers that project onto their own
# FIELDS just ignore them and the column letters of the existing fields don't
# shift in the Excel Entry sheet.
FIELDS = [
    "animal_id", "date", "weight_g", "baseline_g", "pct_baseline",
    "water_on", "water_off", "water_ml", "supp_water", "notes",
    "water_regime", "water_method",
    "supp_water_ml", "supp_access_min",
]

# Bumped in lockstep with the resources builder's schema. v2 adds water_regime
# (docs/decisions/0076 + 0100); v3 adds water_method (docs/decisions/0104); v4
# adds supp_water_ml + supp_access_min (docs/decisions/0160). Readers stay
# backward-compatible: a row from an older file reads the missing columns as "".
SCHEMA_VERSION = 4

# Supplemental water (schema v4, ADR-0160). The legacy free-text ``supp_water``
# cell mixed two different facts - a VOLUME ("2.5 ml") and an ACCESS DURATION
# ("2 min", "30 min bottle") - so nothing downstream could add it up. v4 splits
# it into two NUMERIC cells: ``supp_water_ml`` (millilitres hand-given) and
# ``supp_access_min`` (minutes of free bottle access). The legacy column stays
# in the file, read-only in every writer, until a human has split each old
# cell (the records audit lists the ones still to do); readers never guess a
# split. A later v5 drops the legacy column.
SUPP_LEGACY_FIELD = "supp_water"
SUPP_FIELDS = ("supp_water_ml", "supp_access_min")


def supp_water_ml(row: Optional[Dict[str, str]]) -> Optional[float]:
    """The hand-given supplemental volume in mL from the v4 cell, or None when
    blank/non-numeric. The legacy ``supp_water`` text is deliberately NOT
    parsed: "2 min" is not 2 mL."""
    try:
        return float(str((row or {}).get("supp_water_ml", "") or "").strip())
    except ValueError:
        return None


def supp_access_min(row: Optional[Dict[str, str]]) -> Optional[float]:
    """Minutes of free bottle access from the v4 cell, or None."""
    try:
        return float(str((row or {}).get("supp_access_min", "") or "").strip())
    except ValueError:
        return None


def supp_unsplit(row: Optional[Dict[str, str]]) -> bool:
    """True when a row still carries a legacy ``supp_water`` text and NEITHER
    v4 cell is filled - i.e. the human split hasn't happened yet."""
    r = row or {}
    legacy = str(r.get(SUPP_LEGACY_FIELD, "") or "").strip()
    return bool(legacy) and not any(
        str(r.get(f, "") or "").strip() for f in SUPP_FIELDS)

# Water regime for a (animal, date): blank/"restriction" is the default
# (on water restriction), "ad_lib" marks an off-restriction day (typically a
# weekend when water is freely available). See docs/decisions/0100 (FW-0030).
REGIME_AD_LIB = "ad_lib"
_AD_LIB_TOKENS = {"ad_lib", "adlib"}
_RESTRICTION_TOKENS = {"", "restriction", "restrict", "normal"}


def _regime_token(value) -> str:
    """A regime cell folded to a comparison token (lowercased, -/space -> _)."""
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_regime(value) -> str:
    """Canonicalise a regime cell to ``"ad_lib"`` or ``""`` (restriction default).

    Loose so "ad-lib"/"Ad Lib"/"adlib" all resolve to ``ad_lib`` and any
    restriction synonym (or blank/unknown) collapses to ``""``; a save/export
    stores this normalised form so the column stays blank except on ad-lib days.
    """
    return REGIME_AD_LIB if _regime_token(value) in _AD_LIB_TOKENS else ""


def is_ad_lib(row: Optional[Dict[str, str]]) -> bool:
    """Whether a daily row marks an ad-lib (off-restriction) day (ADR-0100)."""
    return _regime_token((row or {}).get("water_regime")) in _AD_LIB_TOKENS


# Water METHOD (schema v3, ADR-0104): HOW restricted water was delivered that
# day. Orthogonal to water_regime (which says whether the day was on/off
# restriction) - a restriction day can be delivered by any of these; blank means
# unspecified. The three the lab uses:
#   - timed_access:   free water at the bottle for a set duration (e.g. 30 min)
#   - measured_bottle: a measured volume provided in a bottle
#   - task_earned:    water earned by running the task (reward only)
METHOD_TIMED_ACCESS = "timed_access"
METHOD_MEASURED_BOTTLE = "measured_bottle"
METHOD_TASK_EARNED = "task_earned"
# Canonical values, in display order (the GUI/Excel dropdowns render these).
WATER_METHODS = (METHOD_TIMED_ACCESS, METHOD_MEASURED_BOTTLE, METHOD_TASK_EARNED)

# Loose synonyms -> canonical, so hand-typed variants ("timed", "bottle",
# "task", "measured", spaces/hyphens) resolve rather than error.
_METHOD_TOKENS = {
    "timed_access": METHOD_TIMED_ACCESS, "timed": METHOD_TIMED_ACCESS,
    "timed_ad_lib": METHOD_TIMED_ACCESS, "timed_adlib": METHOD_TIMED_ACCESS,
    "measured_bottle": METHOD_MEASURED_BOTTLE, "measured": METHOD_MEASURED_BOTTLE,
    "bottle": METHOD_MEASURED_BOTTLE,
    "task_earned": METHOD_TASK_EARNED, "task": METHOD_TASK_EARNED,
    "task_water": METHOD_TASK_EARNED, "earned": METHOD_TASK_EARNED,
}


def _method_token(value) -> str:
    """A method cell folded to a comparison token (lowercased, -/space -> _)."""
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_method(value) -> str:
    """Canonicalise a water-method cell to one of :data:`WATER_METHODS`, or ``""``
    (unspecified) for blank/unknown. Loose, mirroring :func:`normalize_regime`."""
    return _METHOD_TOKENS.get(_method_token(value), "")

# The two columns that identify a row; edits/writes dedup + sort on this key.
KEY_FIELDS = ("animal_id", "date")


def default_daily_log_path() -> str:
    """Path to `records/animal/daily_log.csv` in the sibling resources repo."""
    return os.path.join(animals.resources_root(), "records", "animal",
                        "daily_log.csv")


def load_rows(path: Optional[str] = None) -> List[Dict[str, str]]:
    """Read the daily table best-effort; [] if it's missing/malformed.

    Skips the leading ``# schema_version`` comment line(s), then parses the
    header + rows with ``csv`` (``read_commented_csv``; a ``#`` inside a quoted
    notes cell is data, not a comment). Rows are projected onto FIELDS by
    column NAME, so an older file lacking the appended columns reads them as "".
    """
    p = path or default_daily_log_path()
    try:
        parsed = read_commented_csv(p)
    except (OSError, ValueError):
        return []
    return [{k: row.get(k, "") for k in FIELDS} for row in parsed.rows]


class DailyLog:
    """The daily husbandry rows, with the two lookups the rig uses.

    Single-threaded like the other cores. ``available`` is False for an empty
    table (missing file), so the caller can fall back rather than treat a blank
    lookup as "no data for this animal today".
    """

    def __init__(self, rows: Optional[List[Dict[str, str]]] = None) -> None:
        self._rows: List[Dict[str, str]] = list(rows or [])

    @property
    def available(self) -> bool:
        return bool(self._rows)

    def __len__(self) -> int:
        return len(self._rows)

    def rows_for(self, animal_id: str) -> List[Dict[str, str]]:
        """Every row for an animal, oldest first (ISO dates sort chronologically).

        Used by the welfare dashboard's weight/% baseline trend (WS4)."""
        aid = str(animal_id).strip()
        out = [dict(r) for r in self._rows if r.get("animal_id") == aid]
        out.sort(key=lambda r: r.get("date", ""))
        return out

    def for_date(self, animal_id: str, date: str) -> Optional[Dict[str, str]]:
        """The row for an exact `(animal_id, date)`, or None."""
        aid = str(animal_id).strip()
        d = str(date).strip()
        for row in self._rows:
            if row.get("animal_id") == aid and row.get("date") == d:
                return dict(row)
        return None

    def latest_for(self, animal_id: str,
                   on_or_before: Optional[str] = None,
                   having: Optional[str] = None) -> Optional[Dict[str, str]]:
        """The most recent row for an animal (optionally on/before a date), or None.

        Dates are ISO ``YYYY-MM-DD`` so a lexicographic ``max`` is chronological.
        ``having`` names a column that must be NON-BLANK for a row to count, so
        "the latest baseline" (``having="baseline_g"``) skips a newer row whose
        baseline cell was left empty instead of returning it and wiping the
        prefill (ADR-0155). Used to recover a baseline when today's row hasn't
        been entered yet.
        """
        aid = str(animal_id).strip()
        cutoff = str(on_or_before).strip() if on_or_before else None
        best = None
        for row in self._rows:
            if row.get("animal_id") != aid:
                continue
            d = row.get("date", "")
            if not d or (cutoff is not None and d > cutoff):
                continue
            if having and not str(row.get(having, "") or "").strip():
                continue
            if best is None or d > best.get("date", ""):
                best = row
        return dict(best) if best is not None else None


def load(path: Optional[str] = None) -> DailyLog:
    """Load the daily husbandry table best-effort (default: the resources repo)."""
    return DailyLog(load_rows(path))


def load_rows_for_write(path: Optional[str] = None) -> List[Dict[str, str]]:
    """Like :func:`load_rows`, but distinguish "absent" from "unreadable".

    The Save path merges edits into the *current* file, so it must not treat an
    unreadable-but-present file as empty (that would overwrite it with only the
    edited row - see :class:`RecordsFileError` / bug #260810_RecordsIntegrity).

    - File absent -> ``[]`` (creating a fresh file is safe).
    - File present but it can't be opened/decoded -> raise
      :class:`RecordsFileError` so the caller can abort before clobbering it.
    - File present but its header isn't FIELDS (or an older prefix of it), or
      its ``schema_version`` is newer than this code -> raise
      :class:`RecordsFileError` (``check_header``; ADR-0155). Without this a
      renamed column read as blank and ONE save blanked it for every row.
    """
    p = path or default_daily_log_path()
    if not os.path.exists(p):
        return []
    try:
        parsed = read_commented_csv(p)
    except (OSError, ValueError) as exc:
        raise RecordsFileError(
            f"{p} exists but could not be read ({exc}); refusing to overwrite "
            "it, which would drop the rows already in the file.") from exc
    problems = check_header(parsed.header, FIELDS, parsed.schema_version,
                            SCHEMA_VERSION)
    if problems:
        raise RecordsFileError(
            f"{p} is not safe to save into: " + " ".join(problems)
            + " Fix the file by hand (or update the software) first.")
    return [{k: row.get(k, "") for k in FIELDS} for row in parsed.rows]


# --------------------------------------------------------------------------- #
# Writing (Records Manager GUI). The CSV is the source of truth; these keep it
# in the exact shape the resources builder emits so either surface can rebuild
# the Excel workbook from it.
# --------------------------------------------------------------------------- #

def _key(row: Dict[str, str]) -> tuple:
    """The `(animal_id, date)` identity of a row, whitespace-normalised."""
    return tuple(str(row.get(f, "") or "").strip() for f in KEY_FIELDS)


def _normalise(row: Dict[str, str]) -> Dict[str, str]:
    """A row projected onto FIELDS as trimmed strings (None -> "")."""
    return {f: str(row.get(f, "") or "").strip() for f in FIELDS}


def _with_recomputed_pct(row: Dict[str, str]) -> Dict[str, str]:
    """A normalised row whose ``pct_baseline`` mirror is recomputed from its
    weight/baseline (blank when not computable).

    Weight/baseline are the source of truth and ``pct_baseline`` is a derived
    mirror, so every persisted row rewrites it here rather than trusting whatever
    the caller passed. That keeps the stored column in step with the reader
    (:func:`dashboard.pct_baseline_of`, which also recomputes first), so the two
    surfaces can't drift. See docs/decisions/0163.
    """
    out = _normalise(row)
    out["pct_baseline"] = pct_str(out.get("weight_g"), out.get("baseline_g"))
    return out


def row_has_weight(row: Optional[Dict[str, str]]) -> bool:
    """True if a daily row carries a usable (numeric) weight.

    Session Start only prefills the frozen ``animal_state`` from the daily table
    when the row actually has a weight; a present-but-incomplete row (blank or
    non-numeric ``weight_g``) should fall through to the Start weight dialog
    rather than freeze an empty ``pre_session_weight_g`` (#4). Baseline is still
    recoverable separately via :meth:`DailyLog.latest_for`.
    """
    if not row:
        return False
    try:
        float(str(row.get("weight_g", "")).strip())
        return True
    except (TypeError, ValueError):
        return False


def _is_iso_date(value: str) -> bool:
    """True if ``value`` is a real ``YYYY-MM-DD`` date (the row-key format)."""
    try:
        _dt.date.fromisoformat(str(value).strip())
        return True
    except (ValueError, TypeError):
        return False


# The numeric daily-entry cells a save/import must not accept as garbage. These
# are the fields analysis joins/aggregates on (weight, baseline, cage water mL,
# the v4 supplemental mL/minutes); the legacy supp_water and water_on/off stay
# free text (hardening -> FW-0029).
NUMERIC_ENTRY_FIELDS = ("weight_g", "baseline_g", "water_ml",
                        "supp_water_ml", "supp_access_min")


def validate_entry(row: Dict[str, str]) -> List[str]:
    """Human-readable HARD errors for one daily row ([] means OK to write).

    Blank numeric cells are allowed - an incomplete row is legitimate (e.g. a
    weight is logged before the day's water is turned off). But a *non-blank*
    weight / baseline / water mL must parse as a number >= 0, and a present date
    must be ISO ``YYYY-MM-DD`` (the row key), so a typo can't mint a mis-keyed or
    non-numeric row on either surface (the Records Manager Save or the Excel
    ``--export``; Phase B). Soft, warn-but-allow checks (an id off the roster,
    % baseline < 90) are the caller's policy, not errors here.
    """
    errors: List[str] = []
    if not str(row.get("animal_id", "")).strip():
        errors.append("Animal id is required.")
    date = str(row.get("date", "")).strip()
    if not date:
        errors.append("Date is required.")
    elif not _is_iso_date(date):
        errors.append(f"Date '{date}' must be in YYYY-MM-DD form.")
    labels = {"weight_g": "Weight (g)", "baseline_g": "Baseline (g)",
              "water_ml": "Water (mL)", "supp_water_ml": "Supp. water (mL)",
              "supp_access_min": "Supp. access (min)"}
    for field in NUMERIC_ENTRY_FIELDS:
        raw = str(row.get(field, "")).strip()
        if not raw:
            continue
        try:
            if float(raw) < 0:
                errors.append(f"{labels[field]} can't be negative ({raw}).")
        except ValueError:
            errors.append(f"{labels[field]} must be a number, not '{raw}'.")
    regime = str(row.get("water_regime", "")).strip()
    if (regime and _regime_token(regime) not in _AD_LIB_TOKENS
            and _regime_token(regime) not in _RESTRICTION_TOKENS):
        errors.append(
            f"Water regime '{regime}' must be blank/'restriction' or 'ad_lib'.")
    method = str(row.get("water_method", "")).strip()
    if method and normalize_method(method) == "":
        errors.append(
            f"Water method '{method}' must be blank or one of: "
            + ", ".join(WATER_METHODS) + ".")
    return errors


def compute_pct_baseline(weight, baseline) -> Optional[float]:
    """weight / baseline as a fraction rounded to 4 dp, or None if not computable.

    Mirrors the Entry sheet's ``% baseline`` formula so the GUI can fill the
    column the same way the workbook does. Blank/zero/non-numeric -> None (the
    caller writes "" rather than a bogus number).
    """
    try:
        w = float(weight)
        b = float(baseline)
    except (TypeError, ValueError):
        return None
    if b == 0:
        return None
    return round(w / b, 4)


def pct_str(weight, baseline) -> str:
    """The stored ``pct_baseline`` mirror string for a weight/baseline pair.

    The single formatter every writer uses so the on-disk mirror is byte-for-byte
    consistent: a 4-dp fraction, or "" when not computable (docs/decisions/0163).
    """
    pct = compute_pct_baseline(weight, baseline)
    return "" if pct is None else f"{pct:.4f}"


def upsert_rows(existing: List[Dict[str, str]],
                edits: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Merge ``edits`` into ``existing`` by `(animal_id, date)`, edits winning.

    A GUI save reads the current CSV (``existing``) and applies only the rows the
    user touched (``edits``) - a whole-row replace per key, never a blind
    overwrite of the file - so rows added by another surface (the Excel Entry
    sheet) survive. Returns a fresh list; input lists are not mutated. Ordering
    is irrelevant here because :func:`write_rows` sorts.
    """
    by_key: Dict[tuple, Dict[str, str]] = {}
    for row in existing:
        by_key[_key(row)] = _normalise(row)
    for row in edits:
        by_key[_key(row)] = _normalise(row)
    return list(by_key.values())


def write_rows(rows: List[Dict[str, str]],
               path: Optional[str] = None,
               expect_mtime=NO_MTIME_CHECK) -> List[Dict[str, str]]:
    """Write the daily table atomically; returns the deduped, sorted rows written.

    On-disk shape matches the resources builder: a ``# schema_version=N`` comment
    line, the FIELDS header, then one row per `(animal_id, date)` (last-wins on
    duplicates) sorted by that key. ISO dates make the string sort chronological.
    Writes to a temp file then ``os.replace`` so a crash can't leave a half-file.
    Lines end in ``\\n`` (the repo's stored form), not the csv default ``\\r\\n``.

    ``expect_mtime``: the :func:`tmaze.core.records.file_mtime` of the file when
    the caller read it (None if it was absent). If the file has changed since,
    raise :class:`RecordsFileError` without writing, so a save can't overwrite
    an edit made by another surface in the meantime (ADR-0155). The default
    skips the check.
    """
    p = path or default_daily_log_path()
    check_unchanged(p, expect_mtime)
    deduped: Dict[tuple, Dict[str, str]] = {}
    for row in rows:
        deduped[_key(row)] = _with_recomputed_pct(row)
    ordered = sorted(deduped.values(), key=_key)

    parent = os.path.dirname(os.path.abspath(p))
    os.makedirs(parent, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# schema_version={SCHEMA_VERSION}\n")
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in ordered:
            writer.writerow(row)
    os.replace(tmp, p)
    return ordered
