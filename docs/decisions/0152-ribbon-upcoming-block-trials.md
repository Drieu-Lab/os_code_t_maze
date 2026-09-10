# ADR 0152: Mark upcoming block/probe trials on the Phase 3 ribbon

- **Status:** Accepted
- **Date:** 2026-09-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_ribbon.py`,
  `host/tmaze/core/phase3/phase3_log.py` (snapshot),
  `host/tmaze/core/phase3/trial_sequence.py`,
  `host/tmaze/core/phase3/{debias,alternation,intervention_stack,probe_blocks}.py`,
  `host/tmaze/ui/trial_ribbon_view.py`
- **Supersedes / Superseded by:** extends ADR-0051 (ribbon) and ADR-0096
  (provisional display); builds on ADR-0115-probe / ADR-0134 (probe blocks) and
  ADR-0135 (scrollable ribbon)

## Context

The trial ribbon (ADR-0051) draws the current trial plus a few trials behind and
ahead. A running **intervention block** (side-bias debias / de-alternation) rings
its trials in a purple outline, and an **unrewarded probe block** (ADR-0115-probe
/ ADR-0134) fills its trials teal. On the bench the operator noticed that when a
block starts, only the *immediate* (current) trial carries the marker - the
upcoming cells ahead of it look like ordinary trials, even though they are part of
the same block.

The reason is a deliberate data-integrity rule. A ribbon cell's block/probe status
comes from the persisted trial-log columns (`sched_state`, `probe`), and those are
filled only for a **committed** trial - the one in progress. `refresh_provisional`
(ADR-0096) aligns the *cue side* of upcoming un-committed trials but intentionally
leaves their scheduler columns blank, so the CSV never records a decision that was
only guessed. So the columns can't be the source of an upcoming marker without
polluting the data file.

The two block kinds also differ in how much of the future is actually knowable:

- A **probe block** is deterministic the instant it fires: `ProbeBlockController`
  holds the full forced plan (`block_len` trials) and how many remain, so every
  remaining probe trial is known.
- An **intervention block** is closed-loop: it runs until an exit criterion that
  depends on the rat's *future* choices (or an operator "End"). Only the single
  immediate next trial can be reliably predicted to continue it - exactly the
  assumption `_provisional_arm` already makes for the cue side; anything beyond is
  a genuine guess.

## Decision

Add a **display-only, provisional** upcoming-block marker to the ribbon, computed
from a fresh prediction carried on the published snapshot and never written to the
CSV.

1. **Non-mutating prediction predicates in the pure controllers.**
   - `DebiasBlockController.will_continue()` and
     `AlternationBlockController.will_continue()` mirror each controller's
     `decide()` exit checks (open block, no latched operator end, and neither the
     success criterion - 3 consecutive correct / two-sided repeats - nor the
     `max_block_len` cap already met) without advancing any state.
   - `InterventionStackController.will_continue()` delegates to the active child
     (`False` when none active).
   - `ProbeBlockController.upcoming_forced()` returns the deterministic remainder
     of the forced plan (`block_len - issued`), or `0` when idle or an operator
     end/disable is latched (`_manual_end`).

2. **Expose on `TrialSequence`** as `intervention_will_continue()` and
   `probe_upcoming()`, and copy both into `Phase3StateSnapshot` as the display
   hints `intervention_next: bool` and `probe_upcoming: int` (defaulted, appended
   last, so old readers are unaffected). These are snapshot fields, **not** `_Row`
   / CSV columns.

3. **Mark upcoming rows in the ribbon core.** In `phase3_ribbon._make_row`, for an
   upcoming (`i > current`), not-yet-resolved trial: mark `probe` when it falls in
   `current+1 .. current+probe_upcoming`, else mark `corrective` when it is the
   single next trial (`i == current+1`) and `intervention_next` is set. Committed
   and scored rows keep deriving their status from their own persisted columns, so
   a marker never retro-flags a resolved trial. Probe wins over the intervention
   marker on the same cell (a probe suspends interventions). Both the fixed 7-cell
   window and the scrollable full-plan strip (ADR-0135) share this helper.

4. **Redraw on prediction change.** `trial_ribbon_view.progress_signature` gains
   `intervention_next` and `probe_upcoming`, so the change-gated poll redraws when
   only the prediction changes - e.g. a block about to exit while its `sched_state`
   is still INTERVENTION, or a probe running down / cancelled.

Net UX: a probe block shows all its remaining upcoming cells; an open intervention
block marks only the single next cell. Because the intervention predicate reflects
the exit criteria already satisfied by the just-scored trial, the upcoming marker
clears **reactively** - the moment a success is scored or an operator ends the
block - rather than lagging until the next trial commits.

## Alternatives considered

- **Write provisional block/probe flags onto the upcoming rows.** Simplest to
  render, but it would put guessed decisions into the CSV, contradicting ADR-0096
  (the log records committed decisions only) and misleading offline readers.
  Rejected.
- **Mark the upcoming intervention cell purely from `target is not None`** (the
  existing provisional-cue assumption). Simpler, but the marker would linger for a
  tick after a success/manual-end (state flips only on the next `decide()`), so a
  block the rat just escaped would still look active. The `will_continue()`
  predicate makes it reactive for a few extra lines and is unit-testable.
- **Extrapolate several intervention trials ahead.** Rejected: block length is
  outcome-dependent, so anything past the immediate next trial is unfounded and
  would routinely mislead.
- **Compute entirely inside `phase3_ribbon` from existing snapshot fields.** Not
  possible: the snapshot did not carry the remaining probe length nor an
  exit-aware intervention prediction, hence the two new fields.

## Consequences

- The ribbon now reads as one continuous block: the full probe run, and the next
  intervention trial, are visible before they commit; the markers clear the moment
  the block exits or is cancelled.
- The CSV / `_Row` schema is unchanged - the two new snapshot fields are display
  hints only, so offline readers and the data contract are untouched.
- A small, testable prediction surface (`will_continue` / `upcoming_forced`) now
  duplicates the controllers' exit logic; if an exit rule changes, its
  `will_continue` must change with it (guarded by the new unit tests).
- The intervention-vs-probe asymmetry (one cell vs the whole block) is intentional
  and reflects what is actually knowable, not a bug.

## Evidence / links

- Extends ADR-0051 (ribbon "what to show") and ADR-0096 (provisional cue
  alignment; blank scheduler columns for un-committed trials); builds on
  ADR-0115-probe / ADR-0134 (probe blocks, correct-choice window trigger),
  ADR-0137 (mid-session probe arm/disarm), ADR-0135 (scrollable strip), and
  ADR-0050 (published snapshot for cross-tab readers).
- Code refs: `will_continue` in `debias.py` / `alternation.py` /
  `intervention_stack.py`; `upcoming_forced` in `probe_blocks.py`;
  `intervention_will_continue` / `probe_upcoming` in `trial_sequence.py`;
  `Phase3StateSnapshot.{intervention_next,probe_upcoming}` +
  `Phase3Log.snapshot` in `phase3_log.py`; `_make_row` in `phase3_ribbon.py`;
  `progress_signature` in `trial_ribbon_view.py`.
- Tests: `host/tests/test_phase3_ribbon.py` (`TestUpcomingBlockMarkers`,
  `TestProbeUpcomingIntegration`), `test_debias.py` / `test_alternation.py` /
  `test_intervention_stack.py` (`TestWillContinue`), `test_phase3_probe_blocks.py`
  (`upcoming_forced` count-down / end / disarm).
