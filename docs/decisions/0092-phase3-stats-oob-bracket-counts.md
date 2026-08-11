# ADR 0092: Report out-of-block count numerators so the accuracy/reward brackets match their percentages

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_stats.py`;
  `host/tmaze/ui/behavior_view.py`, `tooltip_text.py`;
  `host/tmaze/core/session/session_summary.py`;
  tests `test_phase3_stats.py`
- **Supersedes / Superseded by:** Refines the display of the partitioned measures
  added by [ADR-0086](0086-phase3-debias-blocks.md) /
  [ADR-0090](0090-debias-bias-normalized-by-errors.md); neither is superseded.

## Context

Since the closed-loop debias work (ADR-0086/0090), the Phase 3 learning measures
are computed over **out-of-block (`oob`) completed trials only** — the animal's
uncoerced trials, with the forced corrective-block trials excluded so they can't
distort accuracy or reward rate. But the raw counts on the same
`Phase3Stats` (`correct`, `incorrect`, `rewarded`, `completed`) are over **all**
completed trials, including the in-block ones.

The live panel and the session summary then render the `oob` **percentage** next
to an **all-completed** count bracket:

```
Accuracy: 80%  (last 20: 82%)   [40/60]
```

where `40/60` = 67%, not the 80% shown. The bracket is `[correct/completed]`
(all completed) while the percentage is `correct_oob / oob_completed`. In a
balanced session `oob == completed`, so the two agree and the defect is invisible
on the bench; it only mis-reads once a debias session runs a corrective block.
The `behavior.phase3.accuracy` / `.reward` tooltips likewise described the
all-completed definition, matching neither the percentage nor the bracket. This
is the core of bug `#260720_BehaviorStatError` (host stats / behavior view). The
per-cue accuracy line was already internally consistent (both its numerator and
its bracket are `oob`), so it was the model to follow.

## Decision

Expose the **out-of-block numerators** as their own fields and render *those* in
the brackets, so the count and the percentage are the same population:

- Add `Phase3Stats.oob_correct` (correct over `oob` completed — the numerator of
  `accuracy_cum`) and `Phase3Stats.oob_rewarded` (rewarded over `oob` — the
  numerator of `reward_rate`). Their shared denominator, `oob_completed`, already
  existed.
- Render `[{oob_correct}/{oob_completed}]` beside the accuracy line and
  `[{oob_rewarded}/{oob_completed}]` beside the reward line in both the live panel
  (`behavior_view.py`) and the summary text (`session_summary.py`).
- Keep `correct` / `incorrect` / `rewarded` **as all-completed counts.** They are
  the honest "everything the rat did" progress totals (the "Completed: C/T
  correct.../incorrect..." line), and `rewarded` (plus `topups`) must stay
  all-completed because the water estimate counts *every* dispensed pulse, in-block
  ones included (`session_summary.estimated_water`).
- Update the two tooltips to say the measure is over out-of-block trials under the
  debias generator and that the `[n/N]` bracket counts the same trials.

For a balanced session `oob_correct == correct` and `oob_rewarded == rewarded`, so
the rendered brackets are byte-for-byte identical to before — this only changes
what a debias session shows.

## Alternatives considered

- **Render the percentage over all completed trials instead** (drop the `oob`
  partition for the headline). Rejected: it re-introduces exactly the distortion
  ADR-0086 removed — a corrective block that forces one side would swing the
  reported accuracy — which is scientifically the wrong number to headline.
- **Drop the bracket entirely, show only the percentage.** Rejected: the raw
  count is useful at a glance (how many trials the number rests on), and the fix
  is cheap.
- **Bump the summary `SCHEMA_VERSION`.** Not done: the summary emits the cued
  stats as a transparent `vars()` dump, and prior `Phase3Stats` field additions
  (ADR-0086/0090) did not bump it — the version tracks the summary's own top-level
  layout, not additive stats fields. Two new fields appear in `cued_task`
  additively; existing readers are unaffected.

## Consequences

- The accuracy/reward count and percentage now agree in every session, debias or
  balanced. The remaining review findings (win-stay/lose-shift adjacency across
  in-block trials, the reward-rate shaping-aid case, the bias-readout declutter,
  the non-stem L/R split, RT removal) are tracked separately and land on their own.
- `Phase3Stats` grows two fields; the JSON summary's `cued_task` gains
  `oob_correct` / `oob_rewarded` additively.
- **Revisit if:** a future readout wants an all-completed accuracy on purpose (it
  would use `correct` / `completed`, which are still present).

## Evidence / links

- Core: `host/tmaze/core/phase3/phase3_stats.py` (`oob_correct` / `oob_rewarded`
  fields + `compute`).
- Surfaces: `host/tmaze/ui/behavior_view.py` (`_refresh_phase3` brackets),
  `host/tmaze/core/session/session_summary.py` (`_cued_lines` reward bracket),
  `host/tmaze/ui/tooltip_text.py` (`behavior.phase3.accuracy` / `.reward`).
- Tests: `host/tests/test_phase3_stats.py`
  (`test_oob_count_fields_match_the_displayed_percentages`, and the balanced-case
  equality in `test_balanced_generator_has_neutral_debias_fields`).
- Bug: `docs/notes/Bugs.md` `#260720_BehaviorStatError` (first fix of the pass).
