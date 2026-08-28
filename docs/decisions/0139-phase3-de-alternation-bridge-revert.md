# ADR 0139: Edge-triggered bridge (revert to last choice) for the de-alternation far arm

- **Status:** Accepted
- **Date:** 2026-08-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/alternation.py` (forced-side rule + `record`/`reset`/`_start_block`/`_end_block` state), `host/tmaze/ui/tooltip_text.py` (`phase3.alt_exit_repeats_per_side`), `host/tests/test_alternation.py`
- **Supersedes / Superseded by:** — (amends [ADR-0129](0129-phase3-de-alternation-two-sided-exit.md): keeps its two-sided exit, changes how the far arm is forced)

## Context

ADR-0129 made the de-alternation block's exit bias-aware and two-sided: a block
ends on success only after `exit_repeats_per_side` (k) rewarded repeats on BOTH
arms. To make the far arm reachable it changed *which side is forced* — once one
arm reached k, the block **held the far arm as a fixed target** every trial until
it too reached k.

On the bench that held far arm leaks reward to exactly the strategy the block
exists to punish. A left-biased alternator complies briefly (two stays on L,
satisfying that arm), the block then holds R, and the rat reverts to alternating:
because R is a *fixed* side, an alternator lands on it every other trial — a
correct SWITCH onto R, which is not a stay, so `rep[R]` never grows — and it
collects ~50% all the way to the `max_block_len` cap without ever repeating.
Observed directly in sessions: "the animal gets into a groove of mindlessly
alternating without pausing and getting rewarded every other trial on R." The
held far arm defeats the block's purpose for the very animal it targets.

The root cause is structural. "Force the last choice" is the only rule that drives
a pure alternator to 0% (it always demands the opposite of what an alternator will
do). ANY held fixed side hands an alternator ~50%. The two goals — get a camper
OFF its arm (reward a switch to the far arm) vs. punish an alternator (never reward
a switch) — cannot both be met by a single forced-side signal. ADR-0129 optimised
for the camper; that is the wrong priority for THIS block, whose job is
anti-alternation (side bias is the concurrent debias block's job).

## Decision

Keep ADR-0129's two-sided exit, but make the far-arm demand **edge-triggered** and
**reverting**, in `alternation.py`:

- The default forced side is the last choice (`prev_chosen`) — the original
  de-alternation contract, 0% for a pure alternator.
- `record()` counts consecutive rewarded stays (`_consec`) on the current arm.
  When a run of k completes and the far arm still needs repeats, it latches a
  one-off bridge (`_flip_pending`).
- `decide()` forces the far (unsatisfied) arm for exactly ONE trial when
  `_flip_pending` is set, then clears it. Every other trial it **reverts to the
  last choice**. So a rat that does not repeat on the far arm is never given a held
  side to alternate against — the far-arm reward is at most the single bridge
  catch, and alternation stays ~0%.
- The bridge re-fires whenever another k-run completes on the current arm, so a
  genuine complier still crosses over (L-run → bridge → R-run → two-sided exit),
  while an alternator (which never completes a run) is nailed at the last choice.

`exit_repeats_per_side` (=2) keeps its meaning, so `phase3.json`,
`phase3_config.py`, `phase3_log.py`, and the GUI spinbox are unchanged.

## Alternatives considered

- **Keep ADR-0129's held far arm.** Rejected: it is the source of the observed
  ~50% alternation reward.
- **Drop the two-sided exit entirely (pure repeat-last-choice, relaxed-rate or
  repeat-run exit).** Rejected for now: the two-sided exit still usefully prevents
  a block from *ending* on a one-sided bias; only the far-arm forcing needed
  fixing.
- **Random run length N (1-3) before the bridge instead of fixed k.** Deferred:
  would need a seeded RNG in the controller to stay reproducible; fixed k is the
  smaller change and enough to close the leak. Can revisit if animals learn to
  pace to the bridge.
- **Also keep some camper-starvation (e.g. hold the far arm for a few trials
  before reverting).** Rejected: reintroduces a (shorter) held side and so a
  partial ~50% window; the clean revert plus the concurrent debias block is
  simpler.

## Consequences

- A rat that escapes into mindless alternation after one compliant run no longer
  earns ~50%; it earns at most the single bridge catch, then ~0% to the cap. This
  is the intended fix.
- **Accepted trade-off:** the block no longer strongly starves a rat that CAMPS
  one arm — a camper is rewarded on its camp trials and only eats each bridge
  error (~`k/(k+1)`), riding to the cap. It still cannot *exit* one-sided (it never
  repeats on the far arm), so ADR-0129's "no bias exit" property holds. Side-bias
  correction is delegated to the concurrent debias block.
- Interaction with [ADR-0136](0136-freeze-side-bias-during-dealternation.md): the
  debias detector is frozen while a de-alternation block runs, so a camper's
  in-block bias reward is corrected only after the block caps/exits (bounded by
  `max_block_len`), not concurrently. ADR-0136's claim that a de-alternation block
  cannot *exit* on a reinforced bias remains true here.
- A pure alternator and a perfect complier are behaviourally unchanged from
  ADR-0129 (the existing exit/cap/trigger tests still pass); determinism is
  preserved (no new randomness).

## Evidence / links

- Amends ADR-0129 (its held far arm) and composes with ADR-0136 (debias freeze),
  ADR-0107 (intervention stack), ADR-0086/0090 (debias score).
- Bench observation: left-biased alternator rewarded every other trial on the held
  far arm within de-alternation blocks (sessions on/before 2026-08-28).
- Headless tests: `host/tests/test_alternation.py`
  (`test_alternator_not_rewarded_every_other_trial_after_one_arm` regression, plus
  the unchanged `test_success_exit_requires_repeats_on_both_arms` and
  `test_bias_camper_cannot_exit_one_sided`).
