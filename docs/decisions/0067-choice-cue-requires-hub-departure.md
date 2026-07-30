# ADR 0067: Gate the choice cue on a hub-departure credential

- **Status:** Accepted
- **Date:** 2026-07-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/task_events.py` (new `TaskEventFrame.left_hub_via_stem`
  predicate), `host/cue_gate.py` (`CueGate.step` gains an `on_outbound_leg` arg),
  `host/phase3_controller.py` (passes the credential),
  `host/maze_state_validate.py` (the offline replay now folds the task-event
  layer), `host/test_cue_gate.py`, `host/test_task_events.py`,
  `host/test_behavior_stats.py` (synthetic-frame helper),
  `host/tooltip_text.py` (`phase3.choice`, `phase3.up_stem` tips)
- **Supersedes / Superseded by:** — (narrows ADR-0045, mirrors ADR-0061)

## Context

Running Phase 3 on the rig, the central-stem CHOICE tone played while the animal
ran the lap **backwards**: out of the hub along a return arm, round through a
choice arm, and home down the stem. The trial tone sounded for that whole
descent, so the cue that is supposed to mark "a trial is starting, choose now"
was also marking the animal's way home.

The cue's window was purely positional (`cue_gate.py`): the frame is valid, the
zone is `CENTRAL_STEM`, and — only when `require_up_stem` is on — the direction
is `up_stem`. **The rig is operated with `require_up_stem` off**, because the
direction estimate comes from a three-frame projection with a 0.01 deadband and
chops the tone during normal pauses and head movements. With that switch off the
window collapses to "the animal is in the stem", so nothing at all constrained
how the animal got there. Unlike the RETURN cue, the CHOICE cue also has no
per-visit latch (it deliberately fails open, ADR-0045), so it re-fires on every
frame in the window.

ADR-0061 had already fixed the structurally identical bug on the other side: the
return cue fired when a run *started* up a return arm, and the fix was to pass in
`task_events.outbound_used_stem` — the same predicate that gates a clean loop and
the reward — rather than invent a latch inside the gate. Its reasoning applies
here verbatim: the cues and the reward are phases of one task sequence and should
be gated off one shared vocabulary.

Two further facts shaped the choice of predicate:

- `outbound_used_stem` cannot serve the choice cue. It means "the stem has been
  visited since the animal last left the hub", and the choice cue's own window
  **is** the stem, so it is already true on the backwards lap by the time the
  animal is standing there. It works for the return cue only because a return arm
  is a different zone from the stem.
- The animal must still hear the tone if it commits a choice and then reverses
  back into the stem (an explicit requirement from Celine, 2026-07-30). That
  rules out any credential consumed at the choice-arm entry — including the
  existing `_excursion_open` latch that `choice_committed` spends.

## Decision

Introduce a second shared credential describing **how the trip began**, and gate
the choice cue on it.

1. `task_events.py` publishes `TaskEventFrame.left_hub_via_stem`: this trip
   departed the hub up the central stem. It is a small latch beside the
   choice-commit one — armed on the `HUB -> CENTRAL_STEM` transition (read off
   the estimator's existing `entered` / `exited` fields) and cleared on HUB
   *presence*, not on the hub-entry edge, so a dropped edge cannot strand a stale
   credential (the robustness argument of ADR-0038). It starts False, so a
   session that begins with the animal already in the maze is silent until it has
   been home once.

2. `CueGate.step` takes `on_outbound_leg` and folds it into the choice window,
   **outside** the `require_up_stem` clause so it applies in the configuration
   the rig actually uses:

   ```python
   in_choice = (zone == CENTRAL_STEM
                and on_outbound_leg
                and (not p.require_up_stem or direction == UP_STEM))
   ```

   It is hard-wired with no `CueParams` toggle, exactly as ADR-0061 decided for
   the return credential: there is no bring-up case where a trial tone on a
   backwards lap is wanted, and with the direction gate off this credential is
   the *only* thing separating an outbound trial leg from the way home.

3. The live path (`phase3_controller._process_frame`) passes
   `task_frame.left_hub_via_stem`; the offline replay
   (`maze_state_validate.cue_onset_events`) now folds a real `TaskEvents` beside
   the estimator and feeds both credentials from the same frame, so it mirrors
   the rig by construction rather than by re-deriving the queries.

The two credentials now carve up the lap: RETURN needs a stem-out excursion
(ADR-0061), CHOICE needs a stem departure.

### Behaviour deliberately kept

Both of the following are intended. A future reader should not "fix" them:

- **The tone replays on a post-choice reversal.** The credential is held for the
  whole trip and cleared only at the hub, so an animal that commits its choice
  and then backs into the stem hears that trial's tone again — after the choice
  has already been scored (the trial index does not advance until the lap closes
  at the hub). The cue is a function of position within the trip, so a
  choice-point vacillation should hear it.
- **The tone does not follow direction within a trip.** With `require_up_stem`
  off, a partial retreat down the stem toward the hub keeps the tone playing.
  That is a property of running with the direction gate off, not of this change,
  and it is pinned by a test so it stays a decision rather than an accident.

## Alternatives considered

- **"No return arm visited yet this trip"** — `not (visited_since(LEFT_RETURN,
  HUB) or visited_since(RIGHT_RETURN, HUB))`. Attractive: stateless, no edge to
  miss, the exact mirror of the return credential, and the offline replay would
  have stayed a one-line query. It agrees with the chosen rule on every observed
  trajectory and differs on one: out via the stem, into a choice arm, into a
  return arm, then doubling back into the choice arm and down the stem. It would
  go silent there; the chosen rule keeps the tone, on the view that a trip
  remains a trial until the animal actually gets home. Chosen against per
  Celine's call on the semantics, not on the mechanics.
- **Reuse the existing `_excursion_open` latch** (consumed by the first
  choice-arm entry, ADR-0055). Rejected: it silences exactly the post-choice
  reversal that has to keep playing.
- **A bespoke latch inside `CueGate`** tracking where the animal came from.
  Rejected for the reason ADR-0061 gives by name: it re-derives, in a second
  place, a notion of the trip that `task_events` already owns, so the cue and the
  reward could drift apart.
- **Re-enabling `require_up_stem`** instead of adding a credential. Rejected: it
  does not fix the bug (a backwards lap still produces up-stem frames when the
  animal turns), and the direction estimate is too noisy to gate a tone on, which
  is why it is switched off in the first place.

## Consequences

- The choice tone now marks only trips that set off up the stem. A backwards lap
  is silent crossing the stem on its way home.
- Both cues and the reward share one task-phase vocabulary, so they cannot
  disagree about what a trip is.
- `task_events` gains a second per-trip latch, so it is slightly less "a direct
  read of the current RatState" than it was. The alternative above would have
  avoided that; the trade was made knowingly.
- `maze_state_validate.cue_onset_events` now folds the task-event layer, which is
  a small extra cost offline and a fidelity gain: the replay exercises the same
  three stages as the actuation thread.
- Adding a required field to `TaskEventFrame` broke one synthetic-frame helper in
  `test_behavior_stats.py` (updated). Consumers that build frames by hand must
  set the new predicate.
- `phase3_controller` still derives the same HUB-to-stem edge privately, with
  `_prev_zone`, to stamp `trial_started` in the session data file. Folding that
  into the shared predicate would change what a logged trial start means, so it
  is left for FW-0027.
- If a future protocol makes a non-stem departure a legitimate trial start, this
  credential and `outbound_used_stem` should be revisited together — they flow to
  the cues, the reward, and the stats.

## Evidence / links

- ADR-0045 (the cue as a zone-membership gate — narrowed here), ADR-0061 (the
  return cue's credential, whose reasoning this follows), ADR-0026 / ADR-0055
  (`task_events` as the single task-phase source of truth), ADR-0038 (clear on
  presence, not on an edge), ADR-0046 / ADR-0047 (host-owned Phase 3).
- `host/test_cue_gate.py` — `TestChoiceCueOutboundLeg`, written with
  `require_up_stem=False` to match the rig:
  `test_backwards_lap_is_silent_through_the_stem`,
  `test_reversal_after_the_choice_still_plays`,
  `test_retreat_down_the_stem_still_plays`,
  `test_next_hub_departure_fires_normally`.
- `host/test_task_events.py` — `TestLeftHubViaStem`, including
  `test_backwards_lap_never_arms_it`, which asserts the two stem predicates
  genuinely differ on the descent.
- `host/maze_state_validate.py --selftest` — the synthetic left lap departs up
  the stem, so both cue onsets still match their beams (choice +110 ms at ENTRY,
  return +60 ms at LRETURN), 0 unmatched. Full host suite green (384 tests).
- Follow-up: FW-0027 (one trigger/effect framework across Phases 1, 2a and 3).
