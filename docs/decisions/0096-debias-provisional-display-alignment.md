# ADR 0096: Align the debias ribbon's provisional trial preview to the spine cursor

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/trial_sequence.py`
  (`_provisional_arm`, new `is_committed`), `host/tmaze/core/phase3/phase3_log.py`
  (new `refresh_provisional`, `_apply_cue`), `host/tmaze/ui/phase3_controller.py`
  (call `refresh_provisional` on publish); tests `test_debias.py`,
  `test_phase3_log.py`
- **Supersedes / Superseded by:** Refines the display half of
  [ADR-0086](0086-phase3-debias-blocks.md) (the `debias_blocks` generator); the
  scheduling behaviour of ADR-0086 is unchanged.

## Context

In `debias_blocks` mode the correct side of each trial is decided lazily, the
moment it runs, because it can depend on the animal's realized choices (ADR-0086).
Until a trial is committed, the trial ribbon and planned-trial list show a
**provisional** cue.

On the bench the operator saw the ribbon show the current/upcoming trial as one cue
type while the tone that actually played was the opposite; once the animal chose,
the row snapped to the correct value. Critically, it kept happening **in NORMAL
mode**, after a corrective block had run to its `max_block_len` cap and reverted.

Root cause: a corrective block inserts forced trials **without consuming the
counterbalanced spine** (only NORMAL trials advance the spine cursor `_spine_pos`).
The committed side of a NORMAL trial is `seq[_spine_pos]`, but the provisional
preview read `seq[index]` — the spine indexed by the absolute trial number. Once a
block of length B has run, `_spine_pos` trails the absolute index by B forever, so
every later preview cell was drawn from the wrong spine slot (≈50 % of the time the
opposite side). Two things compounded it: (1) the preview read the wrong slot, and
(2) the display rows were frozen at arm time and only refreshed per trial by
`set_decision()` at trial start / scoring, so a block never re-aligned the rows
ahead of the cursor. The played tone and the saved CSV were always correct (both
come from the committed side); only the *preview* was wrong.

## Decision

Make the provisional preview honour the spine cursor, and re-align the display rows
whenever the schedule shifts:

- **`_provisional_arm` walks the spine from `_spine_pos`, not `seq[index]`.** For an
  un-committed trial `ahead = index - _frontier` trials past the decision frontier:
  if a block is open the immediate next trial continues it (forced side) and trials
  beyond it are assumed NORMAL; NORMAL trials take `seq[_spine_pos + ahead]` (minus
  one slot when the immediate trial is the open block's). This makes the immediate
  next trial's preview equal what `commit()` will decide (for a clean NORMAL
  transition), and an aligned best-guess further ahead.
- **`Phase3Log.refresh_provisional(current_index)`** re-points the DISPLAY cue
  columns (`correct_arm`, `choice_cue`) of the current + not-yet-scored trials to
  the sequence's current reading — finalized side + debias context for the committed
  current trial, aligned provisional (cue only) for trials ahead. The debias-context
  columns (`sched_state`/`block_id`/`bias`) stay blank for un-committed trials, so
  the data file never records a decision before it happens. The controller calls it
  on every table change (the existing publish path), so a block re-aligns the whole
  forward plan immediately. No-op for the `balanced` generator.

## Alternatives considered

- **Blank all future preview cells (show "provisional/unknown").** Honest about the
  closed-loop uncertainty, but the operator wants to see the upcoming cues at a
  glance; the aligned guess is correct for the immediate next trial and reasonable
  beyond, so we keep showing it. (Visually flagging far-ahead cells as provisional
  is a possible future refinement.)
- **Recompute cues live inside `snapshot()` instead of storing them on rows.**
  Rejected: the snapshot is a by-value freeze decoupled from the live sequence
  (ADR-0050); computing from the sequence there would recouple them and run on the
  reader (Tk) thread. Refreshing the owned rows on the actuation thread keeps the
  snapshot a pure copy.
- **Only fix `_provisional_arm` (no row refresh).** Insufficient: rows are frozen at
  arm time, so without a refresh the forward plan stays stale after a block even
  with a correct provisional function.

## Consequences

- The ribbon and planned-trial list now show the correct cue for the current trial
  and an aligned guess for upcoming trials, in both NORMAL and INTERVENTION, before
  and after a block. Trials far ahead remain inherently uncertain (a future bias can
  trigger a new block); they self-correct as each trial commits.
- Display-only change: no effect on the played tone, scoring, the committed schedule,
  or the CSV's finalized columns (still written by `set_decision` when each trial is
  decided). `balanced` sessions are untouched.
- `refresh_provisional` is O(remaining trials) per table change (~hundreds of rows,
  a few times per trial) — negligible next to the existing full-table CSV re-render.

## Evidence / links

- Core: `host/tmaze/core/phase3/trial_sequence.py` (`_provisional_arm`,
  `is_committed`); `phase3_log.py` (`refresh_provisional`, `_apply_cue`).
- Wiring: `host/tmaze/ui/phase3_controller.py` (publish path).
- Tests: `test_debias.py::TestProvisionalDisplay` (spine-cursor alignment after a
  block; open-block continuation), `test_phase3_log.py`
  (`test_refresh_provisional_realigns_future_rows_after_block`).
- Related: ADR-0086 (the `debias_blocks` generator + lazy commit), ADR-0050 (the
  published state snapshot), ADR-0051 (the trial ribbon), ADR-0090 (the bias signal).
