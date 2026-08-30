"""phase3_format.py - the analysis-side data contract for Phase 3 session bundles.

The offline analysis stack reads the self-describing bundles the rig writes, so it
depends only on the *on-disk format* - never on the live rig-control code. This
module is that dependency boundary: it owns the small set of tokens, geometry, and
readers the analysis needs, so nothing under ``tmaze.analysis`` has to import
``tmaze.core`` / ``tmaze.paths``. That lets the analysis layer version (and, later,
move to its own repo) independently of the rig code, which is stabilizing on a
different cadence (see the decoupling ADR).

What lives here:

- **Tokens** - the exact strings the rig writes into the trial CSV / notes /
  zones files: outcome labels (CORRECT / INCORRECT / ABANDONED), cue-tone labels
  (HIGH / LOW), arm labels (LEFT / RIGHT), and the maze zone-name vocabulary.
  These are the on-disk *format*, so the analysis repo legitimately owns a copy;
  a same-repo parity test (``test_analysis_phase3_format``) asserts they still
  match ``tmaze.core`` while both live here, catching drift until the split.
- **Geometry** - a vendored copy of the pure point-in-polygon zone classifier
  (``ZoneMap`` + ``classify_point`` / ``stem_progress``), so offline zone
  classification matches the rig bit-for-bit without importing ``maze_state``.
- **Notes** - the ``summary/notes_<id>.jsonl`` parser + the ``auto:`` tag
  vocabulary, mirroring ``tmaze.core.session.session_notes`` (docs/decisions/0074).
- **Recordings roots** - where session bundles live, from the same env vars the
  rig uses (``TMAZE_RECORDINGS_DIR`` / ``TMAZE_RECORDINGS_ARCHIVES``).
- **Version guards** - read the bundle's ``config/format.json`` (when present)
  and otherwise infer compatibility from ``zones.json`` ``version`` + the trial
  CSV ``proto=`` stamp, raising only on a genuinely newer/unsupported format so a
  future format change fails loudly instead of being silently misread.

Dependency-light on purpose: stdlib + optional numpy only; no ``tmaze.core``,
no ``tmaze.paths``, no Tk/OpenCV/serial. Headless tests:
``python -m unittest test_analysis_phase3_format``.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ============================== outcome / cue / arm tokens ====================
# Trial ``outcome`` column values (mirror tmaze.core.phase3.phase3_task).
CORRECT = "CORRECT"
INCORRECT = "INCORRECT"
ABANDONED = "ABANDONED"
OUTCOME_LABELS: Tuple[str, ...] = (CORRECT, INCORRECT, ABANDONED)

# ``choice_cue`` / return-cue column values (mirror tmaze.core.phase3.trial_sequence).
HIGH = "HIGH"
LOW = "LOW"
CUE_LABELS: Tuple[str, ...] = (HIGH, LOW)

# ``correct_arm`` / ``chosen_arm`` column values. phase3_log upper-cases the arm
# names; the sequence's own LEFT/RIGHT are ints, so the on-disk labels are the
# strings kept here (there is no core string constant to compare against).
LEFT_LABEL = "LEFT"
RIGHT_LABEL = "RIGHT"
ARM_LABELS: Tuple[str, ...] = (LEFT_LABEL, RIGHT_LABEL)

# ============================== zone vocabulary ==============================
# Mirror of tmaze.core.maze_state's zone names + geometry (normalized 0..1 coords).
HUB = "HUB"
CENTRAL_STEM = "CENTRAL_STEM"
LEFT_CHOICE = "LEFT_CHOICE"
RIGHT_CHOICE = "RIGHT_CHOICE"
LEFT_RETURN = "LEFT_RETURN"
RIGHT_RETURN = "RIGHT_RETURN"
JUNCTION = "JUNCTION"

# Names an editor may assign to a drawn polygon. HUB first so, in any overlap at
# the convergence, the reward zone wins the first-match classification (matches
# maze_state.EDITABLE_ZONES, which drives classify_point ordering).
EDITABLE_ZONES: Tuple[str, ...] = (HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE,
                                   RIGHT_CHOICE, LEFT_RETURN, RIGHT_RETURN)
ZONE_NAMES: Tuple[str, ...] = EDITABLE_ZONES

# Bump alongside the rig's zones.json schema (mirror maze_state.ZONES_VERSION).
ZONES_VERSION = 1


# ============================== geometry helpers =============================
def point_in_polygon(x: float, y: float,
                     polygon: Sequence[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test (pure arithmetic, any coord space).

    Verbatim copy of ``maze_state.point_in_polygon`` so offline zone
    classification matches the rig exactly (the parity test enforces this).
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def project_scalar(x: float, y: float,
                   base: Tuple[float, float],
                   tip: Tuple[float, float]) -> Optional[float]:
    """Scalar projection t of (x, y) onto the base->tip axis (0=base, 1=tip).

    Verbatim copy of ``maze_state.project_scalar``. Returns None for a degenerate
    axis (base == tip).
    """
    bx, by = base
    tx, ty = tip
    dx, dy = tx - bx, ty - by
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return None
    return ((x - bx) * dx + (y - by) * dy) / denom


# ================================= zone map ==================================
@dataclass(frozen=True)
class Zone:
    """A labeled region: a vocabulary ``name`` + a normalized (0..1) polygon."""
    name: str
    polygon: Tuple[Tuple[float, float], ...]


@dataclass(frozen=True)
class ZoneMap:
    """The labeled maze regions + the stem axis + the (optional) well region.

    A read-only mirror of ``maze_state.ZoneMap`` (the classify / project surface
    the analysis uses). Everything is in NORMALIZED (0..1) image coordinates.
    """
    zones: Tuple[Zone, ...] = ()
    well: Optional[Tuple[Tuple[float, float], ...]] = None
    stem_base: Optional[Tuple[float, float]] = None   # HUB / well end of stem
    stem_tip: Optional[Tuple[float, float]] = None    # junction end of stem

    def valid(self) -> bool:
        """True once at least one usable (>= 3 vertex) zone polygon exists."""
        return any(len(z.polygon) >= 3 for z in self.zones)

    def classify_point(self, x: float, y: float) -> Optional[str]:
        """Zone name containing normalized point (x, y), or None if in a gap.

        First matching polygon wins (author non-overlapping zones). Mirrors
        ``maze_state.ZoneMap.classify_point``.
        """
        for z in self.zones:
            if len(z.polygon) >= 3 and point_in_polygon(x, y, z.polygon):
                return z.name
        return None

    def in_well(self, x: float, y: float) -> bool:
        """True if the normalized point is inside the well sub-region."""
        return (self.well is not None and len(self.well) >= 3
                and point_in_polygon(x, y, self.well))

    def stem_progress(self, x: float, y: float) -> Optional[float]:
        """Unclamped scalar progress along the stem axis (0=well, 1=junction)."""
        if self.stem_base is None or self.stem_tip is None:
            return None
        return project_scalar(x, y, self.stem_base, self.stem_tip)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "ZoneMap":
        """Rebuild a ZoneMap from a parsed zones.json dict (mirror of maze_state)."""
        if not d:
            return ZoneMap()
        zones: List[Zone] = []
        for z in d.get("zones", []) or []:
            name = str(z.get("name", "")).strip()
            if name not in EDITABLE_ZONES:
                continue  # ignore unknown/UNKNOWN names rather than trust them
            verts = _clean_polygon(z.get("polygon_norm"))
            if len(verts) >= 3:
                zones.append(Zone(name, verts))
        well_verts = _clean_polygon(d.get("well_norm"))
        well = well_verts if len(well_verts) >= 3 else None
        base = tip = None
        axis = d.get("stem_axis")
        if isinstance(axis, dict):
            base = _clean_point(axis.get("base"))
            tip = _clean_point(axis.get("tip"))
        return ZoneMap(zones=tuple(zones), well=well, stem_base=base, stem_tip=tip)


def _clean_point(p) -> Optional[Tuple[float, float]]:
    try:
        return (float(p[0]), float(p[1]))
    except (TypeError, ValueError, IndexError):
        return None


def _clean_polygon(verts) -> Tuple[Tuple[float, float], ...]:
    out: List[Tuple[float, float]] = []
    for v in (verts or []):
        p = _clean_point(v)
        if p is not None:
            out.append(p)
    return tuple(out)


def load_zone_map(path: str) -> ZoneMap:
    """Load a ZoneMap from a bundle's ``config/zones.json`` file."""
    with open(path, "r", encoding="utf-8") as f:
        return ZoneMap.from_dict(json.load(f))


# ================================== notes ====================================
# Mirror of tmaze.core.session.session_notes: the note ORIGIN + the reserved
# ``auto:`` tag namespace for machine-emitted intervention events (docs/decisions/
# 0111/0137). Kept here so the analysis can read the notes JSONL without importing
# the rig core.
OPERATOR_SOURCE = "operator"
AUTO_SOURCE = "auto"

AUTO_TAG_PREFIX = "auto:"
AUTO_MANUAL_REWARD = "auto:manual_reward"
AUTO_DEBIAS_START = "auto:debias_start"
AUTO_DEBIAS_END = "auto:debias_end"
AUTO_ALT_START = "auto:alt_start"
AUTO_ALT_END = "auto:alt_end"
AUTO_PROBE_START = "auto:probe_start"
AUTO_PROBE_END = "auto:probe_end"
AUTO_PROBE_DISABLED = "auto:probe_disabled"
AUTO_PROBE_ENABLED = "auto:probe_enabled"
AUTO_REPEAT = "auto:repeat"
AUTO_SKIP = "auto:skip"
AUTO_BACK = "auto:back"
AUTO_RESUME = "auto:resume"


def is_auto_tag(tag: Optional[str]) -> bool:
    """Whether ``tag`` is in the reserved auto-event namespace (docs/decisions/0111)."""
    return bool(tag) and tag.startswith(AUTO_TAG_PREFIX)


def parse_notes_jsonl(text: str) -> List[Dict[str, Any]]:
    """Parse ``summary/notes_<id>.jsonl`` text into a list of entry dicts.

    Equivalent to ``[e.as_dict() for e in session_notes.parse_jsonl(text)
    .entries()]``: one dict per note with the same keys/defaults the rig writes,
    tolerating blank lines and a corrupt tail (a half-written last line still
    yields its intact predecessors). See docs/decisions/0074.
    """
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or "id" not in obj:
            continue
        try:
            out.append(_normalize_note(obj))
        except (TypeError, ValueError):
            continue
    return out


def _normalize_note(d: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one parsed note dict (mirror NoteEntry.from_dict -> as_dict)."""
    return {
        "id": int(d["id"]),
        "iso": str(d.get("iso", "")),
        "text": str(d.get("text", "")),
        "mono": d.get("mono"),
        "trial_index": d.get("trial_index"),
        "tag": d.get("tag"),
        "count": int(d.get("count", 1) or 1),
        "edited_iso": d.get("edited_iso"),
        "source": str(d.get("source", OPERATOR_SOURCE) or OPERATOR_SOURCE),
    }


# ============================== recordings roots =============================
# Where the rig writes session bundles. Mirrors tmaze.paths (the env contract),
# minus the in-repo landmark logic the analysis doesn't need. The machine default
# is overridable per-machine via TMAZE_RECORDINGS_DIR (see docs/decisions/0088);
# archive roots via TMAZE_RECORDINGS_ARCHIVES (docs/decisions/0113).
_RECORDINGS_ENV = "TMAZE_RECORDINGS_DIR"
_DEFAULT_CAMERA_OUTPUT_DIR = r"C:\Users\Celine\Documents\t-maze_recordings"
_RECORDINGS_ARCHIVES_ENV = "TMAZE_RECORDINGS_ARCHIVES"


def _camera_output_dir() -> Path:
    return Path(os.environ.get(_RECORDINGS_ENV) or _DEFAULT_CAMERA_OUTPUT_DIR)


def phase3_recordings_root() -> Path:
    """The PRIMARY recordings root: ``<camera_output_dir>/phase3_recordings``."""
    return _camera_output_dir() / "phase3_recordings"


def _phase3_recordings_archives() -> List[Path]:
    raw = os.environ.get(_RECORDINGS_ARCHIVES_ENV) or ""
    return [Path(p) for p in raw.split(os.pathsep) if p.strip()]


def phase3_recordings_roots() -> List[Path]:
    """The full recordings search path: primary root then any archive roots,
    order-preserving de-duplicated (mirror of ``tmaze.paths``)."""
    seen: set = set()
    out: List[Path] = []
    for p in [phase3_recordings_root(), *_phase3_recordings_archives()]:
        key = os.path.normcase(os.path.normpath(str(p)))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


# ============================== version guards ===============================
# The ``config/format.json`` contract this reader understands. New sessions carry
# it; legacy bundles predate it, so absence is NOT an error - compatibility is
# then inferred from zones.json ``version`` + the trial CSV ``proto=`` stamp.
FORMAT_FILENAME = "format.json"
FORMAT_VERSION = 1


class UnsupportedBundleFormat(RuntimeError):
    """Raised when a bundle's on-disk format is newer than this reader supports."""


@dataclass(frozen=True)
class FormatInfo:
    """What the version guard learned about a bundle's on-disk format."""
    format_version: Optional[int]   # from config/format.json, else None (legacy)
    proto: Optional[int]            # trial-CSV proto= stamp, when readable
    zones_version: Optional[int]    # config/zones.json version, when readable
    source: str                     # "format.json" or "inferred"
    raw: Dict[str, Any]             # the parsed format.json, or {}


def _read_json_quiet(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except (OSError, ValueError):
        return None


_PROTO_RE = re.compile(r"\bproto=(\d+)")


def _read_proto(bundle: str) -> Optional[int]:
    """Best-effort read of the ``proto=`` stamp from the trial-CSV ``#`` header."""
    task_dir = os.path.join(bundle, "task")
    try:
        names = sorted(os.listdir(task_dir))
    except OSError:
        return None
    for name in names:
        if name.startswith("phase3_") and name.endswith(".csv") \
                and not name.endswith("_excursions.csv"):
            try:
                with open(os.path.join(task_dir, name), "r",
                          encoding="utf-8") as fh:
                    head = "".join(fh.readline() for _ in range(12))
            except OSError:
                return None
            m = _PROTO_RE.search(head)
            return int(m.group(1)) if m else None
    return None


def read_format_info(bundle: str) -> FormatInfo:
    """Inspect a bundle's on-disk format without raising.

    Prefers ``config/format.json``; falls back to inferring from ``zones.json``
    ``version`` + the CSV ``proto=`` stamp for legacy bundles that predate the
    manifest.
    """
    manifest = _read_json_quiet(os.path.join(bundle, "config", FORMAT_FILENAME))
    zones = _read_json_quiet(os.path.join(bundle, "config", "zones.json")) or {}
    zones_version = zones.get("version") if isinstance(zones, dict) else None
    if manifest:
        fv = manifest.get("format_version")
        return FormatInfo(
            format_version=int(fv) if isinstance(fv, int) else fv,
            proto=manifest.get("proto", _read_proto(bundle)),
            zones_version=manifest.get("zones_version", zones_version),
            source="format.json",
            raw=manifest,
        )
    return FormatInfo(
        format_version=None,
        proto=_read_proto(bundle),
        zones_version=zones_version,
        source="inferred",
        raw={},
    )


def assert_supported(bundle: str) -> FormatInfo:
    """Raise :class:`UnsupportedBundleFormat` if ``bundle`` is a newer format than
    this reader supports; otherwise return the :class:`FormatInfo`.

    A missing ``format.json`` is treated as a legacy bundle and allowed. Only a
    positively-newer ``format_version`` (from the manifest) or ``zones.json``
    ``version`` fails - so old sessions stay readable while a genuinely
    incompatible future format fails loudly instead of being misread.
    """
    info = read_format_info(bundle)
    if info.format_version is not None and info.format_version > FORMAT_VERSION:
        raise UnsupportedBundleFormat(
            f"{bundle}: config/format.json format_version={info.format_version} "
            f"is newer than this reader supports (<= {FORMAT_VERSION}). "
            "Update the analysis package.")
    if info.zones_version is not None and info.zones_version > ZONES_VERSION:
        raise UnsupportedBundleFormat(
            f"{bundle}: zones.json version={info.zones_version} is newer than "
            f"this reader supports (<= {ZONES_VERSION}). Update the analysis "
            "package.")
    return info
