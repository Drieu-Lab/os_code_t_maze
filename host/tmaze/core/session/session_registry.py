"""
session_registry.py - the cross-session index / analysis entry point (pure core).

Each Phase 3 recording is a self-describing bundle (manifest + summary +
animal_state + session_meta + notes), but answering "how did rat 600 do across
the last two weeks, and which sessions had a non-stem-dissuasion intervention?"
still means opening dozens of folders by hand. This core folds every bundle under
the recordings root into ONE tidy table - ``session_registry.csv`` - so sessions
become queryable/filterable in a spreadsheet or pandas: one row per session with
its husbandry context, behaviour, the four water channels, and per-tag
intervention tallies.

Design (docs/decisions/0081):

- **Full, idempotent rebuild.** Re-scan all bundles every run (never append), so
  re-running never double-counts. The thin ``build_session_registry.py`` shell
  resolves the recordings root + the note-tag vocabulary and writes the files;
  this core does the reading/joining/formatting and is headlessly testable.

- **Prefer the bundle's frozen state over re-joining by date.** Read the
  husbandry scalars straight from ``config/animal_state.json`` (ADR-0077); only
  fall back to a ``(animal, date)`` join against the daily table (ADR-0076) for
  older bundles that lack it. This retires the fragile date half of the key for
  new sessions and is robust to later edits of ``daily_log.csv``.

- **Four water channels, not one.** Surface ``task_reward_ml`` (auto rewards, from
  the summary's ``estimated_water``), ``manual_reward_ml`` (operator GUI-button
  reward dispenses, from the summary's ``manual_water``; ADR-0084, supersedes
  0078), the supplemental top-up from the daily table (legacy free-text
  ``supp_water`` plus its v3/v4 numeric split ``supp_water_ml`` /
  ``supp_access_min``; docs/decisions/0160) and the restriction span
  ``water_on``/``water_off`` as SEPARATE, provenance-labelled columns so the
  "accounted several ways" water reconciles in one row. Task and operator reward
  are disjoint, so those two add up; the rest are distinct figures.

- **Stable tag output.** Because the note-tag vocabulary (``note_tags.json``,
  ADR-0074) is user-editable, "one wide column per tag" would make the schema
  non-stationary across rebuilds. So the churn-proof form is the tidy long file
  ``session_registry_tags.csv`` (``session_id, tag, count``); the wide ``tag_*``
  columns in ``session_registry.csv`` are derived from the *current* vocabulary at
  build time (a convenience view over the tidy truth).

- **Degrades gracefully.** A bundle missing ``animal_state.json`` or an unmatched
  daily-table row leaves those columns blank rather than failing; the daily-table
  join is skipped entirely when the table is absent.

The behaviour columns are NOT recomputed here: accuracy / per-cue / side bias come
from the summary's ``cued_task`` block (``phase3_stats``, ADR-0052) and the
excursion/compliance columns from its ``locomotion`` block (``behavior_stats``,
ADR-0032, mapped per ADR-0080). This module only selects, joins, labels, and
serialises what the bundles already hold.

Schema v2 (docs/decisions/0157) makes a build SELF-DESCRIBING and REFUSABLE:

- **Provenance header.** Besides ``# schema_version``, the CSV opens with
  ``# built_at=``, ``# code_commit=``, ``# roots=`` (JSON list) and one
  ``# <input>_sha=`` line per joined input (daily log, tag vocabulary, ignore
  list), so anyone holding the file can tell which folders were scanned and
  which inputs shaped it (:class:`BuildMeta`).
- **Root-relative ``bundle`` + a ``root`` column.** A history split across
  drives (docs/decisions/0113) is recorded as (which root, which folder under
  it) rather than one absolute path; :func:`bundle_path` re-joins them (and
  still understands a v1 absolute ``bundle``).
- **Shrink check.** :func:`shrink_check` compares the previous build's session
  ids with the new ones so a build that would LOSE sessions (a drive not
  mounted, a mistyped root) can be refused instead of silently accepted.

Pure stdlib (``json`` / ``csv`` / ``glob`` / ``os``), no Tk/serial; the file
reads are all best-effort. See docs/records-schema.md for the column reference.

Headless tests: python -m unittest test_session_registry.
"""

from __future__ import annotations

import csv
import glob
import io
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from tmaze.core.records import parse_commented_csv
from tmaze.core.session import session_notes

# Bump when the registry CSV column set / semantics change so an offline reader
# can branch on it (written as a leading `# schema_version=N` comment line, the
# same convention as daily_log.csv; docs/decisions/0076/0081).
# v2 (docs/decisions/0157): provenance header lines; `bundle` is root-relative
# and a `root` column is appended.
# v3 (docs/decisions/0160): `supp_water_ml` + `supp_access_min` inserted after
# the legacy free-text `supp_water` (the daily table's v4 split cells).
# v4 (docs/decisions/0188): `sync_method` + `sync_residual_ms` appended (each
# session's video<->firmware clock-fit status, from the LED-fit pipeline).
SCHEMA_VERSION = 4

# The fixed (non-tag) columns of session_registry.csv, in output order. The
# per-tag wide columns (tag_<name>) are appended from the current vocabulary at
# render time. Rates/fractions stay 0..1 (a reader converts to % for humans);
# see docs/records-schema.md.
REGISTRY_COLUMNS: Tuple[str, ...] = (
    "session_id", "animal", "date", "start_iso", "duration_s",
    # husbandry (preferred from the bundle's animal_state, else a daily join)
    "husbandry_source", "weight_g", "baseline_g", "pct_baseline",
    "hours_since_water",
    # provenance-labelled water channels (never summed). `supp_water` is the
    # legacy free-text cell; `supp_water_ml` / `supp_access_min` are its v4
    # numeric split (docs/decisions/0160).
    "task_reward_ml", "manual_reward_ml", "supp_water", "supp_water_ml",
    "supp_access_min", "water_on", "water_off",
    # cued-task behaviour (from the summary's phase3_stats block)
    "trials_total", "completed", "correct", "incorrect",
    "accuracy_cum", "high_accuracy", "low_accuracy",
    "side_bias_left", "side_bias_right", "reward_rate", "seed",
    "high_tone_hz", "low_tone_hz", "high_tone_is_left",
    # excursions / compliance (locomotion block; ADR-0080 mapping)
    "total_excursions", "stem_choices", "nonstem_choices", "stem_compliance",
    # sticky session metadata (ADR-0079)
    "trainer", "stage", "stage_source", "reward_type",
    # cross-stream clock sync (v4; docs/decisions/0188). `sync_method` is
    # `led-fit` (a usable video<->firmware fit was produced), `failed` (fiducials
    # seen but no usable fit - actionable, flagged by records_check), or `host-t0`
    # (never fit / no LED fiducials in frame). `sync_residual_ms` is the fit's
    # residual spread (MAD, ms) when `led-fit`, else blank.
    "sync_method", "sync_residual_ms",
    # provenance: the bundle folder RELATIVE to its recordings root (v2; was an
    # absolute path in v1) + the root it was found under
    "bundle", "root",
)

# sync_method values (docs/decisions/0188).
SYNC_LED_FIT = "led-fit"
SYNC_FAILED = "failed"
SYNC_HOST_T0 = "host-t0"

TAGS_COLUMNS: Tuple[str, ...] = ("session_id", "tag", "count")


# --------------------------------------------------------------- build meta
@dataclass
class BuildMeta:
    """What a registry build was made FROM, written as ``# key=value`` header
    lines (after ``# schema_version``) so the CSV describes itself
    (docs/decisions/0157).

    ``built_at`` is local ISO time; ``code_commit`` the code repo's short sha
    (or ``unknown``); ``roots`` the folders scanned, in scan order; ``inputs``
    maps an input name (``daily_log`` / ``note_tags`` / ``session_ignore``) to a
    short content hash, or ``absent`` when that file was not found. The shell
    gathers these; this class only formats/parses them.
    """
    built_at: str = ""
    code_commit: str = "unknown"
    roots: List[str] = field(default_factory=list)
    inputs: Dict[str, str] = field(default_factory=dict)

    def comment_lines(self) -> List[str]:
        lines = [f"# built_at={self.built_at}",
                 f"# code_commit={self.code_commit or 'unknown'}",
                 "# roots=" + json.dumps(list(self.roots))]
        for name in sorted(self.inputs):
            lines.append(f"# {name}_sha={self.inputs[name] or 'absent'}")
        return lines

    @classmethod
    def from_comments(cls, comments: Iterable[str]) -> "BuildMeta":
        """Parse the ``# key=value`` lines back (unknown keys ignored; a missing
        or malformed ``roots`` reads as [])."""
        meta = cls()
        for raw in comments:
            body = raw.lstrip("#").strip()
            if "=" not in body:
                continue
            key, val = body.split("=", 1)
            key, val = key.strip(), val.strip()
            if key == "built_at":
                meta.built_at = val
            elif key == "code_commit":
                meta.code_commit = val or "unknown"
            elif key == "roots":
                try:
                    parsed = json.loads(val)
                except ValueError:
                    parsed = []
                meta.roots = [str(r) for r in parsed] if isinstance(
                    parsed, list) else []
            elif key.endswith("_sha"):
                meta.inputs[key[:-4]] = val
        return meta


# --------------------------------------------------------------- shrink check
@dataclass
class ShrinkReport:
    """Result of :func:`shrink_check`: which session ids the new build would
    DROP relative to the previous one (``vanished``) and which it adds."""
    vanished: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing vanishes (growth or no change is always fine)."""
        return not self.vanished


def shrink_check(previous_ids: Iterable[str],
                 new_ids: Iterable[str]) -> ShrinkReport:
    """Compare two builds' session-id sets. A registry is a FULL rebuild, so a
    session missing from the new scan means its folder is gone or unreachable
    (an unmounted archive drive, a mistyped root) - almost never intended. The
    caller decides whether to refuse (docs/decisions/0157)."""
    prev = {s.strip() for s in previous_ids if s and s.strip()}
    new = {s.strip() for s in new_ids if s and s.strip()}
    return ShrinkReport(vanished=sorted(prev - new), added=sorted(new - prev))


def unindexed_sessions(scanned_ids: Iterable[str], registry_ids: Iterable[str],
                       ignore: Optional[Iterable[str]] = None) -> List[str]:
    """Session ids present on disk (``scanned_ids``) but absent from the
    registry (``registry_ids``), excluding the ignore-list - i.e. the bundles
    a rebuild would ADD. Feeds the Records Manager's "N bundles not yet
    indexed" staleness hint (docs/decisions/0158)."""
    have = {s.strip() for s in registry_ids if s and s.strip()}
    skip = {s.strip() for s in (ignore or ()) if s and s.strip()}
    return sorted({s.strip() for s in scanned_ids if s and s.strip()}
                  - have - skip)


def registry_ids(text: str) -> List[str]:
    """The ``session_id`` column of a rendered registry CSV (any schema
    version), in file order; [] for empty/malformed text."""
    try:
        parsed = parse_commented_csv(text)
    except ValueError:
        return []
    return [r.get("session_id", "") for r in parsed.rows if r.get("session_id")]


def registry_meta(text: str) -> Tuple[Optional[int], BuildMeta]:
    """(schema_version, BuildMeta) from a registry CSV's header comments."""
    try:
        parsed = parse_commented_csv(text)
    except ValueError:
        return None, BuildMeta()
    return parsed.schema_version, BuildMeta.from_comments(parsed.comments)


# --------------------------------------------------------------- file reads
def _read_json(path: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse a JSON object best-effort; None if absent/malformed/not an object."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _read_text(path: Optional[str]) -> str:
    """Read a text file best-effort; "" if absent/unreadable."""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _find_one(bundle_dir: str, subdir: str, pattern: str) -> Optional[str]:
    """The first path matching ``bundle_dir/subdir/pattern`` (sorted), or None."""
    matches = sorted(glob.glob(os.path.join(bundle_dir, subdir, pattern)))
    return matches[0] if matches else None


# --------------------------------------------------------------- discovery
def is_bundle(path: str) -> bool:
    """Whether ``path`` is a session bundle: a directory carrying its manifest
    (``session_<name>.json``, written at Start; docs/decisions/0049)."""
    if not os.path.isdir(path):
        return False
    name = os.path.basename(os.path.normpath(path))
    return os.path.isfile(os.path.join(path, f"session_{name}.json"))


def scan(root: str) -> List[str]:
    """Every session bundle directly under ``root`` (sorted by session_id).

    Best-effort: a missing/unreadable root yields []. Non-bundle entries (stray
    files, the registry CSVs themselves) are skipped by :func:`is_bundle`."""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    return [os.path.join(root, e) for e in entries
            if is_bundle(os.path.join(root, e))]


def scan_roots_with_root(roots: Iterable[str]) -> List[Tuple[str, str]]:
    """Every session bundle across several recordings roots as ``(root,
    bundle_dir)`` pairs, de-duplicated by session_id (the bundle folder name)
    with the FIRST listed root winning.

    A recordings history split across drives (the rig's disk plus one or more
    archive locations; docs/decisions/0113) is scanned as one. A session that
    momentarily exists in two roots - e.g. mid-copy while being archived - is
    counted once, keeping the copy in the earliest-listed root so an incomplete
    destination copy can't shadow the source. Order within a root is preserved
    (sorted by session_id); across roots, the first root's bundles come first."""
    out: List[Tuple[str, str]] = []
    seen: set = set()
    for root in roots:
        for bundle in scan(root):
            sid = _session_id_of(bundle)
            if sid in seen:
                continue
            seen.add(sid)
            out.append((root, bundle))
    return out


def scan_roots(roots: Iterable[str]) -> List[str]:
    """:func:`scan_roots_with_root` without the root: just the bundle paths."""
    return [b for _root, b in scan_roots_with_root(roots)]


def bundle_path(row: Dict[str, Any]) -> str:
    """The bundle folder's full path from a registry row: ``root`` joined with
    the root-relative ``bundle`` (v2), or ``bundle`` as-is when it is already
    absolute / no ``root`` is present (v1 files). "" when the row has none."""
    bundle = str(row.get("bundle") or "").strip()
    root = str(row.get("root") or "").strip()
    if not bundle:
        return ""
    if root and not os.path.isabs(bundle):
        return os.path.normpath(os.path.join(root, bundle))
    return bundle


# --------------------------------------------------------------- one bundle
def _session_id_of(bundle_dir: str) -> str:
    return os.path.basename(os.path.normpath(bundle_dir))


def _animal_date_from_id(session_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse ``<animal>_<YYYYMMDD>_<HHMMSS>`` into (animal, ISO date), or
    (None, None) if it doesn't match (the animal may itself contain ``_``)."""
    parts = session_id.rsplit("_", 2)
    if len(parts) == 3 and len(parts[1]) == 8 and parts[1].isdigit():
        ymd = parts[1]
        return parts[0], f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return None, None


def _ml_from_ul(ul: Any) -> Optional[float]:
    """Microliters -> milliliters (rounded), or None when absent."""
    try:
        return round(float(ul) / 1000.0, 3)
    except (TypeError, ValueError):
        return None


def bundle_row(bundle_dir: str, daily: Optional[Any] = None,
               root: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Fold one bundle into (base-column dict, per-tag count dict).

    ``daily`` is an optional ``daily_log.DailyLog`` used only as the fallback join
    for older bundles without ``animal_state.json`` (and always for the
    post-session ``supp_water``). ``root`` is the recordings root the bundle was
    found under: when given, ``bundle`` is written RELATIVE to it and ``root``
    holds its absolute path (v2); without it ``bundle`` is the absolute path and
    ``root`` blank. Every read is best-effort; missing inputs leave the
    corresponding columns blank."""
    session_id = _session_id_of(bundle_dir)
    summary = _read_json(_find_one(bundle_dir, "summary",
                                   "session_summary_*.json")) or {}
    astate = _read_json(os.path.join(bundle_dir, "config", "animal_state.json"))
    meta = _read_json(os.path.join(bundle_dir, "config",
                                   "session_meta.json")) or {}
    notes_text = _read_text(_find_one(bundle_dir, "summary", "notes_*.jsonl"))

    id_animal, id_date = _animal_date_from_id(session_id)
    animal = (astate or {}).get("animal") or summary.get("animal") or id_animal or ""
    date = (astate or {}).get("date") or id_date or ""

    row_daily = None
    if daily is not None and animal and date:
        try:
            row_daily = daily.for_date(animal, date)
        except Exception:
            row_daily = None

    row: Dict[str, Any] = {c: None for c in REGISTRY_COLUMNS}
    row["session_id"] = session_id
    row["animal"] = animal
    row["date"] = date
    if root:
        row["root"] = os.path.abspath(root)
        row["bundle"] = os.path.relpath(os.path.abspath(bundle_dir), row["root"])
    else:
        row["root"] = None
        row["bundle"] = os.path.abspath(bundle_dir)

    t0 = summary.get("t0") or {}
    row["start_iso"] = t0.get("iso")
    row["duration_s"] = summary.get("duration_s")

    _fill_husbandry(row, astate, row_daily)

    # Four water channels (never summed).
    est = summary.get("estimated_water") or {}
    man = summary.get("manual_water") or {}
    row["task_reward_ml"] = _ml_from_ul(est.get("ul"))
    row["manual_reward_ml"] = _ml_from_ul(man.get("ul"))
    row["supp_water"] = (row_daily or {}).get("supp_water") or None
    row["supp_water_ml"] = (row_daily or {}).get("supp_water_ml") or None
    row["supp_access_min"] = (row_daily or {}).get("supp_access_min") or None

    _fill_cued(row, summary)
    _fill_locomotion(row, summary.get("locomotion") or {})
    _fill_sync(row, bundle_dir)

    row["trainer"] = meta.get("trainer")
    row["stage"] = meta.get("stage")
    row["stage_source"] = meta.get("stage_source")
    row["reward_type"] = meta.get("reward_type")

    return row, _tally_tags(notes_text)


def _fill_husbandry(row: Dict[str, Any], astate: Optional[Dict[str, Any]],
                    row_daily: Optional[Dict[str, str]]) -> None:
    """Prefer the bundle's frozen animal_state; else the daily-table join; else
    leave blank. ``supp_water`` is filled separately (it is post-session)."""
    if astate:
        row["husbandry_source"] = astate.get("source") or "bundle"
        row["weight_g"] = astate.get("pre_session_weight_g")
        row["baseline_g"] = astate.get("baseline_g")
        row["pct_baseline"] = astate.get("pct_baseline")
        row["hours_since_water"] = astate.get("hours_since_water")
        # Water span: the frozen value, backfilled from the daily row if the
        # bundle didn't capture it (start_dialog/manual sources).
        row["water_on"] = astate.get("last_water_on") or (
            (row_daily or {}).get("water_on") or None)
        row["water_off"] = astate.get("last_water_off") or (
            (row_daily or {}).get("water_off") or None)
    elif row_daily:
        row["husbandry_source"] = "daily_table_join"
        row["weight_g"] = row_daily.get("weight_g") or None
        row["baseline_g"] = row_daily.get("baseline_g") or None
        row["pct_baseline"] = row_daily.get("pct_baseline") or None
        row["water_on"] = row_daily.get("water_on") or None
        row["water_off"] = row_daily.get("water_off") or None
    else:
        row["husbandry_source"] = "none"


def _fill_cued(row: Dict[str, Any], summary: Dict[str, Any]) -> None:
    """Cued-task columns from the summary's phase3_stats block (verbatim)."""
    cued = summary.get("cued_task")
    row["seed"] = summary.get("seed")
    tones = summary.get("tones") or {}
    row["high_tone_hz"] = tones.get("high_tone_hz")
    row["low_tone_hz"] = tones.get("low_tone_hz")
    row["high_tone_is_left"] = tones.get("high_tone_is_left")
    if not cued:
        return
    row["trials_total"] = cued.get("total")
    row["completed"] = cued.get("completed")
    row["correct"] = cued.get("correct")
    row["incorrect"] = cued.get("incorrect")
    row["accuracy_cum"] = cued.get("accuracy_cum")
    row["high_accuracy"] = cued.get("high_accuracy")
    row["low_accuracy"] = cued.get("low_accuracy")
    row["reward_rate"] = cued.get("reward_rate")
    lr = cued.get("lr_bias")
    if isinstance(lr, (list, tuple)) and len(lr) == 2:
        row["side_bias_left"], row["side_bias_right"] = lr[0], lr[1]


def _fill_locomotion(row: Dict[str, Any], loco: Dict[str, Any]) -> None:
    """Excursion/compliance columns from the locomotion block (ADR-0080 mapping:
    total_choices -> total_excursions, compliance_cum -> stem_compliance)."""
    row["total_excursions"] = loco.get("total_choices")
    row["stem_choices"] = loco.get("stem_choices")
    row["nonstem_choices"] = loco.get("nonstem_choices")
    row["stem_compliance"] = loco.get("compliance_cum")


def _fill_sync(row: Dict[str, Any], bundle_dir: str) -> None:
    """Cross-stream clock-sync columns from the bundle's LED clock-fit artifact
    (``sync/clock_fit_<id>.json``; docs/decisions/0188). Derived from the fit's
    OWN numbers (not the manifest's ``fiducial.method``, which older bundles
    flipped to led-fit even on a failed fit): a present fit with the OK verdict is
    ``led-fit`` (residual = its MAD in ms); a present-but-unusable fit is
    ``failed``; no clock-fit file at all is ``host-t0``. All reads best-effort."""
    row["sync_method"] = SYNC_HOST_T0
    fit_doc = _read_json(_find_one(bundle_dir, "sync", "clock_fit_*.json"))
    if not fit_doc:
        return
    fit = fit_doc.get("fit")
    if isinstance(fit, dict) and fit_doc.get("verdict") == "ok":
        row["sync_method"] = SYNC_LED_FIT
        resid = fit.get("residual_ms")
        if isinstance(resid, dict):
            row["sync_residual_ms"] = resid.get("mad_ms")
    else:
        row["sync_method"] = SYNC_FAILED


def _tally_tags(notes_text: str) -> Dict[str, int]:
    """Per-tag totals for a session's notes JSONL (sums each tagged entry's
    ``count`` so a counter tag reads as its running total; ADR-0074)."""
    counts: Dict[str, int] = {}
    log = session_notes.parse_jsonl(notes_text or "")
    for e in log.entries():
        if e.tag:
            counts[e.tag] = counts.get(e.tag, 0) + int(e.count or 1)
    return counts


# --------------------------------------------------------------- build all
def build(root: Union[str, Iterable[str]], daily: Optional[Any] = None,
          ignore: Optional[Any] = None) -> Tuple[
        List[Dict[str, Any]], List[Tuple[str, str, int]]]:
    """Full idempotent rebuild: every bundle under ``root`` -> (registry rows,
    tidy tag rows). Each registry row carries its per-tag counts under the
    private ``_tags`` key so the renderer can project the current vocabulary into
    wide columns; the tidy rows are the churn-proof source of truth.

    ``root`` is either one recordings root or a sequence of them (primary +
    archives; docs/decisions/0113). Multiple roots are merged and de-duplicated
    by session_id via :func:`scan_roots` (first-listed root wins).

    ``ignore`` is an optional collection of session ids to SKIP (non-real
    sessions kept on disk; the ``session_ignore`` core, ADR-0097): those bundles
    produce no registry row and no tag rows, so the whole downstream pipeline
    (View sheets, Records Manager list) drops them without deleting anything."""
    roots = [root] if isinstance(root, str) else list(root)
    skip = set(ignore or ())
    rows: List[Dict[str, Any]] = []
    tag_rows: List[Tuple[str, str, int]] = []
    for found_root, bundle in scan_roots_with_root(roots):
        if _session_id_of(bundle) in skip:
            continue
        row, counts = bundle_row(bundle, daily=daily, root=found_root)
        row["_tags"] = counts
        rows.append(row)
        for tag in sorted(counts):
            tag_rows.append((row["session_id"], tag, counts[tag]))
    return rows, tag_rows


# --------------------------------------------------------------- rendering
def _fmt(v: Any) -> Any:
    """CSV cell value: None -> "" (blank), everything else as-is."""
    return "" if v is None else v


def render_registry_csv(rows: List[Dict[str, Any]],
                        tag_names: Optional[Sequence[str]] = None,
                        meta: Optional[BuildMeta] = None) -> str:
    """Render ``session_registry.csv`` text: a ``# schema_version`` comment line,
    the provenance ``# key=value`` lines from ``meta`` (when given), the fixed
    columns + one ``tag_<name>`` column per current-vocabulary tag (0 when the
    session has none), then one row per session."""
    names = list(tag_names or [])
    tag_cols = [f"tag_{n}" for n in names]
    buf = io.StringIO()
    buf.write(f"# schema_version={SCHEMA_VERSION}\n")
    if meta is not None:
        for line in meta.comment_lines():
            buf.write(line + "\n")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(list(REGISTRY_COLUMNS) + tag_cols)
    for row in rows:
        counts = row.get("_tags") or {}
        base = [_fmt(row.get(c)) for c in REGISTRY_COLUMNS]
        wide = [int(counts.get(n, 0)) for n in names]
        writer.writerow(base + wide)
    return buf.getvalue()


def render_tags_csv(tag_rows: List[Tuple[str, str, int]]) -> str:
    """Render the tidy long ``session_registry_tags.csv`` (``session_id, tag,
    count``) behind a ``# schema_version`` comment line."""
    buf = io.StringIO()
    buf.write(f"# schema_version={SCHEMA_VERSION}\n")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(list(TAGS_COLUMNS))
    for session_id, tag, count in tag_rows:
        writer.writerow([session_id, tag, int(count)])
    return buf.getvalue()
