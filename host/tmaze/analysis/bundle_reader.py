"""bundle_reader.py - discover + load Phase 3 session bundles for offline analysis.

The rig writes one self-describing bundle per session under the recordings root
(``paths.phase3_recordings_roots()`` - the primary folder plus any archive roots;
docs/decisions/0113): a directory ``<animal>_<YYYYMMDD>_<HHMMSS>/``
holding the per-trial CSV in ``task/``, the per-excursion log beside it (when that
feature is deployed), and JSON context in ``config/`` / ``summary/``. This module
folds a chosen animal set + date span into four tidy pandas frames the rest of the
analysis stack consumes:

- ``trials``     - one row per ATTEMPTED trial (planned-but-unrun placeholder rows
  are dropped; see docs/decisions/0131), every session concatenated and
  time-ordered, with a global ``seq`` order column and per-session context attached.
- ``excursions`` - one row per outbound trip (empty when no ``*_excursions.csv``
  exists yet - the log is new, so all current recordings lack it; everything
  downstream degrades gracefully).
- ``sessions``   - one row per session: identity, timing, the parsed ``#``-header
  config (cue->side mapping, tones, generator, block params) and husbandry/stage
  context read straight from the bundle's ``config/`` JSON (no built registry
  required), plus probe-planned/disabled flags (docs/decisions/0137).
- ``notes``      - one row per session-timeline note entry (operator notes + auto
  events: manual rewards, debias/alt/probe start-end, skips/backs, and the probe
  enable/disable events of docs/decisions/0137), or empty when no session had a
  notes file.

Read-only and pure aside from the file reads: no Tk/OpenCV/serial. The token
constants (CORRECT/INCORRECT, HIGH/LOW) are imported from the live cores so the
analysis can't drift from what the rig writes. Headless tests:
``python -m unittest test_analysis_bundle_reader``.
"""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Union

import pandas as pd

from tmaze.analysis import phase3_format
from tmaze.analysis.phase3_format import (
    ABANDONED,
    CORRECT,
    INCORRECT,
    LEFT_LABEL,
    RIGHT_LABEL,
)

# Token constants (outcomes, cue tones, arm labels) + the notes-JSONL parser come
# from the analysis-side data contract (tmaze.analysis.phase3_format), which mirrors
# what the rig writes, so the analysis stack needn't import the live rig core. A
# same-repo parity test asserts the mirror still matches tmaze.core. Arm labels are
# the CSV's uppercase strings (phase3_log upper-cases chosen_arm/correct_arm; the
# trial_sequence LEFT/RIGHT are ints).

# Outcome tokens re-exported for downstream modules (single import site).
COMPLETED_OUTCOMES = (CORRECT, INCORRECT)

# sched_state token that marks an in-block (corrective/adversarial) trial.
INTERVENTION_LABEL = "INTERVENTION"
NORMAL_LABEL = "NORMAL"

# The trial-CSV columns the analysis relies on, with a default for older sessions
# that predate a column (the closed-loop intervention columns were added mid-project,
# so a July CSV may lack them). Missing columns are back-filled with these so every
# session frame has a uniform shape.
_COLUMN_DEFAULTS: Dict[str, object] = {
    "trial": pd.NA,
    "correct_arm": "",
    "choice_cue": "",
    "return_cue_left": "",
    "return_cue_right": "",
    "chosen_arm": "",
    "outcome": "",
    "rewarded": "0",
    "return_arm_used": "",
    "return_cue_played": "",
    "trial_start_iso": "",
    "choice_cue_on_iso": "",
    "choice_time_iso": "",
    "sched_state": NORMAL_LABEL,
    "block_id": "",
    "block_trigger": "",
    "bias": "0.0",
    "block_kind": "",
    "alt_rate": "0.0",
    # Unrewarded probe-trial marker ("1" / ""); absent in pre-probe sessions, so it
    # back-fills to "" (= not a probe). See docs/decisions/0115-probe.
    "probe": "",
    # Correct-choice probe TRIGGER marker ("1" / ""); absent before docs/decisions/
    # 0134, back-filling to "" (= not a trigger, so it never gets excluded from probe
    # accuracy in an older session that had none).
    "probe_trigger": "",
    # Trial-edit lifecycle (docs/decisions/0119; absent before it, back-filling to
    # a single natural attempt). ``superseded`` == "1" marks a preserved attempt a
    # later Repeat/Back replaced - dropped below so the tidy frame counts each
    # trial once (its current attempt); the raw CSV keeps the full history.
    "attempt": "1",
    "superseded": "",
    "edit": "",
}


@dataclass(frozen=True)
class SessionInfo:
    """Per-session identity + parsed config/context (one row of the sessions frame)."""

    session_id: str
    animal: str
    date: str
    start_iso: str            # canonical anchor (manifest t0) when present, else gate-arm
    generator: str
    high_tone_is_left: Optional[bool]
    high_tone_hz: Optional[int]
    low_tone_hz: Optional[int]
    n_trials: int
    gate_arm_iso: str = ""    # the gate rising edge (may precede start_iso); ADR-0124
    stage: str = ""
    trainer: str = ""
    pct_baseline: Optional[float] = None
    weight_g: Optional[float] = None
    hours_since_water: Optional[float] = None
    header: Dict[str, str] = field(default_factory=dict)
    bundle: str = ""
    # Unrewarded probe-block context (docs/decisions/0137). ``probe_planned`` is read
    # from the CSV header (a probe WAS armed by config); ``probe_disabled`` is True
    # when the operator turned probes off at some point this session (an
    # ``AUTO_PROBE_DISABLED`` note). Together they let a reader tell a manually
    # disabled session apart from one where a planned window simply never fired -
    # the archived config alone can't show that.
    probe_planned: bool = False
    probe_disabled: bool = False


@dataclass
class AnimalData:
    """The tidy frames for one or more animals over a date span.

    ``notes`` is one row per operator/auto note entry across all sessions (the
    session timeline: manual rewards, debias/alt/probe events, skips/backs, and the
    probe enable/disable events of docs/decisions/0137), or an empty frame when no
    session had a notes file. A defaulted field so existing ``trials``/``excursions``/
    ``sessions`` consumers are unaffected."""

    trials: pd.DataFrame
    excursions: pd.DataFrame
    sessions: pd.DataFrame
    notes: pd.DataFrame = field(default_factory=pd.DataFrame)


# --------------------------------------------------------------- header parsing
def parse_header_comments(text: str) -> Dict[str, str]:
    """Fold the leading ``#`` comment lines of a trial CSV into a flat dict.

    Every whitespace-separated ``key=value`` token across all ``#`` lines becomes an
    entry (last write wins). This captures the essentials the analysis needs -
    ``high_tone_is_left``, ``high_tone_hz`` / ``low_tone_hz``, ``generator`` - plus
    incidental tokens (``seed``, ``block_size`` ...); block-param collisions between
    the ``debias_blocks`` and ``alternation_blocks`` lines are unimportant here. Free
    prose after a ``# note:`` prefix is ignored. Non-comment lines end the scan.
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


def _to_float(val: Optional[object]) -> Optional[float]:
    try:
        f = float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f


# --------------------------------------------------------------- discovery
def _animal_date_from_id(session_id: str):
    """Parse ``<animal>_<YYYYMMDD>_<HHMMSS>`` into (animal, ISO date), else (None,None).

    Mirrors ``session_registry._animal_date_from_id`` (the animal may contain ``_``).
    """
    parts = session_id.rsplit("_", 2)
    if len(parts) == 3 and len(parts[1]) == 8 and parts[1].isdigit():
        ymd = parts[1]
        return parts[0], f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return None, None


def _is_bundle(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    name = os.path.basename(os.path.normpath(path))
    return os.path.isfile(os.path.join(path, f"session_{name}.json"))


def discover_bundles(
    root: Union[str, Iterable[str]],
    animals: Optional[Iterable[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[str]:
    """Bundle directories under ``root`` matching the animal set + [start,end] dates.

    ``root`` is either one recordings root or a sequence of them (a history split
    across drives: the rig's primary folder plus archive locations; see
    docs/decisions/0113). Several roots are merged and de-duplicated by session_id
    with the FIRST listed root winning, so a mid-archive copy that briefly exists
    in two places is loaded once.

    ``animals`` is a set of canonical ids (e.g. ``{"600","601","602"}``); ``None``
    keeps all. ``start_date`` / ``end_date`` are inclusive ``YYYY-MM-DD`` bounds
    (``None`` = open). Junk/test ids (``000``/``123`` ...) are excluded simply by
    passing the real animal set. Best-effort: an unreadable root yields ``[]``.
    """
    roots = [root] if isinstance(root, str) else list(root)
    want = None if animals is None else {str(a) for a in animals}
    keep: List[str] = []
    seen: set = set()
    for one_root in roots:
        try:
            entries = sorted(os.listdir(one_root))
        except OSError:
            continue
        for name in entries:
            path = os.path.join(one_root, name)
            if not _is_bundle(path):
                continue
            if name in seen:      # same session_id already taken from an earlier root
                continue
            animal, date = _animal_date_from_id(name)
            if want is not None and animal not in want:
                continue
            if date is not None:
                if start_date is not None and date < start_date:
                    continue
                if end_date is not None and date > end_date:
                    continue
            seen.add(name)
            keep.append(path)
    return keep


def _task_trial_csvs(bundle: str) -> List[str]:
    """The per-trial CSVs in a bundle's ``task/`` (the non-``_excursions`` files)."""
    found = sorted(glob.glob(os.path.join(bundle, "task", "*.csv")))
    return [p for p in found if not p.endswith("_excursions.csv")]


def _task_excursion_csvs(bundle: str) -> List[str]:
    return sorted(glob.glob(os.path.join(bundle, "task", "*_excursions.csv")))


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _read_notes(bundle: str) -> List[dict]:
    """The session's operator/auto notes as a list of entry dicts, or ``[]`` when the
    file is absent/unreadable (older bundles, or a session that jotted none).

    Read from ``summary/notes_<id>.jsonl`` via a parser that mirrors the pure one
    the rig writes with (``phase3_format.parse_notes_jsonl`` == the analysis-side
    copy of ``session_notes.parse_jsonl(...).entries()``), so the analysis can't
    drift from the on-disk note format (docs/decisions/0074/0137). Best-effort like
    the JSON reads.
    """
    session_id = os.path.basename(os.path.normpath(bundle))
    path = os.path.join(bundle, "summary", f"notes_{session_id}.jsonl")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    return phase3_format.parse_notes_jsonl(text)


# --------------------------------------------------------------- per-bundle load
def _read_trial_csv(path: str) -> pd.DataFrame:
    """Read one trial CSV (skipping ``#`` header lines), normalising the schema.

    Missing columns (older sessions) are back-filled from ``_COLUMN_DEFAULTS`` so
    every session frame has a uniform shape. Numeric-ish columns are coerced; the
    arm/cue/outcome columns stay categorical strings.
    """
    df = pd.read_csv(path, comment="#", dtype=str, keep_default_na=False)
    for col, default in _COLUMN_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    # Drop preserved (superseded) trial-edit attempts (ADR-0119): the tidy trials
    # frame counts each trial once - its current attempt - matching the live/rig
    # tallies; the raw CSV still holds the replaced attempts for anyone who wants
    # the full edit history.
    df = df[df["superseded"].astype(str).str.strip() != "1"].copy()
    df["trial"] = pd.to_numeric(df["trial"], errors="coerce")
    df["rewarded"] = pd.to_numeric(df["rewarded"], errors="coerce").fillna(0).astype(int)
    df["bias"] = pd.to_numeric(df["bias"], errors="coerce")
    df["alt_rate"] = pd.to_numeric(df["alt_rate"], errors="coerce")
    for iso in ("trial_start_iso", "choice_cue_on_iso", "choice_time_iso"):
        df[iso + "_dt"] = pd.to_datetime(df[iso], errors="coerce")
    # Blank sched_state (balanced generator) reads as NORMAL for a uniform partition.
    df["sched_state"] = df["sched_state"].replace("", NORMAL_LABEL)
    return df


def load_bundle(bundle: str) -> Optional[tuple]:
    """Load one bundle -> (trials_df, excursions_df, SessionInfo, notes_df), or None
    if empty.

    Concatenates all per-trial CSVs in ``task/`` (restarts add ``_r<n>`` files),
    tagging each with a ``run_idx`` and preserving per-run ``trial`` order. Reads
    header config from the first file and husbandry/stage from ``config/`` JSON, and
    folds the session timeline notes (``summary/notes_<id>.jsonl``) into a per-session
    notes frame + the probe-planned/disabled flags (docs/decisions/0137). Only
    ATTEMPTED trials are kept (started or scored); a bundle with none (an aborted
    start) returns ``None`` and is skipped. See docs/decisions/0131.
    """
    csvs = _task_trial_csvs(bundle)
    if not csvs:
        return None
    # Fail loudly on a genuinely newer on-disk format rather than silently
    # misreading it; a legacy bundle without config/format.json is allowed and its
    # compatibility is inferred from zones.json + the CSV proto stamp.
    phase3_format.assert_supported(bundle)
    session_id = os.path.basename(os.path.normpath(bundle))
    id_animal, id_date = _animal_date_from_id(session_id)

    frames: List[pd.DataFrame] = []
    header: Dict[str, str] = {}
    for run_idx, path in enumerate(csvs):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                head = "".join(fh.readline() for _ in range(24))
        except OSError:
            head = ""
        if run_idx == 0:
            header = parse_header_comments(head)
        df = _read_trial_csv(path)
        df["run_idx"] = run_idx
        df["run_file"] = os.path.basename(path)
        frames.append(df)
    trials = pd.concat(frames, ignore_index=True)

    # Keep only trials the rat actually attempted. phase3_log pre-writes the full
    # planned cue matrix up front (docs/decisions/0046/0047), so un-run trials sit
    # as placeholder rows (planned cue only, no trial_start_iso / chosen_arm /
    # outcome). Counting them pads every session to its planned length and scatters
    # the animal-wide ``seq`` with empty gaps that render as long connecting lines;
    # keep a trial only if it was STARTED or SCORED (abandoned trials are kept - they
    # matter for the engagement gap and for breaking own-choice pairs). A session
    # with zero attempted trials (an aborted start) then yields an empty frame and
    # is skipped below. See docs/decisions/0131.
    started = trials["trial_start_iso"].astype(str).str.strip().ne("")
    scored = trials["outcome"].isin(COMPLETED_OUTCOMES + (ABANDONED,))
    trials = trials[started | scored].reset_index(drop=True)
    if trials.empty:
        return None

    astate = _read_json(os.path.join(bundle, "config", "animal_state.json")) or {}
    meta = _read_json(os.path.join(bundle, "config", "session_meta.json")) or {}
    animal = str(astate.get("animal") or id_animal or "")
    date = str(astate.get("date") or id_date or "")
    # Prefer the canonical session anchor (manifest t0, Start Session) when the
    # newer header carries it; fall back to the gate-arm time for pre-ADR-0124
    # bundles. Keep the gate-arm time as its own field so no anchor is lost and it
    # agrees with the session registry (docs/decisions/0124).
    gate_arm_iso = str(header.get("session_start_iso", ""))
    start_iso = str(header.get("session_t0_iso", "")) or gate_arm_iso

    # Session timeline notes (docs/decisions/0074/0137): fold into a per-session frame
    # and derive the probe flags. ``probe_planned`` comes from the CSV header token
    # (a probe was armed by config); ``probe_disabled`` is True if the operator turned
    # probes off at any point (an AUTO_PROBE_DISABLED note) - so a manually disabled
    # session is not misread as "planned but the window never fired".
    note_dicts = _read_notes(bundle)
    probe_planned = bool(_to_bool(header.get("probe_enabled")))
    probe_disabled = any(
        d.get("tag") == phase3_format.AUTO_PROBE_DISABLED for d in note_dicts)

    trials.insert(0, "session_id", session_id)
    trials.insert(1, "animal", animal)
    trials.insert(2, "date", date)
    high_tone_is_left = _to_bool(header.get("high_tone_is_left"))
    trials["high_tone_is_left"] = high_tone_is_left

    info = SessionInfo(
        session_id=session_id,
        animal=animal,
        date=date,
        start_iso=start_iso,
        gate_arm_iso=gate_arm_iso,
        generator=str(header.get("generator", "")),
        high_tone_is_left=high_tone_is_left,
        high_tone_hz=_to_int(header.get("high_tone_hz")),
        low_tone_hz=_to_int(header.get("low_tone_hz")),
        n_trials=int(trials["trial"].notna().sum()),
        stage=str(meta.get("stage") or ""),
        trainer=str(meta.get("trainer") or ""),
        pct_baseline=_to_float(astate.get("pct_baseline")),
        weight_g=_to_float(astate.get("pre_session_weight_g")),
        hours_since_water=_to_float(astate.get("hours_since_water")),
        header=header,
        bundle=os.path.abspath(bundle),
        probe_planned=probe_planned,
        probe_disabled=probe_disabled,
    )

    if note_dicts:
        notes_df = pd.DataFrame(note_dicts)
        notes_df.insert(0, "session_id", session_id)
        notes_df.insert(1, "animal", animal)
        notes_df.insert(2, "date", date)
    else:
        notes_df = pd.DataFrame()

    exc_frames: List[pd.DataFrame] = []
    for path in _task_excursion_csvs(bundle):
        try:
            edf = pd.read_csv(path, comment="#", dtype=str, keep_default_na=False)
        except (OSError, ValueError, pd.errors.EmptyDataError):
            continue
        edf["session_id"] = session_id
        edf["animal"] = animal
        edf["date"] = date
        exc_frames.append(edf)
    excursions = pd.concat(exc_frames, ignore_index=True) if exc_frames else pd.DataFrame()

    return trials, excursions, info, notes_df


# --------------------------------------------------------------- top level
def load_animals(
    animals: Optional[Sequence[str]] = ("600", "601", "602"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    root: Optional[Union[str, Iterable[str]]] = None,
) -> AnimalData:
    """Load every matching bundle into the three tidy, time-ordered frames.

    ``root`` defaults to ``paths.phase3_recordings_roots()`` - the full recordings
    search path (the primary folder, honouring ``TMAZE_RECORDINGS_DIR``, plus any
    ``TMAZE_RECORDINGS_ARCHIVES`` archive roots), so a history split across drives
    is loaded as one (docs/decisions/0113). Pass a single path or a sequence to
    override. ``trials`` gets a global ``seq`` order column (session start, then
    run, then trial) so downstream traces run straight through the animal's
    history. Sessions with no loadable trial CSV, or with no attempted trials
    (an aborted start), are skipped.
    """
    if root is None:
        root = [str(p) for p in phase3_format.phase3_recordings_roots()]

    bundles = discover_bundles(root, animals, start_date, end_date)
    trial_frames: List[pd.DataFrame] = []
    exc_frames: List[pd.DataFrame] = []
    note_frames: List[pd.DataFrame] = []
    infos: List[SessionInfo] = []
    for bundle in bundles:
        loaded = load_bundle(bundle)
        if loaded is None:
            continue
        tdf, edf, info, ndf = loaded
        trial_frames.append(tdf)
        if not edf.empty:
            exc_frames.append(edf)
        if not ndf.empty:
            note_frames.append(ndf)
        infos.append(info)

    trials = (
        pd.concat(trial_frames, ignore_index=True)
        if trial_frames
        else pd.DataFrame()
    )
    excursions = (
        pd.concat(exc_frames, ignore_index=True) if exc_frames else pd.DataFrame()
    )
    notes = (
        pd.concat(note_frames, ignore_index=True) if note_frames else pd.DataFrame()
    )
    sessions = pd.DataFrame([_info_row(i) for i in infos])

    if not trials.empty:
        sort_start = pd.to_datetime(
            trials["session_id"].map(_session_sort_key), errors="coerce"
        )
        trials = trials.assign(_sort_start=sort_start)
        trials = trials.sort_values(
            ["animal", "_sort_start", "session_id", "run_idx", "trial"],
            kind="stable",
        ).reset_index(drop=True)
        trials["seq"] = trials.groupby("animal").cumcount()
        trials = trials.drop(columns=["_sort_start"])

    return AnimalData(trials=trials, excursions=excursions, sessions=sessions,
                      notes=notes)


def _session_sort_key(session_id: str) -> str:
    """A sortable ``YYYYMMDDHHMMSS`` key from the session id (blank if unparsable)."""
    parts = session_id.rsplit("_", 2)
    if len(parts) == 3 and len(parts[1]) == 8 and len(parts[2]) == 6:
        return parts[1] + parts[2]
    return ""


def _info_row(info: SessionInfo) -> dict:
    return {
        "session_id": info.session_id,
        "animal": info.animal,
        "date": info.date,
        "start_iso": info.start_iso,
        "gate_arm_iso": info.gate_arm_iso,
        "generator": info.generator,
        "high_tone_is_left": info.high_tone_is_left,
        "high_tone_hz": info.high_tone_hz,
        "low_tone_hz": info.low_tone_hz,
        "n_trials": info.n_trials,
        "stage": info.stage,
        "trainer": info.trainer,
        "pct_baseline": info.pct_baseline,
        "weight_g": info.weight_g,
        "hours_since_water": info.hours_since_water,
        "probe_planned": info.probe_planned,
        "probe_disabled": info.probe_disabled,
        "bundle": info.bundle,
    }
