# ADR 0169: A trial trip requires an unbroken outbound leg

- **Status:** Accepted
- **Date:** 2026-09-17
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/task_events.py` (the `trial_trip` definition +
  new `_outbound_broken` latch); doc-only in `phase3_task.py`, `cue_gate.py`,
  `reward_dispenser.py` (they read `trial_trip` unchanged); `test_task_events.py`
- **Supersedes / Superseded by:** — (extends ADR-0168/0099; supersedes the
  semantic call in ADR-0067 that a stem-departed trip stays a trial until home)

## Context

ADR-0168 made `task_events.trial_trip` the ONE credential the Phase 3 scorer, the
cue gate, and the reward's `choice_via_stem` all read, defined `== left_hub_via_stem`
(this trip departed the hub up the stem). That credential only asks HOW THE TRIP
BEGAN. It says nothing about what the rat did on the way out, so two "back-door"
routes still counted as trials:

- The rat departs up the stem (so `left_hub_via_stem` is true), then **dips into a
  return arm** before committing a choice, then carries on to a choice arm.
- The rat reaches a choice arm by coming **up that arm's own matching return arm**.

ADR-0067 had explicitly seen the first route and, on the operator's call at the
time, kept the tone playing ("a trip remains a trial until the animal actually
gets home"). Re-evaluating with the current cohort, the operator now judges these
detour/figure-8 routes as NOT genuine trials: the rat should reach its choice from
the stem, not loop in from a return arm. Because ADR-0168 unified the credential,
this can be tightened in exactly one place.

## Decision

Tighten the single credential to require a clean outbound leg:

```
trial_trip = left_hub_via_stem AND NOT outbound_broken
```

`outbound_broken` is a new per-trip latch in `TaskEvents` (beside the choice-commit
re-arm and `left_hub_via_stem`), cleared on HUB presence like the others
(docs/decisions/0038):

- **G2 (strict) — any pre-commit return-arm entry breaks the trip.** Before a
  choice commits, the shared `_excursion_open` latch is True; a return-arm
  `entered` edge while it is open is on the OUTBOUND leg, so it sets
  `outbound_broken`. A return-arm visit AFTER the commit is the normal INBOUND leg
  and does NOT break it — so the return cue still fires on a clean trial.
- **G1 (backstop) — a choice committed directly from its own return arm breaks
  it.** If the committing choice-arm entry's `exited` zone is that side's matching
  return arm, the trip is broken even if the return-arm `entered` edge was never
  seen (a dropout or a sub-`min_dwell` dwell). In the common case G1 is subsumed by
  G2; it exists so a missed edge cannot slip a back-door commit through.

A broken trip is DENIED exactly like the ADR-0099 divider-jump: no score, no
reward, no cue, and the trial index does not advance, so the scheduled trial is
preserved and scored on the next clean hub→stem trip. It is NOT reclassified as a
non-stem excursion (the stem WAS used, so `outbound_used_stem` stays true; the two
predicates simply differ here, which is why ADR-0168 kept `outbound_used_stem` as a
separate descriptive field). Applies to all three consumers, live, per the
operator's call.

No consumer code changes: the scorer, cue gate, and reward already read
`trial_trip` (ADR-0168), so they inherit the tighter rule.

## Alternatives considered

- **Only disqualify a same-side return dip** (leave an opposite-side wander a
  trial) — rejected: the operator chose the strict rule; any pre-commit return-arm
  entry means the rat did not run a clean stem→choice outbound leg.
- **Keep ADR-0067's semantics (trip stays a trial until home), add only G1** —
  rejected: it leaves the substantive detour route counting as a trial.
- **Tighten the score + cue but leave reward on the looser credential** — rejected:
  re-opens the ADR-0099 split where the reward and the cue/score disagree about
  what a trip is.
- **Demote a broken trip to the non-stem reward fade** — rejected: Phase 3 has no
  fade, and a broken trip is a stem trip, not a non-stem excursion; hard-deny keeps
  one clean definition.
- **Apply direction gating instead** — rejected for the same reasons as ADR-0067
  (the direction estimate is too noisy and is run off).

## Consequences

- The detour/figure-8 routes are now fully inert as trials (no cue, no reward, no
  score, no advance); the scheduled trial is preserved for the next clean trip.
- This REVERSES ADR-0067's recorded semantic choice for the choice cue on the
  stem-departed-then-detoured route; that is deliberate and owned by the operator.
- Normal forward trials are unchanged: a clean hub→stem→junction→choice→return→hub
  lap never touches a return arm before its commit, so `trial_trip` is true
  throughout, and the post-commit return leg keeps it true for the return cue.
- `task_events` gains a third per-trip latch; the clear-on-presence rule keeps it
  robust to dropped edges (ADR-0038).
- If a future protocol makes a return-arm-first or detour route a legitimate trial
  start, this is the one place to revisit — together with `left_hub_via_stem` and
  `outbound_used_stem`.

## Evidence / links

- Extends ADR-0168 (one `trial_trip` credential) and ADR-0099 (hub-departure
  credential for cue + score + reward); supersedes the ADR-0067 semantic call.
- Tests: `test_task_events.TestTrialTrip` -
  `test_return_arm_on_the_outbound_leg_breaks_the_trip` (G2, real estimator),
  `test_return_arm_after_the_commit_does_not_break` (inbound leg keeps the return
  cue), `test_choice_committed_from_own_return_arm_backstop` (G1, hand-built
  RatStates), plus the retained clean-trip + divider-jump cases.
- Roadmap: Increment 2a of the sensor-polarity/honest-logging plan; built on the
  6b unification (ADR-0168).
