"""
maze_state.py - pure, headless maze-state estimator for the T-maze rig.

A pure core (like tracker.py / rules_engine.py) that reduces the markerless
tracker's continuous centroid into a DISCRETE, per-tick maze-state stream: which
zone the rat is in, the transition edges for this tick, a short zone history, an
optional direction along the central stem, and completed traversal loops. It has
NO Tk and NO OpenCV/numpy: it consumes a duck-typed TrackResult (fields read via
getattr, exactly like RulesEngine._make_frame) and a ZoneMap, and returns an
immutable RatState. The editor UI and any OpenCV/Pillow/Tk live in the separate
shell maze_state_view.py, so importing this module never needs the GUI deps.

Why this exists (state observability). The rig's body-beam sensors are
non-directional: a single beam break can't tell whether the rat is progressing
UP the central stem or turning back toward the reward well, so the firmware task
FSM can only disambiguate via which beam breaks NEXT. Continuous position can
resolve this directly - this module projects the centroid onto the stem
centerline to detect direction (up-stem vs returning) and tracks the zone
sequence to detect completed stem -> choice-arm -> stem loops, not just "which
region is the centroid in right now."

Coordinates. The tracker centroid is FULL-frame px of the already undistorted +
oriented frame (see docs/decisions/0015). Zones are stored NORMALIZED (0..1)
like the ROI (roi_polygon.json) and landmarks (landmarks.json), so they survive
preview/proc/full-frame scaling and the undistort pipeline; the point is
normalized by ``frame_size`` before classification. Point-in-polygon is
ray-casting; direction is a dot-product projection - all pure arithmetic, so no
numpy is needed here.

Contract (this is the coupling interface a future rules primitive / behavior-
statistics module will consume - both OUT OF SCOPE here; see docs/decisions/0025):
  - Zone vocabulary: HUB (the central reward zone where the well sits and the
    stem + both return arms converge; substates in_well / outside_well),
    CENTRAL_STEM, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN, RIGHT_RETURN, JUNCTION,
    UNKNOWN. The maze is a figure-8 / continuous-alternation loop: the rat runs
    HUB -> CENTRAL_STEM -> JUNCTION -> a choice arm -> its return arm -> HUB.
  - Per-tick RatState: current zone + substate; entered / exited edges;
    optional direction + stem_progress; loop_completed / loop_side; a bounded
    zone history with a visited_since() query; confidence + a validity flag.
  - When the tracker is COASTING/LOST or below the confidence threshold, the
    estimator emits zone=UNKNOWN, valid=False, and NEVER a false transition
    (internal committed state is held so a return to TRACKING resumes cleanly).

Thread-safety: intended to be driven from a single thread (the Tk main thread in
the GUI, where the poll loop runs), like rules_engine.py. It takes no locks.
"""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Deque, List, Optional, Sequence, Tuple

from tmaze.paths import host_dir

# ============================== zone vocabulary ==============================
HUB = "HUB"  # central reward zone: the well + where stem and both returns meet
CENTRAL_STEM = "CENTRAL_STEM"
LEFT_CHOICE = "LEFT_CHOICE"
RIGHT_CHOICE = "RIGHT_CHOICE"
LEFT_RETURN = "LEFT_RETURN"
RIGHT_RETURN = "RIGHT_RETURN"
JUNCTION = "JUNCTION"
UNKNOWN = "UNKNOWN"

# All zone names. UNKNOWN is an output-only state (never a drawn polygon).
ZONES = (HUB, CENTRAL_STEM, LEFT_CHOICE, RIGHT_CHOICE, LEFT_RETURN, RIGHT_RETURN,
         JUNCTION, UNKNOWN)
# Names an editor may assign to a drawn polygon (everything except UNKNOWN). HUB
# is listed first so, in any drawn overlap at the convergence, the reward zone
# wins the first-match classification.
EDITABLE_ZONES = (HUB, CENTRAL_STEM, JUNCTION, LEFT_CHOICE, RIGHT_CHOICE,
                  LEFT_RETURN, RIGHT_RETURN)

CHOICE_ZONES = (LEFT_CHOICE, RIGHT_CHOICE)
RETURN_ZONES = (LEFT_RETURN, RIGHT_RETURN)
# Zones where a stem centerline projection (direction / stem_progress) is
# meaningful. Kept to the stem + the junction it feeds into (not the HUB pool).
STEM_ZONES = (CENTRAL_STEM, JUNCTION)

# HUB substates (the well sits inside the HUB).
IN_WELL = "in_well"
OUTSIDE_WELL = "outside_well"

# Direction-along-stem values.
UP_STEM = "up_stem"      # toward the junction / choice arms (progress increasing)
DOWN_STEM = "down_stem"  # returning toward the reward well / start (decreasing)

# Which side of a completed loop.
LEFT = "left"
RIGHT = "right"

_CHOICE_SIDE = {LEFT_CHOICE: LEFT, RIGHT_CHOICE: RIGHT}

# Tracker state that means "confirmed lock" (kept local so this pure core does
# not import tracker.py / opencv). Must match tracker.STATE_TRACKING.
STATE_TRACKING = "TRACKING"

ZONES_VERSION = 1

# Default per-rig zone map, gitignored like roi_polygon.json / landmarks.json.
# It lives in host/ (beside the package), resolved by landmark search rather than
# relative to this module's location (see docs/decisions/0083).
ZONES_PATH = os.path.join(str(host_dir()), "zones.json")

# Physical maze adjacency (undirected). Documents the figure-8 topology the
# direction / loop model assumes; a transition between non-adjacent zones is
# still reported (the tracker/zone map is the ground truth), this is just the
# reference graph. The HUB is the convergence: stem and both return arms rejoin
# there.
ADJACENCY = {
    HUB: (CENTRAL_STEM, LEFT_RETURN, RIGHT_RETURN),
    CENTRAL_STEM: (HUB, JUNCTION),
    JUNCTION: (CENTRAL_STEM, LEFT_CHOICE, RIGHT_CHOICE),
    LEFT_CHOICE: (JUNCTION, LEFT_RETURN),
    RIGHT_CHOICE: (JUNCTION, RIGHT_RETURN),
    LEFT_RETURN: (LEFT_CHOICE, HUB),
    RIGHT_RETURN: (RIGHT_CHOICE, HUB),
}


# ============================== geometry helpers =============================
def point_in_polygon(x: float, y: float,
                     polygon: Sequence[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test (pure arithmetic, any coord space).

    Returns True if (x, y) is inside the polygon (>= 3 vertices). Points exactly
    on an edge are reported inconsistently, as is normal for ray casting; the
    min-dwell hysteresis in the estimator absorbs such border cases.
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
    """Scalar projection t of (x, y) onto the base->tip axis.

    t == 0 at base, t == 1 at tip (unclamped). Returns None for a degenerate
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

    Everything is in NORMALIZED (0..1) image coordinates so it survives scaling
    and the undistort-at-source pipeline (ADR-0015), like the ROI/landmarks.
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

        First matching polygon wins; author non-overlapping zones (the editor
        keeps them so). UNKNOWN is never returned here - a gap yields None and
        the estimator holds the current zone (sticky) rather than flicker.
        """
        for z in self.zones:
            if len(z.polygon) >= 3 and point_in_polygon(x, y, z.polygon):
                return z.name
        return None

    def in_well(self, x: float, y: float) -> bool:
        """True if the normalized point is inside the well sub-region (the well
        sits inside the HUB and drives its in_well substate)."""
        return (self.well is not None and len(self.well) >= 3
                and point_in_polygon(x, y, self.well))

    def stem_progress(self, x: float, y: float) -> Optional[float]:
        """Unclamped scalar progress along the stem axis (0=well, 1=junction)."""
        if self.stem_base is None or self.stem_tip is None:
            return None
        return project_scalar(x, y, self.stem_base, self.stem_tip)

    # ----------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        d: dict = {
            "version": ZONES_VERSION,
            "zones": [{"name": z.name,
                       "polygon_norm": [[float(x), float(y)] for x, y in z.polygon]}
                      for z in self.zones],
        }
        if self.well is not None:
            d["well_norm"] = [[float(x), float(y)] for x, y in self.well]
        if self.stem_base is not None and self.stem_tip is not None:
            d["stem_axis"] = {"base": [float(self.stem_base[0]), float(self.stem_base[1])],
                              "tip": [float(self.stem_tip[0]), float(self.stem_tip[1])]}
        return d

    @staticmethod
    def from_dict(d: Optional[dict]) -> "ZoneMap":
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


def load_zone_map(path: str = ZONES_PATH) -> ZoneMap:
    """Load a ZoneMap from a zones.json file."""
    with open(path, "r", encoding="utf-8") as f:
        return ZoneMap.from_dict(json.load(f))


def save_zone_map(zone_map: ZoneMap, path: str = ZONES_PATH) -> None:
    """Write a ZoneMap to a zones.json file (with a short _schema note)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    d = zone_map.to_dict()
    d["_schema"] = ("version:int; zones:[{name in "
                    f"{list(EDITABLE_ZONES)}, polygon_norm:[[x,y],...] in 0..1}}]; "
                    "well_norm:[[x,y],...] optional (drives the HUB in_well "
                    "substate; draw it inside the HUB); stem_axis:{base:[x,y], "
                    "tip:[x,y]} optional (direction/stem_progress, base=HUB/well "
                    "end, tip=junction end)")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


# ================================ parameters =================================
@dataclass
class MazeStateParams:
    """Tuning knobs for the estimator (all have safe defaults)."""
    min_confidence: float = 0.5      # gate below which the state is UNKNOWN/invalid
    min_dwell_ticks: int = 3         # consecutive ticks a NEW zone must persist to commit
    direction_deadband: float = 0.01  # min |delta progress| over the window to assign a direction
    direction_window: int = 3        # ticks of stem_progress used for the direction delta
    history_len: int = 32            # bounded zone-visit history length
    off_track_after_s: float = 1.0   # a valid point sitting in a polygon gap this long -> off_track (see docs/decisions/0170)


# ============================== output records ===============================
@dataclass(frozen=True)
class ZoneVisit:
    """One committed zone visit. ``exit_now`` is None while it is the current one."""
    zone: str
    enter_now: float
    exit_now: Optional[float] = None


@dataclass(frozen=True)
class RatState:
    """Per-tick discrete maze state (the coupling interface; see module docstring)."""
    zone: str                             # one of ZONES
    substate: Optional[str] = None        # in_well/outside_well when zone==HUB
    entered: Optional[str] = None         # zone entered this tick (edge), else None
    exited: Optional[str] = None          # zone exited this tick (edge), else None
    direction: Optional[str] = None       # up_stem/down_stem (stem/junction only), else None
    stem_progress: Optional[float] = None  # clamped 0..1 along the stem, else None
    loop_completed: bool = False          # a stem->choice-arm->stem loop closed this tick
    loop_side: Optional[str] = None       # "left"/"right" of the completed loop
    confidence: float = 0.0
    valid: bool = False                   # False => UNKNOWN (no trustworthy state this tick)
    now: float = 0.0
    history: Tuple[ZoneVisit, ...] = ()   # bounded recent visits (most recent last)
    off_track: bool = False               # valid point stuck in a polygon gap > off_track_after_s (docs/decisions/0170)

    def visited_since(self, target_zone: str, since_zone: str) -> bool:
        """Was ``target_zone`` entered since the animal last LEFT ``since_zone``?

        The canonical query the rules/stats consumers need, e.g.
        ``visited_since(LEFT_CHOICE, CENTRAL_STEM)`` == "was in the left choice
        arm since last leaving the stem". Anchored on the most recent CLOSED
        ``since_zone`` visit's exit time, so it answers correctly even while the
        animal is currently back in ``since_zone``. With no closed ``since_zone``
        visit yet, it checks the whole history for ``target_zone``.
        """
        anchor: Optional[float] = None
        for v in self.history:
            if v.zone == since_zone and v.exit_now is not None:
                anchor = v.exit_now
        if anchor is None:
            return any(v.zone == target_zone for v in self.history)
        return any(v.zone == target_zone and v.enter_now >= anchor
                   for v in self.history)


# ================================ estimator ==================================
class MazeStateEstimator:
    """Reduces a stream of TrackResults into a per-tick RatState stream.

    Pure (no Tk/OpenCV/numpy); driven single-threaded via ``update()``. Holds
    the committed zone, a min-dwell candidate counter (hysteresis), a bounded
    zone-visit history, a stem-progress window (direction), and the
    loop-in-progress side. ``update(track_result, now)`` returns a RatState.
    """

    def __init__(self, zone_map: Optional[ZoneMap] = None,
                 params: Optional[MazeStateParams] = None):
        self.zone_map = zone_map or ZoneMap()
        self.params = params or MazeStateParams()
        self.reset()

    def set_zone_map(self, zone_map: ZoneMap) -> None:
        """Swap in a new zone map and drop any in-progress classification."""
        self.zone_map = zone_map
        self.reset()

    def set_params(self, params: MazeStateParams) -> None:
        self.params = params

    def reset(self) -> None:
        """Drop all internal state (committed zone, history, direction window)."""
        self._committed: Optional[str] = None
        self._cand_zone: Optional[str] = None
        self._cand_count = 0
        self._visits: List[ZoneVisit] = []
        self._prog_window: Deque[float] = deque(maxlen=max(2, self.params.direction_window))
        self._choice_since_hub: Optional[str] = None  # loop-in-progress side (since leaving HUB)
        # ``now`` when the current unbroken run of valid-in-gap ticks began, else
        # None. Drives the off_track flag (docs/decisions/0170).
        self._gap_since: Optional[float] = None

    # ------------------------------------------------------------------ tick
    def update(self, track_result, now: float) -> RatState:
        """Fold one tracker result into the discrete state; return a RatState."""
        p = self.params
        state, confidence, centroid, frame_size = self._extract(track_result)
        w, h = frame_size
        track_ok = (state == STATE_TRACKING and confidence >= p.min_confidence
                    and centroid is not None and w > 0 and h > 0)

        if not track_ok:
            # Hold internal committed state; emit UNKNOWN, no edges, no loop. The
            # off_track gap timer is NOT touched here: a brief tracking dropout
            # mid-perch must not reset "how long the rat has been off track"
            # (docs/decisions/0170). off_track is reported False while invalid.
            self._cand_zone = None
            self._cand_count = 0
            self._prog_window.clear()
            return RatState(zone=UNKNOWN, confidence=confidence, valid=False,
                            now=now, history=tuple(self._visits))

        x = centroid[0] / float(w)
        y = centroid[1] / float(h)
        raw = self.zone_map.classify_point(x, y)

        entered = exited = None
        loop_completed = False
        loop_side: Optional[str] = None

        if raw is None:
            # In a gap between polygons: stay in the current zone (sticky),
            # clear any pending candidate so a brief gap does not commit.
            self._cand_zone = None
            self._cand_count = 0
        elif self._committed is None:
            # First localization: commit immediately (no prior zone to flicker).
            entered, exited, loop_completed, loop_side = self._commit(raw, now)
        elif raw == self._committed:
            self._cand_zone = None
            self._cand_count = 0
        else:
            # Candidate differs from committed: require min-dwell persistence.
            if raw == self._cand_zone:
                self._cand_count += 1
            else:
                self._cand_zone = raw
                self._cand_count = 1
            if self._cand_count >= max(1, p.min_dwell_ticks):
                entered, exited, loop_completed, loop_side = self._commit(raw, now)

        zone = self._committed if self._committed is not None else UNKNOWN
        valid = self._committed is not None

        # Off-track "perch" timer (docs/decisions/0170): a valid point in a polygon
        # GAP holds the current zone (sticky, above), so a rat that stops in a gap
        # would read as "in the stem" forever and never release the cue. Time how
        # long the point has sat in a gap; once it exceeds off_track_after_s, flag
        # off_track (zone + valid are UNCHANGED, so a consumer that ignores the flag
        # is unaffected). The timer clears the instant the point is inside ANY zone
        # again. Only runs once localized; the invalid path above holds it.
        if raw is None and self._committed is not None:
            if self._gap_since is None:
                self._gap_since = now
        else:
            self._gap_since = None
        off_track = (self._gap_since is not None
                     and (now - self._gap_since) >= p.off_track_after_s)

        substate = None
        if zone == HUB:
            substate = IN_WELL if self.zone_map.in_well(x, y) else OUTSIDE_WELL

        direction, stem_progress = self._direction(zone, x, y)

        return RatState(
            zone=zone, substate=substate, entered=entered, exited=exited,
            direction=direction, stem_progress=stem_progress,
            loop_completed=loop_completed, loop_side=loop_side,
            confidence=confidence, valid=valid, now=now,
            history=tuple(self._visits), off_track=off_track)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _extract(track_result):
        """Duck-type a TrackResult (mirrors RulesEngine._make_frame)."""
        if track_result is None:
            return "NONE", 0.0, None, (0, 0)
        state = getattr(track_result, "state", "NONE")
        confidence = float(getattr(track_result, "confidence", 0.0) or 0.0)
        frame_size = getattr(track_result, "frame_size", (0, 0)) or (0, 0)
        smoothed = getattr(track_result, "smoothed", None)
        raw = getattr(track_result, "centroid", None)
        centroid = smoothed if smoothed is not None else raw
        try:
            frame_size = (int(frame_size[0]), int(frame_size[1]))
        except (TypeError, ValueError, IndexError):
            frame_size = (0, 0)
        return state, confidence, centroid, frame_size

    def _commit(self, new_zone: str, now: float):
        """Commit a zone transition; update history + loop tracking.

        Returns (entered, exited, loop_completed, loop_side).
        """
        prev = self._committed
        # Close the current open visit, then open the new one.
        if self._visits and self._visits[-1].exit_now is None:
            self._visits[-1] = replace(self._visits[-1], exit_now=now)
        self._visits.append(ZoneVisit(new_zone, now))
        while len(self._visits) > max(1, self.params.history_len):
            self._visits.pop(0)

        loop_completed = False
        loop_side: Optional[str] = None
        # Loop model: a choice-arm visit between leaving and re-entering the HUB
        # closes a full figure-8 lap (HUB->stem->junction->choice->return->HUB).
        # Track the side while away from the HUB; report + reset it on HUB
        # re-entry.
        if new_zone == HUB:
            if self._choice_since_hub is not None:
                loop_completed = True
                loop_side = self._choice_since_hub
            self._choice_since_hub = None
        elif new_zone in _CHOICE_SIDE:
            self._choice_since_hub = _CHOICE_SIDE[new_zone]

        self._committed = new_zone
        self._cand_zone = None
        self._cand_count = 0
        # A new zone means the stem projection history is no longer continuous.
        if new_zone not in STEM_ZONES:
            self._prog_window.clear()
        return new_zone, prev, loop_completed, loop_side

    def _direction(self, zone: str, x: float, y: float):
        """Direction + clamped stem_progress when on the stem/junction, else None."""
        if zone not in STEM_ZONES:
            self._prog_window.clear()
            return None, None
        prog = self.zone_map.stem_progress(x, y)
        if prog is None:
            return None, None
        self._prog_window.append(prog)
        clamped = min(1.0, max(0.0, prog))
        direction = None
        if len(self._prog_window) >= 2:
            delta = self._prog_window[-1] - self._prog_window[0]
            if delta > self.params.direction_deadband:
                direction = UP_STEM
            elif delta < -self.params.direction_deadband:
                direction = DOWN_STEM
        return direction, clamped


# ============================== CSV row helpers ==============================
# Pure formatting helpers for the state-timeline CSV (the shell / the future
# stats module writes the file; this core only shapes the rows).
STATE_TIMELINE_COLUMNS = (
    "now", "zone", "substate", "entered", "exited", "direction",
    "stem_progress", "loop_completed", "loop_side", "confidence", "valid",
)


def state_timeline_header() -> List[str]:
    return list(STATE_TIMELINE_COLUMNS)


def state_row(s: RatState) -> tuple:
    """Flatten a RatState into STATE_TIMELINE_COLUMNS values (blanks for None)."""
    return (
        f"{s.now:.6f}",
        s.zone,
        s.substate or "",
        s.entered or "",
        s.exited or "",
        s.direction or "",
        ("" if s.stem_progress is None else f"{s.stem_progress:.4f}"),
        int(s.loop_completed),
        s.loop_side or "",
        f"{s.confidence:.3f}",
        int(s.valid),
    )
