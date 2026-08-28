# ADR 0136: Freeze the side-bias detector during a de-alternation block

- **Status:** Accepted
- **Date:** 2026-08-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/intervention_stack.py` (`record` fan-out), `host/tests/test_intervention_stack.py`
- **Supersedes / Superseded by:** — (stopgap; refines the composition of [ADR-0086](0086-phase3-debias-blocks.md)/[ADR-0090](0090-debias-bias-normalized-by-errors.md) with [ADR-0107](0107-phase3-alternation-intervention-stack.md)/[ADR-0129](0129-phase3-de-alternation-two-sided-exit.md))

## Context

Under the `intervention_blocks` generator the stack runs two closed-loop
interventions concurrently, one active at a time: the side-bias *debias* block
(ADR-0086/0090) and the *de-alternation* block (ADR-0107/0129). The stack keeps
every enabled detector's sliding window warm by fanning each scored trial's
`(cued, chosen)` pair out to ALL children, even the one whose block isn't running
(`InterventionStackController.record`).

The side-bias score is only valid under a COUNTERBALANCED schedule. It measures a
signed *directional-error* bias: left-pull errors `(cued RIGHT, chose LEFT)` vs
right-pull errors `(cued LEFT, chose RIGHT)`, normalised by the error count
(ADR-0090). That assumes the cued side is drawn from the balanced spine so, over a
window, either pull is equally available.

Inside a de-alternation block that assumption is broken: the cued/forced side is
derived from the animal's OWN choices — its last choice, or the "bridge" arm once
one side is satisfied (ADR-0129) — so the cued side can repeat on one arm for
several trials in a row. An animal that is simply ALTERNATING (a completely
unbiased strategy) then errs on ~50% of these trials, and because the cue is
pinned to one side, every one of those errors is the SAME pull. The directional-
error bias climbs toward ±1 even though the behaviour is unbiased.

Observed on the bench (a session the day before this ADR): a de-alternation block
inflated the side-bias score, so a debias block fired immediately after it; the
still-alternating animal then re-triggered de-alternation, which again inflated
the score, and so on — many such blocks cascaded in one session. A proper fix
needs a bias score that accounts for the non-counterbalanced task sequence inside
a block, which is more than can be designed and validated before the next
sessions.

## Decision

While a DE-ALTERNATION block owns the active block, do NOT feed the scored trial
to the side-bias child. Concretely, in `InterventionStackController.record`, skip
the `KIND_SIDE_BIAS` child whenever `active_kind == KIND_ALTERNATION`; all other
children are fed unchanged. The record/decide ordering makes this exact: `decide`
(which sets the active block) runs at commit, before the trial, and `record`
runs at scoring, so `active_kind` at record time identifies precisely the trials
that ran inside the block.

The side-bias window is FROZEN, not cleared: the pre-block trials are preserved,
so once the de-alternation block exits the detector resumes from genuine
counterbalanced evidence and nothing accrued during the block. The debias child
is not polled for triggering while another child owns the active block, so
freezing its window is sufficient to stop the spurious re-trigger and the
cascade.

This is a deliberate STOPGAP. It removes the invalid accumulation without
attempting a better in-block score; that follow-up is tracked as FW-0047.

## Alternatives considered

- **Clear the side-bias window when the de-alternation block ends.** Rejected for
  now: it also discards legitimate pre-block bias evidence, so a real bias present
  before the block would need a full fresh window to be caught again. Freezing is
  the smaller, more literal "stop accumulating in the block" change; a clear can
  be revisited with the proper score.
- **Leave the fan-out as-is and only raise the debias `trigger_bias`/`min_errors`
  gates.** Rejected: tuning the gates can't distinguish a genuine bias from the
  block-induced artefact, so it either still cascades or masks real bias.
- **Design the schedule-aware bias score now.** Deferred (FW-0047): it needs
  design and headless validation we don't have time for before the next sessions;
  this ADR is the safe interim.

## Consequences

- The alternation → debias → alternation cascade can no longer be driven by an
  unbiased alternator: de-alternation blocks stop feeding the side-bias detector,
  so its score holds steady across a block and reflects only out-of-block,
  counterbalanced trials.
- The logged per-trial `bias` value simply does not move during a de-alternation
  block (it is the frozen window read), which is the intended behaviour; no CSV
  column, protocol, or config changes.
- The freeze is scoped to the side-bias child only — the alternation child (and
  any other) still sees every trial, so its own detector stays warm.
- A genuine side bias that develops WHILE a long de-alternation block runs will be
  noticed only once the block exits (on the next windows). Acceptable: ADR-0129's
  two-sided exit already prevents a de-alternation block from reinforcing a bias
  on its way out, and the debias detector re-engages immediately afterward.
- Revisit when FW-0047 lands a schedule-aware bias score; this freeze is expected
  to be superseded rather than kept.

## Evidence / links

- Bench observation: cascading de-alternation/debias blocks in a session on
  2026-08-27 driven by an alternating (unbiased) animal.
- Code: `host/tmaze/core/phase3/intervention_stack.py` (`record`).
- Headless tests: `host/tests/test_intervention_stack.py`
  (`TestSideBiasFrozenDuringAlternation`). Builds on ADR-0086/0090 (debias score),
  ADR-0107/0129 (de-alternation forced side). Follow-up: FW-0047.
