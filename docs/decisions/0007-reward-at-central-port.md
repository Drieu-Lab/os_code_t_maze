# ADR 0007: Deliver reward at the central port (v1 cue-guided task)

- **Status:** Accepted
- **Date:** 2026-06-03
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/task_core.h`, `firmware/common/rig_config.h`, `firmware/PROTOCOL.md`, `firmware/cue-guided-task/test/test_task_core.cpp`
- **Supersedes / Superseded by:** —

## Context

The behavioral design for the first version of the cue-guided task is:

> tone cue -> rat runs the central stem -> enters a choice arm (left/right) ->
> returns via the return arm -> collects reward at the **central port**.

In v1 only the central port dispenses reward; the left/right lick ports have no
task role (they are recorded for monitoring only). This may change in a later
task variant.

The pre-existing `task_core.h` did **not** match this design: it pulsed the
*chosen left/right arm valve* at the moment of a correct choice
(`openValve(arm)` in `AwaitChoice`), and only optionally pulsed the central
("START") valve on return when `startRewardEnabled` was set — unconditionally,
i.e. not contingent on a correct choice. There was also a naming mismatch: the
central port is called both "start" (`V_START`, `PIN_VALVE_START`,
`START_REWARD_ENABLED`) and, behaviorally, the "reward" port.

Reward *location* and *contingency* are scientific decisions (per this folder's
README), so they warrant an ADR rather than a silent diff.

## Decision

For v1, reward is delivered at the **central port** (`V_REWARD`, an alias of
`V_START` = `PIN_VALVE_START`), **contingent on a correct choice**, triggered at
the **choice-arm beam** (`B_LCHOICE`/`B_RCHOICE`). The reward is dispensed to the
central spout the moment a correct choice is registered; the animal collects it
at center on its way back.

- `AwaitChoice` records `CHOICE`/`OUTCOME` and pulses the central valve
  (`openValve(V_REWARD)`, `REWARD,CENTER`) iff the choice was correct.
- `AwaitReturn` only logs `RETURN_ARM` and advances the trial; it no longer
  dispenses reward.
- `START_REWARD_ENABLED` is repurposed from "optional unconditional start
  reward" to "**also** reward the central port on *incorrect* trials" (a
  free/shaping reward); default `false` = correct-only.
- The `REWARD` serial payload token changes from `LEFT|RIGHT|START` to `CENTER`.

Geometric + role labels for the lick ports were added to `rig_config.h`
(`PIN_LICK_LEFT/RIGHT/CENTER`, `PIN_LICK_REWARD = PIN_LICK_CENTER`), parallel to
the existing beam labels. The physical pin<->port mapping is still unverified
(`CONFIRM`); it follows the working assumption in `host/rig_view.py`
(center == pin 37).

## Alternatives considered

- **Keep reward at the chosen left/right arm (original code).** — Rejected: does
  not match the v1 behavioral design (reward is collected at center on return).
- **Trigger reward at the return-arm beam instead of the choice-arm beam.** —
  Rejected: dispensing at the choice-arm beam arms the central spout as soon as
  the correct choice is committed, so the reward is already waiting when the rat
  reaches center; it also gives a single, unambiguous trigger per trial.
- **Gate the central reward on the actual central *lick* event.** — Deferred:
  more faithful to "reward at the central lick port", but the functional core
  deliberately keeps licks out of the decision logic (monitor-only). Triggering
  on a beam needs no architectural change. Revisit if the animal must lick
  before reward is armed (would require feeding `PIN_LICK_REWARD` into `Inputs`
  and adding an `AwaitReward` phase).
- **Reward unconditionally on every completed trial.** — Rejected: the tone cue
  would carry no consequence, defeating the task. `START_REWARD_ENABLED` keeps
  this available only as an explicit shaping aid.

## Consequences

- v1 matches the intended trajectory; data shows `CHOICE`/`OUTCOME` and (if
  earned) `REWARD,CENTER` together at the choice-arm beam, with `RETURN_ARM` at
  return.
- Reward fires on the choice-arm *beam* edge, not on a verified central lick; if
  precise "lick-then-reward" timing is needed, see the deferred alternative.
- The central port now has one consistent identity ("start" == "center" == v1
  reward port); the `V_START`/`START_REWARD_ENABLED` names are retained to avoid
  churn but documented as the central reward port.
- Host tools that parsed `REWARD,LEFT|RIGHT|START` must accept `REWARD,CENTER`
  (in practice `START` was never emitted, since `START_REWARD_ENABLED` defaulted
  off, and no in-repo tool dispatches on the `REWARD` detail).
- Physical lick pin<->port mapping remains `CONFIRM`; verify on the rig with
  `tmaze_test` before running an animal.

## Evidence / links

- Base commit: `51b68a4` (functional-core refactor; pin mapping to be confirmed).
- Code: `firmware/cue-guided-task/task_core.h` — `AwaitChoice` dispenses the
  central reward (`openValve(V_REWARD)`, `correct || startRewardEnabled`);
  `AwaitReturn` only logs `RETURN_ARM` and advances; `V_REWARD` alias.
- Config: `firmware/common/rig_config.h` — lick geometric/role block,
  `PIN_VALVE_START` and `START_REWARD_ENABLED` comments.
- Protocol: `firmware/PROTOCOL.md` — `REWARD,CENTER`.
- Tests: `firmware/cue-guided-task/test/test_task_core.cpp` —
  `test_full_correct_trial`, `test_incorrect_no_reward`,
  `test_shaping_rewards_incorrect_at_center`.
