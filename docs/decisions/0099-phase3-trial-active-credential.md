# ADR 0099: Gate the Phase 3 trial (reward + return cue) on the hub-departure credential

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_task.py` (scoring gate),
  `host/tmaze/core/cue_gate.py` (return-cue onset), `host/tmaze/core/task_events.py`
  (docstring only), `host/tests/test_phase3_task.py`, `host/tests/test_cue_gate.py`
- **Supersedes / Superseded by:** — (supersedes the return-cue independence decided
  in ADR-0067; extends ADR-0061)

## Context

Running Phase 3 on the rig, the animal sometimes starts an excursion up a left or
right (side) path and, while on it, jumps over the divider into the mid stem. The
choice cue correctly stays silent — it is gated on the hub-departure credential
`task_events.left_hub_via_stem` ("this trip DEPARTED the hub up the stem";
ADR-0067). But if the animal then makes the trial's correct choice by accident,
**reward is dispensed and the return cue plays**, marking a trial that never
really started.

The cause is that reward/scoring and the return cue were gated on a *looser*
credential than the choice cue:

- `phase3_task.Phase3Task.update()` scored the committed choice when
  `outbound_used_stem` was true. That predicate means "the stem has been visited
  at all since the hub", so it is already true once the animal has hopped into the
  stem from a side arm — even though the trip never departed the hub up the stem.
- `cue_gate.CueGate.step()` fired the return cue on the same `outbound_used_stem`
  credential (ADR-0061), so the return cue played on the way home from that same
  divider-jump excursion.

ADR-0061's original principle was "the return cue shares the reward's credential",
and at that time the reward used `outbound_used_stem`, so the two agreed. ADR-0067
later introduced `left_hub_via_stem` for the choice cue (a technical necessity:
the choice cue's window *is* the stem, so `outbound_used_stem` cannot distinguish a
forward trial from a backwards lap there) and deliberately left the return cue on
the looser credential, pinned by `test_credential_does_not_gate_the_return_cue`.
The two credentials only ever disagree in exactly this divider-jump / backwards
route — which is the case now judged illegitimate.

## Decision

Make one credential — `left_hub_via_stem` ("this trip departed the hub up the
stem") — the single "this is a genuine trial excursion" gate, shared by the choice
cue (already), the reward/scoring, and the return cue. A trial is *active* only if
the excursion started hub → mid stem.

1. `phase3_task.Phase3Task.update()` scores the committed choice on
   `left_hub_via_stem` instead of `outbound_used_stem`. A commit on a non-trial
   excursion yields no score and no reward; the trial index does not advance, so
   the sequence trial is preserved and scored on the next genuine hub → stem trip.
   This also aligns scoring with when the trial is logged as started (the
   controller stamps `trial_started` off `departed_hub_via_stem`).

2. `cue_gate.CueGate.step()` requires `on_outbound_leg` (the caller's
   `left_hub_via_stem`) for the return-cue onset. Because `left_hub_via_stem`
   implies `outbound_used_stem`, the RETURN guard becomes
   `outbound_used_stem and on_outbound_leg`, which collapses to the hub-departure
   requirement; the older `outbound_used_stem` term is kept for continuity but no
   longer changes the decision on its own.

No controller change is needed: `phase3_controller._process_frame` and the offline
replay `maze_state_validate.cue_onset_events` already pass both credentials into
`gate.step`.

## Alternatives considered

- **Reward/scoring only** (leave the return cue on `outbound_used_stem`).
  Rejected: it would break the ADR-0061 principle that the return cue shares the
  reward's credential — after tightening the reward, the cue and the reward could
  disagree about whether a divider-jump trip is a trial. The operator explicitly
  wanted the spurious return cue gone too.
- **Rewire only the controller** to feed `left_hub_via_stem` as the return
  credential, leaving `cue_gate` unchanged. Rejected: it leaves the gate's
  `outbound_used_stem` parameter meaning something different from its name and
  hides the decision from the pure-core tests; putting the tightening in the gate
  keeps its ON/OFF state coherent and headless-testable.
- **Remove `outbound_used_stem` from `CueGate.step` entirely** now that both cues
  key off `left_hub_via_stem`. Deferred: it widens the change (signature, the
  controller call, the offline replay, more tests) for no behavioural gain; the
  redundant guard is harmless and documented.

## Consequences

- A divider-jump / backwards-route excursion is now fully inert as a trial: no
  choice cue (already), no return cue, no score, no reward, no advance. The trial
  it would have consumed is preserved for the next genuine hub → stem trip.
- The choice cue, the return cue, the reward, and the score all share one
  task-phase credential, so they cannot disagree about what a trip is — restoring
  ADR-0061's principle after ADR-0067 had split the return cue off.
- `test_credential_does_not_gate_the_return_cue` (which asserted the opposite) is
  replaced by `test_hub_departure_credential_gates_the_return_cue`; ADR-0067's
  behaviour for the *choice* cue is unchanged.
- Normal forward trials are unaffected: `left_hub_via_stem` is true throughout a
  genuine hub → stem → choice → return → hub lap, so both cues, the reward, and
  scoring behave exactly as before. The offline `--selftest` lap departs up the
  stem, so its return onset still matches its beam.
- If a future protocol makes a non-stem departure a legitimate trial start, this
  credential must be revisited together with `outbound_used_stem` — they now flow
  to the cues, the reward, and the score as one (the caution ADR-0067 already
  raised).

## Evidence / links

- ADR-0061 (return cue's original shared credential — extended here), ADR-0067
  (choice-cue hub-departure credential; its return-cue independence is superseded
  here), ADR-0026 / ADR-0055 (`task_events` as the single task-phase source of
  truth), ADR-0046 / ADR-0047 (host-owned Phase 3).
- `host/tests/test_phase3_task.py` — `test_divider_jump_choice_is_not_scored`
  (unit) and `test_divider_jump_choice_is_not_scored_then_real_trip_scores`
  (integration over the real estimator → task_events → Phase3Task pipeline).
- `host/tests/test_cue_gate.py` — `test_hub_departure_credential_gates_the_return_cue`,
  `test_no_fire_without_hub_departure`.
- `host/tmaze/tools/maze_state_validate.py --selftest` — synthetic left lap
  departs up the stem, so both cue onsets still match their beams, 0 unmatched.
