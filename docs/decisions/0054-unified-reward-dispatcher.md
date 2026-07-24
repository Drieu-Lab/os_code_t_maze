# ADR 0054: Unify host reward dispatch behind one shared RewardDispatcher (global rate backstop + pulse/volume record + R<port> send)

- **Status:** Accepted
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/reward_dispatcher.py` (new), `host/rules_engine.py`
  (`_apply_global` removed - backstop no longer in the engine), `host/rules_view.py`
  (`_execute`/`_dispatch_reward`, interval sync on engine build),
  `host/phase3_controller.py` (`_fire_reward`), `host/task_view.py` +
  `host/experiment_control.py` (one shared instance injected into both
  controllers), `host/test_reward_dispatcher.py` (new), `host/test_rules_engine.py`
  (backstop assertion relocated)
- **Supersedes / Superseded by:** extends [ADR-0023](0023-position-based-rules-engine.md)
  (which put the backstop in the engine) and [ADR-0043](0043-rules-actuation-thread.md)
  (the actuation-thread model); builds on [ADR-0053](0053-gui-tab-streamlining.md)
  (the shared vision-actuator skeleton) and [ADR-0019](0019-reward-volume-and-pulse-duration-logging.md)
  (reward-volume accounting); —

## Context

The two host task controllers dispensed reward through **two unrelated paths**.
The Rules engine routed every `FireReward` through `RulesEngine._apply_global`,
which enforced a global minimum-interval **rate backstop** (a last-resort guard
against runaway firing) and paired each accepted fire with a `LogEvent` (and a
`"suppressed"` event when rate-limited). The host-owned Phase 3 controller instead
fired reward **directly** (`phase3_controller._process_frame` -> `R<port>1,<ms>`)
with **no** shared backstop and its own logging.

So the anti-spam guarantee, and the shape of the reward record, differed between
the two task modes for no principled reason - an accident of the Cue/Rules/Phase 3
tabs having grown separately (surfaced during the ADR-0053 GUI review and deferred
there as FW-0019). Both modes emit the *same* on-wire command (`R<port>1,<ms>`,
solenoid=1 = reward; ADR-0007), so there was no framing reason for the split.

## Decision

Introduce **one shared reward seam**, `host/reward_dispatcher.py`
(`RewardDispatcher`), that owns the three concerns a reward fire has in common,
and route **both** controllers through it:

1. **The global rate backstop.** The minimum-interval check moves **out of the
   pure Rules engine** into the dispatcher. `RulesEngine.tick`/`event` now return
   raw `FireReward` intents (plus the rules' own `LogEvent`s); `_apply_global` is
   gone. `EngineDefaults.global_min_interval_ms` is still read from `rules.json`
   and pushed into the shared dispatcher (`set_min_interval_ms`) when the Rules
   engine (re)builds, so the JSON knob still drives the interval.
2. **The pulse/volume record.** `fire(...)` returns an immutable `RewardRecord`
   (`fired`, `reason`, `rule_id`, `port`, `pulse_ms`, `volume_ul`,
   `suppressed_reason`), computing `volume_ul` from `reward_volume.reward_volume_ul`
   (ADR-0019). The dispatcher owns no log file: each controller persists the
   record into its own data file (the Rules track-log vs the Phase 3 session
   file), so the record **shape** is shared while each mode keeps its file.
3. **The `R<port>1,<ms>` send** - unchanged on-wire framing (no protocol change).

**One shared instance.** A single `RewardDispatcher` is created in
`experiment_control.make_task` and injected through `task_view` into both the
Rules and Phase 3 controllers, so the backstop and volume accounting are truly
rig-global and survive a task-tab switch. Because the two controllers run their
own ADR-0043 actuation threads (and the Rules tab can also fire from its Tk-poll
fallback), `fire` takes a small lock; fires are rare (a few per second at most),
so the lock is off the hot path in practice.

## Alternatives considered

- **Keep the backstop in the engine; add a second one in the dispatcher for
  Phase 3.** Rejected - two backstops for one guarantee is exactly the kind of
  silent divergence the comprehension-debt guardrails warn about; the user chose a
  single owner.
- **Per-controller dispatcher instances sharing config.** Rejected - simpler
  wiring, but the backstop and volume accounting would reset on every task-tab
  switch, so the guarantee would not be rig-global.
- **Make the dispatcher a pure core that only returns the command + record (shell
  does the send).** Considered; kept the send inside the dispatcher because "gate
  + record + send" is one indivisible reward action, and injecting the `send`
  (and `clock`) keeps it just as headless-testable.
- **Leave Phase 3 unguarded.** Rejected - Phase 3 fires reward at the same central
  well and deserves the same runaway protection; unifying is the whole point of
  FW-0019.

## Consequences

- A Phase 3 reward is now rate-limited and recorded identically to a rules reward;
  future task controllers inherit the same seam for free.
- The pure Rules engine no longer carries the backstop, so its `tick`/`event`
  return every rule's `FireReward` and the executor (via the dispatcher) decides
  what actually fires + logs. The one engine-level backstop test moved to
  `test_reward_dispatcher.py`; `test_well_reward.py` is unchanged (it asserts the
  rules' own `armed`/`credit_denied`/`debias_withhold` logs + the `FireReward`
  reason, none of which moved).
- No on-wire framing change and no change to ADR-0019 volume accounting (the
  firmware still echoes `REWARD,...` for the GUI tally; the dispatcher's
  `volume_ul` is an in-record convenience).
- The backstop interval is a single shared value for both modes (the FW-0019 open
  question "shared or per-mode"): shared, sourced from `rules.json`'s
  `global_min_interval_ms` (default 500 ms) with `DEFAULT_GLOBAL_MIN_INTERVAL_MS`
  as the fallback. Revisit if the two modes ever need different reward timing
  budgets.
- A suppressed Phase 3 reward is accurately **not** marked rewarded in the session
  file (the trial's `rewarded` stays 0), so the anti-spam drop is visible offline.

## Evidence / links

- Code: `host/reward_dispatcher.py`, `host/rules_engine.py` (backstop removed),
  `host/rules_view.py` (`_dispatch_reward`), `host/phase3_controller.py`
  (`_fire_reward`), `host/task_view.py`, `host/experiment_control.py`.
- Tests: `host/test_reward_dispatcher.py` (backstop one-through/next-dropped,
  interval reconfig, send-failure resilience, Rules/Phase 3 parity),
  `host/test_rules_engine.py::TestCooldownAndBackstop::test_engine_no_longer_rate_limits`.
  Full host suite green (304 tests, 17 OpenCV skips).
- Backlog: implements FW-0019; the reward-parameter parity follow-up is FW-0024.
- Related: ADR-0023 (engine + Free-Roam-gated executor), ADR-0043 (actuation
  thread), ADR-0053 (shared vision-actuator skeleton), ADR-0019 (reward volume),
  ADR-0007 (central-port reward), ADR-0046/0047 (host-owned Phase 3).
