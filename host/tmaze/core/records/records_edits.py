"""Pure decision logic behind the Records Manager's edit and build actions.

The Records Manager GUI (`tmaze.ui.records_manager`) is a thin shell: it reads
widget values, calls into cores like this one, and paints the result. The
multi-step logic that used to live inline in the GUI - turning a form into a
writeable daily row, expanding a date-span dialog into per-day edits, modelling a
Generate/Import chain's outcome, and deciding whether the session registry looks
stale - lives here so it can be unit-tested without a display (docs/decisions/0163
follow-up; the "pure core + thin shell" pattern in CONTRIBUTING.md).

Nothing in this module touches Tk, the filesystem, or the clock: callers pass in
already-read data (a :class:`~tmaze.core.records.daily_log.DailyLog`, the raw form
dict, the scanned bundle ids) and get back plain data structures.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tmaze.core.records import daily_log
from tmaze.core.session import session_registry

# The sentinel a span dialog uses for "don't touch this field". Kept here (not in
# the GUI) so span_edits and the dialog agree on it.
SPAN_LEAVE = "(leave unchanged)"

# The longest span a single "Mark span" action may cover. A guard against a
# fat-fingered year-long range silently writing hundreds of rows.
MAX_SPAN_DAYS = 31


# --------------------------------------------------------------------------- #
# Daily form -> writeable row
# --------------------------------------------------------------------------- #
def daily_row_from_form(fields: Dict[str, str]) -> Dict[str, str]:
    """Normalise a raw Daily-tab form dict into a row ready to upsert/write.

    Trims every FIELD to a string, recomputes the ``pct_baseline`` mirror from
    weight/baseline (the source of truth; docs/decisions/0163), and canonicalises
    the water regime/method the same way both write surfaces do. Does NOT
    validate - the caller runs :func:`daily_log.validate_entry` first and blocks
    the save on any hard error.
    """
    row = {f: str(fields.get(f, "") or "").strip() for f in daily_log.FIELDS}
    row["pct_baseline"] = daily_log.pct_str(row.get("weight_g"),
                                            row.get("baseline_g"))
    row["water_regime"] = daily_log.normalize_regime(row.get("water_regime", ""))
    row["water_method"] = daily_log.normalize_method(row.get("water_method", ""))
    return row


# --------------------------------------------------------------------------- #
# "Mark span" -> per-day edits
# --------------------------------------------------------------------------- #
def _iso_or_none(value: str) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return None


def validate_span(spec: Dict[str, str]) -> List[str]:
    """Human-readable HARD errors for a Mark-span request ([] means OK to run).

    Checks the animal is chosen, both dates are ISO and ordered, the range isn't
    longer than :data:`MAX_SPAN_DAYS`, and that at least one of regime/method is
    actually being set (a span that changes nothing is almost always a mistake).
    """
    errors: List[str] = []
    if not str(spec.get("animal", "")).strip():
        errors.append("Pick an animal.")
    d0 = _iso_or_none(spec.get("start", ""))
    d1 = _iso_or_none(spec.get("end", ""))
    if d0 is None or d1 is None:
        errors.append("Start and End must both be YYYY-MM-DD.")
    else:
        if d1 < d0:
            errors.append("End date is before Start date.")
        elif (d1 - d0).days > MAX_SPAN_DAYS:
            errors.append(
                f"Span is over {MAX_SPAN_DAYS} days - mark a shorter range.")
    if not _sets_regime(spec) and not _sets_method(spec):
        errors.append("Choose a regime and/or a method to apply over the span.")
    return errors


def _sets_regime(spec: Dict[str, str]) -> bool:
    return spec.get("regime", SPAN_LEAVE) != SPAN_LEAVE


def _sets_method(spec: Dict[str, str]) -> bool:
    return spec.get("method", SPAN_LEAVE) != SPAN_LEAVE


def span_edits(daily: "daily_log.DailyLog", spec: Dict[str, str]
               ) -> List[Dict[str, str]]:
    """Expand a validated span request into one edit row per day in the range.

    Each day starts from that (animal, date)'s CURRENT row in ``daily`` (so a
    span only overwrites the regime/method/boundary-time cells it targets and
    every other cell - weight, notes - is preserved), or a fresh keyed row when
    the day has no entry yet. Assumes :func:`validate_span` already passed.

    - regime / method are set only when the spec chooses them (not SPAN_LEAVE),
      and are canonicalised the same way a single save is.
    - an optional on-time lands on the FIRST day and an optional off-time on the
      LAST day - the ad-lib within-day window (ADR-0100/0104).
    """
    aid = str(spec["animal"]).strip()
    d0 = _dt.date.fromisoformat(str(spec["start"]).strip())
    d1 = _dt.date.fromisoformat(str(spec["end"]).strip())
    n = (d1 - d0).days + 1
    set_regime = _sets_regime(spec)
    set_method = _sets_method(spec)
    regime_val = daily_log.normalize_regime(spec.get("regime", ""))
    method_val = daily_log.normalize_method(spec.get("method", ""))

    edits: List[Dict[str, str]] = []
    for i in range(n):
        day = (d0 + _dt.timedelta(days=i)).isoformat()
        row = dict(daily.for_date(aid, day) or {"animal_id": aid, "date": day})
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
    return edits


# --------------------------------------------------------------------------- #
# Generate / Import chain outcome
# --------------------------------------------------------------------------- #
@dataclass
class StepResult:
    """One step of a Generate / Import chain, as data - not a text line to sniff
    (docs/decisions/0158)."""
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        tag = "OK" if self.ok else "FAILED"
        return f"[{self.name} {tag}] {self.detail}".rstrip()


@dataclass
class GenerateResult:
    """The result of a Generate / Import chain: its steps in order, whether it
    stopped early (a failed step halts the chain so later builders never run on a
    half-written input), and - for Generate - the registry's shrink report when
    the build REFUSED because sessions would vanish (docs/decisions/0158)."""
    steps: List[StepResult] = field(default_factory=list)
    halted: bool = False
    shrink: Optional[session_registry.ShrinkReport] = None
    shrink_refused: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps) \
            and not self.halted

    def text(self) -> str:
        lines = [s.line() for s in self.steps]
        if self.halted:
            lines.append("Stopped here - the remaining steps were not run.")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Registry staleness (the top banner's decision)
# --------------------------------------------------------------------------- #
@dataclass
class RegistryStaleness:
    """Whether the session registry looks out of date, as data for the banner.

    ``n_indexed`` is how many sessions the registry holds; ``unindexed`` lists the
    on-disk bundle ids not yet in it (and not on the ignore-list); ``roots_seen``
    is False when the disk scan found NOTHING anywhere (an unreachable root, so we
    can't judge "unindexed"); ``excel_unsynced`` is the separate "Daily Excel
    edited since last sync" signal.
    """
    n_indexed: int
    unindexed: List[str] = field(default_factory=list)
    roots_seen: bool = True
    excel_unsynced: bool = False

    @property
    def stale(self) -> bool:
        """True when the registry is missing real sessions that exist on disk."""
        return bool(self.unindexed)

    @property
    def warn(self) -> bool:
        """True when the banner should warn (stale registry OR unsynced Excel)."""
        return self.stale or self.excel_unsynced


def registry_staleness(on_disk_ids: Optional[List[str]],
                       indexed_ids: List[str],
                       ignored_ids,
                       n_indexed: int,
                       excel_unsynced: bool = False) -> RegistryStaleness:
    """Fold the already-scanned inputs into a :class:`RegistryStaleness`.

    Pure: the shell does the disk scan (once per load, since an archive root may
    be a network drive) and passes the bundle ids here. ``on_disk_ids`` is None
    when the scan reached no root at all (we then report ``roots_seen=False`` and
    no unindexed list, rather than crying "everything is missing").
    """
    if on_disk_ids is None:
        return RegistryStaleness(n_indexed=n_indexed, unindexed=[],
                                 roots_seen=False,
                                 excel_unsynced=excel_unsynced)
    unindexed = session_registry.unindexed_sessions(
        on_disk_ids, indexed_ids, ignored_ids)
    return RegistryStaleness(n_indexed=n_indexed, unindexed=unindexed,
                             roots_seen=True, excel_unsynced=excel_unsynced)
