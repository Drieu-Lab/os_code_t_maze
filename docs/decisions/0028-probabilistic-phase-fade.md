# ADR 0028: Fade Phase 1 into Phase 2a with a probabilistic non-stem reward, not a hard switch

- **Status:** Accepted
- **Date:** 2026-07-08
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rules_engine.py` (`WellRewardWithExcursionRule`:
  `nonstem_reward_prob`, seeded RNG), `host/rules_view.py` (Shaping control +
  `nonstem_prob_set` logging), `host/rules.json` (phase1 `nonstem_reward_prob`),
  `host/test_well_reward.py`
- **Supersedes / Superseded by:** extends [ADR-0026](0026-reward-shaping-phases.md); —

## Context

ADR-0026 defined the shaping phases as discrete credential steps: Phase 1
(`choice`, any choice-arm excursion credits the next well reward) and Phase 2a
(`choice_via_stem`, only a stem-outbound excursion credits). Advancing a rat from
1 to 2a as a hard switch means every non-stem excursion that paid yesterday
suddenly pays nothing. On an intermittent appetitive schedule that produces an
**extinction burst** (a spike of frantic, variable non-stem attempts) and risks
disengagement / task-quitting right at a transition - the opposite of the
"purely reward-motivated, keep the ROI network engaged" goal.

Because `task_events` already reports `outbound_used_stem` per choice entry, the
two phases are in fact the two endpoints of a single continuous quantity: the
probability that a *non-stem* excursion is reinforced (a stem excursion is always
reinforced). Phase 1 is that probability = 1.0; Phase 2a is = 0.0. This invites
replacing the cliff with a gradient.

The training regime is one phase per animal advanced over days, and the
experimenter makes the promotion call (see the chat that produced this ADR), so
the fade should be a single knob the experimenter sets per session, not an
autonomous controller.

## Decision

Add a `nonstem_reward_prob` parameter (default 1.0) to the `choice` credential of
`WellRewardWithExcursionRule`. On a choice-arm entry that would earn a credit:

- if the outbound leg used the stem -> always credit (probability 1.0);
- otherwise -> credit only with probability `nonstem_reward_prob`, drawing from a
  seeded RNG (also used for the 2b debias cap, so a session is reproducible).

`nonstem_reward_prob = 1.0` is exactly Phase 1; `= 0.0` is exactly Phase 2a; a
value in between is the fade. The experimenter lowers it across days
(1.0 -> 0.0) from a "Shaping" spinbox in the Rules tab that applies live to the
active rule and logs a `nonstem_prob_set` rule-event; each armed/denied non-stem
decision logs the `p` and the `roll` used, so the schedule and outcomes are
recoverable. Stem excursions are unaffected throughout, so the target behavior
stays on continuous reinforcement.

Guidance recorded with the decision: **fade all the way to 0** - do not plateau
at a small non-zero `p`. Intermittently reinforced responses resist extinction
(the partial-reinforcement extinction effect), so a non-zero floor would keep the
non-stem shortcut alive indefinitely; taking it to 0 (while the stem path stays
continuously reinforced) is what lets the stem behavior win by contrast.

## Alternatives considered

- **Hard P1 -> P2a switch (ADR-0026 as written).** Rejected as the default path
  because of the extinction burst / disengagement risk at the transition; still
  available (just set `nonstem_reward_prob` to 0 directly, or select the
  `phase2a` profile).
- **Automatic within-session decay** (step `p` down per rewarded loop / per
  minute). Rejected for now: a fixed clock can outrun or lag the individual rat's
  learning; the manual per-session knob keeps the human in the loop and is
  simplest.
- **Performance-adaptive `p`** (track the rat's own stem preference). Attractive
  and self-paced but more to tune/validate and can stall or oscillate; deferred.
- **A context cue to mark the phase change.** Rejected (see the design
  discussion): with one phase per animal over days there is no context contrast
  for the animal to learn from, and an added sensory variable - especially an
  auditory one - risks confounding the AC cue channel; the smooth fade addresses
  the same disengagement worry without a new cue.

## Consequences

- The P1 -> 2a transition is now a smooth, experimenter-paced dial rather than a
  cliff, reducing extinction bursts while preserving the RPE-driven discovery the
  VS/DS signals depend on.
- Phases 1 and 2a are unified as endpoints of one parameter; the `phase2a`
  profile remains as an explicit, self-documenting endpoint (equivalent to
  `choice` with `p = 0`).
- The same fade idea generalizes to 2a -> 2b later (fade the probability that a
  stem-out-but-not-clean loop pays); not implemented here.
- Reproducibility depends on the seed; an unseeded rule is nondeterministic
  (fine for the rig, but tests seed it).
- The fade only applies to the `choice` credential; the Shaping control disables
  itself for `choice_via_stem` / `clean_loop` profiles.

## Evidence / links

- Code: `host/rules_engine.py` (`WellRewardWithExcursionRule.nonstem_reward_prob`,
  `_earn_credential` choice branch), `host/rules_view.py`
  (`_apply_nonstem_prob` / Shaping control), `host/rules.json` (phase1).
- Tests: `host/test_well_reward.py::TestNonStemFade` (p=1 credits non-stem; p=0
  denies non-stem but stem still credits; intermediate p matches the arm rate
  under a fixed seed; stem always credits). Full host suite green.
- Behavioral basis: extinction burst on abrupt removal; partial-reinforcement
  extinction effect (why fade to 0); matching-law preference shift as relative
  reinforcement changes. Builds on ADR-0026.
