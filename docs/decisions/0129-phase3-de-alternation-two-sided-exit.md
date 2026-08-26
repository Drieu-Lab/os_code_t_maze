# ADR 0129: Bias-aware two-sided exit for the Phase 3 de-alternation block

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/alternation.py` (forced-side rule + exit), `host/tmaze/core/phase3/phase3_config.py` (`_to_alternation_config`, default dict), `host/phase3.json` (`alternation_blocks`), `host/tmaze/core/phase3/phase3_log.py` (header line), `host/tmaze/ui/phase3_controller.py` + `host/tmaze/ui/tooltip_text.py` (spinbox + tooltips), `host/tests/test_alternation.py` + `host/tests/test_intervention_stack.py` + `host/tests/test_phase3_config.py`
- **Supersedes / Superseded by:** — (refines [ADR-0107](0107-phase3-alternation-intervention-stack.md); removes the `exit_rate` / `exit_min_samples` params it introduced)

## Context

The de-alternation block (ADR-0107) forces the correct side to the animal's LAST
CHOICE, so the rat must REPEAT to be rewarded, and it exited when the in-block
cue-aware `alt_rate` relaxed below `exit_rate`. On the bench we noticed this
exit has a perverse escape that ADR-0107 only partially anticipated: because
reward is given for a correct choice and the correct side IS the last choice,
**a rat can leave the block by camping one arm** — every repeat there is rewarded,
the one-sided in-block rate falls to zero, and the block ends. When the arm it
camps is its preferred side, the block has just handed it a run of rewarded biased
trials — the intervention *reinforces the very side bias* we want gone. ADR-0107
deferred this residual bias to the concurrently-running debias block ("caught on a
later window"), but that lets the reinforcement happen first and only cleans up
afterward.

The mechanical subtlety: since the forced side is the last choice, a committed
one-sided rat is only ever cued to its own arm, so a naive "require balanced
compliance to exit" would be unreachable (the rat is never given a chance to
repeat on the other arm) and would just ride every biased rat to the welfare cap.
Fixing the escape therefore requires changing *which side is forced*, not only the
exit test.

## Decision

Make the block exit **bias-aware and two-sided**, self-contained in
`alternation.py`:

- **Per-arm rewarded-repeat counters.** Count a "repeat" when the rat chose the
  forced side AND stayed (`chosen == target == prev_chosen`), attributed to that
  arm. Bridge-correct trials (chose the forced side but switched onto it) are not
  repeats and — since `cued != prev_chosen` there — are already excluded from the
  cue-aware `alt_rate`.
- **Bias-aware forced side (recomputed each trial from `exit_repeats_per_side` =
  k):** while NEITHER arm has k repeats, force the last choice (the original
  de-alternation contract — 0% for a pure alternator); once one arm reaches k,
  force the OTHER arm (a one-off "bridge" switch demand, then repeats there). A rat
  can only comply by showing it repeats on both arms.
- **Two-sided exit (reps only).** A block ends on success once `rep[LEFT] >= k`
  AND `rep[RIGHT] >= k`, or at the `max_block_len` welfare cap. We do NOT exit on a
  relaxed one-sided rate (the escape above) nor on N-consecutive-correct (which is
  N repeats on one arm — same problem). The in-block `alt_rate` is still computed
  for the live readout; it no longer gates the exit.
- **Config.** `AlternationBlockConfig` drops `exit_rate` / `exit_min_samples`
  (now meaningless) and adds `exit_repeats_per_side: int = 2`
  (`0 < k <= max_block_len`). A compliant rat exits in ~`2k + 1 = 5` trials, with
  in-block rewards split across both arms.

## Alternatives considered

- **Keep the relaxed-rate exit and let the debias block clean up (ADR-0107 as
  written).** Rejected: the alternation block still reinforces the bias once
  before anything corrects it, and can oscillate with the debias block.
- **Bias-gated exit only (don't change the forced side).** Rejected: with the
  forced side still the last choice, a camper is rewarded on every trial up to the
  cap, so blocking the early exit only *lengthens* the rewarded biased run — it can
  make reinforcement worse, not better.
- **Reps AND the relaxed-rate gate.** Rejected as redundant: once both arms have k
  clean rewarded stays the in-block rate is already low, so the extra gate only
  adds friction and leaves two now-orphaned params on screen.
- **Stack-level handoff (start a debias block immediately when alternation exits
  one-sided).** Deferred: leaves the reinforcement to happen first; the two-sided
  exit prevents it at the source. Still available as a future complement.
- **Explicitly consult the debias `bias()` to pick which arm to bridge to first.**
  Not needed: requiring repeats on *both* arms subsumes the bias question and keeps
  `alternation.py` pure and self-contained (no cross-controller signal).

## Consequences

- The de-alternation block no longer trains a side bias on its way out: a rat
  escapes only by demonstrating it can repeat regardless of side, and in-block
  rewards are balanced across arms. A biased rat that refuses the non-preferred arm
  earns 0% there and rides to the `max_block_len` cap (flagged), bounded exactly as
  before.
- A pure alternator is unchanged (it never earns a repeat, so the forced side stays
  the last choice — 0% until the cap), so existing detector/trigger behaviour and
  determinism are preserved.
- Blocks are a little longer for a complier (~`2k + 1` vs the old ~`exit_min_samples`),
  still well under the welfare cap.
- **Breaking config change:** `exit_rate` / `exit_min_samples` are removed from
  `AlternationBlockConfig`, `phase3.json`, the validator, the CSV header line, and
  the Phase 3-tab GUI (replaced by an "Exit repeats/side" spinbox). Old
  `phase3.json` files that still carry the two keys are harmless (ignored by
  `_to_alternation_config`'s `dict.get`).
- Would revisit if beta shows the bridge trials frustrate rats (e.g. tune k down to
  1, or add the stack-level handoff as a complement).

## Evidence / links

- Refines ADR-0107 (Context #2 argued a consecutive-correct exit reinforces bias;
  this generalises the concern to the relaxed-rate exit and fixes it at the forced
  side). Builds on ADR-0086 / ADR-0090 (debias) and ADR-0126 (anti-alternation
  schedule cap).
- Headless tests: `host/tests/test_alternation.py`
  (`test_success_exit_requires_repeats_on_both_arms`,
  `test_bias_camper_cannot_exit_one_sided`, updated cap/trigger tests) and
  `host/tests/test_phase3_config.py` (`test_bad_alt_exit_repeats_per_side`). Full
  host suite green (930 tests, `python -m unittest discover -s tests -t .` under
  `host/`).
