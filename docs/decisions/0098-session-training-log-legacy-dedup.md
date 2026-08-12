# ADR 0098: De-duplicate legacy history against rig sessions in the session training log view

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** resources repo `records/animal/build_session_training_log.py` (`build_workbook`)
- **Supersedes / Superseded by:** — (refines ADR-0082)

## Context

`session_training_log.xlsx` is a generated view (ADR-0082): each per-rat "View"
sheet stitches together **rig** rows (from `session_registry.csv`) and **history**
rows seeded read-only from the legacy hand-typed `training_log.xlsx`. The design
assumed the legacy log only supplies *pre-rig* history, so the two sources never
overlap.

That assumption broke in practice: the operator kept hand-updating
`training_log.xlsx` after the rig came online (entries through 2026-08-10), and
`seed_history_from_legacy` seeds **every** legacy row with no date cutoff. So for
every day the rig recorded *and* the operator also hand-logged, the View sheet
listed the day twice - once as rig rows, once as a history row - inflating the
"Session #" counter and any per-rat totals (bug `#260810_RecordsIntegrity`,
finding D; e.g. `View 600` doubled 07-30, 07-31, 08-03, 08-04, 08-05...).

## Decision

Drop a legacy history row **only when that animal also has a rig session on the
same date** (per-animal same-date overlap), computed in `build_workbook`:

- build the set of rig-session dates per animal from the registry rows;
- keep a history row unless its `(animal, date)` is in that set;
- undated history rows (no date) never collide, so they are always kept.

Genuinely pre-rig days (before the rig recorded that animal) stay; a paper-only
day the rig missed stays; true duplicates collapse to the **rig** row (the
authoritative, richer record).

## Alternatives considered

- **A single fixed cutover date** (drop all legacy rows on/after e.g. 2026-07-24).
  Rejected by the user: it would also drop a legacy row on a day the rig *didn't*
  record (a rig-failure day that only exists on paper), losing that hand entry
  from the view. Per-animal overlap is more precise and future-proof.
- **Drop the rig row, keep the hand entry.** Rejected: the rig row is the
  authoritative, machine-computed record (behaviour, water channels); the hand
  entry's unique value (procedures/notes) already lives in
  `session_annotations.csv` and is joined onto the rig row.
- **Leave it (document the duplication).** Rejected: the double-counting
  misleads the "Session #" and per-rat totals a human reads at a glance.

## Consequences

- View sheets stop double-listing post-cutover days; each real session appears
  once (as the rig row) while pre-rig and paper-only history is preserved.
- The de-dup is keyed on the *date* only (not time), so if the same animal had
  both a rig session and a genuinely separate un-recorded event on the same
  calendar day, the hand row for that day is dropped. This is acceptable: such a
  same-day paper note belongs in the session's annotation, and the rig row is the
  record of what actually ran.
- The legacy `training_log.xlsx` is still only ever read, never written
  (ADR-0082 unchanged); this only changes which of its rows the view *shows*.
- The resources repo has no unit-test harness; the logic was verified by building
  a workbook from synthetic registry + history rows into a temp file and
  confirming the overlapping legacy day was dropped while pre-rig, paper-only, and
  undated rows were kept.

## Evidence / links

- `records/animal/build_session_training_log.py::build_workbook` (per-animal
  `rig_dates` filter).
- Verification run (2026-08-11): rig {07-30, 08-03} + history {07-02, 07-30,
  08-09, undated} -> view {07-02(hist), 07-30(rig), 08-03(rig), 08-09(hist),
  undated(hist)} with `n_hist_kept=3`.
- Bug: `docs/notes/Bugs.md` `#260810_RecordsIntegrity` finding D. Refines
  ADR-0082.
