# ADR 0149: Veto the debias auto-trigger while the animal is switch-dominant

- **Status:** Accepted
- **Date:** 2026-09-03
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/intervention_stack.py` (arbitration in `decide`, `_debias_vetoed`, `_last_kind`), `host/tmaze/core/phase3/debias.py` (`DebiasBlockConfig.alt_veto_rate`, `manual_pending`), `host/tmaze/core/phase3/phase3_config.py`, `host/phase3.json` (`debias_blocks.alt_veto_rate`), `host/tmaze/core/phase3/phase3_log.py` (header line), `host/tmaze/ui/phase3_controller.py` + `host/tmaze/ui/tooltip_text.py` (spinbox + tooltip), `host/tests/test_intervention_stack.py`, `host/tests/test_phase3_config.py`
- **Supersedes / Superseded by:** — (refines the arbitration of [ADR-0107](0107-phase3-alternation-intervention-stack.md); composes with [ADR-0136](0136-freeze-side-bias-during-dealternation.md) and [ADR-0139](0139-phase3-de-alternation-bridge-revert.md))

## Context

The intervention stack (ADR-0107) polls its children side-bias first, and the
two cheats it counters were assumed near mutually exclusive. On the bench they are
not. Across recent sessions the rats **alternate**, but with a dead-time-gated
**left default**: after a long hub pause (>= 10 s) P(left) rises to ~0.7–0.8 and
P(switch | previous LEFT) collapses to ~0.3, while after a RIGHT choice they
switch ~0.8 regardless. Those extra left choices are right-pull errors, which
**inflate the directional-error bias and deflate the cue-aware `alt_rate` at the
same time** (a stick is a repeat-cued non-switch), so the debias child crosses its
trigger first and wins the race.

That is the wrong tool for this animal, and it makes things worse. A debias block
forces one **fixed** side for the whole block. Any held fixed side is a reliable
50% "oasis" for an alternator: it lands on the side every other trial, and because
it never strings `exit_consecutive` (3) correct choices it cannot exit on success,
so it rides the full `max_block_len` (15). With `max_alt_run: 0` the NORMAL spine
pays alternation a *noisy* ~50%; the block pays a *deterministic* every-other-trial
50%. The animals visibly drop into an automatic figure-8 loop for the block's
duration — the intervention trains the loop it exists to break. This is the same
structural fact ADR-0139 met on the de-alternation side ("only 'force the last
choice' drives an alternator to 0%; any held side hands it ~50%").

## Decision

Gate the debias **auto**-trigger on the animal actually being a camper, at the
stack level (the composition seam), leaving both pure cores' own logic unchanged:

- **Principle: a debias block presupposes a camper.** `alt_rate = P(switch | told
  to repeat)` is exactly the camper-vs-switcher discriminator (camper and cue-user
  ~0, alternator ~1). So `InterventionStackController.decide()` skips the
  side-bias child's poll while the animal reads **switch-dominant**:
  `alt_rate >= debias.alt_veto_rate` with at least `alt.min_samples` repeat-cued
  samples in the alt window. A level, not a trend — deliberately (see
  alternatives).
- **Stay NORMAL when vetoed.** No routing, no substitute block: the
  counterbalanced spine is no worse than the oasis, the de-alternation child fires
  on its own `trigger_rate` when the alternation is strong enough, and debias
  re-enables the moment the rat drops to camper levels.
- **Post-de-alt grace (closes the handoff seam).** A de-alternation block clears
  its own window on exit ("fresh evidence", ADR-0107), so a samples-gated veto
  would switch *off* for ~2x`min_samples` trials while the bias window — frozen
  during that block (ADR-0136) — may still be over threshold: exactly the
  alternator we just corrected would be handed a fixed side. So the veto also
  holds while the alt window has fewer than `min_samples` samples **and** the most
  recently exited block was a de-alt block (`_last_kind`). No new knob; at session
  start (no prior block) the grace does not apply.
- **Operator force bypasses the veto** (`DebiasBlockController.manual_pending`):
  a deliberate manual debias block is never skipped.
- **Config.** `DebiasBlockConfig.alt_veto_rate: float = 0.5` (`0` = off,
  validated in `[0, 1]`). It is *not* read by the debias core; it lives there so it
  rides the existing `debias_blocks` json -> config -> `TrialConfig.debias` ->
  stack -> CSV header -> GUI plumbing with the other trigger parameters. Default
  0.5 = "switches more than it repeats when told to repeat".

## Alternatives considered

- **Trend criterion ("alt_rate has been rising / high-but-below-threshold when
  debias fires").** Rejected: needs a lookback and a slope, is sensitive to window
  noise, and is the band-aid it feels like. The level criterion encodes the
  block's precondition directly.
- **Route the vetoed bias-fire into a de-alternation block.** Deferred: acts
  sooner on a mixed animal, but starts de-alt below its own `trigger_rate` and
  needs a new trigger label in the log. Staying NORMAL is the smaller, cleaner
  change; revisit if mixed animals sit in NORMAL too long.
- **In-block bail-out (end a debias block early when its in-block switch rate is
  high).** Deferred: would catch cases the gate misses (too few alt samples yet)
  but is a second mechanism; observe the gate on the bench first.
- **Make the debias block itself alternation-proof.** Not possible: correcting a
  camper requires rewarding a switch to the far arm, which is exactly what rewards
  an alternator (the ADR-0139 tension). The fix has to be *which* block runs.
- **Put the knob on the stack / `TrialConfig` instead of `DebiasBlockConfig`.**
  Rejected for now: more plumbing for the same effect; the field is documented as
  stack-consumed.

## Consequences

- The mixed alternator-with-a-left-default no longer gets a fixed-R oasis: with
  the bias over threshold but `alt_rate` high, the stack stays counterbalanced and
  the de-alternation block fires when alternation is strong enough. The
  alternation -> debias -> figure-8 pattern should stop.
- A genuine camper (low `alt_rate` with samples, or no samples and no prior block)
  gets exactly the debias behaviour it did before; determinism is unchanged (no
  randomness added).
- A mixed animal whose `alt_rate` sits between `alt_veto_rate` and the de-alt
  `trigger_rate` may stay NORMAL for a while with no block at all. Accepted: no
  worse than the oasis, and the two knobs are tunable.
- The veto is silent per trial (no block starts), so an operator watching a bias
  over threshold may wonder why no block fires. The CSV header records
  `alt_veto_rate` and the per-trial `bias` / `alt_rate` / `sched_state` columns
  reconstruct it post hoc; a live `[ALT-VETO]` readout tag is deferred to FW-0057.
- Tests that exercised raw side-bias-first priority now set `alt_veto_rate=0` in
  their fixture; the veto has its own test class.

## Evidence / links

- Bench: multiple sessions (late Aug 2026) with alternation -> debias (fixed R)
  -> automatic figure-8 at ~50%; the choice-by-hub-dead-time figure
  (2026-08-03..08-30, rats 600/601/602) showing the pause-gated left default.
- Structural argument: ADR-0139 (held side = 50% for an alternator); composition:
  ADR-0107 (stack), ADR-0136 (bias frozen during de-alt — why the grace is
  needed), ADR-0086/0090 (debias score).
- Headless tests: `host/tests/test_intervention_stack.py`
  (`TestDebiasVetoWhenSwitchDominant`: switch-dominant routed to de-alt; between
  veto and trigger stays NORMAL; camper still gets debias; manual force bypasses;
  no samples + no prior block not vetoed; post-de-alt grace; veto off; no alt
  child), `host/tests/test_phase3_config.py` (`test_bad_debias_alt_veto_rate`).
  Full host suite green (946 tests).
