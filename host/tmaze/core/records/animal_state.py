"""
animal_state.py - the pre-session physiological/motivational state (pure core).

Each session bundle should stand on its own for later analysis. Weight, % of
free-feeding baseline, and when water was last available are the motivational
context for a water-rewarded task, but today they live only in the husbandry
spreadsheet, joined to a recording by a fragile "animal id + date" key
(docs/decisions/0076). This core is the typed record that Session Start freezes
INTO the bundle (`config/animal_state.json`) so the join is captured once, at
capture time, and never has to be reconstructed.

It follows the ``session_notes`` precedent (docs/decisions/0074): a pure dataclass
that renders/parses JSON with no file I/O and no Tk - the thin
``session_controller`` shell writes it atomically and registers it as its OWN
manifest stream (not via ``config_snapshot``, which indexes the copied host
``*.json`` files; this is session-authored data). Division of labour: this file
holds the structured husbandry *scalars*; timestamped observations/interventions
stay in the notes log, not here.

``source`` records where the values came from - ``daily_table`` (prefilled from
`daily_log.csv`), ``start_dialog`` (typed at Start), or ``manual`` (neither
available). ``schema_version`` + ``session_id`` make the file self-describing on
its own, matching the manifest's ``SCHEMA_VERSION`` pattern
([session_log.py](session_log.py)).

Note on water fields (ADR-0101 / FW-0029): the daily table records water on/off
as tolerant free text ("6pm", "5:30PM", "17:00", or an irregular "Till Monday
evening"). ``last_water_on`` / ``last_water_off`` are kept **verbatim** (today's
row cells, for human reference). ``hours_since_water`` is now DERIVED when a
caller supplies the session-start time: it is the gap from the most recent
*parseable* water-off (typically the previous evening's - see
:func:`resolve_last_water_off`) to session start. A cell that isn't a clock time
leaves it null - we parse best-effort and never fight the irregular entries.

Headless tests: python -m unittest test_animal_state.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

# Bump when the animal_state.json layout changes so an offline reader can branch.
# v2 (ADR-0101): hours_since_water may now be a real derived number.
SCHEMA_VERSION = 2

SOURCE_DAILY_TABLE = "daily_table"
SOURCE_START_DIALOG = "start_dialog"
SOURCE_MANUAL = "manual"


def _num(value: Any) -> Optional[float]:
    """Coerce to a number (int when whole), or None for blank/non-numeric."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


@dataclass
class AnimalState:
    """The pre-session husbandry scalars frozen into a bundle at Session Start."""
    session_id: str
    animal: str
    date: str
    source: str = SOURCE_MANUAL
    pre_session_weight_g: Optional[float] = None
    baseline_g: Optional[float] = None
    pct_baseline: Optional[float] = None
    last_water_on: Optional[str] = None
    last_water_off: Optional[str] = None
    hours_since_water: Optional[float] = None
    measured_water_ml: Optional[float] = None
    note: str = ""
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def render(self) -> str:
        """Pretty JSON text for the shell to write atomically."""
        import json
        return json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AnimalState":
        """Rebuild from a parsed dict, tolerating missing keys (older records)."""
        return cls(
            session_id=str(d.get("session_id", "")),
            animal=str(d.get("animal", "")),
            date=str(d.get("date", "")),
            source=str(d.get("source", SOURCE_MANUAL)),
            pre_session_weight_g=_num(d.get("pre_session_weight_g")),
            baseline_g=_num(d.get("baseline_g")),
            pct_baseline=_num(d.get("pct_baseline")),
            last_water_on=d.get("last_water_on") or None,
            last_water_off=d.get("last_water_off") or None,
            hours_since_water=_num(d.get("hours_since_water")),
            measured_water_ml=_num(d.get("measured_water_ml")),
            note=str(d.get("note", "") or ""),
            schema_version=int(d.get("schema_version", SCHEMA_VERSION) or SCHEMA_VERSION),
        )


def _pct(weight: Optional[float], baseline: Optional[float],
         stored: Optional[float]) -> Optional[float]:
    if stored is not None:
        return stored
    if weight is not None and baseline:
        return round(weight / baseline, 4)
    return None


# Clock-time formats seen in the real daily log ("6pm", "5:30PM", "17:00",
# "11:30AM"). strptime requires a whole-string match, so a free-text cell like
# "Till Monday evening" or an ml note in the water column simply fails all of them
# and parses to None (we never try to pull a time out of a sentence).
_CLOCK_FORMATS = ("%I:%M%p", "%I%p", "%H:%M")


def parse_clock_time(text: Any) -> Optional[dt.time]:
    """A time-of-day from a water cell, or None for blank / free text.

    Tolerant of the lab's shorthand: "6pm", "5:30PM", "17:00", "11:30AM". A cell
    that isn't purely a clock time (e.g. "Till Monday evening", "8.46ml rig")
    returns None so :func:`hours_since_water` stays honest rather than guessing.
    """
    if text is None:
        return None
    norm = str(text).strip().upper().replace(" ", "")
    if not norm:
        return None
    for fmt in _CLOCK_FORMATS:
        try:
            return dt.datetime.strptime(norm, fmt).time()
        except ValueError:
            continue
    return None


def water_datetime(date_str: Any, time_text: Any) -> Optional[dt.datetime]:
    """A row's date (``YYYY-MM-DD``) + a clock cell combined into a datetime, or
    None if either the date or the time doesn't parse."""
    t = parse_clock_time(time_text)
    if t is None:
        return None
    try:
        d = dt.date.fromisoformat(str(date_str).strip())
    except (ValueError, TypeError):
        return None
    return dt.datetime.combine(d, t)


def hours_since_water(last_off: Optional[dt.datetime],
                      session_start: Optional[dt.datetime]) -> Optional[float]:
    """Hours from the last water-off to session start (2 dp), or None.

    None when either datetime is missing OR the gap is negative (a water-off that
    is *after* the session start is an inconsistent pairing we decline to report).
    """
    if last_off is None or session_start is None:
        return None
    delta = (session_start - last_off).total_seconds() / 3600.0
    return round(delta, 2) if delta >= 0 else None


def resolve_last_water_off(rows: List[Dict[str, str]],
                           session_start: Optional[dt.datetime]
                           ) -> Optional[dt.datetime]:
    """The most recent water-off datetime at/before ``session_start``.

    ``rows`` are daily rows for ONE animal (any order). Each row's ``water_off``
    clock cell is parsed against that row's own ``date``; blank / free-text cells
    are skipped. This captures the real convention where the thirst-relevant event
    is usually the PREVIOUS evening's water-off (today's row's water_off, if it is
    an evening time after a morning session, is in the future and excluded).
    Returns a datetime, or None when nothing parseable precedes the session.
    """
    best: Optional[dt.datetime] = None
    for r in rows:
        wdt = water_datetime(r.get("date"), r.get("water_off"))
        if wdt is None:
            continue
        if session_start is not None and wdt > session_start:
            continue
        if best is None or wdt > best:
            best = wdt
    return best


def from_daily_row(session_id: str, animal: str, date: str,
                   row: Dict[str, str],
                   session_start: Optional[dt.datetime] = None,
                   last_water_off: Optional[dt.datetime] = None) -> AnimalState:
    """Build state prefilled from a `daily_log.csv` row (source=daily_table).

    ``last_water_on`` / ``last_water_off`` carry today's row cells verbatim.
    ``hours_since_water`` is derived only when the caller passes ``session_start``
    and a resolved ``last_water_off`` datetime (see :func:`resolve_last_water_off`,
    which the Session Start shell uses to find the most recent prior water-off);
    otherwise it stays null, preserving the old behaviour.
    """
    weight = _num(row.get("weight_g"))
    baseline = _num(row.get("baseline_g"))
    return AnimalState(
        session_id=session_id, animal=animal, date=date,
        source=SOURCE_DAILY_TABLE,
        pre_session_weight_g=weight,
        baseline_g=baseline,
        pct_baseline=_pct(weight, baseline, _num(row.get("pct_baseline"))),
        last_water_on=(row.get("water_on") or None),
        last_water_off=(row.get("water_off") or None),
        hours_since_water=hours_since_water(last_water_off, session_start),
        measured_water_ml=_num(row.get("water_ml")),
        note=(row.get("notes") or ""),
    )


def from_weight(session_id: str, animal: str, date: str, weight_g: float,
                baseline_g: Optional[float] = None, note: str = "") -> AnimalState:
    """Build state from a weight typed at Start (source=start_dialog).

    ``baseline_g`` may come from the animal's most recent daily row so the
    % baseline still computes even when today's row hasn't been entered."""
    weight = _num(weight_g)
    baseline = _num(baseline_g)
    return AnimalState(
        session_id=session_id, animal=animal, date=date,
        source=SOURCE_START_DIALOG,
        pre_session_weight_g=weight, baseline_g=baseline,
        pct_baseline=_pct(weight, baseline, None), note=note)


def unknown(session_id: str, animal: str, date: str, note: str = "") -> AnimalState:
    """A minimal state when neither the daily table nor a typed weight is
    available (source=manual), so the bundle still carries the stream."""
    return AnimalState(session_id=session_id, animal=animal, date=date,
                       source=SOURCE_MANUAL, note=note)
