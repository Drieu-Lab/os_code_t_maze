# ADR 0041: One shared maze-state pipeline on the tracking worker

- **Status:** Accepted
- **Date:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/maze_pipeline.py` (new), `host/test_maze_pipeline.py` (new),
  `host/camera_view.py` (`subscribe_track`), `host/experiment_control.py`
  (shared pipeline creation + wiring), `host/maze_state_view.py`,
  `host/behavior_view.py`, `host/rules_view.py`; the Maze state state-timeline log
  `now` column semantics
- **Supersedes / Superseded by:** Extends ADR-0025 (maze-state module) and
  ADR-0040 (tracking off the grab thread); does not supersede either. Phase 2a of
  the low-latency-position work whose Phase 1 was ADR-0040.

## Context

The discrete maze-state reduction (ADR-0025, `MazeStateEstimator`) was run
**three times** - once per tab. The Maze state, Rules, and Behavior tabs each
built a private estimator and re-reduced `tracker.snapshot_result()` on its own
Tk timer ([maze_state_view.py], [rules_view.py], [behavior_view.py]). Three
problems followed:

- **Duplicated compute** - the same per-frame reduction ran three times.
- **Divergence risk** - each tab has its own params (`min_dwell`, `min_confidence`),
  so the three could commit different zones for the same rat, and the reward the
  animal earns (Rules) could be derived from a different state than the statistics
  reported (Behavior) - a scientific-validity bug, not just code duplication.
- **Poll-rate commit floor** - each estimator only ticked at its tab's poll
  (~100-150 ms), so a new zone took `min_dwell_ticks x poll` (~3 x 150 ms ~=
  450 ms) to commit, and a fast transient entirely between two polls could be
  missed outright.

ADR-0040 already moved tracking onto a dedicated worker that publishes each
result via a `TrackPublisher`. That gives us a single, frame-rate, low-jitter
seam to hang one estimator on.

## Decision

Add `host/maze_pipeline.py`: a headless, stdlib-only `MazeStatePipeline` wrapping
**one** `MazeStateEstimator`. It subscribes to the camera worker
(`CameraView.subscribe_track` -> `TrackPublisher`), so `on_track(track_result,
block_id, mono_ts)` runs on the worker for **every** published result, folds it
into a `RatState`, and fans a `TrackBundle` (result + BlockID + monotonic/wall
stamps + the RatState) out two ways:

- `snapshot()` - the freshest bundle, for **display-only** reads (readout labels,
  preview overlay).
- `register() -> Subscriber` - a **per-consumer bounded FIFO** that the consumer
  `drain()`s on its Tk poll and folds **every** bundle through its own
  `TaskEvents` / `BehaviorStats` / engine.

The three tabs drop their private estimators and consume this one:

- **Maze state** owns editing: its zone-map edits and param sliders call
  `set_zone_map` / `set_params` on the shared pipeline (reconfiguring the one
  estimator all tabs see); it renders from `snapshot()` and drains the stream for
  the state-timeline log.
- **Behavior** and **Rules** drain the stream and fold each `RatState` in order.

**Embrace frame-rate ticks.** The one estimator now ticks per worker frame, so
`min_dwell_ticks` counts *camera frames*, not GUI polls: a zone commits in ~3
frames (~50-100 ms) instead of ~450 ms - a beneficial latency drop. The Maze
state tab's "Min dwell" label now reads *frames*.

**Edge-safe delivery via the drained queue.** `entered` / `exited` /
`loop_completed` are one-frame flags, and `TaskEvents` aggregates
(`same_side_choice_run`, loop counts) are sequential, so a consumer must fold
every state, not sample the latest - otherwise a choice/loop landing between two
polls is silently lost (wrong anti-bias credential, wrong stats, missed reward).
The queue is bounded drop-oldest with a `dropped()` counter so a stalled GUI is
visible, never unbounded growth.

**Actuation stays on the Tk thread (2a scope).** Folding + engine tick now run at
frame rate but **batched** on the existing Tk poll, so reward still fires on the
Tk thread. Moving actuation onto the worker callback (and adding a second, fast
low-latency view) is Phase 2b.

**Global estimator; consumers never reset it.** A consumer's session boundary
(Rules gate rising edge, Behavior MODE switch) resets only that consumer's own
`TaskEvents` / stats. `RatState.history` is global physical position and belongs
to the shared estimator.

**Clock.** The bundle stamps `RatState.now` with `mono_ts` (the worker's
`time.monotonic()`), the same clock the rules engine ticks and processes lick
edges on, so Rules passes `bundle.mono_ts` straight to `engine.tick`. The bundle
also carries `wall_ts` (`time.time()`) so the state-timeline log keeps recording
wall-clock time (its `now` column is unchanged in meaning).

## Alternatives considered

- **Keep per-tab estimators, just share params** - would fix divergence but not
  the triple compute or the poll-rate commit floor, and leaves three code paths
  that can still drift. Rejected.
- **Let consumers sample `snapshot()` at poll rate** - simplest, but drops
  one-frame edges between polls (the core correctness bug above). Rejected in
  favor of the drained queue.
- **A single global `TaskEvents` / `RulesEngine` too** - couples every consumer's
  session state and moves actuation off the Tk thread prematurely. Deferred: only
  the estimator is shared in 2a; the fast view + on-worker actuation are 2b.
- **Coalesce non-edge states in the queue** - added complexity for no benefit
  once the queue is sized to several seconds of frames. Rejected.

## Consequences

- One reduction, one definition of a zone/choice/loop across all tabs - reward
  and statistics can no longer disagree.
- Lower commit latency (frame-rate `min_dwell`) - the point of the low-latency
  position work; validate with `maze_state_validate.py` (tighter floor -> lower
  per-beam latency).
- The rules engine now ticks per frame (batched on the poll); its per-tick
  cooldown/top-up/latch logic is unchanged but reward-timing granularity is
  finer - on the 2a validation list. Behavior distance also integrates per frame
  (finer, a plus).
- New failure surface: a stalled consumer overflows its bounded queue and drops
  states (edges). Sized generously (`DEFAULT_QUEUE_MAX = 256`, several seconds)
  and counted (`Subscriber.dropped()`).
- Revisit if a consumer genuinely needs sub-poll actuation latency - that is the
  2b on-worker move, not a change to this seam.

## Evidence / links

- New pure core `host/maze_pipeline.py`; headless tests `host/test_maze_pipeline.py`
  (fan-out to multiple subscribers, one-frame-edge capture via drain vs snapshot,
  bounded drop-oldest + count, thread-safe `on_track` vs reconfigure). Full host
  suite green: `run_tests.bat` (185 tests, 17 OpenCV-skips).
- `maze_state_validate.py --selftest` PASS (estimator behavior unchanged by the
  wrapper).
- Builds on ADR-0040's `TrackPublisher.subscribe` seam and ADR-0025's estimator
  contract; consumed clock matches `rules_engine` (`time.monotonic`).
