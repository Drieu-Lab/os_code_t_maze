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
    re-arm on exit, lick debounce). tick(track_result, landmarks, scale, now)
    and event(line, now) return List[Action]; the engine holds all state. The
    global max-reward-rate backstop is no longer here - it moved to the shared
    reward_dispatcher.RewardDispatcher (see docs/decisions/0054).

Thread-safety: the engine is intended to be driven from a single thread (the Tk
main thread in the GUI, which is where both the poll loop and serial-line
callbacks run). It takes no locks.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from tmaze.core import protocol  # shared serial-line parser (pure stdlib; see docs/decisions/0024)
# The well-reward "how" (WellDispenser) and "why" (RewardTrigger) live in the
# pure reward_dispenser core (see docs/decisions/0068, FW-0009). The shared
# reward primitives (FireReward / LogEvent / LickAt / CONJUNCTION_FRESH_S) are
# defined there too and re-exported here so ``from rules_engine import
# FireReward`` and the landmark rule below keep working. The dependency runs one
# way: rules_engine -> reward_dispenser (that core imports nothing from here).
from tmaze.core.reward.reward_dispenser import (  # noqa: F401  (LickAt/CONJUNCTION_FRESH_S re-export)
    CONJUNCTION_FRESH_S, FireReward, LickAt, LogEvent, RewardTrigger, WellDispenser,
)

# Tracker state string that means "confirmed lock" (kept local so this core does
# not import tracker.py / opencv). Must match tracker.STATE_TRACKING.
STATE_TRACKING = "TRACKING"

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
    """Per-tick context handed to condition primitives.

    ``task`` is the optional task-semantic context (a task_events.TaskEventFrame)
    for zone/loop-based rule templates; it is None for the legacy landmark/scale
    templates, which ignore it. Kept duck-typed (no import of task_events here) so
    this pure core stays free of that dependency. See docs/decisions/0026.
    """
    centroid_px: Optional[Tuple[float, float]]
    frame_size: Tuple[int, int]
    confidence: float
    state: str
    now: float
    landmarks: List[dict]
    scale: Scale
    task: Optional[object] = None


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


# ``LickAt`` (marker condition) and the action intents ``FireReward`` /
# ``LogEvent`` now live in reward_dispenser.py and are imported at the top of this
# module (re-exported for back-compat). See docs/decisions/0068.


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
    needs_scale = True   # cm<->px distances require a valid Scale
    needs_task = False   # landmark/scale based; no zone/task context

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


class WellRewardWithExcursionRule:
    """Credential-gated well reward for the reward-shaping curriculum.

    Reward at the central well is CREDENTIALED by a behavioral excursion. The
    animal must first earn a credit somewhere in the maze; only then does the
    next visit to the well pay out (a single "prime"), after which licks top up
    (up to ``max_topups``) as in prime_and_lick_topup. The credit is consumed by
    the prime, so each payout requires a fresh excursion - this is the
    anti-camping / build-on-each-other mechanism of the phases (see ADR-0026).

    The ``credential`` parameter selects the phase, all reading the SAME
    task-event vocabulary (task_events.py), so the phases are pure config, not
    new code:
      - "choice"          (Phase 1): entering EITHER choice arm credits. Teaches
                          well = reward and encourages whole-maze exploration.
      - "choice_via_stem" (Phase 2a/2b): entering a choice arm credits ONLY if
                          the outbound leg used the central stem. Shapes
                          unidirectional stem travel. With a ``debias`` cap it is
                          Phase 2b (adds choice-side anti-bias).
      - "clean_loop"      : a completed clean loop (stem out + matching return
                          arm) credits, subject to an optional anti-bias debias
                          cap on the same-side loop run length.

    ``prime_on`` selects prime timing: "well_arrival" (default) primes the moment
    the animal reaches the well while credited; "well_lick" is lick-gated (the
    first lick at the well while credited fires the prime); "credential" fires the
    prime the instant the credential arms, out at the choice zone, so the
    central-spout solenoid becomes the correct-choice signal (Phase 2a/2b),
    matching the full cue-guided task. The legacy boolean ``prime_on_arrival`` is
    still accepted (True->"well_arrival", False->"well_lick"). See ADR-0030.

    Debias (``clean_loop`` only): ``debias`` = {"mode":"fixed","n":3} caps the
    same-side run at a fixed N; {"mode":"uniform","min":3,"max":5,"seed":...}
    redraws the cap uniformly each time the run resets (a side switch), so the
    animal cannot learn a fixed alternation period. When the current same-side
    run exceeds the cap, the clean loop is logged but WITHHELD (no credit),
    gently pushing the animal to the other side. The run counts every completed
    loop, rewarded or withheld (task_events.py), so the pressure reflects actual
    behavior. See ADR-0026.

    State: OUTSIDE / AT_WELL, mirroring prime_and_lick_topup, plus ``armed`` (a
    pending credit). Fail-safe: with no confident position (task None / invalid)
    nothing fires, and the well visit is only exited on a confident outside
    reading so a brief track loss does not drop the visit or double-prime.
    """

    TEMPLATE = "well_reward_with_excursion"
    needs_scale = False   # zone/loop based; no cm scale required
    needs_task = True     # requires the task-event context (zone map + estimator)
    # Canonical tuples live on RewardTrigger; kept as class attributes here
    # because the Rules-tab editor reads them (RULE_FIELD_CHOICES / validation).
    # Only the choice-family credentials are gated on the shared once-per-
    # excursion choice-commit edge (task_events.choice_committed; docs/decisions/
    # 0055, FW-0020); clean_loop earns at loop close and needs no latch.
    _CREDENTIALS = RewardTrigger.CREDENTIALS
    _CHOICE_CREDENTIALS = RewardTrigger.CHOICE_CREDENTIALS

    def __init__(self, params: dict, defaults: EngineDefaults):
        self.id = str(params.get("id", "well_reward_with_excursion"))
        self.enabled = bool(params.get("enabled", True))
        # Compose the two pure cores (docs/decisions/0068): the Trigger owns the
        # credential "why/when" (and the single pending-credit flag ``armed``);
        # the Dispenser owns the well "how" (port/pulse, the visit lifecycle,
        # prime delivery, lick top-ups). This rule is now just their wiring - the
        # only place that knows both, so ``prime_on`` (which credit edge the
        # dispenser spends) is applied here.
        self._trigger = RewardTrigger(params, self.id)
        self._dispenser = WellDispenser(
            params, default_topup_cooldown_ms=defaults.topup_cooldown_ms,
            rule_id=self.id)
        # Read-only mirrors used by the wiring below and status().
        self.credential = self._trigger.credential
        self.prime_on = self._dispenser.prime_on

    # The credit (``armed``) is owned by the Trigger; expose it as this rule's own
    # attribute so existing callers (status(), the Rules tab) and the direct-drive
    # credential tests keep working unchanged.
    @property
    def armed(self) -> bool:
        return self._trigger.armed

    @armed.setter
    def armed(self, value: bool) -> None:
        self._trigger.armed = bool(value)

    def _earn_credential(self, task) -> List:
        """Delegate to the Trigger (kept for direct-drive tests of the fade)."""
        return self._trigger._earn_credential(task)

    # ------------------------------------------------------------------ tick
    def tick(self, frame: Frame) -> List:
        task = frame.task
        if task is None:
            return []
        actions: List = []
        if task.valid:
            # The Trigger folds the frame, gating the choice-family credentials on
            # the shared once-per-excursion commit edge and arming a credit when
            # earned (docs/decisions/0026, 0028, 0030, 0055).
            actions.extend(self._trigger.earn(task))
            # Prime the instant the credential arms (Phase 2a/2b): the dispenser
            # fires out at the choice zone so the central spout signals the correct
            # choice, and the credit is spent. See docs/decisions/0030.
            if self.prime_on == "credential" and self._trigger.armed:
                actions.append(self._dispenser.prime_at_credential(task.now))
                self._trigger.armed = False

        # The dispenser advances the well-visit lifecycle and, for the arrival/lick
        # modes, spends the pending credit; it reports back so we clear the Trigger.
        res = self._dispenser.visit_tick(task, credit_pending=self._trigger.armed)
        actions.extend(res.actions)
        if res.consumed_credit:
            self._trigger.armed = False
        return actions

    # ------------------------------------------------------------- lick edge
    def on_lick(self, port: int, now: float) -> List:
        res = self._dispenser.on_lick(port, now,
                                      credit_pending=self._trigger.armed)
        if res.consumed_credit:
            self._trigger.armed = False
        return res.actions

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        return {"id": self.id, "state": self._dispenser.state,
                "armed": self._trigger.armed,
                "credential": self._trigger.credential,
                "prime_on": self._dispenser.prime_on,
                "topups": self._dispenser.topups,
                "max_topups": self._dispenser.max_topups,
                "fires": self._dispenser.fires,
                "nonstem_reward_prob": self._trigger.nonstem_reward_prob}


TEMPLATES = {
    PrimeAndLickTopupRule.TEMPLATE: PrimeAndLickTopupRule,
    WellRewardWithExcursionRule.TEMPLATE: WellRewardWithExcursionRule,
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
    returns raw ``FireReward`` intents (plus the rules' own ``LogEvent``s). The
    GLOBAL max-reward-rate backstop no longer lives here: it moved to the shared
    ``reward_dispatcher.RewardDispatcher`` so the Rules engine and the Phase 3
    controller share ONE backstop + reward record (see docs/decisions/0054,
    FW-0019). ``EngineDefaults.global_min_interval_ms`` is still read from
    rules.json and pushed into that shared dispatcher by the Rules tab.
    """

    def __init__(self, rules: List, defaults: EngineDefaults, enabled: bool = False):
        self.rules = rules
        self.defaults = defaults
        self.enabled = enabled

    @classmethod
    def from_config(cls, config: dict) -> "RulesEngine":
        defaults = EngineDefaults.from_dict(config.get("defaults"))
        rules = [build_rule(rp, defaults) for rp in config.get("rules", [])]
        return cls(rules, defaults, enabled=bool(config.get("enabled", False)))

    # -------------------------------------------------------------- driving
    def _make_frame(self, track_result, landmarks, scale: Scale,
                    now: float, task=None) -> Frame:
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
                     landmarks or [], scale, task)

    def tick(self, track_result, landmarks, scale: Optional[Scale],
             now: float, task=None) -> List:
        """Evaluate all rules for one frame; return action intents.

        ``scale`` may be None for zone/task-based rules (they need no cm scale);
        the per-rule ``needs_scale`` flag decides whether a missing scale gates
        that rule. ``task`` is the optional task-event context. See ADR-0026.
        """
        if not self.enabled:
            return []
        scale_ok = scale is not None and scale.valid()
        task_ok = task is not None
        frame = self._make_frame(track_result, landmarks, scale, now, task)
        out: List = []
        for rule in self.rules:
            if not getattr(rule, "enabled", True):
                continue
            # Skip any rule whose capability is missing (ADR-0026): rules that
            # measure cm distances need a valid scale; zone/loop rules need the
            # task-event context. Rules declaring neither run unconditionally.
            if getattr(rule, "needs_scale", False) and not scale_ok:
                continue
            if getattr(rule, "needs_task", False) and not task_ok:
                continue
            out.extend(rule.tick(frame))
        return out

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
        return out

    # --------------------------------------------------------------- gating
    @staticmethod
    def _parse_lick(line: str) -> Optional[int]:
        """Return the port of a rising lick edge (``<ms>,L,<i>,1``) else None."""
        edge = protocol.lick_edge(line)
        if edge is not None and edge[1]:
            return edge[0]
        return None

    # ---------------------------------------------------------------- status
    def status(self) -> List[dict]:
        return [rule.status() for rule in self.rules
                if hasattr(rule, "status")]


def load_rules_config(path: str) -> dict:
    """Load and return the rules.json config dict."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================ GUI editor support =============================
# The Rules tab (rules_view.py) exposes a per-rule field editor that writes back
# to rules.json. The editable-field spec, value coercion, whole-profile
# validation, and the write itself live here (pure core) so they are
# unit-testable and the Tk shell stays thin. Only a curated subset of each
# template's params is editable from the GUI; any key NOT in the spec (a rule's
# landmark, a well rule's legacy min_confidence, the _schema block, ...) is
# preserved untouched because the GUI mutates the loaded dict in place before
# saving. See docs/decisions/0031.

# Editable fields per template, as (key, kind) with kind in
# {"int","float","bool","str"}. Enumerated string fields also list their allowed
# values in RULE_FIELD_CHOICES so the GUI renders a dropdown.
RULE_EDIT_FIELDS: Dict[str, List[Tuple[str, str]]] = {
    PrimeAndLickTopupRule.TEMPLATE: [
        ("enabled", "bool"),
        ("port", "int"),
        ("pulse_ms", "int"),
        ("max_topups", "int"),
        ("lick_debounce_ms", "int"),
        ("topup_cooldown_ms", "int"),
        ("rearm_refractory_ms", "int"),
        ("rearm_on_exit", "bool"),
        ("radius_cm", "float"),
        ("min_confidence", "float"),
    ],
    WellRewardWithExcursionRule.TEMPLATE: [
        ("enabled", "bool"),
        ("port", "int"),
        ("pulse_ms", "int"),
        ("max_topups", "int"),
        ("lick_debounce_ms", "int"),
        ("topup_cooldown_ms", "int"),
        ("credential", "str"),
        ("prime_on", "str"),
        ("nonstem_reward_prob", "float"),
    ],
}

RULE_FIELD_CHOICES: Dict[str, List[str]] = {
    "credential": list(WellRewardWithExcursionRule._CREDENTIALS),
    "prime_on": ["well_arrival", "well_lick", "credential"],
}

# Credentials for which the conditionally-meaningful well fields apply; the GUI
# grays them out otherwise (the engine already ignores them). nonstem_reward_prob
# only fades the "choice" credential (ADR-0028); debias only caps the stem-gated
# / loop credentials (ADR-0030).
NONSTEM_CREDENTIALS = ("choice",)
DEBIAS_CREDENTIALS = ("choice_via_stem", "clean_loop")


def coerce_rule_field(kind: str, raw):
    """Convert a GUI-entered value to the typed value for a rule param.

    Raises ValueError/TypeError on malformed input so a caller can reject the
    edit before it is written to disk.
    """
    if kind == "bool":
        return bool(raw)
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    return str(raw)


def set_rule_debias(rule: dict, mode: Optional[str],
                    n: int = 3, lo: int = 3, hi: int = 5) -> None:
    """Set or clear the ``debias`` sub-config on a well_reward rule dict.

    ``mode`` of None or "none" removes the key entirely (no cap); "fixed" writes
    ``{mode, n}``; "uniform" writes ``{mode, min, max}``.
    """
    if not mode or mode == "none":
        rule.pop("debias", None)
        return
    if mode == "fixed":
        rule["debias"] = {"mode": "fixed", "n": int(n)}
    elif mode == "uniform":
        rule["debias"] = {"mode": "uniform", "min": int(lo), "max": int(hi)}
    else:
        raise ValueError(f"unknown debias mode {mode!r}")


def validate_profile_rules(rules: List[dict], defaults: Optional[dict]) -> None:
    """Raise if any rule dict is invalid (unknown template/credential/prime_on).

    Mirrors how the engine is built for a profile, so a clean return means the
    rules will load. The Rules tab calls this to guard a save.
    """
    d = EngineDefaults.from_dict(defaults)
    for rp in rules:
        build_rule(rp, d)


def save_rules_config(path: str, config: dict) -> None:
    """Write a rules config dict back to disk (round-trips load_rules_config).

    Pretty-prints with indent=2 and preserves every key in ``config`` (the
    ``_schema`` docs block, defaults, and any params the editor left untouched),
    since callers mutate only edited fields in place before saving. See
    docs/decisions/0031.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
