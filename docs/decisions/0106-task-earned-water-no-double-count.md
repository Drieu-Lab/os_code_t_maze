# ADR 0106: Don't double-count task-earned water in the welfare dashboard

- **Status:** Accepted
- **Date:** 2026-08-13
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/dashboard.py` (`collect_water`),
  `host/tests/test_dashboard.py`
- **Supersedes / Superseded by:** Builds on ADR-0104 (`water_method`) and
  ADR-0084 (the four provenance-labelled water channels)

## Context

The welfare dashboard's daily water total sums four provenance channels
(`dashboard.collect_water`): `task` + `manual` (reward water the rig dispensed
during recorded sessions, from `session_registry.csv`) and `supplemental` +
`cage` (the daily row's `supp_water` / `water_ml` cells). That model assumes
`water_ml` is water given *in addition to* task rewards.

On the bench that assumption broke. With `water_method = task_earned` (ADR-0104)
the operator records the day's task-earned water into the `water_ml` column - the
*same* water the rig already measures as `task_reward_ml`. The dashboard then
counted it twice. For rat 602 the water bars came out ~2x the entered amount:

| Date | entered `water_ml` | rig task+manual | old bar (summed) |
| --- | --- | --- | --- |
| 2026-08-10 | 4.52 | 4.40 + 0.12 = 4.52 | 9.04 |
| 2026-08-12 | 4.57 | 4.345 + 0.223 = 4.57 | 9.14 |

The entered value equalling the rig total on those days is the tell: it's one
quantity entered on two surfaces, not two separate waterings.

## Decision

Make `collect_water` `water_method`-aware. On a `task_earned` day where the
entered `water_ml` is a positive number, the entered amount **wins**: it's
reported as the `cage` amount and the rig `task`/`manual` reward is dropped, so
the water is counted **once**. If `water_ml` is blank on a `task_earned` day, the
rig reward stands (a session day with no typed amount isn't lost). `supplemental`
(a genuinely separate hand-given source) always counts. All other methods
(`measured_bottle`, `timed_access`, blank/unspecified) are unchanged - there
`water_ml` is separate cage/bottle water and still sums with any reward.

The change lives entirely in the pure `collect_water`, so every surface that
reads it - the dashboard's per-rat status strip, the "water by source"
breakdown, and the single-rat water-overlay bars (`water_ml_trend`) - is fixed at
once, with no GUI change.

## Alternatives considered

- **Rig reward wins (use `task_reward_ml`+`manual_reward_ml` when a session
  exists, entered `water_ml` only otherwise).** Rejected by the operator: the
  rig's reward is a calibration-derived estimate and the bars would no longer
  match the number they typed. Entered-wins keeps the dashboard showing what the
  operator recorded (they can still see the rig's figure in the registry).
- **Keep summing; tell operators not to type task water into `water_ml`.**
  Rejected: fragile (relies on discipline), and it conflicts with how the paper
  log has always recorded rig water in that column. The method field exists
  precisely to disambiguate this, so the code should use it.
- **Infer "task water" whenever `water_ml` ≈ the rig reward.** Rejected: a
  fuzzy numeric match is brittle and surprising; the explicit `water_method` is
  the right signal.

## Consequences

- Water bars/totals on `task_earned` days now equal the entered `water_ml`
  (+ any `supplemental`), matching operator expectation; earlier days are no
  longer inflated.
- The rig's per-session `task_reward_ml`/`manual_reward_ml` remain in the
  registry (and in the session detail) - they're just not re-added to the daily
  total when the operator has already recorded that water as `water_ml`.
- The rule keys off `water_method`, so an unmarked historical day is unaffected
  (its typically non-numeric `water_ml` already read as 0 mL anyway).
- One more reason the method field earns its keep; a future change could surface
  a small "counted once (task-earned)" hint in the breakdown if it's unclear.

## Evidence / links

- Core: `host/tmaze/core/records/dashboard.py` (`collect_water` task-earned
  branch; docstring signal 3).
- Tests: `host/tests/test_dashboard.py`
  (`test_task_earned_day_counts_entered_water_once`,
  `test_task_earned_blank_water_ml_falls_back_to_rig_reward`,
  `test_measured_bottle_day_still_sums_cage_and_reward`) - full suite passing.
- Bench data: rat 602 daily rows (`water_method=task_earned`, `water_ml`
  4.52/6.40/4.57/4.23) vs `session_registry.csv` `task/manual_reward_ml`.
