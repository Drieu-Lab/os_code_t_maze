# ADR 0132: The offline analysis baseline set adopts the live GUI out-of-block convention

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/analysis/rolling_metrics.py`,
  `host/tmaze/analysis/progression.py`,
  `host/tests/test_analysis_rolling_metrics.py`,
  `host/tests/test_analysis_progression.py`,
  `host/notebooks/animal_progress.ipynb`
- **Supersedes / Superseded by:** — (refines ADR-0112; does NOT supersede ADR-0114)

## Context

Two code paths report the same headline behavioural rates (accuracy, side bias,
cue-follow), and they disagreed on WHICH trials count:

- The LIVE GUI - `phase3_stats` (the behavior-panel scalar readout) and
  `phase3_traces` (the Metrics-tab rolling traces, ADR-0114) - computes them over
  OUT-OF-BLOCK trials: completed trials that are not an intervention block and not
  an unrewarded probe. This INCLUDES the free trials that follow a block (the
  post-block recovery trials).
- The OFFLINE analysis (`rolling_metrics`, and the notebook's own `session_summary`)
  computed them over the `epoch == free` set from `choice_signals`, which additionally
  EXCLUDES a `post_window` of post-block trials (ADR-0112 tags trials
  `free`/`in_block`/`post_block`).

So the offline `animal_progress` figures and the live Metrics tab could show
different accuracy / bias for the same session - confusing when the notebook is
meant to be the offline twin of the live tab. The divergence is only around
intervention blocks (a session with no blocks is identical either way), but the
whole point of the intervention generators is that blocks happen.

A second, separate divergence exists in the ENGAGEMENT metric: the GUI's inter-trial
interval is the DEAD TIME `trial_start[N] - trial_end[N-1]` (ADR-0114), while the
offline layer uses the start-to-start gap `trial_start[N] - trial_start[N-1]`. That
one is deliberately left alone here (see below).

## Decision

Make the OFFLINE baseline set match the live out-of-block convention, and leave the
live core untouched:

- In `rolling_metrics.build_traces`, compute `side_bias_left`, `cue_follow`,
  `reward_rate` (and the newly added rolling `accuracy` / per-cue `high_acc` /
  `low_acc`) over `oob = ~in_block & ~probe` instead of `epoch == free`. The
  own-pair rates (`switch`/`win_stay`/`lose_shift`) already matched the GUI
  (own-choice adjacency, ADR-0093) and are unchanged.
- The new per-session summary `progression.per_session_summary` (extracted from the
  notebook, replacing its in-cell `session_summary`) uses the SAME `oob` mask, so the
  per-session progression matches the traces and the GUI.
- The `epoch` column (`free`/`in_block`/`post_block`) STAYS. It is still used by
  `mode_label` and by `intervention_response`'s pre-onset baselines; only the
  baseline RATE masks moved off it.
- The live core (`phase3_traces`, `phase3_stats`, and the rest of the Metrics tab)
  is NOT changed - it already uses out-of-block, so aligning offline to it needs no
  live-side edit.

## Alternatives considered

- **Change the live core to exclude post-block (make the GUI match the old offline
  definition)** — rejected: the operator chose the GUI convention as the reference,
  and the live out-of-block readout (ADR-0086/0090/0092/0114) is the established,
  on-bench definition. Aligning offline to it is the smaller, safer change.
- **Keep both definitions and just document the difference** — rejected: the notebook
  is explicitly the offline twin of the live tab; two silently-different "accuracy"
  numbers is exactly the comprehension debt to avoid.
- **Also fix the engagement definition now** — deferred, not rejected. The offline
  layer already uses the intended start-to-start gap; the GUI's dead-time ITI is one
  of several live-core Metrics-tab issues the operator wants handled together as a
  separate task (captured in `docs/future-work.md`). Changing the live ITI here would
  exceed this change's one purpose (offline baseline reconciliation).

## Consequences

- Offline `rolling_metrics` / `progression` and the live `phase3_traces` /
  `phase3_stats` now report accuracy / side-bias / cue-follow over the same trial
  set, so the notebook and the Metrics tab agree for a given session.
- Post-block recovery trials now contribute to the offline baseline. This is the
  intended reading (they are ordinary free behaviour); it can slightly change a
  historical figure for a session that had blocks, versus the pre-change notebook.
- A follow-up remains: the engagement-definition divergence (dead-time ITI vs
  start-to-start) and the other live-core Metrics-tab fixes the operator flagged are
  deferred to the separate live-core metrics task (FW entry added).

## Evidence / links

- Code: `host/tmaze/analysis/rolling_metrics.py` (`oob` mask + rolling accuracy),
  `host/tmaze/analysis/progression.py` (`per_session_summary` over `oob`),
  tests `test_analysis_rolling_metrics` (out-of-block baseline + accuracy) and
  `test_analysis_progression`.
- Live reference: `phase3_stats` (out-of-block accuracy/reward brackets,
  ADR-0086/0090/0092), `phase3_traces` (`accuracy`/`high_acc`/`low_acc` over `oob`,
  ADR-0114).
- Refines ADR-0112 (analysis layer trial partitioning); relates to ADR-0114 (live
  traces) without superseding it; builds on ADR-0093/0090/0083.
