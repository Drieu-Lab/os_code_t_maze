# ADR 0043: Move reward actuation onto a dedicated thread fed by fast-view pushes and routed lick edges

- **Status:** Accepted
- **Date:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rules_view.py` (actuation thread, merged queue, gate
  snapshot, thread-safe log), `host/maze_pipeline.py` (fast push seam; ADR-0042),
  `host/test_maze_pipeline.py`
- **Supersedes / Superseded by:** Extends ADR-0023 (rules engine + Free-Roam-gated
  executor), ADR-0040 (tracking off the grab thread), ADR-0041 (shared pipeline),
  ADR-0042 (fast view). Phase 2b of the low-latency-position work.

## Context

Through ADR-0041 the Rules tab still actuated on its **Tk poll**: every ~100 ms it
drained the shared pipeline, folded each `RatState` through its `TaskEvents`,
ticked the engine, and executed. Lick edges were handled inline in `apply` on the
Tk thread. So the beam/zone -> reward path carried up to a full poll interval of
avoidable latency (and jitter tied to Tk's scheduling), even though the reduction
itself is now frame-rate and low-jitter (ADR-0040/0041). This is the last poll on
the path the low-latency-position work exists to shorten, and it must shrink before
a host-owned positional audio cue can hit a tight spatial budget.

The hazard in moving off Tk is concurrency: the `RulesEngine` and `TaskEvents` are
pure, single-threaded objects that mutate per-tick state (cooldowns, latches,
top-up counts, loop/choice aggregates). Frames arrive on the tracking worker and
lick edges arrive on the Tk serial-dispatch thread; if both drove the engine we
would need a lock on the hot path or risk torn state.

## Decision

Give the Rules tab a **single dedicated actuation thread** that is the *only*
caller of `engine.tick` / `engine.event` / `TaskEvents.update`, fed by one merged
`queue.Queue`:

- **Frames**: the pipeline's fast view (ADR-0042) pushes each frame inline on the
  worker to `_on_fast_state`, which just enqueues `("frame", bundle)`.
- **Licks**: `apply` (Tk) no longer touches the engine; it enqueues
  `("lick", line, monotonic_ts)`.

The thread drains both **in arrival order** and folds them through the one engine,
so the engine stays single-threaded **with no lock on the hot path** - the
"route the licks onto the worker" model, chosen over "one lock around the engine".

Tk keeps ownership of everything Tk: config, UI, the live engine object (built and
swapped wholesale on Tk; the thread reads the reference per item). The Tk<->thread
hand-off is an **immutable `_GateSnapshot`** published once per poll
(`gate_open`, `needs_task`, `scale`, `landmarks`) plus a captured log dir; the
thread reads the whole snapshot atomically and guards every tick/event on
`gate_open`, so it never reads Tk state or the live config and **actuation fires in
exactly one place** (no Tk/thread double-fire). `TaskEvents` becomes
thread-owned: the gate rising edge resets it on the thread, and Tk config changes
request a reset via a flag the thread consumes before the next fold. The
rule-event log is written from both threads (thread fires; Tk logs nonstem-prob
edits) so it is guarded by a lock, and its status text is mirrored through a plain
field the Tk poll copies into the label (never a Tk var write off-thread). The
queue is bounded (`ACT_QUEUE_MAX = 512`, several seconds of frames) and drops +
counts on overflow rather than blocking the tracking worker.

Scope (this ADR): move the **existing reward rules** only. No new positional
audio-cue path yet - but this seam (fast push -> thread -> execute) is what it will
hang on. With no pipeline the tab degrades to the legacy Tk-poll path for
proximity-only rules.

## Alternatives considered

- **One lock around the engine, tick on the worker + event on Tk.** Simpler wiring
  but puts a lock on the per-frame hot path and splits engine access across two
  threads. Rejected in favour of a single-threaded engine (the user's call).
- **Actuate directly inside the fast callback (on the tracking worker).** Lowest
  latency, but `_execute` sends over TCP and writes a CSV - blocking I/O that would
  stall tracking, and it would put licks and frames on different threads. Rejected:
  the callback must stay cheap; hand off to our own thread.
- **Keep actuating on the Tk poll (ADR-0041 status quo).** Leaves the ~100 ms poll
  on the reward path - the exact latency this phase removes. Rejected.
- **Let Tk keep mutating `TaskEvents` / engine while the thread also does.** Torn
  per-tick state. Rejected: single-writer via the thread, with a reset flag and
  wholesale engine swap for the rare Tk-side changes.

## Consequences

- Reward fires within ~1-2 frames of a fast-view zone/lick event instead of up to
  a Tk poll later, and with less jitter (off Tk's scheduler) - the goal.
- The engine/`TaskEvents` per-tick logic is unchanged and still single-threaded,
  so all `rules_engine` / `well_reward` / `task_events` tests keep applying as-is;
  only the *caller* moved.
- New moving parts: a thread, a bounded queue (with a drop counter as a lag
  indicator), an immutable gate snapshot, a log lock, and a task-reset flag. The
  gate is now sampled at poll rate (~100 ms) - fine, since arming/mode/tracking are
  slow-changing; only tick/event timing became fast.
- The status line and preview read live engine state off Tk without a lock; these
  are display-only and race at worst by one frame (cosmetic). Rule identity is only
  ever replaced wholesale, so Tk never iterates a half-built rules list.
- Follow-up: the deferred positional audio-cue consumer attaches to the same fast
  push / thread seam. Revisit the "no lock, route everything" model if a future
  consumer needs to mutate engine config from yet another thread.

## Evidence / links

- `host/rules_view.py`: `_start_actuation`, `_on_fast_state`, `_enqueue_act`,
  `_actuation_loop`, `_process_act_item`, `_publish_gate_snapshot`, `_GateSnapshot`,
  `_request_task_reset`, thread-safe `_log_event` / `_stop_log`; `apply` now routes
  licks; `_poll` only publishes the snapshot + renders status.
- Fast seam + isolation tested in `host/test_maze_pipeline.py::TestFastView`
  (ADR-0042). Engine/task correctness unchanged: existing `test_rules_engine`,
  `test_well_reward`, `test_task_events` still green. Full host suite:
  `run_tests.bat` (193 tests, 17 OpenCV-skips).
- Builds on ADR-0042's `subscribe_fast`; clock is `time.monotonic()` end-to-end
  (bundle `mono_ts` and routed lick timestamps), matching `rules_engine`.
