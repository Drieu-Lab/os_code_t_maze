# ADR 0040: Run rat tracking on its own worker off the camera grab thread

- **Status:** Accepted
- **Date:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/track_worker.py` (new), `host/camera_view.py`
  (`_grab_loop`, new `_track_loop`, `_Counters.track_skips`, sidecar
  `track_src_block_id`), `host/test_track_worker.py` (new); recording sidecar CSV
  schema
- **Supersedes / Superseded by:** Extends ADR-0011 (grab/encode thread split) and
  ADR-0014 (tracking on the grab thread); does not supersede either.

## Context

Tracking ran **inline on the camera grab thread**: `_grab_loop` called
`self._tracker.process(frame)` between `RetrieveResult` calls (ADR-0011/0014). A
slow `process()` therefore delayed the next retrieve, overflowed the Pylon
buffer, and dropped frames **at the source** (counted as `src_drops` via BlockID
gaps). Those bigger inter-frame gaps then broke the association gate and decayed
tracking confidence. So per-frame tracking cost turned directly into dropped
frames **and** tracking unreliability - the structural root of
`#260720_TrackingDelays`. ADR-0039 cut the per-frame cost (one detect+cluster
pass) but did not remove the coupling: any future cost (e.g. clustering, a richer
model) re-introduces the same failure, and a later host-owned positional audio
cue needs a low-latency, low-jitter position signal that the grab-thread coupling
cannot guarantee.

Facts that made an off-thread move feasible and shaped the design:
- `RatTracker` is already lock-guarded for cross-thread use (`process()` on one
  thread; control methods from Tk), and each grabbed frame is a **fresh** numpy
  array (`converter.Convert(grab).GetArray()` -> undistort -> orient) that
  `process()` never mutates in place, so handing the reference to another thread
  read-only is safe.
- The recording sidecar carries `track_row(track_result)` per frame so the CSV's
  track columns are frame-aligned (ADR-0014). Moving tracking off-thread breaks
  that exact-frame alignment - the main thing to preserve.
- Downstream consumers (behavior / maze-state / rules tabs) already read
  `tracker.snapshot_result()` on 100-150 ms Tk polls.

## Decision

Add a dedicated **tracking worker thread**; the grab thread no longer runs the
tracker.

- **New headless module `host/track_worker.py`** with two stdlib-only,
  thread-safe primitives (unit-tested directly, keeping `camera_view` a thin
  shell):
  - `LatestFrameSlot` - a **1-slot, drop-oldest** mailbox. The grab thread
    `put()`s the newest oriented frame plus `(block_id, cam_ts, monotonic_ts)`;
    the worker `take()`s it, blocking until one exists. A `put()` over an
    unconsumed frame overwrites it and returns `True` so the skip is counted.
  - `TrackPublisher` - holds the latest `(track_result, block_id, monotonic_ts)`
    under a lock. `snapshot()` serves pollers / preview (a freshest position of
    **known age**); `subscribe(cb)` invokes callbacks **synchronously on the
    worker thread** the instant a result is published, isolated by try/except.
- **Grab thread** (`_grab_loop`) now does only: grab -> undistort -> orient ->
  update the latest-frame slot -> hand the newest frame to `LatestFrameSlot`
  (when tracking is on) -> enqueue for recording. It never calls `process()`.
- **Worker** (`_track_loop`) drains the slot, runs `process()` on the freshest
  frame, records `note_track_ms` (so the `track: <avg>/<peak> ms` readout still
  reflects real cost), and publishes the result. One worker, not a pool: the
  tracker is stateful/sequential (Kalman + `NO_REFERENCE->ACQUIRING->TRACKING->
  COASTING->LOST`), so it requires ordered frames.
- **Newest-available frame wins** (drop stale), not every-Nth: tracking degrades
  in *rate*, not correctness, and always acts on the most current position.
- **Sidecar alignment (nearest-tagged).** A recorded frame attaches the
  **latest-available** track result (`_track_pub.snapshot()`) plus a new
  `track_src_block_id` column = the BlockID of the frame that result was actually
  computed on. Staleness is thus explicit and measurable offline, with no
  fabricated (interpolated) positions. This **adds a column** to the ADR-0014
  sidecar schema; ADRs are immutable, so the schema change is recorded here.
- **Consumer seam.** `tracker.snapshot_result()` is unchanged (the worker still
  calls `process()`), so the Tk pollers keep working untouched. The
  `TrackPublisher.subscribe` callback is the low-latency, low-jitter seam a future
  positional-cue gate (FW-0014) will consume instead of the 100-150 ms Tk poll.

## Alternatives considered

- **A worker pool.** Rejected: the tracker's Kalman + state machine assume
  ordered, sequential frames; parallel `process()` would corrupt that temporal
  state.
- **Track every Nth frame on the grab thread.** The ADR-0014 escape hatch. Still
  couples cost to capture (an Nth frame that is slow still delays retrieve) and
  fixes a rate blindly rather than adapting to load. Rejected in favour of a
  newest-wins worker that self-throttles.
- **Interpolate skipped frames' sidecar positions.** Rejected: fabricates data;
  nearest + an explicit `track_src_block_id` is honest and sufficient for offline
  alignment.
- **Leave track columns blank on skipped frames.** Rejected: loses the freshest
  known position on most frames; nearest-tagged keeps it while still marking age.
- **Bigger-bang: move all consumers off the Tk poll now.** Deferred to a separate
  change (a single shared maze-state pipeline); it touches the actuating reward
  path and three tabs, too large for one reviewable commit.

## Consequences

- A slow `process()` (heavy clustering, blocker in view, recording on) now costs
  tracking **rate** (`track_skips`), not `src_drops`: capture keeps full frame
  rate and the recorded video stays complete. This is the root-cause fix for
  `#260720` that ADR-0039 only mitigated.
- Exact per-frame track/video alignment is replaced by nearest + a measurable
  source-BlockID offset. Offline readers must tolerate the new
  `track_src_block_id` column (older recordings lack it); the offset is normally
  a few frames and quantifiable.
- One more thread and a cross-thread frame handoff. Safe because frames are fresh
  and read-only downstream and the primitives are lock-guarded; a tracker
  exception still can't kill the worker or a recording.
- The `TrackPublisher` callback establishes the seam for the low-latency cue path;
  the duplicate-estimator cleanup + cue gate build on it (FW-0014).
- Revisit if a single worker can't keep a useful tracking rate under load (then
  consider FW-0013, freeing CPU cores via NVENC / encode-thread caps, using the
  `track_ms` readout to decide).

## Evidence / links

- Code: `host/track_worker.py`, `host/camera_view.py` (`_grab_loop`,
  `_track_loop`, `_Counters.track_skips`, `TRACK_SRC_COLUMN`,
  `_open_outputs`/`_write_frame`).
- Tests: `host/test_track_worker.py` (newest-wins/drop-oldest + skip reporting,
  take timeout, `stop()` unblocks a waiting `take()`, `clear()`; publisher
  snapshot / subscribe fan-out / callback-exception isolation / clear). Full
  suite green via `run_tests.bat` (173 tests).
- Acceptance (FW-0012): with a deliberately heavy `process()` + recording,
  `src_drops` ~0 while tracking degrades in rate; video keeps full frame rate;
  `track_ms` still reflects real cost. Latency vs physical crossings quantified
  with `host/maze_state_validate.py` (beam `S,<i>,<state>` ground truth).
- On-rig measurement (2026-07-22, live rat + tracked blocker): per-frame
  `process()` ~3.2 ms tracking, rising to ~4.4 ms with recording on (the ~1.2 ms
  is encoder CPU contention - the FW-0013 lever). Over ~10.5k frames `src-drop`
  stayed flat (~0.09%, warmup-level) while the new `track-skip` counter absorbed
  the rare worker lag (~0.12%) - i.e. tracking cost turned into skipped-for-track
  frames, not lost capture. At ~4.4 ms << the per-frame budget the single worker
  keeps large headroom, so FW-0013 stays deferred (decided from `track_ms`, as the
  task intended).
- Bug: `docs/notes/Bugs.md` `#260720_TrackingDelays`. Related: ADR-0011, 0014,
  0039; FW-0012 (this), FW-0013, FW-0014.
