# ADR 0068: Split the well-reward rule into a Trigger ("why/when") and a Dispenser ("how")

- **Status:** Accepted
- **Date:** 2026-07-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/reward_dispenser.py` (new pure core: `RewardTrigger` +
  `WellDispenser` + the moved reward primitives `FireReward` / `LogEvent` /
  `LickAt` / `CONJUNCTION_FRESH_S`), `host/rules_engine.py`
  (`WellRewardWithExcursionRule` becomes wiring; re-exports the primitives),
  `host/test_reward_dispenser.py` (new)
- **Supersedes / Superseded by:** — (implements the deferred FW-0009; builds on
  [ADR-0026](0026-reward-shaping-phases.md), [ADR-0028](0028-probabilistic-phase-fade.md),
  [ADR-0030](0030-prime-at-choice-entry.md))

## Context

`WellRewardWithExcursionRule` (the credential-gated well reward behind Phases 1 /
2a / 2b, ADR-0026) had grown to bundle three concerns in one ~300-line class:

- **why/when a payout is deserved** — the excursion `credential`, the Phase-1→2a
  probabilistic `nonstem_reward_prob` fade (ADR-0028), and the anti-bias `debias`
  cap over a seeded RNG (ADR-0030);
- **where/when the prime fires** — `prime_on` = well-arrival / well-lick / out at
  the credential (ADR-0030); and
- **how it dispenses at the well** — the port/pulse, the OUTSIDE/AT_WELL visit
  lifecycle, the lick top-up counter + count cap + cooldown, and the lick
  freshness/debounce gates.

ADR-0030 deliberately added the third `prime_on` mode *in place* rather than
extracting an abstraction, because CONTRIBUTING #6 ("no abstraction until ~2–3
real callers") was not met: the well rule was the only live caller. That has now
changed. The host-owned Phase 3 cued task (ADR-0046) is a second real caller that
wants the *same* dispensing behaviour — prime the central well the instant a
choice is committed, then let licks top up — but with a different trigger
(trial-sequence correctness, not a maze excursion). The plan to give Phase 3 a
reward pulse-ms field and lick top-ups, and ultimately to unify Phases 1/2a/3
under one config-driven framework, needs a reusable dispenser rather than a
copied one. FW-0009 anticipated exactly this seam.

## Decision

Extract two composable **pure cores** into a new `host/reward_dispenser.py`, and
reduce `WellRewardWithExcursionRule` to the wiring that composes them:

- **`RewardTrigger` (the why/when).** Owns `credential`, `nonstem_reward_prob`,
  the `debias` cap, and the seeded RNG. `earn(task)` folds one task-event frame,
  gates the choice-family credentials on the shared once-per-excursion
  choice-commit edge (ADR-0055), sets the single pending-credit flag `armed`, and
  returns the `armed` / `credit_denied` / `debias_withhold` `LogEvent`s. It holds
  no port/pulse/top-up state. `_earn_credential` moved here largely intact.

- **`WellDispenser` (the how).** Owns `port` / `pulse_ms`, the OUTSIDE/AT_WELL
  visit lifecycle (entered/exited only on confident readings), prime delivery,
  the top-up counter + `max_topups` cap + `topup_cooldown_ms` cooldown, and the
  lick `lick_debounce_ms` / freshness gates. It emits `FireReward` intents.
  `prime_on` lives here because it is about *where/when the credit is spent*.

- **The seam.** The credit (`armed`) has a single owner — the Trigger — and is
  handed to the Dispenser each tick as `credit_pending`. When the Dispenser
  spends it (an arrival or first-lick prime) it reports `consumed_credit` so the
  wiring clears the Trigger. A credential-timed prime (fired out at the choice) is
  spent immediately by the wiring and carried into the visit by the Dispenser so
  later licks still top up. So `prime_on` is "which credit edge the Dispenser
  acts on", not a conditional threaded through `tick`/`on_lick`.

Dependency direction runs one way: `rules_engine → reward_dispenser`. The shared
reward primitives (`FireReward`, `LogEvent`, `LickAt`, `CONJUNCTION_FRESH_S`) now
live in `reward_dispenser` and are re-exported from `rules_engine`, so
`from rules_engine import FireReward` and the landmark `prime_and_lick_topup` rule
keep working with no change to their callers.

This is a **behaviour-preserving refactor**: the full `test_well_reward.py` suite
(and the rest of the host suite) passes unchanged, and the shipped `rules.json`
Phase 1/2a/2b behaviour is byte-identical.

## Alternatives considered

- **Leave it as one class (status quo).** Rejected: a second real caller (Phase 3)
  now exists, so CONTRIBUTING #6 is satisfied and copying the dispensing logic
  into Phase 3 would duplicate the top-up/visit/anti-camp state machine — exactly
  the comprehension debt the guardrail guards against.
- **Extract only a narrow Dispenser (prime + top-up), leave the trigger in the
  rule.** Rejected: it would leave the `prime_on` conditional and the `armed`
  hand-off split awkwardly across the rule and a half-core, and would have to be
  re-opened for the unification work. Doing the full split now pre-builds the seam
  Phase 3 slots into as a peer Trigger on a shared Dispenser.
- **Keep the reward primitives in `rules_engine` and import them into
  `reward_dispenser`.** Rejected: that makes the dependency mutual (import-order
  fragile). Moving them into the lower-level core and re-exporting keeps a clean
  one-way dependency with no change to existing importers.
- **Make `armed` a two-owner flag (both objects mutate it).** Rejected: a single
  owner (the Trigger) with an explicit `credit_pending` in / `consumed_credit` out
  contract keeps the rule's public `armed` / `_earn_credential` surface intact and
  the ownership legible.

## Consequences

- The dispensing machinery (prime, lick top-ups with a count cap + cooldown, the
  confident-only visit lifecycle) is now reusable. Phase 3 can gain lick top-ups
  and a reward pulse-ms field by feeding a `WellDispenser`, instead of a parallel
  implementation — the next planned step, and a step toward the unified
  block-selected framework.
- Each core is unit-testable in isolation; `test_reward_dispenser.py` adds focused
  Trigger and Dispenser tests alongside the end-to-end `test_well_reward.py`.
- The rule keeps a small back-compat surface: `armed` (a property over the
  Trigger) and `_earn_credential` (a delegate) so the direct-drive fade tests and
  the Rules-tab status/editor are unchanged; the class-level `_CREDENTIALS` /
  `_CHOICE_CREDENTIALS` now alias the canonical tuples on `RewardTrigger`.
- Cost: one more module and a small indirection (the wiring clears the Trigger on
  a reported consume). No runnable program or launch command changed, so
  `PROGRAMS.md` is untouched. If the dormant `prime_and_lick_topup` is ever
  retired or migrated onto the Dispenser, revisit the two-template split.

## Evidence / links

- New pure core: `host/reward_dispenser.py`; wiring: `host/rules_engine.py`
  (`WellRewardWithExcursionRule`); tests: `host/test_reward_dispenser.py`.
- Behaviour pinned by `host/test_well_reward.py` (unchanged, passing) and the full
  host suite (`python -m unittest discover -p "test_*.py"`, 384 tests OK).
- Implements [FW-0009](../future-work.md#fw-0009-triggerdispenser-decomposition-of-the-well-reward-rule).
- Refs ADR-0026 (reward-shaping phases), ADR-0028 (probabilistic fade), ADR-0030
  (prime at choice entry), ADR-0055 (shared choice-commit edge).
