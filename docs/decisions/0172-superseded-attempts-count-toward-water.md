# ADR 0172: Count superseded (Repeat/Back) attempts toward the water estimate

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/phase3/phase3_log.py`
  (`Phase3StateSnapshot.superseded_rows`), `host/tmaze/core/phase3/phase3_stats.py`
  (`compute` water counts), `host/tmaze/core/session/session_summary.py`
  (`estimated_water` docstring), and by derivation
  `session_registry.task_reward_ml`
- **Supersedes / Superseded by:** —

## Context

Trial edits never erase behaviour (ADR-0119, bug #260810): a Repeat/Back on a
trial that already recorded behaviour ARCHIVES the animal's real attempt
(append-only) and opens a fresh attempt in the live row, so the CSV is one row per
`(trial, attempt)` while the live table stays one row per trial index. Readers
that want only the trials that COUNT drop the `superseded` rows.

The Phase 3 stats core (`phase3_stats.compute`) reduces the published snapshot to
the training + welfare measures. It read `snapshot.rows` ALONE - one row per trial
index - so a rewarded trial that was later Repeated/Backed contributed its reward
water only if the LIVE row still showed it. But the live row was CLEARED for the
re-run, so the archived attempt's prime + top-up pulses vanished from three water
counts:

- `Phase3Stats.rewarded` (rewarded prime count, water numerator),
- `Phase3Stats.topups` (lick top-up pulse count), and
- `Phase3Stats.reward_pulses_by_ms` (the per-width tally the exact water math sums;
  ADR-0110).

Those three feed `session_summary.estimated_water`, which in turn feeds
`session_registry.task_reward_ml`. The animal DRANK that water - a reward pulse is
delivered on the wire and a later operator trial edit does not undo it - so
`estimated_water` UNDER-reported the day's task-reward intake whenever a rewarded
trial was re-run. Under water restriction (protocol RA23A232) an undercounted
intake is a welfare-record error, not a cosmetic one. This is review finding L1.

## Decision

Publish the archived attempts on the snapshot and fold the REWARDED ones into the
water counts only.

1. `Phase3StateSnapshot` gains a `superseded_rows: Tuple[Phase3RowView, ...]`
   field, built in `snapshot()` from the `_archive` (each preserved attempt as a
   `Phase3RowView` keyed to its trial). It is kept DELIBERATELY SEPARATE from
   `rows`, which retains its one-row-per-trial-index contract - the ribbon,
   traces, the planned-trial list, the redraw signature, `Phase3RowView`, and the
   offline reader (`phase3_log_reader`, which already drops `superseded` rows,
   ADR-0119) are all untouched.
2. `phase3_stats.compute` computes the three WATER counts over `completed +
   superseded_rows`:
   - `rewarded = count(rewarded == "1")`,
   - `topups = sum(topups)`,
   - `reward_pulses_by_ms = tally(prime + reconstructed top-up widths)`.
   Every OTHER measure - accuracy, per-cue accuracy, side bias, the reward RATE
   (`reward_rate = rewarded_oob / oob_completed`, its numerator `oob_rewarded`
   over the LIVE out-of-block rows), win-stay/lose-shift, cue-zone dwell,
   block/probe summaries - stays on the live one-per-trial rows, so a re-run trial
   is scored exactly ONCE.

An UNREWARDED superseded attempt (`rewarded != "1"`, no top-ups: an ABANDONED,
UNCUED, or empty attempt) contributes NOTHING, because the tally keys on the
reward fields, not the outcome. `session_summary` and `session_registry` need no
change: they read the corrected `rewarded`/`topups`/`reward_pulses_by_ms` through
`estimated_water` and pick up the fix automatically.

## Alternatives considered

- **Interleave the archived attempts into `snapshot.rows` with a `superseded`
  flag** (the review plan's literal wording) — rejected. `rows` is positional-by-
  trial for six consumers (`phase3_ribbon._make_row`/window builders index
  `rows[i]`; `phase3_controller._seq_line`; the `trial_ribbon_view`
  progress-signature counts; `phase3_stats` `completed`; `phase3_traces`
  one-point-per-completed), so interleaving would desync the ribbon, duplicate
  trace/list rows, and inflate accuracy unless a `not superseded` filter was added
  at every site — plus a `Phase3RowView.superseded` field and a matching change to
  the offline reader to keep online/offline symmetric. A separate field confines
  the change to the one consumer that needs it.
- **Include superseded attempts in the water VOLUME only, but keep
  `rewarded`/`topups` as live counts** — rejected. The operator chose to have the
  counts reflect the water actually delivered; `reward_rate` already has its own
  live numerator (`oob_rewarded`), so folding the superseded pulses into
  `rewarded`/`topups` does not distort any rate and keeps a single water source.
- **Recompute water in `session_summary` from the CSV/archive directly** —
  rejected. It would duplicate the per-width tally logic that already lives in
  `phase3_stats` (ADR-0110) and re-introduce the drift the single-source design
  avoids.
- **Leave it (live rows only)** — rejected. It silently under-reports intake for
  any edited rewarded trial, a welfare-record error under water restriction.

## Consequences

- `estimated_water` / `task_reward_ml` now count every reward pulse the animal
  drank, including on trials later Repeated/Backed, so the welfare record matches
  what was delivered. On a clean session (no trial edits) `superseded_rows` is
  empty and every figure is byte-for-byte unchanged.
- `Phase3Stats.rewarded` can now exceed the number of completed trials (more than
  one rewarded attempt per trial index). Its docstring says so; it is the water
  numerator, not the reward-rate numerator (`oob_rewarded`), so no rate is
  affected.
- The snapshot carries a little more data (the archive as views). It is built on
  the same by-value freeze as `rows`, so the cross-thread hand-off (ADR-0050)
  stays decoupled.
- Follow-up: none required. If a future consumer wants the superseded attempts for
  something other than water, it now has them on the snapshot without touching
  `rows`.

## Evidence / links

- Trial-edit archive + one-row-per-attempt CSV: ADR-0119 (bug #260810).
- Per-width, calibration-free water math: ADR-0110; water channels: ADR-0073/0078/
  0084; cross-thread snapshot: ADR-0050.
- Tests: `host/tests/test_phase3_stats.py::TestSupersededWater` (Repeat/Back on a
  rewarded trial preserves the water while accuracy/reward-rate stay live;
  superseded top-ups counted; an unrewarded superseded attempt adds nothing;
  a no-edit session is unchanged) and
  `host/tests/test_phase3_log.py::TestTrialEditArchive::test_snapshot_exposes_superseded_rows_for_water`.
