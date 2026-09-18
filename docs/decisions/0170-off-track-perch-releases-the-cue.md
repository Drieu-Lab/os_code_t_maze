# ADR 0170: An off-track "perch" releases the spatial cue

- **Status:** Accepted
- **Date:** 2026-09-17
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/maze_state.py` (`MazeStateParams.off_track_after_s`,
  `RatState.off_track`), `host/tmaze/core/cue_gate.py` (`CueGate.step`),
  `host/tmaze/core/task_events.py` (`TaskEventFrame.off_track`),
  `host/tmaze/ui/phase3_controller.py`, `host/tmaze/tools/maze_state_validate.py`
- **Supersedes / Superseded by:** —

## Context

The maze-state estimator classifies the tracked centroid into a zone by
point-in-polygon. When a point falls in a **gap** between polygons (the zones do
not tile the whole frame), the estimator deliberately holds the last committed
zone rather than flicker to UNKNOWN — the "gap is sticky" rule
(docs/decisions/0025; `MazeStateEstimator.update`, `raw is None` branch). That is
correct for a rat *crossing* a thin seam between two zones.

But the spatial cue (docs/decisions/0045) is a function of the *current* held
zone: the CHOICE tone sounds while `RatState.zone == CENTRAL_STEM`, the RETURN
tone while in a return zone. A rat that simply **stops in a gap** — grooming on
the stem/junction seam, or sitting where the polygons do not quite meet — keeps
the estimator reporting the stale stem/return zone with `valid == True`
indefinitely, and the tone plays on and on. The tone is meant to be a brief,
position-locked cue, not a signal that stays on because the animal parked on a
seam. Nothing timed how long a valid point had been sitting in a gap, so nothing
could tell "crossing a seam" (fine) from "perched on a seam" (stale).

## Decision

Add a duration-based **off-track** flag to the estimator and let it release the
cue.

- `MazeStateParams.off_track_after_s = 1.0` — how long a valid point may sit in a
  polygon gap before it is called off-track. One second: long enough that a
  normal seam crossing (a few frames at ~30 Hz) never trips it, short enough that
  a genuine perch stops the tone promptly.
- The estimator times the current unbroken run of valid-in-gap ticks
  (`_gap_since`, set to `now` on the first gap tick, cleared the instant the point
  is inside **any** zone again). Once `now - _gap_since >= off_track_after_s` it
  sets the new `RatState.off_track = True`. The held **zone and `valid` are
  unchanged** — `off_track` is purely additive, so any consumer that ignores it
  behaves exactly as before.
- A tracking **dropout** (an invalid tick) does **not** reset the timer: the rat
  has not moved, so a brief loss mid-perch must not restart "how long off track."
  `off_track` is reported `False` on any invalid tick (it is meaningless without a
  trustworthy zone), and the timer is only evaluated once localized (a gap point
  before the first commit is invalid, never off-track).
- `CueGate.step` gains an `off_track` argument (default `False`); a true value
  forces the point **out of both windows** (`in_choice`/`in_return` require
  `not off_track`). The active tone therefore stops on the OFF-first edge. The
  CHOICE cue fails **open**: it re-fires on the next confident in-window frame
  once the rat moves back onto the track. The RETURN cue is one-shot per visit
  (docs/decisions/0045), so a perch that stops it does not replay for that visit —
  the intended fail-safe.
- `TaskEventFrame.off_track` passes the flag straight through from `RatState`, so
  perches are countable at the task level without reaching into `rat_state`.
- Both the live controller (`phase3_controller`) and the offline replay
  (`maze_state_validate.cue_onset_events`) feed `st.off_track` to the gate, so the
  offline onset trace matches the rig by construction. The same offline-parity
  edit brings the replay's credential to the shared `trial_trip`
  (docs/decisions/0168/0169) it had still been passing as `left_hub_via_stem`.

## Alternatives considered

- **Tick-count threshold instead of seconds.** Rejected: frame rate varies (drops,
  COASTING), so a fixed tick count is an implicit, unstable time. A wall-clock
  duration is what the intent ("been sitting there a second") actually means.
- **Make a long gap dwell go UNKNOWN (`valid = False`).** Rejected: that would
  ripple into every consumer of `valid` (task events, reward, logging) for what is
  only a cue-gating concern, and it would throw away the still-useful "which zone
  was it last near" information. An additive flag is surgical.
- **Reset the perch timer on any tracking dropout.** Rejected: a stationary rat
  whose tracker blinks would keep restarting the clock and never trip off-track,
  which is the opposite of the intent. Holding the timer across a dropout matches
  the physical fact that the rat has not moved.
- **Also deny scoring/reward when off-track.** Rejected for now: off-track is a
  *hold-the-tone* artifact, not a route-honesty question (that is `trial_trip`,
  docs/decisions/0169). A perched rat that then commits a real choice has cleared
  off-track by the time it enters the choice arm, so scoring is unaffected.

## Consequences

- A rat that stops in a polygon gap no longer holds a cue on its stale zone; the
  tone releases after ~1 s and (for CHOICE) re-arms when it moves back on track.
- No format or protocol change: `off_track` is a new `RatState`/`TaskEventFrame`
  field with a `False` default, and `CueGate.step`'s new argument defaults
  `False`, so every existing caller and test is behaviour-identical until it opts
  in. The trial CSV and bundle format are untouched.
- The threshold is a single named constant with this ADR as its reason
  (CONTRIBUTING guideline #5). If seam geometry or frame rate changes make 1.0 s
  feel wrong, this is the one knob to revisit.
- Follow-up left open: `off_track` is surfaced but not yet *logged* as a
  per-session count or written to the bundle; a future increment could add a
  perch tally to the behavior readout if it proves useful.

## Evidence / links

- Gap-stickiness rule: docs/decisions/0025; `MazeStateEstimator.update`
  (`raw is None` branch) in `host/tmaze/core/maze_state.py`.
- Cue gate design: docs/decisions/0045; shared trial credential: docs/decisions/
  0168, 0169.
- Tests: `TestOffTrack` in `host/tests/test_maze_state.py` (timer under/over
  threshold, clear-on-return, dropout-holds-timer, pre-localization),
  `TestOffTrack` in `host/tests/test_cue_gate.py` (stop + fail-open re-fire, onset
  block, return stop), `TestOffTrackPassthrough` in
  `host/tests/test_task_events.py`.
