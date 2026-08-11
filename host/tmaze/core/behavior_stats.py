"""
behavior_stats.py - live Phase 2a training-readout aggregator (pure core).

Folds the task-semantic event stream (task_events.TaskEventFrame) plus the
tracked centroid into a small set of scalar behavior measures for the shaping
curriculum's Phase 2a: stem-compliance, L/R choice balance, non-stem
(credit-denied) count, same-side run, trial throughput, and total distance run.
Each rate is kept both cumulatively (whole session) and over a rolling window of
the most recent N choice entries.

Why a separate pure core (single source of truth). Like task_events.py and
rules_engine.py, the measurement logic lives in a headless, stdlib-only core the
Tk shell (behavior_view.py) drives on the poll loop. It reuses task_events'
shared per-excursion choice-commit edge (choice_committed) plus outbound_used_stem
so the definition of a choice and of stem use cannot drift from the reward rule.
The same-side run is recomputed here over stem-gated commits (see the counting
note below) rather than read from task_events' same_side_choice_run, so all
readout measures share one counting unit. See docs/decisions/0032 and FW-0007.

Distance. Locomotion is summed from successive smoothed centroids using a caller-
supplied scale object (rules_engine.Scale) exposing ``distance_cm(p_px, q_px)``
and ``valid()`` - the single canonical px<->cm conversion; no second calibration
is introduced. A confidence gap is treated as a DISCONTINUITY: on any
non-accumulated tick the last position is dropped, so the straight-line jump when
tracking re-acquires a distance away is never added (a conservative undercount
rather than fabricated distance). See docs/decisions/0032.

Time. Throughput and distance-rate denominators use ACTIVE time - the summed dt
between successive update() calls, capped per-tick so a paused/backgrounded
stretch cannot inflate it (the shell stops calling update() while paused and
calls mark_gap() on resume). See docs/decisions/0032.

Thread-safety: driven single-threaded (the Tk poll loop), like the estimator and
the rules engine. It takes no locks.

Counting unit - one choice per OUTBOUND EXCURSION. A "choice" here is the FIRST
choice-zone entry since the animal last left the hub, not every choice-zone entry.
This matters because a lap that goes out one arm and back the other (a figure-8
that crosses the junction) enters two choice zones in a single hub->...->hub trip;
counting per-entry would score that as two choices (and, when neither used the
stem, as two non-stem denials), double-counting one trip and skewing compliance.
task_events.py exposes this de-duplicated signal directly as the once-per-trip
choice_committed edge (docs/decisions/0055, FW-0020); this readout, the reward
rule, and the Phase 3 scorer all consume that one edge, so "trials" match trips
identically across them. See docs/decisions/0032.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from tmaze.core.maze_state import LEFT, RIGHT
from tmaze.core.task_events import TaskEventFrame

DEFAULT_WINDOW = 10          # rolling window size, in most-recent choice entries
DEFAULT_MAX_DT_S = 0.5       # per-tick cap on the gap counted as active time
DEFAULT_MAX_JUMP_PX = 200.0  # reject a single-tick centroid jump larger than this


@dataclass(frozen=True)
class BehaviorSnapshot:
    """Immutable readout of the current measures (the view formats display).

    Rates are fractions (0..1) or None when their denominator is empty; the view
    turns them into percentages / per-minute figures. ``lr_*`` is
    ``(left_frac, right_frac)`` among stem-gated trials.
    """
    window_n: int
    # ---- choices / compliance ----
    total_choices: int
    stem_choices: int
    nonstem_choices: int
    # The denied (non-stem) excursions split by the side the rat committed to, so
    # a directional pull on the shortcut trips is visible (cumulative only; a
    # distinct signal from the stem-gated ``lr_*`` and the cued dir-bias). By
    # construction ``nonstem_left + nonstem_right == nonstem_choices``.
    # ``nonstem_lr`` is ``(left_frac, right_frac)`` among them, None if there are
    # none yet. See docs/decisions/0095.
    nonstem_left: int
    nonstem_right: int
    nonstem_lr: Optional[Tuple[float, float]]
    compliance_cum: Optional[float]              # stem / total
    compliance_win: Optional[float]
    # ---- L/R balance among stem-gated trials ----
    lr_cum: Optional[Tuple[float, float]]
    lr_win: Optional[Tuple[float, float]]
    left_choices: int
    right_choices: int
    # ---- perseveration ----
    same_side_run_cur: int
    same_side_run_max: int
    same_side_run_side: Optional[str]            # side of the CURRENT run ("left"/"right")
    same_side_run_max_side: Optional[str]        # side the session-max run was on
    # ---- throughput ----
    active_s: float
    trials_per_min: Optional[float]
    # ---- distance ----
    distance_cm: float
    distance_contributed: bool                   # any valid-scale segment summed
    distance_m_per_min: Optional[float]


class BehaviorStats:
    """Folds a ``TaskEventFrame`` (+ position + scale) stream into the measures.

    Pure and single-threaded. Cumulative counters + a bounded ``deque`` of the
    most recent choice entries (for the rolling window) + an active-time
    accumulator + the last position anchor for distance.
    """

    def __init__(self, window_n: int = DEFAULT_WINDOW,
                 max_dt_s: float = DEFAULT_MAX_DT_S,
                 max_jump_px: float = DEFAULT_MAX_JUMP_PX) -> None:
        self._window_n = max(1, int(window_n))
        self._max_dt_s = float(max_dt_s)
        self._max_jump_px = float(max_jump_px)
        self.reset()

    def reset(self) -> None:
        self._recent: Deque[Tuple[str, bool]] = deque(maxlen=self._window_n)
        self._total = 0
        self._stem = 0
        self._left = 0
        self._right = 0
        self._nonstem_left = 0        # denied (non-stem) trips committed LEFT
        self._nonstem_right = 0       # denied (non-stem) trips committed RIGHT
        self._run_cur = 0
        self._run_max = 0
        self._run_side: Optional[str] = None      # side of the current same-side run
        self._run_max_side: Optional[str] = None  # side the max run was on
        self._active_s = 0.0
        self._last_now: Optional[float] = None
        self._distance_cm = 0.0
        self._distance_contributed = False
        self._last_pos: Optional[Tuple[float, float]] = None

    def set_window(self, window_n: int) -> None:
        """Resize the rolling window, preserving the most recent entries."""
        n = max(1, int(window_n))
        if n == self._window_n:
            return
        self._window_n = n
        self._recent = deque(self._recent, maxlen=n)

    def mark_gap(self) -> None:
        """Break continuity (called on pause/resume). Drops the timing and
        position anchors so the idle interval is counted as neither active time
        nor distance."""
        self._last_now = None
        self._last_pos = None

    def update(self, frame: TaskEventFrame,
               pos_px: Optional[Tuple[float, float]] = None,
               scale=None) -> None:
        """Fold one tick. ``pos_px`` is the smoothed centroid (full-frame px);
        ``scale`` is a rules_engine.Scale (or None). Call once per poll tick
        while running."""
        self._accumulate_time(frame.now)
        self._accumulate_distance(frame, pos_px, scale)
        self._accumulate_choice(frame)

    # ------------------------------------------------------------- internals
    def _accumulate_time(self, now: float) -> None:
        # A too-large dt (missed ticks, or a resume after pause) is dropped
        # entirely rather than clamped, so it never inflates active time.
        if self._last_now is not None:
            dt = now - self._last_now
            if 0.0 < dt <= self._max_dt_s:
                self._active_s += dt
        self._last_now = now

    def _accumulate_distance(self, frame: TaskEventFrame, pos_px, scale) -> None:
        usable = (frame.valid and pos_px is not None
                  and scale is not None and scale.valid())
        if not usable:
            # Discontinuity: drop the anchor so a later re-acquisition does not
            # add the straight-line jump across the untrusted gap.
            self._last_pos = None
            return
        if self._last_pos is not None:
            d_px = math.hypot(pos_px[0] - self._last_pos[0],
                              pos_px[1] - self._last_pos[1])
            if d_px <= self._max_jump_px:
                self._distance_cm += scale.distance_cm(self._last_pos, pos_px)
                self._distance_contributed = True
            # A jump over the cap (a spurious confident hop) is skipped, and the
            # anchor advances to the new point so we don't chain onto a stale one.
        self._last_pos = pos_px

    def _accumulate_choice(self, frame: TaskEventFrame) -> None:
        # Count only the FIRST choice-zone entry per hub->...->hub trip (a figure-8
        # that crosses between the two arms enters two choice zones but is one
        # trip). This is exactly the shared once-per-excursion choice-commit edge
        # from task_events (choice_committed; docs/decisions/0055, FW-0020), which
        # replaces the local per-excursion latch this module used to keep. See the
        # module docstring / docs/decisions/0032.
        side = frame.choice_committed
        if side is None:
            return
        stem = bool(frame.outbound_used_stem)
        self._total += 1
        self._recent.append((side, stem))
        if stem:
            self._stem += 1
            self._advance_run(side)
            if side == LEFT:
                self._left += 1
            elif side == RIGHT:
                self._right += 1
        else:
            # A denied (non-stem) excursion: keep the committed side so its L/R
            # split is available (docs/decisions/0095). Does NOT touch the
            # stem-gated run/balance above.
            if side == LEFT:
                self._nonstem_left += 1
            elif side == RIGHT:
                self._nonstem_right += 1

    def _advance_run(self, side: str) -> None:
        """Extend or reset the same-side run over stem-gated excursions (a side
        switch resets to 1; non-stem excursions don't reach here, so they neither
        extend nor reset it - mirroring task_events.same_side_choice_run)."""
        if side == self._run_side:
            self._run_cur += 1
        else:
            self._run_cur = 1
            self._run_side = side
        if self._run_cur > self._run_max:
            self._run_max = self._run_cur
            self._run_max_side = side

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> BehaviorSnapshot:
        compliance_cum = (self._stem / self._total) if self._total else None
        win = list(self._recent)
        win_total = len(win)
        win_stem = sum(1 for _side, stem in win if stem)
        compliance_win = (win_stem / win_total) if win_total else None
        win_left = sum(1 for side, stem in win if stem and side == LEFT)
        win_right = sum(1 for side, stem in win if stem and side == RIGHT)

        trials_per_min: Optional[float] = None
        dist_m_per_min: Optional[float] = None
        if self._active_s >= 1.0:
            minutes = self._active_s / 60.0
            trials_per_min = self._stem / minutes
            dist_m_per_min = (self._distance_cm / 100.0) / minutes

        return BehaviorSnapshot(
            window_n=self._window_n,
            total_choices=self._total, stem_choices=self._stem,
            nonstem_choices=self._total - self._stem,
            nonstem_left=self._nonstem_left, nonstem_right=self._nonstem_right,
            nonstem_lr=self._fracs(self._nonstem_left, self._nonstem_right),
            compliance_cum=compliance_cum, compliance_win=compliance_win,
            lr_cum=self._fracs(self._left, self._right),
            lr_win=self._fracs(win_left, win_right),
            left_choices=self._left, right_choices=self._right,
            same_side_run_cur=self._run_cur, same_side_run_max=self._run_max,
            same_side_run_side=self._run_side,
            same_side_run_max_side=self._run_max_side,
            active_s=self._active_s, trials_per_min=trials_per_min,
            distance_cm=self._distance_cm,
            distance_contributed=self._distance_contributed,
            distance_m_per_min=dist_m_per_min)

    @staticmethod
    def _fracs(left: int, right: int) -> Optional[Tuple[float, float]]:
        tot = left + right
        if tot == 0:
            return None
        return (left / tot, right / tot)
