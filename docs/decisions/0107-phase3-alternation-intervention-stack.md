# ADR 0107: De-alternation intervention block + a composable intervention stack (Phase 3)

- **Status:** Accepted
- **Date:** 2026-08-16
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/alternation.py` (new), `host/tmaze/core/phase3/intervention_stack.py` (new), `host/tmaze/core/phase3/trial_sequence.py` (`intervention_blocks` generator + closed-loop generalisation + `trial_meta` `block_kind`/`alt_rate`), `host/tmaze/core/phase3/phase3_config.py` + `host/phase3.json` (resolver, `alternation_blocks` section + `enabled` flags), `host/tmaze/core/phase3/phase3_task.py` (decision fields + alternation passthroughs), `host/tmaze/core/phase3/phase3_log.py` (`block_kind`/`alt_rate` columns + header), `host/tmaze/core/phase3/phase3_stats.py` (kind-aware efficacy + de-alternation summary + live `alt_rate`), `host/tmaze/ui/phase3_controller.py` / `trial_control_bar.py` / `experiment_control.py` / `behavior_view.py` (GUI), `host/tests/test_alternation.py` + `host/tests/test_intervention_stack.py` (new); tooltips
- **Supersedes / Superseded by:** — (extends [ADR-0086](0086-phase3-debias-blocks.md) / [ADR-0090](0090-debias-bias-normalized-by-errors.md); implements FW-0035)

## Context

Rats on the cued T-maze climb a ladder of history-based "cheat" strategies —
side arms → side bias → **alternation** (switch every trial) → [cue use] — and
tend to stall at alternation, never stepping up to using the tone. Against an
animal-independent counterbalanced schedule an alternator still collects ~50% of
trials, so it has no reward pressure to change.

The side-bias `debias_blocks` generator (ADR-0086/0090) already closes the
*order-0* rung: when the sliding-window directional-error bias crosses a
threshold it forces the non-preferred side until the rat complies. The eventual
goal is a full *adversarial* schedule that predicts the rat's next choice from
its whole history and rewards the side it is predicted **not** to take. FW-0035
scoped the **next single rung** — an alternation block — so the
intervention-block mechanism and its welfare tolerance can be **beta-tested on
rats before** building the full predictor.

Two subtleties drove the design during planning:

1. **A naive switch-rate detector punishes a cue-follower.** If the detector just
   counts switches in the choice stream, a rat correctly *following* an
   alternating stretch of the cue looks like an alternator and gets punished for
   using the cue — the exact opposite of the goal. This is also fragile to the
   animal's alternation being *off-phase* with a locally-alternating schedule.
2. **A "consecutive-correct" exit reinforces side bias.** In an alternation
   block the forced correct side is the last choice, so N-consecutive-correct is
   by construction N repeats on **one** side — half the time the rat's already
   preferred side. Exiting that way manufactures the very bias we fight.

## Decision

Add a **de-alternation block** as a pure-core twin of the debias block, and a
**composing controller** that runs it concurrently with the side-bias block, one
active at a time. Both are selected by a new `sequence.generator` value
`intervention_blocks`; `balanced` and `debias_blocks` are unchanged.

- **Cue-aware detector (`alternation.py`).** Condition on trials where the cue
  told the rat to **repeat its own last choice** (`cued_side == prev_chosen`) —
  call these *samples* — and measure
  `alt_rate = P(switch | repeat-cued) = #(chosen != prev_chosen among samples) / #samples`.
  For a pure alternator every repeat-cued trial is an error, so evidence accrues
  in proportion to how much the alternation *costs* the rat; a winning /
  cue-following rat (which repeats when the cue points back to its last choice) is
  never flagged, and the measure is phase-independent by construction. A block
  auto-triggers once `min_samples` repeat-cued trials have accrued in the window
  **and** `alt_rate >= trigger_rate`.
- **Adversarial placement.** Forced correct side = the rat's **last choice**
  (it must *repeat* to be rewarded), recomputed each trial from the last scored
  choice (unlike the fixed-per-block debias side).
- **Relaxed-rate exit (not consecutive-correct).** Inside a block every trial is
  repeat-cued by construction, so the in-block `alt_rate` reads compliance
  directly. Exit when it relaxes to `exit_rate` (`< trigger_rate`, hysteresis)
  after `exit_min_samples` in-block samples, or at the `max_block_len` welfare
  cap. Any residual side bias the animal slides into on the way out is caught by
  the concurrently-running debias block, not this one.
- **Composition (`intervention_stack.py`).** `InterventionStackController` holds
  one child per enabled intervention (side-bias first, then alternation), fans
  `record()` out to all, and arbitrates `decide()`: while a block is active it
  delegates only to that child (clearing it on exit); when NORMAL it polls
  children in priority order and the first to trigger owns the block. Only one
  block runs at a time. It owns a monotonic `block_id` and exposes the same
  `decide()`/`record()`/`target`/`state`/`block_id` contract as a single
  `DebiasBlockController`, so `trial_sequence.py` drives either interchangeably.
- **Independent enablement.** Each intervention has an `enabled` flag in
  `phase3.json`; `to_trial_config` attaches a child only when enabled, so the two
  Phase 3-tab checkboxes map 1:1 onto the constructed stack. With both off the
  stack is inert (the plain counterbalanced spine).
- **Observability.** Two per-trial CSV columns (`block_kind`, `alt_rate`; proto
  stays 7) record which intervention forced a trial and the live cue-aware rate;
  the header logs the alternation params; the behavior panel surfaces the live
  `alt_rate` + alternation-block count; a second Rig-view button and an operator
  passthrough force/end an alternation block manually.

Defaults (`AlternationBlockConfig`): `window=30`, `min_samples=8`,
`trigger_rate=0.8`, `exit_rate=0.6`, `exit_min_samples=6`, `max_block_len=15`.
The window is longer than debias's 20 because the cue-aware conditioning only
uses the repeat-cued subset.

## Alternatives considered

- **Cue-independent switch-rate detector (FW-0035's first sketch).** Rejected: it
  penalises a cue-follower on an alternating schedule stretch and is sensitive to
  the animal's alternation being off-phase with the schedule. The cue-aware
  conditioning removes both failure modes (see `test_alternation`
  `test_cue_follower_on_alternating_schedule_is_zero`).
- **`exit_consecutive` correct in a row (as debias uses).** Rejected: here it is
  N repeats on one side and manufactures a side bias (see Context #2).
- **Merge alternation into the debias block / one entangled controller.**
  Rejected: two contingencies in one block is hard to reason about and test. A
  composing stack keeps each controller single-responsibility, independently
  testable, and doubles as the first "stack the experts" seam the full
  adversarial scheduler will need.
- **A self-contained two-phase block that chains into a bias-guard.** Deferred in
  favour of concurrent composition (simpler, and the debias block already exists).

## Consequences

- The intervention-block mechanism can now be beta-tested against alternation on
  real rats, with per-trial `alt_rate`/`block_kind` logged for analysis, before
  any adversarial-predictor work.
- **Welfare:** an adversarial block drives a committed alternator's reward toward
  zero (not 50%), so `max_block_len` + a manual-water floor are load-bearing;
  the operator can also force/end blocks and disable either intervention live.
- **Reproducibility:** like debias, an `intervention_blocks` session is
  reproducible only from (seed + realized choices + operator overrides); the CSV
  is the record of truth (header carries the caveat).
- `reward_incorrect` defeats any block (it rewards every choice); a non-blocking
  caution now appears when a closed-loop generator is selected with the aid on.
  The aid remains a removal candidate (FW-0036).
- `trial_meta` now returns two extra keys (`block_kind`, `alt_rate`) and
  `Phase3Decision` gained matching fields; the block-efficacy readout is scoped
  to side-bias blocks (alternation blocks are summarised as a count + mean
  length, since their exit is a relaxed-rate test, not consecutive-correct).
- Would revisit if beta shows the cue-aware trigger is too twitchy/too slow, or
  if concurrent arbitration proves insufficient (e.g. a habit that needs two
  blocks at once).

## Evidence / links

- Implements FW-0035; extends ADR-0086 (closed-loop side-bias) and ADR-0090
  (error-count evidence gate, mirrored by the `min_samples` sample-count gate).
- Headless tests: `host/tests/test_alternation.py` (cue-aware measure incl. the
  cue-follower guard, auto trigger forcing the last choice, relaxed-rate success
  exit, cap exit, manual override, and an `intervention_blocks` `TrialSequence`
  driven by a synthetic alternator) and `host/tests/test_intervention_stack.py`
  (side-bias priority when both arm, one-active-at-a-time, record fan-out,
  per-kind force/end with monotonic ids, inert-when-both-disabled). Full host
  suite green (`python -m unittest discover` under `host/tests`).
