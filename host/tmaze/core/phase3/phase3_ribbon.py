"""
phase3_ribbon.py - display rows for the Phase 3 "trial ribbon" (pure core).

A small, Tk-free helper that turns a published ``phase3_log.Phase3StateSnapshot``
(docs/decisions/0050) into a short list of display rows for the in-tab progress
ribbon (docs/decisions/0051): the trial currently in progress plus a few resolved
trials behind it and a few upcoming trials ahead of it. It is the "what to show"
decision; the Phase 3 tab shell (phase3_controller.py) is the "how to draw it".

Each :class:`RibbonRow` carries, for one planned trial:
  - the 1-based ``trial`` number and whether it is the ``is_current`` trial;
  - the CHOICE cue (``choice_cue`` HIGH/LOW) and the arm it instructs the rat to
    pick (``choice_side`` LEFT/RIGHT), derived from the cue + the session's
    ``high_tone_is_left`` mapping (this equals the trial's planned correct arm, by
    construction - see :func:`cue_side`);
  - the two planned return-arm cues (``return_cue_left`` / ``return_cue_right``,
    each HIGH/LOW), one per physical return arm (docs/decisions/0047);
  - for a RESOLVED trial (one the rat has already chosen on), the rat's
    ``chosen_arm`` (LEFT/RIGHT) and ``outcome`` (CORRECT/INCORRECT); both ``None``
    (and ``resolved`` False) for the current/upcoming, not-yet-scored trials.

Pure stdlib, no Tk/OpenCV/serial and no I/O, so it is headless-testable over
deterministic snapshots (build a Phase3Log, fill it, ``.snapshot(idx, armed=...)``):
    python -m unittest test_phase3_ribbon
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from tmaze.core.phase3.phase3_log import Phase3StateSnapshot
from tmaze.core.phase3.trial_sequence import HIGH

# The ribbon shows the current trial flanked by this many resolved trials behind
# it and upcoming trials ahead of it (a 7-trial window at most; clamped near the
# ends of the session). A deliberate display choice - see docs/decisions/0051.
RIBBON_BEFORE = 3
RIBBON_AFTER = 3

# Arm labels match the snapshot rows (phase3_log normalizes to upper-case).
LEFT_LABEL = "LEFT"
RIGHT_LABEL = "RIGHT"


@dataclass(frozen=True)
class RibbonRow:
    """One planned trial's ribbon cell (see the module docstring)."""
    trial: int                     # 1-based trial number
    is_current: bool               # the trial currently in progress
    choice_cue: str                # HIGH / LOW
    choice_side: str               # LEFT / RIGHT - the arm the choice cue means
    return_cue_left: str           # HIGH / LOW - left return arm's planned cue
    return_cue_right: str          # HIGH / LOW - right return arm's planned cue
    resolved: bool                 # the rat has chosen on this trial
    chosen_arm: Optional[str]      # LEFT / RIGHT once scored, else None
    outcome: Optional[str]         # CORRECT / INCORRECT once scored, else None
    corrective: bool = False       # part of a closed-loop debias corrective block
    probe: bool = False            # part of an unrewarded probe block (probe_blocks.py)
    return_enabled: bool = True    # is the return cue played? False -> hide ret info


def cue_side(cue: str, high_tone_is_left: bool) -> str:
    """The choice arm a HIGH/LOW choice cue instructs the rat to pick.

    HIGH means the ``high_tone_is_left`` side, LOW means the other; so the mapping
    is a single XOR of "is this the high tone?" with "is the high tone on the
    left?". This is the display twin of ``trial_sequence._is_high`` (the generator
    picks the cue FROM the correct arm; here we read the arm BACK from the cue), so
    the returned side always equals the trial's planned ``correct_arm``.
    """
    is_left = (cue == HIGH) == high_tone_is_left
    return LEFT_LABEL if is_left else RIGHT_LABEL


def _make_row(state: Phase3StateSnapshot, i: int, current: int) -> RibbonRow:
    """Build one ``RibbonRow`` for planned trial index ``i`` (``current`` is the
    clamped current-trial index, used only to set ``is_current``). Shared by the
    windowed ``ribbon_rows`` and the full-plan ``all_ribbon_rows``."""
    row = state.rows[i]
    return RibbonRow(
        trial=row.trial,
        is_current=(i == current),
        choice_cue=row.choice_cue,
        choice_side=cue_side(row.choice_cue, state.high_tone_is_left),
        return_cue_left=row.return_cue_left,
        return_cue_right=row.return_cue_right,
        resolved=row.outcome is not None,
        chosen_arm=row.chosen_arm,
        outcome=row.outcome,
        corrective=(row.sched_state == "INTERVENTION"),
        probe=(row.probe == "1"),
        return_enabled=state.return_enabled,
    )


def ribbon_rows(state: Optional[Phase3StateSnapshot], *,
                before: int = RIBBON_BEFORE,
                after: int = RIBBON_AFTER) -> List[RibbonRow]:
    """Build the ribbon window around the current trial from a state snapshot.

    Returns the rows for ``[current-before, current+after]`` clamped to the valid
    trial range, current-first-highlighted; an empty list when there is nothing to
    show (``state`` is None - not armed yet - or the plan is empty). ``current`` is
    clamped into range too, so a completed session (``current_index == total``)
    shows the tail of the plan with the last trial marked current.
    """
    if state is None or state.total == 0:
        return []
    current = max(0, min(state.current_index, state.total - 1))
    lo = max(0, current - before)
    hi = min(state.total - 1, current + after)
    return [_make_row(state, i, current) for i in range(lo, hi + 1)]


def all_ribbon_rows(state: Optional[Phase3StateSnapshot]) -> List[RibbonRow]:
    """Build a row for every planned trial (the full session), current-highlighted.

    The unwindowed twin of :func:`ribbon_rows`, for a scrollable strip that shows
    the whole plan rather than a fixed window (docs/decisions/0135). Returns one
    row per trial ``0..total-1`` with ``is_current`` set from the clamped
    ``current_index`` (so a completed session marks the last trial current); an
    empty list when there is nothing to show (``state`` is None or the plan is
    empty).
    """
    if state is None or state.total == 0:
        return []
    current = max(0, min(state.current_index, state.total - 1))
    return [_make_row(state, i, current) for i in range(state.total)]
