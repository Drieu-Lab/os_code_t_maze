# ADR 0030: Deliver the Phase 2a/2b shaping prime at choice-zone entry, and redefine 2b as stem-gated choice + choice-side anti-bias

- **Status:** Accepted
- **Date:** 2026-07-08
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rules_engine.py` (`WellRewardWithExcursionRule`: new
  `prime_on` param + `prime_on_arrival` alias, credential-time prime,
  `choice_via_stem` debias), `host/task_events.py` (`same_side_choice_run`
  aggregate + `TaskEventFrame` field), `host/rules.json` (phase1/2a/2b profiles +
  `_schema`), `host/test_well_reward.py`, `host/test_task_events.py`
- **Supersedes / Superseded by:** extends [ADR-0026](0026-reward-shaping-phases.md);
  relates to [ADR-0028](0028-probabilistic-phase-fade.md); —

## Context

In the full cue-guided task the reward valve fires at the central port the moment
the animal makes a correct choice (`REWARD,CENTER`, see
[firmware/PROTOCOL.md](../../firmware/PROTOCOL.md) and ADR-0007); the solenoid
click is, in effect, the correct-choice signal. The Phase 2a/2b shaping rules
(ADR-0026) instead only *armed* on the choice-arm excursion and deferred the
prime until the animal returned to the well. That trains a weaker, later
association (reward tied to the well arrival, not to the choice) than the full
task uses, so a rat shaped this way still has to re-learn "choice -> reward click"
at the Phase 3 transition.

Separately, Phase 2b as first defined (`clean_loop`: stem out + matching return
arm, credited at loop closure) is incompatible with priming at the choice zone:
whether a lap is "clean" is only known when it closes back at the hub, *after* the
animal has already left the choice arm, so there is nothing to fire at choice
entry. The experimenter wants 2b to keep an anti-bias pressure (don't over-run one
side) but to reward at the choice like 2a.

Finally, the training goal is to fit more trials into a session before satiation;
a smaller per-trial volume in the later phase helps.

## Decision

1. **`prime_on` timing parameter** on `WellRewardWithExcursionRule`, replacing the
   `prime_on_arrival` boolean (kept as a back-compat alias: `True`->`well_arrival`,
   `False`->`well_lick`):
   - `well_arrival` (default; Phase 1) - prime on reaching the well while armed;
   - `well_lick` - lick-gated prime at the well;
   - `credential` - fire the prime at the central spout (`port` 2) the instant the
     credential arms, out at the choice zone, then open the lick-top-up window when
     the animal later reaches the well (no second prime). This makes the
     central-spout solenoid the correct-choice signal, matching the full task.

2. **Phase 2b redefinition** (config only): 2b becomes the `choice_via_stem`
   credential (like 2a) plus a **choice-side** `debias` cap, rewarded at choice
   entry, with **zero top-ups**; the `clean_loop` credential remains available but
   is no longer the shipped 2b. `debias` is therefore extended from `clean_loop`
   to also apply to `choice_via_stem`.

3. **`same_side_choice_run`** aggregate added to `task_events.py` (mirroring
   `same_side_run` for loops): consecutive same-side **stem-gated** choice entries,
   reset on a side switch. A non-stem (denied) entry is not a valid trial and does
   not reset the run. Keeping this in the task-event layer preserves ADR-0026's
   single-source-of-truth (reward and any future stats read one definition).

4. Shipped profiles: **2a** = `choice_via_stem`, `prime_on: credential`,
   `max_topups: 2`; **2b** = `choice_via_stem`, `prime_on: credential`,
   `max_topups: 0`, `debias: {uniform, 3, 5}`. Phase 1 normalized to
   `prime_on: well_arrival`.

The prime stays at the central spout (`port` 2), not the entered arm: this matches
the full task's central-port reward and needs no per-side port logic.

## Alternatives considered

- **Keep priming at the well (ADR-0026 as written).** Rejected for 2a/2b: it
  trains the reward-click association to the well arrival, not the choice, so it
  does not transfer to the Phase 3 cued task; still the Phase 1 default.
- **Reward at the entered side's own spout (port 0/1).** Rejected: the full task
  delivers at the central port, so a side spout would train the wrong collection
  location and add per-side port logic for no behavioral gain.
- **Keep 2b as `clean_loop` and prime at loop closure.** Rejected: cleanliness is
  only known at the hub after the choice arm is left, so it cannot prime at choice
  entry; the clean-loop credential is retained in code as an option.
- **A full Trigger/Dispenser decomposition of the rule first.** Deferred
  ([FW-0009](../future-work.md)): with the legacy `prime_and_lick_topup` rule
  staying dormant, the framework would have a single real caller, which
  CONTRIBUTING #6 ("no abstraction until 2-3 real callers") advises against. The
  credential logic is already its own method, so an in-place change suffices.
- **Compute the choice-side run inside the rule.** Rejected: re-deriving a
  behavioral definition in the reward rule (and again in future stats) is the
  divergence ADR-0026's task-event layer exists to prevent.

## Consequences

- Phases 2a/2b now reinforce the choice with the central-spout click the moment
  the animal enters a choice arm, so the reward-click association transfers to the
  Phase 3 cued task; top-ups still happen at the well (2a) or are off (2b).
- 2b keeps an anti-bias pressure but over stem-gated *choices* rather than closed
  loops; the run counts every stem-gated entry (honored or withheld) so the
  pressure reflects actual behavior. The `uniform` cap redraws on each side switch
  so the animal cannot learn a fixed alternation period.
- 2b's 0 top-ups shrink per-trial volume for more trials before satiation; tune
  `pulse_ms` if the single prime is too small.
- `prime_on_arrival` in older configs still works (alias); new configs use
  `prime_on`.
- The full Trigger/Dispenser refactor is deferred (FW-0009), to be revisited when
  the Phase-3 firmware bridge (FW-0008) adds more rule variants.

## Evidence / links

- Code: `host/rules_engine.py` (`WellRewardWithExcursionRule.prime_on`,
  `_earn_credential` `choice_via_stem` debias, `tick` credential-time prime),
  `host/task_events.py` (`same_side_choice_run`), `host/rules.json`.
- Tests: `host/test_well_reward.py::TestPrimeOnCredential` (prime at choice entry,
  not at the well; top-ups at the well capped at 2; non-stem denied) and
  `::TestPhase2bChoiceBias` (0 top-ups; choice-side `debias_withhold` past the cap;
  side switch resets the run and re-rewards); `host/test_task_events.py::
  TestSameSideChoiceRun`. Full host suite green (104 tests, 2 OpenCV skips).
- Builds on ADR-0026 (task-event layer + credential-gated well rule), ADR-0028
  (the P1->2a fade, unaffected: it applies to the `choice` credential), ADR-0007
  (central-port reward). Refs FW-0008, FW-0009.
