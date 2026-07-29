# ADR 0061: Gate the return cue on the shared outbound-excursion credential

- **Status:** Accepted
- **Date:** 2026-07-27
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/cue_gate.py` (`CueGate.step` gains an `outbound_used_stem`
  arg), `host/phase3_controller.py` (passes the task-event credential),
  `host/maze_state_validate.py` (offline replay passes the same query),
  `host/test_cue_gate.py`, `host/tooltip_text.py` (`phase3.return` tip)
- **Supersedes / Superseded by:** — (narrows ADR-0045)

## Context

The Phase 3 return cue (ADR-0045) fired on the FIRST entry into a return zone
(`LEFT_RETURN` / `RIGHT_RETURN`), one-shot per visit. The maze is a figure-8 in
which the hub is directly adjacent to both return arms (`ADJACENCY` in
`maze_state.py`: `HUB: (CENTRAL_STEM, LEFT_RETURN, RIGHT_RETURN)`). A rat that
begins a run by heading up a return arm from the hub therefore tripped the
return cue even though it had not made an outward excursion — the cue is meant
to mark the INBOUND leg of a lap, not an outbound run that happens to start on a
return arm.

The task layer already has one canonical definition of "the outbound leg was a
real stem run": `task_events.outbound_used_stem == visited_since(CENTRAL_STEM,
HUB)`. This same predicate gates a `loop_clean` lap and, through it, the reward
(`task_events.py`), and the choice-commit edge is documented as route-agnostic
with "a consumer applies its own `outbound_used_stem` check". The choice cue is
analogously gated by a maze-state semantic (`direction == up_stem`). So the cues
and the reward are all different phases of one task sequence that should be
gated off the SAME shared vocabulary, not per-cue re-derived logic.

## Decision

Require the shared `outbound_used_stem` credential for the return cue's onset.
`CueGate.step` takes a new `outbound_used_stem: bool` argument; the RETURN
one-shot fires only when it is True. The live path
(`phase3_controller._process_frame`) passes `task_frame.outbound_used_stem`
(already computed each tick before the gate step); the offline replay
(`maze_state_validate.cue_onset_events`) passes the identical canonical query
`RatState.visited_since(CENTRAL_STEM, HUB)`.

The gate is HARD-WIRED (no `CueParams` toggle): a return cue without a stem-out
excursion is never scientifically wanted. It is kept distinct from the existing
confidence fail-safe: an entry with no outbound excursion is simply "not a
genuine return" (no cue, `skipped_return` stays False), whereas an unconfident
genuine return still latches `skipped_return` as before.

## Alternatives considered

- **A new per-visit latch inside `CueGate`** tracking front-side zones since the
  hub. Rejected: it re-derives, in a second place, the exact "excursion since
  hub" notion `task_events` already owns — the kind of duplicate definition
  ADR-0026 exists to prevent (reward and cue could drift apart).
- **Gate in the consumer (suppress the RETURN edge in `phase3_controller`).**
  Rejected: the gate would still flip `_active`/`_return_visit_decided` and later
  emit a spurious stop edge; keeping the decision inside the gate keeps its
  ON/OFF state coherent.
- **A `CueParams` toggle (like `require_up_stem`).** Rejected per decision:
  there is no bring-up case where a return cue on a non-return run is desired.

## Consequences

- The return cue now marks only genuine inbound legs; a run that starts up a
  return arm is silent until the rat completes a stem-out excursion.
- Return and reward share one credential, so they cannot disagree about what
  counts as an outbound excursion.
- The offline `--selftest` lap already runs a full stem-out excursion, so the
  left-return onset still fires and matches its beam (self-test unchanged).
- If a future rig makes non-stem outbound routes legitimate, revisit the single
  `outbound_used_stem` definition in `task_events.py` (it flows to reward, cue,
  and stats together).

## Evidence / links

- ADR-0045 (return cue as a zone gate — narrowed here), ADR-0026 / ADR-0055
  (`task_events` as the single task-phase source of truth), ADR-0038
  (per-excursion latch precedent), ADR-0047 (per-arm return schedules).
- `host/test_cue_gate.py` — `test_no_fire_without_outbound_excursion`,
  `test_direct_return_suppressed_then_real_lap_fires`.
- `host/maze_state_validate.py --selftest` — synthetic left lap: return onset
  still matches the LRETURN beam, 0 spurious onsets.
