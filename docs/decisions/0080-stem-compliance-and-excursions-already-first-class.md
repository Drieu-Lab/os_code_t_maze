# ADR 0080: Stem compliance and total excursions are already first-class measures (no new computation)

- **Status:** Accepted
- **Date:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** (documentation-only) `host/behavior_stats.py`, `host/session_summary.py` - the mapping WS4/WS7 use; no code change
- **Supersedes / Superseded by:** —

## Context

The paper training log records two behaviour numbers by hand each session -
**"Total excursions"** (e.g. 64) and **"stem compliance"** (e.g. 28%; 46
non-stem). The coordinated-records upgrade (WS8) asked, as a **feasibility-gated**
step: are these cleanly derivable from the recorded Phase 3 task events + tracking?
If so, compute them so they flow summary -> registry (WS4) -> training-log view
(WS7) and retire the hand-count; if not, record a backlog item instead.

## Decision

**Feasibility: yes - and they are already computed.** No new measure is added; a
duplicate would fork the counting vocabulary. The finding + the exact field
mapping are recorded here so WS4/WS7 surface the paper-log names by *reading the
existing fields*, and so a future reader doesn't re-add them to `phase3_stats`.

The live locomotor core `behavior_stats.py` (ADR-0032) already folds the shared
once-per-outbound-excursion choice-commit edge (`task_events.choice_committed`,
ADR-0055/FW-0020 - `trip_id` bumps per excursion) into exactly these scalars, and
they are already serialised verbatim into the session summary's `locomotion`
block (ADR-0071). Mapping the paper fields:

| Paper-log field | Computed field (`locomotion` block) | Definition |
| --- | --- | --- |
| Total excursions | `total_choices` | outbound excursions that entered a choice zone (one per hub->…->hub trip) |
| (of which) non-stem | `nonstem_choices` | excursions whose outbound leg did **not** use the stem (credit-denied) |
| Stem-gated | `stem_choices` | excursions whose outbound leg used the stem |
| Stem compliance | `compliance_cum` | `stem_choices / total_choices` (fraction; `None` when no excursions) |

This reproduces the hand-count arithmetic exactly: 46 non-stem at 28% compliance
=> total = 46 / (1 - 0.28) ≈ 64, stem = 18, and 18 / 64 ≈ 28%.

Scope caveat (inherited from ADR-0071): the locomotor accumulator resets on the
Phase 3 arm edge, so these are "since Phase 3 arm", which equals the session in
the normal arm->Start->Stop workflow (exact Start/Stop windowing deferred to
FW-0028).

## Alternatives considered

- **Add `stem_compliance` / `total_excursions` to `phase3_stats`.** Rejected:
  `phase3_stats` is the *cued-task* core (accuracy, side bias, RT); excursion
  compliance is a *locomotor* measure that already lives in `behavior_stats`.
  Duplicating it there would create two sources of truth for one number - exactly
  the comprehension debt the project guards against.
- **Add a relabeled convenience copy to the summary under the paper names.**
  Rejected: the summary already emits the `locomotion` block verbatim; a second
  copy under different names would drift. A documented mapping (this ADR + the
  WS4 records-schema reference) is enough for WS4/WS7 to use the paper names.
- **Record an FW backlog item.** Rejected: that branch is for the *not-derivable*
  case; here the measures exist and are tested, so nothing is deferred.

## Consequences

- WS4 (registry) reads `total_choices` / `nonstem_choices` / `stem_choices` /
  `compliance_cum` straight from each bundle's summary `locomotion` block and
  surfaces them under the paper-log names; WS7's generated training-log view
  inherits them, retiring the hand-count with no new computation.
- No code change and no new test in this change: the measures are already covered
  by `test_behavior_stats.py` (compliance / total / stem / non-stem) and their
  serialisation by `test_session_summary.py`.
- The paper-name <-> field mapping is defined once here (and will be echoed in the
  WS4 records-schema reference) so it stays a single source of truth.

## Evidence / links

- Coordinated-records upgrade plan (WS8, feasibility-gated).
- Source cores: ADR-0032 (`behavior_stats` measures), ADR-0055/FW-0020
  (`choice_committed` once-per-excursion edge), ADR-0071 (summary folds the
  locomotor snapshot). Scope caveat: FW-0028.
- Tests (existing, no change): `host/test_behavior_stats.py`
  (`test_compliance_cumulative` asserts total=4/stem=3/non-stem=1/compliance=0.75),
  `host/test_session_summary.py` (locomotion block serialised verbatim).
