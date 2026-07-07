# ADR 0026: Host-side reward-shaping phases via a task-event layer and a credential-gated well rule

- **Status:** Accepted
- **Date:** 2026-07-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/task_events.py` (new), `host/rules_engine.py`
  (`WellRewardWithExcursionRule`, `Frame.task`, per-rule `needs_scale`/`needs_task`
  gating), `host/rules_view.py` (maze-state pipeline, phase profiles, gating),
  `host/rules.json` (profiles), `host/test_task_events.py`,
  `host/test_well_reward.py`
- **Supersedes / Superseded by:** builds on ADR-0023 (rules engine) and ADR-0025
  (maze-state module); —

## Context

The training curriculum needs the rat engaged in reward-directed behavior - and
the brain regions of interest (AC-VS-DS-HPC) active - from the very first
sessions, rather than being physically forced through the task until the cued
stage. We want a purely reward-motivated curriculum whose rules *build on each
other* like a teaching curriculum, delivered before the firmware's cue-guided
task (Phase 3) is ready:

- **Phase 1** - learn "the well = reward" and explore the whole maze: any well
  visit pays out once, but the next payout requires an excursion out to a choice
  arm (anti-camping + exploration).
- **Phase 2a** - shape unidirectional travel: the excursion only counts if the
  outbound leg used the central stem.
- **Phase 2b** - require a clean unidirectional loop (stem out + matching return
  arm) and gently balance the two sides with an anti-bias mechanism.

Two forces shaped the design. First, beam-break sensors cannot observe these
states unambiguously (a beam break does not tell you whether the rat continued or
turned back), which is exactly why ADR-0025 added a tracker-based discrete
maze-state estimator. Second, the reward the animal earns and the behavioral
statistics we later report must come from the *same* definitions of "a choice",
"a clean left loop", or "a same-side run" - if the reward rule and a stats module
each re-derive these from raw `RatState`, they can silently disagree, which is a
scientific-validity bug, not just duplication.

Phase 3 stays in firmware (real-time cue timing and neural-recording TTLs);
these shaping phases are host-side reward rules gated to Free Roam, where the
host is already the sole reward authority (ADR-0023).

## Decision

Add a thin **task-event layer** and a **credential-gated well reward template**,
and expose the phases as **named profiles** in `rules.json`.

1. **`host/task_events.py`** - a pure, headless-tested layer over the maze-state
   `RatState` stream (ADR-0025). It is the single source of truth for the
   task-semantic vocabulary, emitting a per-tick `TaskEventFrame` with edges
   (`well_arrived`, `choice_entered`, `loop_closed`), predicates (`at_well`,
   `outbound_used_stem`), and aggregates (`same_side_run`, per-side loop counts).
   It is policy-free (reports *what* happened, never decides reward) and imports
   neither consumer, so a future behavior-statistics module reads the exact same
   definitions. The same-side run counts **every** completed loop (rewarded or
   withheld) so the anti-bias pressure reflects actual behavior.

2. **`WellRewardWithExcursionRule`** (`well_reward_with_excursion` template) -
   reward at the central well is *credentialed* by an excursion selected by a
   `credential` parameter: `choice` (Phase 1), `choice_via_stem` (Phase 2a), or
   `clean_loop` (Phase 2b). The credit arms the rule; the next well arrival
   primes once (consuming the credit) and licks top up as in
   `prime_and_lick_topup`. Because the prime consumes the credit, each payout
   requires a fresh excursion (the anti-camp / build-on-each-other mechanism).
   For `clean_loop`, an optional `debias` caps the same-side run - `fixed` (N) or
   `uniform` (redraw the cap in `[min,max]` on each side switch, so the animal
   cannot learn a fixed alternation period); a clean loop past the cap is logged
   but withheld. Fail-safe: no confident position => nothing fires, and a well
   visit is only exited on a confident outside reading, so a brief track loss
   neither drops the visit nor double-primes.

3. **Frame + gating** - `Frame` carries an optional duck-typed `task`; the engine
   no longer globally requires a valid `Scale`. Each rule declares `needs_scale`
   (legacy landmark rules) and `needs_task` (zone/loop rules); the engine skips a
   rule whose capability is missing, and `rules_view` gates arming on exactly
   what the active profile needs (a valid `Scale` and/or a valid zone map +
   estimator).

4. **Profiles** - `rules.json` gains a `profiles` map + `active_profile`; the
   Rules tab shows a Phase dropdown that rebuilds the engine (and resets the
   maze-state pipeline) on switch. The phases are therefore pure configuration
   over one template, not three code paths.

## Alternatives considered

- **New firmware modes for each phase.** Rejected: the firmware only sees
  ambiguous beam breaks (ADR-0025 context), and iterating a shaping curriculum on
  the microcontroller is slow and inflexible. Host-side rules over the tracker
  are observable and editable in `rules.json`.
- **Derive "choice"/"clean loop"/"run" directly in the rule (and again in
  stats).** Rejected: two independent derivations can disagree, so the reward and
  the reported behavior would diverge. The neutral `task_events` layer gives both
  one definition.
- **Reuse `prime_and_lick_topup` with an InZone credential.** Rejected: it is
  landmark+cm-scale based and has no notion of excursions, direction, or loops;
  the credential needs the maze-state semantics.
- **Fixed anti-bias cap only.** Kept as an option, but the default is a
  `uniform` redraw so the animal cannot exploit a predictable alternation period.

## Consequences

- Phases 1/2a/2b can run today by picking a profile; no firmware change. Tuning
  (radii-free) is JSON-only.
- Reward and future statistics share `task_events` definitions, so what the
  animal earns and what we report stay consistent by construction.
- The engine's `tick` no longer hard-requires a `Scale`; correctness now relies
  on the per-rule `needs_scale`/`needs_task` flags (covered by tests).
- Phase 3 (cued task) remains firmware; a later change adds correction trials and
  the cue for the 2b->3 transition. The `clean_loop` + debias machinery is the
  behavioral substrate that transition builds on.
- Requires a per-rig `zones.json` (ADR-0025) for the task phases; arming is gated
  and clearly reports the missing zone map otherwise.

## Evidence / links

- Code: `host/task_events.py`, `host/rules_engine.py`
  (`WellRewardWithExcursionRule`), `host/rules_view.py`, `host/rules.json`.
- Tests: `host/test_task_events.py`, `host/test_well_reward.py` (Phase 1 credit /
  consumption / top-up cap; 2a stem-gating; 2b clean-loop, stem-reversal denial,
  fixed-debias withhold; lick-gated prime). Full host suite green (68 tests).
- Builds on ADR-0023 (rules engine, Free-Roam-gated executor) and ADR-0025
  (maze-state estimator, `zones.json`, `visited_since`).
