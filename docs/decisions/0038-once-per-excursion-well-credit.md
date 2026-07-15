# ADR 0038: Credit the well reward at most once per outbound excursion (per-excursion choice latch)

- **Status:** Accepted
- **Date:** 2026-07-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rules_engine.py` (`WellRewardWithExcursionRule`: import of
  the `HUB` zone constant, `_CHOICE_CREDENTIALS`, `_excursion_open` latch,
  `tick` gating), `host/test_well_reward.py`
  (`TestExcursionDoubleReward`)
- **Supersedes / Superseded by:** extends [ADR-0026](0026-reward-shaping-phases.md)
  and [ADR-0030](0030-prime-at-choice-entry.md); relates to
  [ADR-0032](0032-live-behavior-readout.md); —

## Context

Bug `#260710_DoubleReward` (from [docs/notes/Bugs.md](../notes/Bugs.md)): running
Phase 2a, the animal could go down the central stem, turn into one choice arm and
trigger the reward, then turn back and enter the OTHER choice arm and trigger the
reward a second time — all within a single hub-to-hub trip.

Phase 2a/2b use `WellRewardWithExcursionRule` with `credential = choice_via_stem`
and `prime_on = credential` (ADR-0030), so the prime fires the instant a stem-gated
choice arm is entered and **consumes the credit** (`self.armed = False`). The only
re-credit guard in `_earn_credential` is `not self.armed`, which the prime has just
re-opened. Meanwhile `task.choice_entered` fires on *every* choice-arm entry, and
`task.outbound_used_stem` (`visited_since(CENTRAL_STEM, HUB)`) stays true for the
whole trip until the rat re-enters the HUB. So the second choice-arm entry re-armed
and re-primed: two rewards for one outbound excursion.

This is the same figure-8 hazard that `host/behavior_stats.py` already guards
against for its counts (a lap that crosses the junction enters two choice zones in
one trip). Its documented design (ADR-0032, `behavior_stats.py` module docstring)
is to keep `task_events` per-entry and **de-duplicate at the consumer** with a
per-excursion latch. The reward rule was the one consumer missing that latch.

## Decision

Add the same per-excursion latch to `WellRewardWithExcursionRule`:

- A boolean `self._excursion_open`, initialized `True`, re-armed on every tick the
  rat is at the HUB (`task.rat_state.zone == HUB`) and consumed (`False`) on the
  FIRST choice-arm entry of the trip.
- In `tick`, for the choice-family credentials only
  (`_CHOICE_CREDENTIALS = ("choice", "choice_via_stem")`), `_earn_credential` is
  called only while `_excursion_open`; a same-trip re-entry of the other arm is
  skipped, so it cannot re-arm or re-prime. `clean_loop` earns at loop close (back
  at the HUB), so it is not gated.

The gate lives in `tick` (the driving/sequencing layer), leaving `_earn_credential`
a pure per-frame decision, so the direct-call fade tests keep exercising it
unchanged. The re-arm condition is `zone == HUB` (persistent, every HUB tick)
rather than a one-tick loop-close edge, so a dropped edge cannot strand the latch
and silently withhold rewards for the rest of a session.

## Alternatives considered

- **Tighten the `not self.armed` guard alone.** Rejected: with
  `prime_on = credential` the prime clears `armed` immediately, so the guard is
  already re-open by the next choice entry — it does not express "once per trip".
- **Reset the latch on the `loop_closed` edge instead of at the HUB.** Rejected:
  `loop_closed` is a single tick; if that tick is dropped (a brief track loss at
  hub entry) the latch stays closed and the next real excursion earns nothing. A
  lost reward on a behavioral rig is worse than the rare cost of the persistent
  `zone == HUB` check.
- **Hoist "first choice per excursion" into `task_events` as the single source.**
  Deferred (see Consequences): it would touch `task_events` + `behavior_stats` +
  their tests, larger than this bug fix warrants, and the current de-dup-at-consumer
  split is the deliberate design of ADR-0032.

## Consequences

- One valid outbound excursion earns at most one credit/prime; a figure-8 that
  enters both choice arms in one trip is one trial, matching the counting unit in
  `behavior_stats.py`, so reward and readout agree. A genuine new excursion (after
  returning to the HUB) earns again.
- The excursion-latch definition now lives in TWO consumers (`rules_engine` and
  `behavior_stats`). This is consistent with ADR-0032's per-entry-at-source /
  de-dup-at-consumer design, but is mild duplication; a future consolidation could
  expose a first-choice-per-excursion signal from `task_events` and have both read
  it. Left as a noted follow-up to keep this fix small and reviewable.
- `clean_loop`, Phase 1 (`choice`, `prime_on = well_arrival`), and the lick-gated
  and fade paths are behaviorally unchanged (each already does one choice per trip).

## Evidence / links

- Code: `host/rules_engine.py` (`WellRewardWithExcursionRule._excursion_open`,
  `_CHOICE_CREDENTIALS`, `tick` gating; `from maze_state import HUB`).
- Test: `host/test_well_reward.py::TestExcursionDoubleReward`
  (`test_opposite_arm_on_way_back_does_not_reprime` — the other arm on the way back
  fires 0 and logs no `armed`; a new post-hub excursion primes again). Fails on the
  pre-fix code, passes after. Full host suite green (146 tests, 7 OpenCV skips).
- Mirrors the per-excursion latch in `host/behavior_stats.py` (`_excursion_open`,
  `_accumulate_choice`). Builds on ADR-0026 (task-event layer + credentialed well
  rule) and ADR-0030 (`prime_on = credential`). Fixes `#260710_DoubleReward`.
