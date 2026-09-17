# ADR 0168: Define "what counts as a trial trip" once, as `trial_trip`

- **Status:** Accepted
- **Date:** 2026-09-17
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/task_events.py`, `host/tmaze/core/phase3/phase3_task.py`,
  `host/tmaze/core/cue_gate.py`, `host/tmaze/core/reward/reward_dispenser.py`
  (`RewardTrigger`); tests `test_task_events.py`, `test_phase3_task.py`,
  `test_reward_dispenser.py`, `test_behavior_stats.py`
- **Supersedes / Superseded by:** — (extends ADR-0099; builds on ADR-0067)

## Context

Three task consumers each ask the same scientific question - "was this a genuine
trial excursion?" - to decide whether to act:

- the **Phase 3 scorer** (`phase3_task.py`) - whether a choice-commit is scored,
- the **cue gate** (`cue_gate.py`) - whether the CHOICE/RETURN tone fires,
- the **reward** (`reward_dispenser.py` `RewardTrigger`, `choice_via_stem`
  credential) - whether a choice-arm entry earns a well payout.

ADR-0099 already unified the first two on the hub-departure credential
`task_events.left_hub_via_stem` (this trip stepped from the HUB up the stem - a
real trial start), replacing the looser `outbound_used_stem` (the stem was
visited AT ALL since the hub, which is also true when the rat set off up a side
arm and hopped into the stem over the divider). But the reward's `choice_via_stem`
was still gating on the looser `outbound_used_stem`: a divider-jump trip that the
scorer and cue correctly ignored could still earn reward. The definition lived in
three places and had already drifted in one of them.

There is also a forthcoming tightening (Increment 2a) that will additionally
require the outbound leg to have not been broken by an early return-arm entry. If
each consumer keeps its own copy of the credential, that rule has to be applied
identically in three spots - the exact kind of divergence that makes the reward
the animal earns and the trial the analysis scores disagree (a scientific-validity
bug, not just duplication).

## Decision

Introduce ONE credential, `TaskEventFrame.trial_trip: bool`, computed in
`task_events.py`, and have all three consumers read it:

- Phase 3 scorer gates on `frame.trial_trip` (was `left_hub_via_stem`).
- Cue gate is fed `trial_trip` as its `on_outbound_leg` argument (the controller
  wiring change; the gate's parameter name is unchanged).
- `RewardTrigger.choice_via_stem` gates on `task.trial_trip` (was
  `task.outbound_used_stem`).

For now `trial_trip == left_hub_via_stem`. Increment 2a will AND-in "the outbound
leg was not broken" in this ONE place, and all three consumers inherit the tighter
rule automatically.

`outbound_used_stem` is retained as a purely DESCRIPTIVE field (the `choice`
credential's stem-vs-nonstem reward fade still reads it - a different question),
and `left_hub_via_stem` / `departed_hub_via_stem` stay as the underlying departure
level and its rising edge (the Phase 3 trial-start stamp). The rule-file credential
NAME `choice_via_stem` is unchanged, so existing `rules.json` files keep working
(it is now an alias for the shared credential).

## Alternatives considered

- **Leave the three definitions as-is** — rejected: they had already drifted
  (reward still on the loose credential after ADR-0099 tightened the other two),
  and 2a would have to touch three call sites in lockstep.
- **Rename `left_hub_via_stem` -> `trial_trip` everywhere** — rejected: the raw
  hub-departure LEVEL and its rising edge (`departed_hub_via_stem`, the trial-start
  stamp) are still needed as distinct facts; the credential is a derived concept
  layered on top, so a new field keeps the two ideas separate for 2a.
- **Rename the cue gate's `on_outbound_leg` parameter to `trial_trip`** —
  rejected for now: it ripples through many `test_cue_gate` calls for no behaviour
  gain; the gate's local role name is fine, and the SOURCE is the single
  `trial_trip` field it is fed.
- **Keep `choice_via_stem` on `outbound_used_stem`** — rejected: that is the exact
  divider-jump leak ADR-0099 closed for the cue and score; a trip the rig does not
  score or cue should not pay reward either.

## Consequences

- One definition of "a trial trip". 2a's tightening is a one-line change in
  `task_events.py` that the scorer, cue, and reward all pick up.
- BEHAVIOUR CHANGE (deliberate, low blast radius): the Phase 1-2 rules-engine
  `choice_via_stem` credential now denies a divider-jump entry (stem visited but
  not departed-from) that it used to credit. The current cohort runs Phase 3
  (host-owned scorer + `WellDispenser`, not `RewardTrigger`), so no live session
  changes today; early-shaping `rules.json` runs get the stricter,
  cue/score-consistent credential. Reversible by reading `outbound_used_stem`
  again if a future shaping stage wants the looser rule.
- `trial_trip == left_hub_via_stem` today, so the Phase 3 scorer and cue gate are
  behaviour-preserving; `test_task_events` pins the equivalence so 2a's divergence
  will show up there.

## Evidence / links

- Extends ADR-0099 (hub-departure credential for cue + score) and ADR-0067
  (`left_hub_via_stem` vs `outbound_used_stem`).
- New/updated tests: `test_task_events.TestTrialTrip` (mirrors `left_hub_via_stem`
  over a full trip and diverges from `outbound_used_stem` on the divider-jump),
  `test_phase3_task` / `test_reward_dispenser` doubles fed `trial_trip`.
- Roadmap: Increment 6b of the sensor-polarity/honest-logging plan; unblocks the
  Increment 2a route-breaking rule.
