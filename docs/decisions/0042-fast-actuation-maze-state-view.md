# ADR 0042: A second "fast" maze-state view for actuation, alongside the debounced one

- **Status:** Accepted
- **Date:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/maze_pipeline.py` (fast estimator + `subscribe_fast` /
  `set_fast_params` / `fast_params`), `host/test_maze_pipeline.py`,
  `host/rules_view.py` (fast min-dwell control + fast-view consumer),
  `host/tooltip_text.py`
- **Supersedes / Superseded by:** Extends ADR-0025 (maze-state module), ADR-0040
  (tracking off the grab thread) and ADR-0041 (shared pipeline). Paired with
  ADR-0043 (on-worker actuation), which consumes this view. Phase 2b of the
  low-latency-position work.

## Context

ADR-0041 put **one** `MazeStateEstimator` on the tracking worker and fanned its
`RatState` out to every tab. That estimator is deliberately **debounced**:
`min_dwell_ticks` (default 3) makes a new zone persist for several frames before
it commits, so the display, the behavior stats, and the state-timeline log don't
flicker on a single noisy centroid.

But the debounced view is now also what the reward rules read, and debouncing is
exactly the wrong trade-off for actuation: it *adds* latency to the beam->reward
path (the thing the whole low-latency-position effort, ADR-0040/0041, is trying to
shrink) and, worse, it is a scientific tuning knob for *display stability* that we
do not want coupled to *when reward fires*. Lowering the one estimator's dwell to
speed up reward would make the readout and the behavior metrics jitter; keeping it
high for a clean readout slows reward. One estimator cannot serve both goals.

## Decision

Run **two** estimators in `MazeStatePipeline` over the same track stream, under
the same lock, from the same `on_track`:

- the **debounced** estimator (unchanged, tolerant `min_dwell`) - display,
  behavior, state log, via `snapshot()` / `register()` (ADR-0041);
- a new **fast** estimator (low `min_dwell`, **default 1 frame**) for actuation,
  exposed through a push-only seam: `subscribe_fast(cb)` invokes `cb(fast_bundle)`
  **inline on the worker** for every frame.

Both share the one zone map - `set_zone_map` reconfigures **both** - so "where the
rat is" is defined identically; only the commit *debounce* differs.
`set_fast_params` tunes the fast estimator alone (its `min_dwell_ticks`), and the
Rules tab gets a "Fast min dwell (frames)" control wired to it (default 1). Fast
subscribers must be cheap and non-blocking because they run inline on the tracking
worker; the Rules consumer (ADR-0043) just enqueues to its own thread.

The fast bundle is the same `TrackBundle` shape (result + BlockID + monotonic/wall
stamps) but carries the **fast** `RatState`, so a consumer can commit a zone in
~1 frame (~17-33 ms) instead of ~3 (~50-100 ms) without waiting for a Tk poll.

## Alternatives considered

- **Single estimator, just lower its dwell.** Couples reward latency to display
  stability - lowering dwell for snappy reward makes the readout/behavior metrics
  flicker; raising it for a clean readout slows reward. Rejected: the two goals
  have opposite dwell needs.
- **Fast view as another `register()` drained queue.** The debounced fan-out is a
  *pull* seam drained on a Tk poll (~100 ms) - reintroducing exactly the poll
  latency we are removing. Actuation needs a *push* the instant the frame is
  reduced, hence `subscribe_fast`. Rejected.
- **Compute the fast state in the Rules consumer from the raw result.** Puts a
  third estimator (and a third zone-map copy) back in a tab - the divergence ADR-
  0041 removed. Rejected: keep the reduction in one place.
- **Reuse the debounced bundle but ignore dwell downstream.** The consumer would
  re-implement commit logic; the estimator already owns it. Rejected.

## Consequences

- The reward path can react a couple of frames sooner and is tunable independently
  of the readout - the point of the exercise. `maze_state_validate.py` can compare
  fast vs debounced commit latency.
- A second estimator runs per frame on the worker (a second point-in-polygon +
  small bookkeeping). Cost is negligible next to tracking (~3-4 ms; ADR-0040) and
  it holds the same lock, so no new race.
- Two "current zone" notions now exist. This is intentional and named (fast =
  actuation, debounced = display/analysis); the state log and behavior stats stay
  on the debounced one so the scientific record is unchanged.
- New knob to get wrong: too low a fast dwell can fire on a one-frame excursion
  into a zone. Default 1 is aggressive; raise it from the Rules tab if bench
  testing shows false commits. Revisit if we want per-rule dwell instead of one
  fast view.

## Evidence / links

- `host/maze_pipeline.py`: `_fast_estimator`, `on_track` (folds + pushes both
  views under one lock), `set_zone_map` (reconfigures both), `set_fast_params` /
  `fast_params`, `subscribe_fast` / `unsubscribe_fast`.
- `host/test_maze_pipeline.py::TestFastView`: fast default dwell = 1; every frame
  pushed; fast bundle carries the fast state; **fast commits a zone ~3 frames
  before the debounced `snapshot()`**; `set_fast_params` tunes only the fast view;
  `set_zone_map` reconfigures both; unsubscribe stops delivery; a raising fast
  callback is isolated from the debounced fan-out and snapshot. Full host suite
  green: `run_tests.bat` (193 tests, 17 OpenCV-skips).
- Consumed by ADR-0043 (Rules actuation thread).
