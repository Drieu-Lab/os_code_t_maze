"""
task_events.py - task-semantic event layer over the maze-state estimator.

A pure, headless-testable core (like maze_state.py / rules_engine.py) that turns
the geometric per-tick ``RatState`` (maze_state.py) into TASK-semantic events,
predicates, and running aggregates that describe the animal's behavior in the
shaping curriculum's own vocabulary: "arrived at the well", "entered a choice
arm", "the outbound leg used the stem", "closed a clean left/right loop", and
"how many same-side loops in a row". See docs/decisions/0026.

Why a separate layer (single source of truth). Both the reward-rule engine
(rules_engine.py, which turns these events into reward) and a future
behavior-statistics module (which turns the SAME events into counts) consume this
vocabulary. Deriving "a clean left loop" or "a choice" independently in each
place risks the reward the animal earns and the statistics you report being
computed from different definitions - a scientific-validity bug, not just code
duplication. This module is the one place those definitions live. It is neutral:
it imports neither consumer and depends only on maze_state.py.

Scope. This layer is policy-free: it reports WHAT happened, never decides whether
to reward (that is the rule template's job, using these events as its
"credential"). It is stateful only for aggregates the raw RatState does not keep
(the same-side run length and per-side loop counts); everything else is a direct,
named read of the current RatState.

Thread-safety: driven single-threaded (the Tk poll loop), like the estimator and
the rules engine. It takes no locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from maze_state import (
    RatState, HUB, IN_WELL, CENTRAL_STEM, LEFT_CHOICE, RIGHT_CHOICE,
    LEFT_RETURN, RIGHT_RETURN, LEFT, RIGHT,
)

# Choice-arm zone -> side, and side -> its matching return arm. Used to name the
# choice edge and to check the "returned via the matching return arm" half of a
# clean loop.
_SIDE_OF_CHOICE = {LEFT_CHOICE: LEFT, RIGHT_CHOICE: RIGHT}
_RETURN_OF_SIDE = {LEFT: LEFT_RETURN, RIGHT: RIGHT_RETURN}


@dataclass(frozen=True)
class TaskEventFrame:
    """Per-tick task-semantic context derived from one ``RatState``.

    Edges (``well_arrived``, ``choice_entered``, ``loop_closed``) are true only on
    the tick the event occurs; predicates (``at_well``, ``outbound_used_stem``)
    describe the current tick / current lap; aggregates (``same_side_run``,
    ``left_loops``, ``right_loops``) accumulate across laps. ``rat_state`` is the
    source state, passed through so consumers can reach raw fields if needed.
    """
    now: float
    valid: bool
    # ---- predicates (this tick / this lap) ----
    at_well: bool                       # zone == HUB and substate == in_well
    outbound_used_stem: bool            # stem visited since last leaving the HUB
    # ---- edges (true only on the tick they occur) ----
    well_arrived: bool                  # entered the well (in_well) this tick
    choice_entered: Optional[str]       # "left"/"right" if a choice arm entered this tick
    loop_closed: Optional[str]          # "left"/"right" if a lap closed at the HUB this tick
    loop_clean: bool                    # that closed lap was clean (stem out + matching return)
    # ---- aggregates (across laps) ----
    same_side_run: int                  # consecutive same-side CLOSED loops (rewarded or not)
    left_loops: int
    right_loops: int
    rat_state: RatState


class TaskEvents:
    """Folds a ``RatState`` stream into ``TaskEventFrame``s.

    Pure and single-threaded. Holds only the small aggregate state the raw
    RatState does not: the previous ``at_well`` (to detect the arrival edge), the
    last closed-loop side, the same-side run length, and per-side loop counts.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._prev_at_well = False
        self._last_loop_side: Optional[str] = None
        self._same_side_run = 0
        self._left_loops = 0
        self._right_loops = 0

    def update(self, s: RatState) -> TaskEventFrame:
        at_well = bool(s.valid and s.zone == HUB and s.substate == IN_WELL)
        well_arrived = at_well and not self._prev_at_well

        choice_entered = _SIDE_OF_CHOICE.get(s.entered) if s.entered else None

        # "This lap the outbound leg used the stem" == the stem was entered since
        # the animal last left the HUB (the canonical visited_since query).
        outbound_used_stem = bool(s.valid and s.visited_since(CENTRAL_STEM, HUB))

        loop_closed: Optional[str] = None
        loop_clean = False
        if s.valid and s.loop_completed and s.loop_side is not None:
            loop_closed = s.loop_side
            ret = _RETURN_OF_SIDE.get(s.loop_side)
            # Clean unidirectional lap: outbound via the stem AND returned via the
            # matching return arm (this rejects a stem-reversal return, which
            # would not have visited a return arm this lap).
            loop_clean = bool(
                s.visited_since(CENTRAL_STEM, HUB)
                and ret is not None and s.visited_since(ret, HUB))
            # Aggregates update on EVERY closed loop (rewarded or withheld), so
            # the run length is a faithful measure of side perseveration. A side
            # switch resets the run to 1. See docs/decisions/0026.
            if s.loop_side == self._last_loop_side:
                self._same_side_run += 1
            else:
                self._same_side_run = 1
            self._last_loop_side = s.loop_side
            if s.loop_side == LEFT:
                self._left_loops += 1
            else:
                self._right_loops += 1

        self._prev_at_well = at_well

        return TaskEventFrame(
            now=s.now, valid=s.valid,
            at_well=at_well, outbound_used_stem=outbound_used_stem,
            well_arrived=well_arrived, choice_entered=choice_entered,
            loop_closed=loop_closed, loop_clean=loop_clean,
            same_side_run=self._same_side_run,
            left_loops=self._left_loops, right_loops=self._right_loops,
            rat_state=s)
