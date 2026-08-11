"""
reward_calibration.py - measured reward-volume calibration: liquids, math, file IO.

The rig dispenses reward by opening a valve for a set time; the volume delivered
depends on the pulse width AND on the liquid (viscosity), so it must be measured
rather than assumed. This pure core owns the *data* side of that calibration:

- the canonical set of reward liquids (:data:`KNOWN_LIQUIDS` + :func:`normalize_liquid`),
  shared by the measurement tool and the GUI so a free-typed name keys consistently;
- the per-pulse volume math (:func:`ul_per_pulse`, :func:`ml_to_ul`);
- a :class:`CalibrationPoint` row and read/write of the on-disk CSV table
  (``host/reward_calibration.csv``), keyed by ``(port, liquid, pulse_ms)``.

The lookup/interpolation that turns this table into a volume for an arbitrary
pulse width lives next door in :mod:`reward_volume`; see docs/decisions/0091
(supersedes 0019) for why volume is measured directly (with a graduated syringe),
per liquid, and stored in a data file instead of a single in-code constant.

Pure standard library (no Tk/serial/OpenCV), so it is headless-testable like the
rest of ``core``.
"""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from tmaze.paths import host_dir

# Bump when the reward_calibration.csv layout changes so an offline reader can
# branch (mirrors the records CSVs' leading "# schema_version=N" convention).
SCHEMA_VERSION = 1

# The reward liquid assumed when a caller doesn't name one - the current reward
# liquid on the rig (see docs/decisions/0091). Lives here (the low-level data
# core) rather than in reward_volume.py so both can share it without a circular
# import; reward_volume re-exports it for its existing callers.
DEFAULT_REWARD_LIQUID = "sucrose_10pct"

# Canonical reward-liquid tokens -> human display label. This is the single
# source shared by the calibration tool's --liquid arg and the Session-panel
# "Reward" combobox, so the token that keys a curve and the label a human sees
# never drift. 10% sucrose is the current reward liquid (see DEFAULT_REWARD_LIQUID
# in reward_volume.py); water is kept for comparison / other tasks.
KNOWN_LIQUIDS: Dict[str, str] = {
    "sucrose_10pct": "10% sucrose",
    "water": "water",
}

# Extra spellings a human might type/paste, mapped to a canonical token. The
# normalizer also strips/lowercases and collapses punctuation, so this only needs
# the genuinely ambiguous forms.
_LIQUID_ALIASES: Dict[str, str] = {
    "10% sucrose": "sucrose_10pct",
    "10%sucrose": "sucrose_10pct",
    "10 sucrose": "sucrose_10pct",
    "10pct sucrose": "sucrose_10pct",
    "10percent sucrose": "sucrose_10pct",
    "sucrose 10%": "sucrose_10pct",
    "sucrose": "sucrose_10pct",
    "suc": "sucrose_10pct",
    "h2o": "water",
    "di water": "water",
    "tap water": "water",
}

# CSV column order (the header written after the schema-version comment line).
FIELDNAMES: Tuple[str, ...] = (
    "port", "liquid", "pulse_ms", "volume_ul",
    "n_pulses", "measured_ul", "date", "operator", "notes",
)


def normalize_liquid(name: Optional[str]) -> str:
    """Canonical liquid token for a free-typed/spelled name.

    Lowercases, trims, collapses inner whitespace, and consults the alias table,
    so "10% Sucrose", "10% sucros" (a common typo), and "sucrose" all map to
    ``"sucrose_10pct"``. An unknown non-empty name is returned in a normalized
    (lowercased, underscore-joined) form so it still keys consistently even
    though no curve is measured for it yet; an empty name falls back to the
    default reward liquid.
    """
    if not name:
        return DEFAULT_REWARD_LIQUID
    key = " ".join(str(name).strip().lower().split())
    if not key:
        return DEFAULT_REWARD_LIQUID
    if key in KNOWN_LIQUIDS:
        return key
    if key in _LIQUID_ALIASES:
        return _LIQUID_ALIASES[key]
    # A "10% sucros"-style typo: catch a leading "10" + a "sucros" stem.
    compact = key.replace("%", " ")
    if "sucros" in compact and "10" in compact:
        return "sucrose_10pct"
    if compact in ("water", "waters"):
        return "water"
    # Unknown liquid: keep it, but as a stable token ('%' -> 'pct', spaces -> _).
    token = "_".join(key.replace("%", "pct").split())
    return token or DEFAULT_REWARD_LIQUID


def liquid_label(token: str) -> str:
    """Human display label for a canonical liquid token (falls back to the token)."""
    return KNOWN_LIQUIDS.get(token, token)


def ml_to_ul(ml: float) -> float:
    """Convert millilitres to microlitres."""
    return float(ml) * 1000.0


def ul_per_pulse(measured_ul: float, n_pulses: int) -> float:
    """Dispensed volume per single pulse (uL) from a measured total.

    The bench measures the total volume aspirated after firing ``n_pulses``
    identical pulses (many pulses so a readable syringe volume accumulates); this
    divides it back to a per-pulse figure. Raises ``ValueError`` for a
    non-positive pulse count.
    """
    n = int(n_pulses)
    if n <= 0:
        raise ValueError("n_pulses must be positive")
    return float(measured_ul) / n


def today_str() -> str:
    """Today's date as ISO ``YYYY-MM-DD`` (for a point's provenance)."""
    return _dt.date.today().isoformat()


@dataclass(frozen=True)
class CalibrationPoint:
    """One measured (port, liquid, pulse_ms) -> volume row, with provenance.

    ``volume_ul`` is the per-pulse dispensed volume (what the lookup uses);
    ``n_pulses`` / ``measured_ul`` record how it was obtained so a reader can
    recompute or sanity-check it, and ``date`` / ``operator`` / ``notes`` say
    when/who/anything odd.
    """
    port: str
    liquid: str
    pulse_ms: int
    volume_ul: float
    n_pulses: int = 0
    measured_ul: float = 0.0
    date: str = ""
    operator: str = ""
    notes: str = ""

    def key(self) -> Tuple[str, str, int]:
        """Upsert key: one row per (port, liquid, pulse_ms)."""
        return (self.port, self.liquid, self.pulse_ms)


def default_calibration_path() -> Path:
    """Canonical location of the calibration table: ``host/reward_calibration.csv``
    (beside the package, like ``rules.json`` / ``rig_scale.json``)."""
    return host_dir() / "reward_calibration.csv"


def read_points(path) -> List[CalibrationPoint]:
    """Read all calibration rows from ``path``.

    Tolerant like the other core readers: a missing file yields an empty list,
    and comment lines (``# ...``, including the schema-version line) and blank or
    unparseable rows are skipped rather than raising.
    """
    p = Path(path)
    if not p.exists():
        return []
    points: List[CalibrationPoint] = []
    with p.open("r", encoding="utf-8", newline="") as fh:
        rows = [ln for ln in fh if not ln.lstrip().startswith("#")]
    reader = csv.DictReader(rows)
    for row in reader:
        try:
            port = normalize_port(row.get("port", ""))
            liquid = normalize_liquid(row.get("liquid", ""))
            pulse_ms = int(float(row["pulse_ms"]))
            volume_ul = float(row["volume_ul"])
        except (KeyError, TypeError, ValueError):
            continue
        points.append(CalibrationPoint(
            port=port, liquid=liquid, pulse_ms=pulse_ms, volume_ul=volume_ul,
            n_pulses=_int_or(row.get("n_pulses"), 0),
            measured_ul=_float_or(row.get("measured_ul"), 0.0),
            date=(row.get("date") or "").strip(),
            operator=(row.get("operator") or "").strip(),
            notes=(row.get("notes") or "").strip(),
        ))
    return points


def write_points(path, points: Iterable[CalibrationPoint]) -> None:
    """Write ``points`` to ``path`` as the schema-versioned CSV, sorted by
    (port, liquid, pulse_ms) for a stable, diffable file."""
    p = Path(path)
    ordered = sorted(points, key=lambda pt: (pt.port, pt.liquid, pt.pulse_ms))
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        fh.write(f"# schema_version={SCHEMA_VERSION}\n")
        writer = csv.DictWriter(fh, fieldnames=list(FIELDNAMES))
        writer.writeheader()
        for pt in ordered:
            writer.writerow({
                "port": pt.port, "liquid": pt.liquid, "pulse_ms": pt.pulse_ms,
                "volume_ul": _trim(pt.volume_ul), "n_pulses": pt.n_pulses,
                "measured_ul": _trim(pt.measured_ul), "date": pt.date,
                "operator": pt.operator, "notes": pt.notes,
            })


def upsert_points(points: Iterable[CalibrationPoint],
                  new: Iterable[CalibrationPoint]) -> List[CalibrationPoint]:
    """Merge ``new`` into ``points``, replacing any row with the same
    (port, liquid, pulse_ms) key (last write wins) and appending the rest."""
    by_key: Dict[Tuple[str, str, int], CalibrationPoint] = {
        pt.key(): pt for pt in points}
    for pt in new:
        by_key[pt.key()] = pt
    return list(by_key.values())


def to_lookup(
        points: Iterable[CalibrationPoint]
) -> Dict[Tuple[str, str], Dict[int, float]]:
    """Group rows into ``{(port, liquid): {pulse_ms: volume_ul}}`` for the lookup."""
    out: Dict[Tuple[str, str], Dict[int, float]] = {}
    for pt in points:
        out.setdefault((pt.port, pt.liquid), {})[pt.pulse_ms] = pt.volume_ul
    return out


def load_calibration(path=None) -> Dict[Tuple[str, str], Dict[int, float]]:
    """Read the calibration table and return the ``(port, liquid) -> curve`` map.

    Defaults to :func:`default_calibration_path`. Missing file -> ``{}`` so the
    caller can fall back to its in-code default.
    """
    return to_lookup(read_points(path or default_calibration_path()))


def normalize_port(name: Optional[str]) -> str:
    """Canonical port label ('center'/'left'/'right') for a free-typed name.

    Accepts the labels themselves, common synonyms ('start'/'central' -> center),
    or a numeric index in the firmware port order (0=left, 1=right, 2=center).
    Unknown names are lowercased/trimmed and returned as-is.
    """
    if name is None:
        return "center"
    key = str(name).strip().lower()
    if not key:
        return "center"
    _INDEX = {"0": "left", "1": "right", "2": "center"}
    if key in _INDEX:
        return _INDEX[key]
    if key in ("center", "centre", "central", "start"):
        return "center"
    if key in ("left", "l"):
        return "left"
    if key in ("right", "r"):
        return "right"
    return key


def _int_or(val, default: int) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _float_or(val, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _trim(x: float) -> str:
    """Format a float without a trailing ``.0`` so the CSV stays tidy."""
    f = float(x)
    return str(int(f)) if f == int(f) else repr(f)
