"""
reward_dispenser.py - the reward "how" and "why" of the well-reward rule, split
into two composable pure cores (see docs/decisions/0068, FW-0009).

The credential-gated well reward (rules_engine.WellRewardWithExcursionRule) used
to bundle three concerns in one class: WHEN a payout is deserved (the excursion
credential + the Phase-1->2a fade + the anti-bias debias cap), WHERE/WHEN to
prime (well arrival / first lick / out at the choice), and HOW it dispenses at
the well (port/pulse, the lick top-up counter + cooldown + count cap, the
well-visit lifecycle, the lick freshness/debounce gates). ADR-0030 added the
third prime-timing mode in place because there was only one live caller
(CONTRIBUTING #6). Phase 3 is now a second real caller (it primes the central
well the instant a choice is committed, then wants licks to top up), so the seam
is worth cutting - this module is that seam.

Two objects, one contract:

  - ``RewardTrigger`` - the WHY/WHEN: owns the ``credential`` selection, the
    ``nonstem_reward_prob`` fade (docs/decisions/0028), and the ``debias`` cap
    (docs/decisions/0030) over a seeded RNG. ``earn(task)`` folds one task-event
    frame and, when the excursion is credentialed, sets ``armed`` (a pending
    credit) and returns the ``LogEvent``s. It owns no port/pulse/top-up state.

  - ``WellDispenser`` - the HOW: owns ``port``/``pulse_ms``, the OUTSIDE/AT_WELL
    visit lifecycle (entered/exited on confident readings), prime delivery, the
    top-up counter + ``max_topups`` cap + ``topup_cooldown_ms`` cooldown, and the
    lick ``lick_debounce_ms`` / freshness gates. It emits ``FireReward`` intents.
    ``prime_on`` (well_arrival / well_lick / credential) lives here because it is
    about where/when the credit is spent.

The credit itself (``armed``) is owned by the Trigger and handed to the Dispenser
per tick as ``credit_pending``; when the Dispenser spends it (an arrival or
first-lick prime) it reports ``consumed_credit`` so the composing rule clears the
Trigger. Keeping a single owner of ``armed`` (the Trigger) means the rule's
public ``armed`` / ``_earn_credential`` surface is unchanged.

This module is a pure, headless-testable core (stdlib only, no Tk/OpenCV/serial),
like maze_state.py / task_events.py: it depends on nothing in rules_engine, so
the dependency runs one way (rules_engine -> reward_dispenser). The shared reward
primitives (``FireReward`` / ``LogEvent`` / ``LickAt`` / ``CONJUNCTION_FRESH_S``)
live here and are re-exported by rules_engine for back-compat. Headless tests:
python -m unittest test_reward_dispenser.

Thread-safety: driven single-threaded (the actuation thread / Tk poll), like the
other cores; it takes no locks.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional

# How stale the last tick may be (seconds) for a lick to still count as "within
# the zone at lick time". Ticks arrive at the GUI poll / actuation rate; this
# tolerates a couple of missed ticks without letting a long-past position gate a
# lick. Shared with the landmark rule in rules_engine (re-exported there).
CONJUNCTION_FRESH_S = 0.5

# Prime-timing modes for the dispenser (see docs/decisions/0030):
#   "well_arrival" - prime on reaching the well while credited (default; Phase 1),
#   "well_lick"    - first lick at the well while credited fires the prime,
#   "credential"   - prime the instant the credential arms, out at the choice
#                    zone, so the central spout signals the correct choice (2a/2b).
PRIME_MODES = ("well_arrival", "well_lick", "credential")

# The excursion credentials selecting the shaping phase (all read the SAME
# task-event vocabulary; see docs/decisions/0026). CHOICE_CREDENTIALS earn at
# choice-arm entry and are gated on the shared once-per-excursion choice-commit
# edge (docs/decisions/0055); clean_loop earns at loop close and needs no latch.
# ``choice_via_stem`` additionally requires the shared ``trial_trip`` credential
# (a hub-up-the-stem departure), the SAME one the Phase 3 scorer + choice cue use
# (docs/decisions/0168); ``choice`` still reads the descriptive ``outbound_used_stem``
# for its stem-vs-nonstem reward fade (a different question).
CREDENTIALS = ("choice", "choice_via_stem", "clean_loop")
CHOICE_CREDENTIALS = ("choice", "choice_via_stem")


# ============================ marker / action intents ========================
@dataclass
class LickAt:
    """Marker condition: a lick at ``port``. Routed by the engine's event()."""
    port: int

    def matches(self, port: int) -> bool:
        return int(port) == int(self.port)


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


@dataclass
class DispenseResult:
    """One Dispenser call's outcome.

    ``actions`` are the reward intents this call produced; ``consumed_credit`` is
    True when a pending credit was spent on a prime (arrival or first lick), so
    the composing rule clears the Trigger's ``armed``.
    """
    actions: List = field(default_factory=list)
    consumed_credit: bool = False


# ================================ the trigger ================================
class RewardTrigger:
    """Decides WHEN a payout is deserved (the credential earning), holding the
    single pending-credit flag ``armed``.

    Parses its own knobs from the rule params dict (``credential``,
    ``nonstem_reward_prob``, ``debias``, ``seed``); needs no engine defaults.
    """

    CREDENTIALS = CREDENTIALS
    CHOICE_CREDENTIALS = CHOICE_CREDENTIALS

    def __init__(self, params: dict, rule_id: str) -> None:
        self.id = rule_id
        self.credential = str(params.get("credential", "choice"))
        if self.credential not in self.CREDENTIALS:
            raise ValueError(
                f"unknown credential {self.credential!r}; "
                f"known: {list(self.CREDENTIALS)}")

        # Probabilistic fade (choice credential only): a NON-stem excursion earns
        # a credit only with probability nonstem_reward_prob, while a stem
        # excursion always credits. p = 1.0 is Phase 1 (any choice credits); p =
        # 0.0 is exactly Phase 2a (stem-only). Fading p 1->0 across sessions
        # morphs P1 into 2a without an extinction cliff. See docs/decisions/0028.
        self.nonstem_reward_prob = float(params.get("nonstem_reward_prob", 1.0))

        # Anti-bias debias: caps consecutive same-side runs. For clean_loop it
        # caps closed loops (same_side_run); for choice_via_stem it caps stem-
        # gated choice entries (same_side_choice_run). See docs/decisions/0030.
        db = params.get("debias") or {}
        self._debias_enabled = bool(db) and self.credential in (
            "clean_loop", "choice_via_stem")
        self._debias_mode = str(db.get("mode", "fixed"))
        self._debias_n = int(db.get("n", 3))
        self._debias_min = int(db.get("min", 3))
        self._debias_max = int(db.get("max", 5))
        # One seeded RNG for both the fade roll and the debias cap draw, so a
        # session is reproducible from ``seed`` (falls back to the debias seed).
        self._rng = random.Random(params.get("seed", db.get("seed")))
        self._cap = self._draw_cap()

        self.armed = False

    # --------------------------------------------------------------- helpers
    def _draw_cap(self) -> int:
        if not self._debias_enabled:
            return 1 << 30           # effectively no cap
        if self._debias_mode == "fixed":
            return self._debias_n
        return self._rng.randint(self._debias_min, self._debias_max)

    # ------------------------------------------------------------------ tick
    def earn(self, task) -> List:
        """Fold one task-event frame; arm a credit if the excursion qualifies.

        The choice-family credentials are gated on the shared once-per-excursion
        choice-commit edge (task_events.choice_committed; docs/decisions/0055,
        FW-0020), so a figure-8 that re-enters the OTHER arm on the way back
        cannot re-credit. clean_loop earns at loop close and is not gated.
        """
        earn = True
        if self.credential in self.CHOICE_CREDENTIALS:
            earn = task.choice_committed is not None
        if earn:
            return self._earn_credential(task)
        return []

    # ---------------------------------------------------- credential earning
    def _earn_credential(self, task) -> List:
        out: List = []
        if self.credential == "choice":
            if task.choice_entered is not None and not self.armed:
                if task.outbound_used_stem or self.nonstem_reward_prob >= 1.0:
                    self.armed = True
                    out.append(LogEvent("armed", self.id,
                                        {"credential": "choice",
                                         "side": task.choice_entered,
                                         "route": ("stem" if task.outbound_used_stem
                                                   else "nonstem")}))
                else:
                    # Non-stem excursion: credit only with the (fading) probability.
                    p = max(0.0, min(1.0, self.nonstem_reward_prob))
                    roll = self._rng.random()
                    if roll < p:
                        self.armed = True
                        out.append(LogEvent("armed", self.id,
                                            {"credential": "choice",
                                             "side": task.choice_entered,
                                             "route": "nonstem",
                                             "p": round(p, 3),
                                             "roll": round(roll, 3)}))
                    else:
                        out.append(LogEvent("credit_denied", self.id,
                                            {"reason": "nonstem_faded",
                                             "side": task.choice_entered,
                                             "p": round(p, 3),
                                             "roll": round(roll, 3)}))
        elif self.credential == "choice_via_stem":
            if task.choice_entered is not None:
                side = task.choice_entered
                # "via_stem" now means the SHARED trial credential (task.trial_trip;
                # docs/decisions/0168), i.e. a genuine trial trip (a hub-up-the-stem
                # departure whose outbound leg was not broken by a return arm;
                # docs/decisions/0169) - the same credential the Phase 3 scorer and
                # the choice cue use, not the looser outbound_used_stem (stem visited
                # at all, which a divider hop satisfies). The rule-file credential
                # NAME is unchanged, so existing rules.json files keep working
                # (alias). Extends the hub-departure unification of docs/decisions/0099.
                if task.trial_trip:
                    # Optional choice-side anti-bias (Phase 2b): redraw the cap on
                    # a side switch, then withhold a stem-gated choice that runs
                    # the same side past the cap. See docs/decisions/0030.
                    if self._debias_enabled and task.same_side_choice_run == 1:
                        self._cap = self._draw_cap()
                    if self._debias_enabled and task.same_side_choice_run > self._cap:
                        out.append(LogEvent("debias_withhold", self.id,
                                            {"side": side,
                                             "run": task.same_side_choice_run,
                                             "cap": self._cap}))
                    elif not self.armed:
                        self.armed = True
                        out.append(LogEvent("armed", self.id,
                                            {"credential": "choice_via_stem",
                                             "side": side,
                                             "run": task.same_side_choice_run,
                                             "cap": (None if not self._debias_enabled
                                                     else self._cap)}))
                else:
                    out.append(LogEvent("credit_denied", self.id,
                                        {"reason": "no_stem", "side": side}))
        elif self.credential == "clean_loop":
            if task.loop_closed is not None:
                side = task.loop_closed
                if task.same_side_run == 1:     # side switch (or first) -> redraw
                    self._cap = self._draw_cap()
                if not task.loop_clean:
                    out.append(LogEvent("credit_denied", self.id,
                                        {"reason": "unclean", "side": side}))
                elif self._debias_enabled and task.same_side_run > self._cap:
                    out.append(LogEvent("debias_withhold", self.id,
                                        {"side": side, "run": task.same_side_run,
                                         "cap": self._cap}))
                else:
                    self.armed = True
                    out.append(LogEvent("armed", self.id,
                                        {"credential": "clean_loop", "side": side,
                                         "run": task.same_side_run,
                                         "cap": (None if not self._debias_enabled
                                                 else self._cap)}))
        return out


# =============================== the dispenser ===============================
class WellDispenser:
    """Delivers reward at the well: the visit lifecycle, prime, and lick top-ups.

    ``default_topup_cooldown_ms`` seeds the within-visit cooldown when the rule
    params set neither ``topup_cooldown_ms`` nor the legacy ``cooldown_ms`` (the
    engine's default lives in rules_engine.EngineDefaults, kept out of this pure
    core, so the composing rule passes the resolved int).
    """

    def __init__(self, params: dict, *, default_topup_cooldown_ms: int,
                 rule_id: str) -> None:
        self.id = rule_id
        self.port = int(params.get("port", 2))
        self.pulse_ms = int(params.get("pulse_ms", 300))
        self.max_topups = int(params.get("max_topups", 3))
        self.lick_debounce_ms = int(params.get("lick_debounce_ms", 150))
        legacy = params.get("cooldown_ms")
        self.topup_cooldown_ms = int(params.get(
            "topup_cooldown_ms",
            legacy if legacy is not None else default_topup_cooldown_ms))
        # Where/when the prime fires. Back-compat: the older boolean
        # prime_on_arrival maps True->well_arrival, False->well_lick.
        prime_on = params.get("prime_on")
        if prime_on is None:
            prime_on = ("well_arrival"
                        if bool(params.get("prime_on_arrival", True))
                        else "well_lick")
        self.prime_on = str(prime_on)
        if self.prime_on not in PRIME_MODES:
            raise ValueError(
                f"unknown prime_on {self.prime_on!r}; known: {list(PRIME_MODES)}")

        self._lick = LickAt(self.port)

        # Mutable state.
        self.state = "OUTSIDE"
        self.topups = 0
        self.fires = 0
        self._primed_this_visit = False
        # A prime fired at credential time (prime_on == "credential") that is
        # awaiting the well visit to open its lick-top-up window.
        self._credit_primed = False
        self._at_well = False
        self._last_tick_now: Optional[float] = None
        self._last_fire_now: Optional[float] = None
        self._last_lick_now: Optional[float] = None

    # --------------------------------------------------------------- helpers
    def reset(self) -> None:
        """Clear the per-visit state (visit lifecycle, pending credential prime,
        top-up counter + timing) WITHOUT changing the configured knobs or the
        lifetime ``fires`` tally. A composing caller uses this at a clean boundary
        (e.g. Phase 3 re-arm / operator Skip) so a prime that never reached the
        well cannot carry a stale top-up window into the next visit."""
        self.state = "OUTSIDE"
        self.topups = 0
        self._primed_this_visit = False
        self._credit_primed = False
        self._at_well = False
        self._last_tick_now = None
        self._last_fire_now = None
        self._last_lick_now = None

    def _topup_ok(self, now: float) -> bool:
        if self._last_fire_now is None:
            return True
        return (now - self._last_fire_now) * 1000.0 >= self.topup_cooldown_ms

    def _fire(self, now: float, reason: str) -> FireReward:
        self._last_fire_now = now
        self.fires += 1
        return FireReward(self.port, self.pulse_ms, self.id, reason)

    # ------------------------------------------------------------- credential
    def prime_at_credential(self, now: float) -> FireReward:
        """Fire the prime out at the choice zone (prime_on == "credential") and
        remember it, so licks at the well later top up. Caller consumes the
        credit on the Trigger. See docs/decisions/0030."""
        self._credit_primed = True
        return self._fire(now, "prime")

    # ------------------------------------------------------------------ tick
    def visit_tick(self, task, *, credit_pending: bool) -> DispenseResult:
        """Advance the OUTSIDE/AT_WELL visit lifecycle; prime on arrival if the
        mode calls for it. Returns any fire + whether it spent the credit.

        Fail-safe: the visit is only EXITED on a confident outside reading, so a
        brief track loss (task.valid False) neither drops the visit nor lets a
        re-prime happen when the lock returns.
        """
        actions: List = []
        consumed = False
        at_well = bool(task.at_well)   # already implies task.valid
        if at_well and self.state == "OUTSIDE":
            self.state = "AT_WELL"
            self.topups = 0
            if self.prime_on == "credential":
                # The prime already fired at the choice; carry it into the visit
                # so licks here can top up.
                self._primed_this_visit = self._credit_primed
                self._credit_primed = False
            else:
                self._primed_this_visit = False
                if self.prime_on == "well_arrival" and credit_pending:
                    actions.append(self._fire(task.now, "prime"))
                    self._primed_this_visit = True
                    consumed = True
        elif not at_well and self.state == "AT_WELL" and task.valid:
            self.state = "OUTSIDE"
            self._primed_this_visit = False
            self.topups = 0

        self._at_well = at_well
        self._last_tick_now = task.now
        return DispenseResult(actions, consumed)

    # ------------------------------------------------------------- lick edge
    def on_lick(self, port: int, now: float, *,
                credit_pending: bool) -> DispenseResult:
        """A lick at the well: a lick-gated prime (well_lick) OR a top-up."""
        if not self._lick.matches(port):
            return DispenseResult()
        if self.state != "AT_WELL" or not self._at_well:
            return DispenseResult()
        if self._last_tick_now is None or \
                (now - self._last_tick_now) > CONJUNCTION_FRESH_S:
            return DispenseResult()
        if self._last_lick_now is not None and \
                (now - self._last_lick_now) * 1000.0 < self.lick_debounce_ms:
            return DispenseResult()
        self._last_lick_now = now
        if not self._primed_this_visit:
            # Lick-gated prime (well_lick): the first lick while credited fires it.
            if self.prime_on == "well_lick" and credit_pending:
                self._primed_this_visit = True
                return DispenseResult([self._fire(now, "prime")], True)
            return DispenseResult()
        if self.topups >= self.max_topups:
            return DispenseResult()
        if not self._topup_ok(now):
            return DispenseResult()
        self.topups += 1
        return DispenseResult([self._fire(now, "topup")])
