# ADR 0131: The offline analysis frame holds attempted trials, not the planned sequence

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/analysis/bundle_reader.py`,
  `host/tests/test_analysis_bundle_reader.py`
- **Supersedes / Superseded by:** — (builds on ADR-0112, ADR-0046/0047, ADR-0119)

## Context

Phase 3 pre-writes the full planned cue matrix up front (ADR-0046/0047): the trial
CSV is created with one row per PLANNED trial, and each row's live fields
(`trial_start_iso`, `chosen_arm`, `outcome`, timestamps) are filled only when that
trial is actually run. So a session that plans 200 trials but runs 139 leaves 61
placeholder rows carrying only the planned cue, and a session that is started but
aborted before any trial runs leaves 200 fully-blank rows.

`bundle_reader` loaded every row. Because the animal-wide order column
`seq = groupby("animal").cumcount()` counts ALL rows, the offline analysis placed
the completed trials at scattered `seq` positions with large empty ranges between
them (the un-run tails, plus whole aborted sessions). The `animal_progress`
notebook plots the completed-only rolling traces against `seq`, so those empty
ranges rendered as long horizontal connecting lines - the "gaps" the operator saw.
Confirmed on rat 600 (2026-08-21..26): 1200 rows loaded but only 405 completed;
two 08-26 sessions had 0 completed trials (200 blank rows each), producing a ~400
wide empty block in the trace. It also made `SessionInfo.n_trials` read 200 for
every session (it counted planned rows, since every planned row has a trial number).

## Decision

In `bundle_reader.load_bundle`, after concatenating a bundle's run CSVs, keep only
the trials the rat actually ATTEMPTED, then drop a bundle with none:

```python
started = trials["trial_start_iso"].astype(str).str.strip().ne("")
scored = trials["outcome"].isin(COMPLETED_OUTCOMES + (ABANDONED,))
trials = trials[started | scored].reset_index(drop=True)
if trials.empty:
    return None
```

A trial counts as attempted if it was STARTED (has a `trial_start_iso`) OR SCORED
(`CORRECT`/`INCORRECT`/`ABANDONED`). This drops planned-but-unrun placeholder rows
while KEEPING abandoned trials (they were run, and they matter for the engagement
inter-trial gap and for breaking own-choice adjacency pairs, ADR-0093). A bundle
that reduces to nothing (an aborted start) returns `None` and is skipped in
`load_animals`, exactly as a bundle with no trial CSV already was.

Consequences downstream, all free: `seq` becomes contiguous over attempted trials
(the trace gaps collapse), aborted zero-trial sessions no longer appear in the
`sessions` frame, and `n_trials` becomes the real attempted count.

## Alternatives considered

- **Fix it in the plot (dense completed-trial index instead of `seq`)** — rejected:
  the empty ranges and the inflated `n_trials` are a property of the loaded frame,
  not just one figure; fixing the reader corrects every downstream consumer at once
  and keeps the notebook a thin front-end.
- **Filter on `outcome != ""` only (drop by "unscored")** — rejected: that would
  also drop a started-but-not-yet-scored trial and, more importantly, is a less
  direct statement of intent than "was it attempted". The started-OR-scored rule
  keeps abandoned trials, which a pure outcome filter handles only if ABANDONED is
  always written (it was absent in the observed data - blanks, not ABANDONED).
- **Leave placeholder rows in and special-case them everywhere** — rejected: pushes
  the "is this a real trial" question onto every consumer; the reader is the one
  place to answer it.

## Consequences

- The tidy `trials` frame now means "attempted trials", stated in the module and
  `load_bundle` docstrings. This is the natural companion to ADR-0119's superseded
  (edited-away) row drop: the frame holds the trials that actually happened and
  count once.
- `ABANDONED` (already imported) is now used, so an abandoned trial survives the
  filter and continues to break pairs / feed the engagement gap.
- Small legitimate `seq` gaps can still occur where abandoned trials sit between two
  completed trials - correct, since those trials are real activity.
- Read-only consumption of recordings; no rig/firmware/GUI change, no live-core
  change.

## Evidence / links

- Code: `host/tmaze/analysis/bundle_reader.py` (`load_bundle` attempted-trials
  filter), `host/tests/test_analysis_bundle_reader.py`
  (`TestDropsUnrunPlaceholders`: un-run rows dropped + contiguous `seq`,
  all-placeholder session skipped, abandoned trial kept).
- Diagnostic (rat 600, 2026-08-21..26): 1200 rows / 405 completed; per-session
  completed 139/99/77/0/0/90; the two 0-completed 08-26 sessions caused the ~400
  wide trace gap.
- Builds on ADR-0046/0047 (planned cue matrix written up front), ADR-0119
  (superseded-row drop), ADR-0112 (analysis layer), ADR-0083 (layering).
