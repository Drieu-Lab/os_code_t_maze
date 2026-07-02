"""
rules_engine.py - UI-free position-based task-rule engine for the T-maze rig.

A pure, headless-testable core (like tracker.py) that watches the markerless
tracker's centroid, the annotated landmark points, and the rig's lick events, and
DECIDES when actions should fire. It never sends serial, never touches Tk, and
never writes files: tick()/event() RETURN a list of action *intents*
(FireReward / LogEvent). A thin GUI executor (rules_view.py) is the single thing
that actuates those intents - and only when explicitly enabled and safe. This
separation (evaluation here, execution + authority in the glue) is deliberate:
the firmware remains the reward authority for its own Full-task logic; the host
executor only actuates in Free Roam. See docs/decisions/0023.

Coordinates. The tracker centroid is in FULL-frame pixels of the already
undistorted + oriented frame (ADR-0015); landmarks are normalized (0..1) and map
into that same px space via frame_size. Rule distances are specified in
centimeters and converted to that px space with a spatial calibration (Scale):

  - Baseline: a single global ``cm_per_px`` (linear scale; good because the feed
    is lens-corrected and the floor is planar/overhead). See rig_scale.json +
    the calibration UI in rules_view.py.
  - Upgrade: an optional image(px) -> world(cm) 3x3 homography. When present,
    distances are computed in world-cm space (perspective-correct); otherwise the
    engine falls back to the global scale. Applying a 3x3 is pure arithmetic, so
    no numpy is needed here (findHomography lives in the glue).

Design (see ADR-0023):
  - Scale: cm<->px conversion (global scale and/or homography).
  - Condition primitives: InZone(landmark, radius_cm), TrackConfident(min_conf),
    LickAt(port) - evaluated against a per-tick Frame context.
  - Action intents: FireReward(port, ms, ...), LogEvent(name, ...).
  - Rule templates (declarative, parameterized) so a non-coder edits rules.json
    without touching Python. First template: prime_and_lick_topup.
  - RulesEngine: holds rules + gating (per-rule cooldown, per-visit max count,
    re-arm on exit, lick debounce, and a global max-reward-rate backstop against
    runaway firing). tick(track_result, landmarks, scale, now) and
    event(line, now) return List[Action]; the engine holds all state.

Thread-safety: the engine is intended to be driven from a single thread (the Tk
main thread in the GUI, which is where both the poll loop and serial-line
callbacks run). It takes no locks.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Tracker state string that means "confirmed lock" (kept local so this core does
# not import tracker.py / opencv). Must match tracker.STATE_TRACKING.
STATE_TRACKING = "TRACKING"

# How stale the last tick may be (seconds) for a lick to still count as "within
# the zone at lick time". Ticks arrive at the GUI poll rate (~150 ms); this
# tolerates a couple of missed ticks without letting a long-past position gate a
# lick.
CONJUNCTION_FRESH_S = 0.5

RULES_VERSION = 1
SCALE_VERSION = 1


# ================================== scale ====================================
def _project(H: List[List[float]], p: Tuple[float, float]) -> Tuple[float, float]:
    """Apply a 3x3 homography to an (x, y) point (pure arithmetic)."""
    x, y = p
    denom = H[2][0] * x + H[2][1] * y + H[2][2]
    if abs(denom) < 1e-12:
        denom = 1e-12
    u = (H[0][0] * x + H[0][1] * y + H[0][2]) / denom
    v = (H[1][0] * x + H[1][1] * y + H[1][2]) / denom
    return (u, v)


@dataclass
class Scale:
    """Spatial calibration: converts between full-frame px and centimeters.

    ``cm_per_px`` is the baseline global linear scale. ``homography`` (optional)
    is a 3x3 image(px) -> world(cm) transform; when present, distances are
    measured in world-cm space (perspective-correct) and cm_per_px is only used
    for the overlay circle (cm_to_px).
    """
    cm_per_px: Optional[float] = None
    homography: Optional[List[List[float]]] = None

    def valid(self) -> bool:
        return (self.cm_per_px is not None and self.cm_per_px > 0) \
            or self.homography is not None

    def distance_cm(self, p_px: Tuple[float, float],
                    q_px: Tuple[float, float]) -> float:
        """Distance in centimeters between two full-frame px points."""
        if self.homography is not None:
            a = _project(self.homography, p_px)
            b = _project(self.homography, q_px)
            return math.hypot(a[0] - b[0], a[1] - b[1])
        return math.hypot(p_px[0] - q_px[0], p_px[1] - q_px[1]) * float(self.cm_per_px)

    def cm_to_px(self, cm: float) -> Optional[float]:
        """A radius in cm -> px (for the preview circle). None if unknown.

        Uses the global scale; with only a homography (no cm_per_px) the px
        radius is position-dependent, so we return None and the overlay is
        skipped (distances are still correct via distance_cm).
        """
        if self.cm_per_px is not None and self.cm_per_px > 0:
            return cm / float(self.cm_per_px)
        return None

    @staticmethod
    def from_dict(d: Optional[dict]) -> "Scale":
        if not d:
            return Scale()
        cm_per_px = d.get("cm_per_px")
        H = d.get("homography")
        return Scale(
            cm_per_px=(float(cm_per_px) if cm_per_px else None),
            homography=([[float(v) for v in row] for row in H] if H else None),
        )


def load_scale(path: str) -> Scale:
    """Load a Scale from a rig_scale.json file (see rules_view.py)."""
    with open(path, "r", encoding="utf-8") as f:
        return Scale.from_dict(json.load(f))


def resolve_landmark(landmarks: List[dict], ref: dict,
                     frame_size: Tuple[int, int]) -> Optional[Tuple[float, float]]:
    """Full-frame px location of the landmark matching ref {kind, index}."""
    w, h = frame_size
    if not w or not h:
        return None
    kind = ref.get("kind")
    index = ref.get("index")
    for lm in landmarks:
        if lm.get("kind") == kind and lm.get("index") == index:
            try:
                return (float(lm["x"]) * w, float(lm["y"]) * h)
            except (KeyError, TypeError, ValueError):
                return None
    return None


# ============================== frame context ================================
@dataclass
class Frame:
    """Per-tick context handed to condition primitives."""
    centroid_px: Optional[Tuple[float, float]]
    frame_size: Tuple[int, int]
    confidence: float
    state: str
    now: float
    landmarks: List[dict]
    scale: Scale


# ============================ condition primitives ===========================
@dataclass
class InZone:
    """True when the centroid is within radius_cm of the referenced landmark."""
    landmark: dict            # {kind, index}
    radius_cm: float

    def evaluate(self, frame: Frame) -> bool:
        if frame.centroid_px is None:
            return False
        lm = resolve_landmark(frame.landmarks, self.landmark, frame.frame_size)
        if lm is None:
            return False
        return frame.scale.distance_cm(frame.centroid_px, lm) <= self.radius_cm


@dataclass
class TrackConfident:
    """True when the track is a confirmed, confident lock."""
    min_conf: float

    def evaluate(self, frame: Frame) -> bool:
        return frame.state == STATE_TRACKING and frame.confidence >= self.min_conf


@dataclass
class LickAt:
    """Marker condition: a lick at ``port``. Routed by the engine's event()."""
    port: int

    def matches(self, port: int) -> bool:
        return int(port) == int(self.port)


# ================================ action intents =============================
@dataclass
class FireReward:
    """Intent: pulse the reward spout at ``port`` for ``ms`` ms."""
    port: int
    ms: int
    rule_id: str
    reason: str               # "prime" | "topup"


@dataclass
class LogEvent:
    """Intent: record a rule event (no hardware effect)."""
    name: str
    rule_id: str
    detail: dict = field(default_factory=dict)


# ================================ rule templates =============================
@dataclass
class EngineDefaults:
    min_confidence: float = 0.5
    # Two distinct timers (see ADR-0023): ``topup_cooldown_ms`` spaces fires
    # WITHIN a visit (prime -> top-up, top-up -> top-up) and should be short;
    # ``rearm_refractory_ms`` is the minimum time since the last fire before the
    # rule may PRIME again on a fresh visit - the anti-camp / between-visit timer
    # (typically much longer). The legacy single ``cooldown_ms`` key, if present,
    # seeds both for backward compatibility.
    topup_cooldown_ms: int = 500
    rearm_refractory_ms: int = 5000
    global_min_interval_ms: int = 500

    @staticmethod
    def from_dict(d: Optional[dict]) -> "EngineDefaults":
        d = d or {}
        legacy = d.get("cooldown_ms")  # back-compat: seeds both timers
        return EngineDefaults(
            min_confidence=float(d.get("min_confidence", 0.5)),
            topup_cooldown_ms=int(d.get(
                "topup_cooldown_ms", legacy if legacy is not None else 500)),
            rearm_refractory_ms=int(d.get(
                "rearm_refractory_ms", legacy if legacy is not None else 5000)),
            global_min_interval_ms=int(d.get("global_min_interval_ms", 500)),
        )


class PrimeAndLickTopupRule:
    """Prime + lick-triggered top-up at a reward well.

    When the tracked centroid comes within ``radius_cm`` of ``landmark`` (while
    the track is confident), dispense once ("prime"). Then each subsequent lick
    at ``port`` while still within the zone dispenses again, up to ``max_topups``
    more times. The animal must LEAVE the zone (``rearm_on_exit``) AND wait out
    the ``rearm_refractory_ms`` timer before the rule will prime again - so
    camping at the well, or quick out-and-back, does not keep paying out. All
    thresholds are parameters.

    Two timers (see ADR-0023):
      - ``topup_cooldown_ms`` spaces fires WITHIN a visit (prime -> top-up and
        top-up -> top-up); short so top-ups feel responsive.
      - ``rearm_refractory_ms`` is the minimum time since the last fire before a
        fresh entry may prime again; the anti-camp / between-visit timer.

    State: OUTSIDE (not in zone) / PRIMED (in zone, awaiting licks). Top-ups only
    happen after a prime actually fired this visit (``_primed_this_visit``), so a
    re-entry blocked by the refractory yields no reward at all. The conjunction
    "within R at lick time" uses the most recent tick's inside + confidence,
    guarded by freshness so a stale position can't gate a lick.
    """

    TEMPLATE = "prime_and_lick_topup"

    def __init__(self, params: dict, defaults: EngineDefaults):
        self.id = str(params.get("id", "prime_and_lick_topup"))
        self.enabled = bool(params.get("enabled", True))
        self.landmark = dict(params["landmark"])
        self.radius_cm = float(params["radius_cm"])
        self.port = int(params["port"])
        self.pulse_ms = int(params.get("pulse_ms", 150))
        self.max_topups = int(params.get("max_topups", 2))
        self.min_conf = float(params.get("min_confidence", defaults.min_confidence))
        self.lick_debounce_ms = int(params.get("lick_debounce_ms", 150))
        # Legacy single "cooldown_ms" seeds both timers if the split keys are
        # absent (back-compat with pre-refinement rules.json). See ADR-0023.
        legacy = params.get("cooldown_ms")
        self.topup_cooldown_ms = int(params.get(
            "topup_cooldown_ms",
            legacy if legacy is not None else defaults.topup_cooldown_ms))
        self.rearm_refractory_ms = int(params.get(
            "rearm_refractory_ms",
            legacy if legacy is not None else defaults.rearm_refractory_ms))
        self.rearm_on_exit = bool(params.get("rearm_on_exit", True))

        self._in_zone = InZone(self.landmark, self.radius_cm)
        self._confident = TrackConfident(self.min_conf)
        self._lick = LickAt(self.port)

        # Mutable state.
        self.state = "OUTSIDE"
        self.topups = 0
        self.fires = 0
        self._primed_this_visit = False
        self._last_inside = False
        self._last_conf_ok = False
        self._last_tick_now: Optional[float] = None
        self._last_fire_now: Optional[float] = None
        self._last_lick_now: Optional[float] = None

    # --------------------------------------------------------------- helpers
    def _elapsed_ms(self, now: float) -> Optional[float]:
        if self._last_fire_now is None:
            return None
        return (now - self._last_fire_now) * 1000.0

    def _prime_ok(self, now: float) -> bool:
        """The between-visit refractory: enough time since the last fire."""
        el = self._elapsed_ms(now)
        return el is None or el >= self.rearm_refractory_ms

    def _topup_ok(self, now: float) -> bool:
        """The within-visit cooldown: enough time since the last fire."""
        el = self._elapsed_ms(now)
        return el is None or el >= self.topup_cooldown_ms

    def _fire(self, now: float, reason: str) -> FireReward:
        self._last_fire_now = now
        self.fires += 1
        return FireReward(self.port, self.pulse_ms, self.id, reason)

    # ------------------------------------------------------------------ tick
    def tick(self, frame: Frame) -> List:
        conf_ok = self._confident.evaluate(frame)
        inside = conf_ok and self._in_zone.evaluate(frame)
        # Record for the lick-time conjunction.
        self._last_inside = inside
        self._last_conf_ok = conf_ok
        self._last_tick_now = frame.now

        actions: List = []
        if inside and self.state == "OUTSIDE":
            # Entered the zone -> a new visit. Prime once, but only if the
            # between-visit refractory has elapsed (else this visit pays nothing:
            # no prime => no top-ups, so camping/quick re-entry is not rewarded).
            self.state = "PRIMED"
            self.topups = 0
            self._primed_this_visit = False
            if self._prime_ok(frame.now):
                actions.append(self._fire(frame.now, "prime"))
                self._primed_this_visit = True
        elif not inside and self.state == "PRIMED":
            if self.rearm_on_exit:
                self.state = "OUTSIDE"
                self.topups = 0
                actions.append(LogEvent("rearm", self.id, {}))
        return actions

    # ------------------------------------------------------------- lick edge
    def on_lick(self, port: int, now: float) -> List:
        if not self._lick.matches(port):
            return []
        if self.state != "PRIMED":
            return []
        # No prime this visit (e.g. re-entered inside the refractory) -> no
        # top-ups either.
        if not self._primed_this_visit:
            return []
        # Debounce rapid lick edges (electrical bounce / bursts).
        if self._last_lick_now is not None and \
                (now - self._last_lick_now) * 1000.0 < self.lick_debounce_ms:
            return []
        self._last_lick_now = now
        # Conjunction: still inside + confident as of a recent tick.
        if not (self._last_inside and self._last_conf_ok):
            return []
        if self._last_tick_now is None or \
                (now - self._last_tick_now) > CONJUNCTION_FRESH_S:
            return []
        if self.topups >= self.max_topups:
            return []
        if not self._topup_ok(now):
            return []
        self.topups += 1
        return [self._fire(now, "topup")]

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        return {"id": self.id, "state": self.state, "topups": self.topups,
                "max_topups": self.max_topups, "fires": self.fires}


TEMPLATES = {
    PrimeAndLickTopupRule.TEMPLATE: PrimeAndLickTopupRule,
}


def build_rule(params: dict, defaults: EngineDefaults):
    """Construct a rule from its declarative params (picks a template)."""
    name = params.get("template")
    cls = TEMPLATES.get(name)
    if cls is None:
        raise ValueError(
            f"unknown rule template {name!r}; known: {sorted(TEMPLATES)}")
    return cls(params, defaults)


# ================================ the engine =================================
class RulesEngine:
    """Owns the rules + gating; returns action intents from tick()/event().

    Enforces per-rule cooldown + per-visit count + re-arm (inside each rule) and
    a GLOBAL max-reward-rate backstop here (``global_min_interval_ms``) that
    drops any FireReward too soon after the last accepted one, regardless of
    which rule produced it - a last-resort guard against runaway firing.
    """

    def __init__(self, rules: List, defaults: EngineDefaults, enabled: bool = False):
        self.rules = rules
        self.defaults = defaults
        self.enabled = enabled
        self._last_global_fire_now: Optional[float] = None

    @classmethod
    def from_config(cls, config: dict) -> "RulesEngine":
        defaults = EngineDefaults.from_dict(config.get("defaults"))
        rules = [build_rule(rp, defaults) for rp in config.get("rules", [])]
        return cls(rules, defaults, enabled=bool(config.get("enabled", False)))

    # -------------------------------------------------------------- driving
    def _make_frame(self, track_result, landmarks, scale: Scale,
                    now: float) -> Frame:
        centroid = None
        frame_size = (0, 0)
        confidence = 0.0
        state = "NONE"
        if track_result is not None:
            state = getattr(track_result, "state", "NONE")
            confidence = float(getattr(track_result, "confidence", 0.0) or 0.0)
            frame_size = getattr(track_result, "frame_size", (0, 0)) or (0, 0)
            smoothed = getattr(track_result, "smoothed", None)
            raw = getattr(track_result, "centroid", None)
            centroid = smoothed if smoothed is not None else raw
        return Frame(centroid, tuple(frame_size), confidence, state, now,
                     landmarks or [], scale)

    def tick(self, track_result, landmarks, scale: Optional[Scale],
             now: float) -> List:
        """Evaluate all rules for one frame; return action intents."""
        if not self.enabled:
            return []
        if scale is None or not scale.valid():
            return []
        frame = self._make_frame(track_result, landmarks, scale, now)
        out: List = []
        for rule in self.rules:
            if getattr(rule, "enabled", True):
                out.extend(rule.tick(frame))
        return self._apply_global(out, now)

    def event(self, line: str, now: float) -> List:
        """Feed one firmware serial line (only rising lick edges act)."""
        if not self.enabled:
            return []
        port = self._parse_lick(line)
        if port is None:
            return []
        out: List = []
        for rule in self.rules:
            if getattr(rule, "enabled", True):
                out.extend(rule.on_lick(port, now))
        return self._apply_global(out, now)

    # --------------------------------------------------------------- gating
    def _apply_global(self, actions: List, now: float) -> List:
        """Apply the global rate backstop and emit a LogEvent per accepted fire.

        Rules emit FireReward intents (carrying a ``reason``); the engine pairs
        each accepted fire with a LogEvent(reason) for the executor to record,
        and replaces a rate-limited fire with a LogEvent("suppressed").
        """
        out: List = []
        for a in actions:
            if isinstance(a, FireReward):
                if self._last_global_fire_now is not None and \
                        (now - self._last_global_fire_now) * 1000.0 \
                        < self.defaults.global_min_interval_ms:
                    out.append(LogEvent("suppressed", a.rule_id,
                                        {"reason": "global_backstop",
                                         "intended": a.reason, "port": a.port}))
                    continue
                self._last_global_fire_now = now
                out.append(a)
                out.append(LogEvent(a.reason, a.rule_id,
                                    {"port": a.port, "pulse_ms": a.ms}))
            else:
                out.append(a)
        return out

    @staticmethod
    def _parse_lick(line: str) -> Optional[int]:
        """Return the port of a rising lick edge (``<ms>,L,<i>,1``) else None."""
        fields = line.split(",")
        if len(fields) == 4 and fields[1] == "L" and fields[2].strip().isdigit() \
                and fields[3].strip() == "1":
            return int(fields[2])
        return None

    # ---------------------------------------------------------------- status
    def status(self) -> List[dict]:
        return [rule.status() for rule in self.rules
                if hasattr(rule, "status")]


def load_rules_config(path: str) -> dict:
    """Load and return the rules.json config dict."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
