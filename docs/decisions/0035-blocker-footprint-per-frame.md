# ADR 0035: Blocker footprint is per-frame (clear when the tape isn't visible)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (RatTracker.process blocker block), `host/test_tracker_blocker.py`, `docs/how-tracking-and-rules-work.md`
- **Supersedes / Superseded by:** Partially supersedes ADR-0034 (the footprint-persistence sub-decision only; the rest of 0034 stands)

## Context

ADR-0034 added parallel blocker tracking: the orange tape locates the blocker and
its convex-hull footprint is subtracted from the mask before the rat is chosen.
That design **persisted** the last footprint whenever the tape was not seen this
frame, so that a rat climbing onto the blocker (briefly hiding the tape) would not
let the merged blob steal the track.

On the bench this backfired in the common case: when the blocker is simply
**removed from view**, `_blocker_footprint_from_orange()` returns nothing, the
stale footprint is retained, and it keeps erasing pixels at the blocker's last
location - so a rat passing through that spot vanishes from tracking, and a
residual `BLOCKER` marker lingers on the preview (observed and reported). The
footprint only cleared via Clear blocker (which deletes the model), Grab
background, or a `proc_max_w` change - none of which happen mid-session.

Two ways to tell "tape briefly hidden by the rat" from "blocker removed" were
considered: (A) keep persistence but auto-clear when the footprint region has
almost no foreground for a few frames; (B) make the footprint strictly per-frame.
The rat in this task does get right up to / onto the blocker only rarely, and the
lingering-mask failure is the frequent, confusing one.

## Decision

Make the blocker footprint **per-frame**: it exists only on frames where the tape
is actually visible. In `RatTracker.process()`, assign `self._blocker_footprint`
and `self._blocker_last` to whatever `_blocker_footprint_from_orange()` returns
each frame - including `None` when no tape is found - instead of retaining the
previous value on a miss:

```python
fp, bc = self._blocker_footprint_from_orange(hsv, mask, orange_range, params, pw, ph)
self._blocker_footprint = fp   # None when no tape this frame
self._blocker_last = bc        # None when no tape this frame
if fp is not None and fp.shape == mask.shape:
    detections = self._detections(cv2.bitwise_and(mask, cv2.bitwise_not(fp)), params)
```

The `TrackResult` marker keys off `self._blocker_last`, so it clears the moment
the tape leaves view.

## Alternatives considered

- **Keep persistence + occupancy-based auto-clear (Option A)** - clear only when
  the footprint region has near-zero foreground for a few frames, preserving the
  anti-stick behaviour while the rat hides the tape. Rejected for now as more
  logic (an occupancy threshold + hysteresis) with a rare edge case (a rat parked
  exactly on the removed blocker's spot keeps the mask alive until it moves); the
  simpler per-frame rule was chosen deliberately.
- **Manual "clear footprint" button** - considered and declined; automatic
  per-frame clearing makes it unnecessary.

## Consequences

- A blocker removed from view (or a fully occluded tape) stops masking
  immediately and the marker disappears - the reported bug is fixed.
- Trade-off accepted: if the rat **fully** covers the tape while the blocker is
  still present, the footprint drops for those frames and the merged blob can
  briefly steal the track until the tape reappears. If this proves common,
  revisit with Option A (occupancy-based clearing).

## Evidence / links

- `host/test_tracker_blocker.py::test_footprint_clears_when_blocker_leaves`
  (rat at the former blocker spot is tracked again; marker cleared). Full suite
  green via `run_tests.bat`.
- Supersedes the footprint-persistence paragraph of ADR-0034.
