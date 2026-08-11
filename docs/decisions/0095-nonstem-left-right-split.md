# ADR 0095: Split the denied (non-stem) excursions into left/right to expose their directional pull

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/behavior_stats.py`;
  `host/tmaze/ui/behavior_view.py`, `tooltip_text.py`;
  `host/tmaze/core/session/session_summary.py`; test `test_behavior_stats.py`
- **Supersedes / Superseded by:** Extends the locomotor readout of
  [ADR-0032](0032-live-behavior-readout.md); builds on the once-per-excursion
  choice edge of [ADR-0055](0055-once-per-trip-choice-commit.md) / ADR-0038.

## Context

The behavior readout counts **non-stem** ("denied") excursions - outbound trips
that reached a choice arm without going up the stem, so the reward rule denies
credit - as a single scalar `nonstem_choices`. The operator asked whether those
denied trips could be split by side to see a directional bias in the rat's
"shortcut" behavior.

The data is already present and was simply being discarded: `task_events`'
`choice_committed` edge carries the committed side (`left`/`right`) for **every**
outbound excursion regardless of route, but `behavior_stats` only tallied left/
right inside its `if stem:` branch (the stem-gated L/R balance), dropping the side
on non-stem trips. So the split needs no new tracking, no new edges, and no
protocol change.

## Decision

Tally the committed side of non-stem excursions too. Add `_nonstem_left` /
`_nonstem_right` counters (an `else:` branch beside the stem-gated tally) and
expose them on `BehaviorSnapshot` as `nonstem_left`, `nonstem_right`, and their
cumulative fraction `nonstem_lr` = `(left_frac, right_frac)` (None until the first
denied trip). By construction `nonstem_left + nonstem_right == nonstem_choices`.

Render it as an `[nL/nR]` split on the existing "Non-stem (denied)" line in both
the live panel and the session summary. **Cumulative only** - no rolling-window
variant (unlike the stem-gated L/R), to keep it simple.

This is a **third, distinct** side-bias view, and is labeled as such so it isn't
confused with the other two: the stem-gated `lr_*` balance (the rat's L/R split on
*valid* trials) and the cued-task `dir_bias` (directional-*error* bias on scored
Phase 3 trials, ADR-0090). This one is the rat's directional pull on the trips it
took *off* the sanctioned path.

Two small companion clarity tweaks land with it (panel comprehension, not new
measures): the excursion throughput line is relabeled "Stem trips" (it sat beside
the cued "Completed:" trials and "Trials" collided in meaning), and the non-stem
tooltips describe the new split.

## Alternatives considered

- **Add a rolling-window non-stem L/R too** (mirroring the stem-gated one).
  Rejected per the operator's request to keep it cumulative-only; denied trips are
  infrequent, so a windowed fraction would be noisy and add surface for little gain.
- **Derive it offline from the CSV instead of live.** Rejected: the live panel is
  where the operator watches for a developing side habit, and the number is free to
  compute inline from the edge already consumed.
- **Fold it into the stem-gated L/R.** Rejected: denied trips are a different
  population (credit-denied, off-path); merging them would blur the valid-trial
  choice bias the L/R balance is meant to show.

## Consequences

- `BehaviorSnapshot` gains three fields; the panel/summary "Non-stem" line gains an
  `[nL/nR]` split when any denied trip has occurred. The JSON summary's
  `locomotion` block emits the new fields additively (no schema bump).
- **Revisit if:** denied trips become common enough that a windowed view is worth
  the extra noise, or if a per-trip-type breakdown (which return arm the shortcut
  used) is wanted.

## Evidence / links

- Core: `host/tmaze/core/behavior_stats.py` (`_nonstem_left`/`_nonstem_right`,
  `_accumulate_choice` else-branch, `snapshot`).
- Surfaces: `host/tmaze/ui/behavior_view.py` (`_nonstem_text`, `_throughput_text`
  relabel), `host/tmaze/core/session/session_summary.py` (`_locomotor_lines`),
  `host/tmaze/ui/tooltip_text.py` (`behavior.nonstem`, `behavior.phase3.nonstem`).
- Tests: `host/tests/test_behavior_stats.py` (`test_nonstem_left_right_split`,
  `test_nonstem_lr_none_before_any_denied_trip`).
- Bug: `docs/notes/Bugs.md` `#260720_BehaviorStatError` (F8 + F6 of the review pass).
