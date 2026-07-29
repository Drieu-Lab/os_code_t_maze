"""
cue_gate.py - the pure decision core for the vision-triggered, spatially-gated
audio cue (FW-0014 / the Phase-3 half of FW-0008).

The scientific intent (BRAINK99 grant) is that the tone is a function of the
rat's CURRENT tracked position - a spatial window - not of past beam events. So
the tone is ON strictly while the rat is inside a cue zone and OFF the moment it
leaves either end; a rat that commits then reverses simply leaves the window and
the tone stops (a partial cue is acceptable). The HOST owns this ON/OFF decision;
the firmware just synthesizes the tone + TTLs on CUE_ON/CUE_OFF (ADR-0044).

Design (see docs/decisions/0045):

  - Zone-membership gate, reusing the shared maze-state estimator's OUTPUT
    (RatState.zone / direction / confidence / valid) - no projection, cm, or
    overlay math. The CHOICE window is the CENTRAL_STEM zone (moving up_stem);
    the RETURN window is the LEFT_RETURN / RIGHT_RETURN zone, redrawn in the zone
    editor to the central-stem length near the sensor (see ADR-0045).
  - Read off the FAST estimator (min_dwell = 1) so onset commits within ~1 frame.
  - CHOICE fails OPEN: a confidence dropout never cuts a playing tone, and onset
    keeps being attempted while the rat is in the window (a late lock still
    fires). RETURN fails SAFE: the onset is a one-shot decision taken when the
    rat enters the return zone - if vision is not confident THEN, that return's
    cue is skipped for the whole visit (a missed cue beats a mis-placed one).
  - RETURN also requires an OUTWARD EXCURSION first: the tone only fires if the
    rat reached the return arm AFTER running out via the stem, not if it started
    a run by heading up a return arm from the hub. The caller passes the shared
    ``outbound_used_stem`` credential (task_events: visited_since(CENTRAL_STEM,
    HUB)) - the SAME predicate that gates a clean loop and the reward - so the
    return cue keys off the same task-phase vocabulary as the choice cue's
    up-stem direction gate rather than a bespoke latch (see docs/decisions/0061).

This module is a stateful PURE core (stdlib only, no Tk/OpenCV/serial), like
maze_state.py / task_events.py / rules_engine.py: it takes RatState fields and
returns a CueDecision; the thin Phase 3 shell (phase3_controller.py) turns
decisions into CUE_ON/CUE_OFF commands. Headless tests: python -m unittest
test_cue_gate.

Thread-safety: driven single-threaded (the cue actuation thread), like the other
cores; it takes no locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from maze_state import (CENTRAL_STEM, LEFT, LEFT_RETURN, RIGHT, RIGHT_RETURN,
                        UP_STEM)

# Cue kinds (match the CUE_ON,<kind> firmware command; see firmware/PROTOCOL.md).
CHOICE = "CHOICE"
RETURN = "RETURN"

_RETURN_ZONES = frozenset({LEFT_RETURN, RIGHT_RETURN})
# Physical return arm ("left"/"right") of each return zone, reported on a RETURN
# onset so the host can pick that arm's planned cue (see docs/decisions/0047).
_SIDE_OF_RETURN = {LEFT_RETURN: LEFT, RIGHT_RETURN: RIGHT}


@dataclass(frozen=True)
class CueParams:
    """Tunables for the cue gate.

    ``min_confidence`` is an ADDITIONAL onset gate on top of the estimator's own
    validity threshold (RatState.valid already means "confidently in a zone");
    default 0.0 defers entirely to the estimator. ``require_up_stem`` keeps the
    choice cue from firing while the rat runs back DOWN the stem. The enables let
    the two cues be armed independently.
    """
    choice_enabled: bool = True
    return_enabled: bool = True
    min_confidence: float = 0.0
    require_up_stem: bool = True


@dataclass(frozen=True)
class CueDecision:
    """One tick's outcome.

    ``desired`` is the cue that should be SOUNDING now (CHOICE / RETURN / None) -
    the controller compares it across ticks to emit CUE_ON/CUE_OFF edges.
    ``started`` / ``stopped`` are the edges this tick (for logging); the raw
    ``desired`` is authoritative. ``skipped_return`` flags a return visit dropped
    by the fail-safe (unconfident at entry) - surfaced for status/telemetry.
    ``return_arm`` is the physical arm ("left"/"right") on a RETURN onset (only
    set when ``started == RETURN``), so the host can look up that arm's planned
    return cue (see docs/decisions/0047).
    """
    desired: Optional[str] = None
    started: Optional[str] = None
    stopped: bool = False
    skipped_return: bool = False
    return_arm: Optional[str] = None


class CueGate:
    """Stateful pure gate mapping the per-frame RatState to a desired cue."""

    def __init__(self, params: Optional[CueParams] = None) -> None:
        self._p = params or CueParams()
        self._active: Optional[str] = None       # cue currently sounding
        self._prev_in_return = False             # last VALID return-zone membership
        self._return_visit_decided = False       # one-shot fail-safe latch per visit

    # -------------------------------------------------------------- config
    def set_params(self, params: CueParams) -> None:
        self._p = params

    @property
    def params(self) -> CueParams:
        return self._p

    @property
    def active(self) -> Optional[str]:
        return self._active

    def reset(self) -> None:
        """Clear all state (e.g. when the cue is disarmed/re-armed)."""
        self._active = None
        self._prev_in_return = False
        self._return_visit_decided = False

    # --------------------------------------------------------------- tick
    def step(self, zone: str, direction: Optional[str],
             confidence: float, valid: bool,
             outbound_used_stem: bool = True) -> CueDecision:
        """Fold one RatState into the desired cue state.

        Invalid frames (no trustworthy zone) HOLD the current tone and change no
        edges - a dropout must not chop a playing cue nor be mistaken for the rat
        leaving/re-entering a window. All edge logic therefore runs only on valid
        frames.

        ``outbound_used_stem`` is the shared task-phase credential the caller
        passes for the RETURN onset (task_events: visited_since(CENTRAL_STEM,
        HUB)) - the same predicate that gates a clean loop / the reward. The
        return tone fires only when it is True, so a rat that starts a run by
        heading up a return arm from the hub (no stem-out) gets no return cue
        (see docs/decisions/0061). It does not affect the CHOICE cue.
        """
        if not valid:
            return CueDecision(desired=self._active)

        p = self._p
        confident = confidence >= p.min_confidence
        in_choice = (zone == CENTRAL_STEM
                     and (not p.require_up_stem or direction == UP_STEM))
        in_return = zone in _RETURN_ZONES

        started: Optional[str] = None
        stopped = False
        skipped_return = False
        return_arm: Optional[str] = None

        # --- OFF first: positional - leaving the window (positively observed on
        # this valid frame) cuts the tone. ---
        if self._active == CHOICE and not in_choice:
            self._active = None
            stopped = True
        elif self._active == RETURN and not in_return:
            self._active = None
            stopped = True

        # --- Return-visit boundary (valid frames only, so a dropout can't fake a
        # new visit and re-trigger the one-shot decision). ---
        if in_return and not self._prev_in_return:
            self._return_visit_decided = False

        # --- ON: only when nothing is sounding. ---
        if self._active is None:
            if in_return and p.return_enabled:
                # One-shot decision per visit. The return cue requires an OUTWARD
                # EXCURSION first (outbound_used_stem; see docs/decisions/0061) -
                # the same credential the reward/clean-loop use - so a run that
                # starts up a return arm from the hub gets no cue. Among genuine
                # returns it fails SAFE: confident -> fire; otherwise skip this
                # whole return's cue (no later retry).
                if not self._return_visit_decided:
                    if not outbound_used_stem:
                        pass  # not a genuine return (no stem-out); no cue
                    elif confident:
                        self._active = RETURN
                        started = RETURN
                        return_arm = _SIDE_OF_RETURN.get(zone)
                    else:
                        skipped_return = True
                    self._return_visit_decided = True
            elif in_choice and p.choice_enabled and confident:
                # Fail-OPEN: no per-visit latch, so a dropout at the boundary only
                # delays onset - the next confident in-window frame still fires.
                self._active = CHOICE
                started = CHOICE

        self._prev_in_return = in_return
        return CueDecision(desired=self._active, started=started,
                           stopped=stopped, skipped_return=skipped_return,
                           return_arm=return_arm)
