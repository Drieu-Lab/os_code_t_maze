# ADR 0055: Promote a single once-per-excursion choice-commit edge into the task-event layer

- **Status:** Accepted
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/task_events.py` (`TaskEventFrame.choice_committed` + `trip_id`,
  the shared latch in `TaskEvents.update`), `host/rules_engine.py`
  (`WellRewardWithExcursionRule`: the private `_excursion_open` latch removed, the
  `tick` gate now reads `task.choice_committed`), `host/phase3_task.py`
  (`Phase3Task`: scores off `frame.choice_committed`, `_scored_this_trial`
  removed), `host/behavior_stats.py` (`_accumulate_choice` reads
  `frame.choice_committed`, its `_excursion_open` removed), and the three test
  suites (`test_task_events.py`, `test_well_reward.py`, `test_phase3_task.py`,
  `test_behavior_stats.py`)
- **Supersedes / Superseded by:** extends [ADR-0026](0026-reward-shaping-phases.md)
  (the task-event layer) and [ADR-0038](0038-once-per-excursion-well-credit.md)
  (the per-excursion latch, now hoisted here as the single source); relates to
  [ADR-0025](0025-maze-state-module.md) (the maze-state stream both consume),
  [ADR-0032](0032-live-behavior-readout.md) (the readout's per-excursion counting)
  and [ADR-0046](0046-host-owned-phase3.md) (host-owned Phase 3 scoring); —

## Context

Three host consumers independently answered the same behavioral question -
*"has the rat just committed an outbound left/right choice, once per trip?"* -
each with its own hand-rolled latch over the same maze-state / `task_events`
stream:

- The **rules engine** (`WellRewardWithExcursionRule`, `choice`/`choice_via_stem`
  credentials) kept a `_excursion_open` boolean, re-armed on HUB presence and
  consumed on the first choice-arm entry of the trip (ADR-0038, added to fix the
  figure-8 double-reward bug `#260710`).
- **Phase 3** (`Phase3Task`) guarded scoring with `_scored_this_trial`, re-armed
  on the `loop_closed` edge (which also advances the trial index).
- The **behavior readout** (`behavior_stats.py`) kept a *third* copy of the same
  `_excursion_open` latch to count one choice per excursion (ADR-0032).

ADR-0038 itself flagged this as "mild duplication" and named the consolidation as
a follow-up (FW-0020). The three latches agree on the normal up-the-stem path but
had drifted in their re-arm rule (HUB-presence vs the `loop_closed` edge) and in
whether a non-stem first entry commits - exactly the silent behavioral divergence
the comprehension-debt guardrails (ADR-0029) warn about.

## Decision

Promote a single **once-per-outbound-excursion choice-commit edge** into the
neutral task-event layer and have all three consumers read it instead of
re-deriving the latch.

- `TaskEventFrame` gains `choice_committed: Optional[str]` (the side of the FIRST
  choice-arm entry since the animal last left the HUB, true only on that tick)
  and `trip_id: int` (bumped per commit so consumers can tell excursions apart).
- The latch in `TaskEvents.update` **re-arms on HUB presence** (`s.valid and
  s.zone == HUB`, every tick - robust to a dropped `loop_closed` edge, per
  ADR-0038's rationale) and is **consumed on the first choice entry regardless of
  route**. The stem gate is deliberately *not* applied here; a consumer that
  cares reads `outbound_used_stem` on the same frame.
- **Rules:** the choice-family credentials gate `_earn_credential` on
  `task.choice_committed is not None`. On the commit tick `task.choice_entered`
  equals the committed side, so `_earn_credential` (and the stem/`credit_denied`
  logic inside it) is unchanged - the rules reward path is byte-identical to
  ADR-0038 on the shipped profiles.
- **Phase 3:** scores off `frame.choice_committed` (still gated by
  `outbound_used_stem`), keeps `_awaiting_return` (blocks a second score before
  the return lap; advancement still on `loop_closed`), and drops the now-redundant
  `_scored_this_trial`.
- **Behavior readout:** counts off `frame.choice_committed`, dropping its own
  `_excursion_open`.

This is "Option A" of the two design forks the plan surfaced: **(1)** the edge
fires on the first choice entry *regardless of stem* (chosen to keep the
safety-critical reward path unchanged; the stem gate stays a per-consumer check),
and **(2)** it re-arms on *HUB presence* (chosen for ADR-0038's dropped-edge
robustness).

## Alternatives considered

- **Make the edge itself stem-gated** (fire only on the first *stem-gated* choice
  entry). Rejected: it would change the rules reward path in the non-stem-first
  edge case (it would no longer deny + burn the trip there), i.e. alter the
  more safety-critical, water-dispensing consumer. Option A instead changes only
  Phase 3's handling of that rare case (see Consequences).
- **Re-arm on the `loop_closed` edge** (Phase 3's original design). Rejected for
  the shared edge: a single dropped edge (a brief track loss at hub entry) would
  strand the latch and silently withhold credit for the rest of a session - the
  exact failure ADR-0038 avoided. HUB-presence is a level check that cannot get
  stranded.
- **Leave `behavior_stats` on its own latch** (scope FW-0020 to rules + Phase 3
  only). Rejected: its latch is byte-identical to the shared edge, and folding it
  in is what makes this a true single source rather than moving duplication.
- **Hoist a full "first stem-gated choice per trip" primitive.** Rejected as
  over-fitting: the stem gate differs per consumer (rules logs `credit_denied` on
  non-stem; the readout counts non-stem separately), so the route-agnostic edge +
  a per-consumer `outbound_used_stem` read is the smaller, faithful contract.

## Consequences

- One definition of "committed an outbound choice" now lives in `task_events.py`;
  the reward the animal earns, the trial Phase 3 scores, and the trials the
  readout counts cannot drift by construction.
- **One deliberate behavioral change, confined to a rare path:** if the FIRST
  choice entry of a trip is *non-stem* and a *stem* entry follows in the SAME trip
  (no HUB between), Phase 3 previously scored the later stem entry; now the trip's
  single commit is already spent, so that trip yields no score. The **sequence
  trial is preserved, not burnt** - the trial index only advances after a real
  score, so the next genuine stem trip scores that same trial. This "backward"
  approach is the invalid route the shaping discourages, so treating it as an
  unscored trip is arguably more correct; it never affects the normal up-the-stem
  path. The rules and readout consumers are unchanged in this case.
- Phase 3's trial *counter* keeps its pre-existing (self-correcting) dependence on
  the `loop_closed` edge; hardening that is explicitly out of scope.
- No firmware / protocol / on-wire change; no controller change (both task
  controllers already feed `task_events` frames to their pure cores).

## Evidence / links

- Code: `host/task_events.py` (`choice_committed`, `trip_id`, `_excursion_open`,
  `_trip_id`), `host/rules_engine.py` (`WellRewardWithExcursionRule.tick` gate),
  `host/phase3_task.py` (`Phase3Task.update`), `host/behavior_stats.py`
  (`_accumulate_choice`).
- Tests: `test_task_events.py::TestChoiceCommitted` (fires once per trip
  regardless of route, re-arms at HUB, figure-8 → one commit, `trip_id` bumps);
  `test_well_reward.py::TestNonStemFirstEntryBurnsTrip` (pins the unchanged rules
  behavior); `test_phase3_task.py::TestPhase3Integration` (end-to-end
  estimator → task_events → Phase3Task, incl.
  `test_nonstem_first_entry_preserves_the_trial`); `test_behavior_stats.py`
  (per-excursion counting off the shared edge). Full host suite green.
- Builds on ADR-0026 (task-event layer + credentialed well rule), ADR-0038 (the
  per-excursion latch this replaces), ADR-0032 (readout counting), ADR-0046
  (Phase 3 scoring). Implements FW-0020.
