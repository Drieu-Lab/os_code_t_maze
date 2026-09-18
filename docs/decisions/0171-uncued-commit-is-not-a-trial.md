# ADR 0171: A choice committed with no cue is UNCUED, not a scored trial

- **Status:** Accepted
- **Date:** 2026-09-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_task.py` (`UNCUED`,
  `Phase3Decision.uncued_cleared`, `_uncued_pending`, `update(..., choice_cue_on)`),
  `host/tmaze/core/phase3/phase3_log.py` (`Phase3StateSnapshot.uncued`),
  `host/tmaze/core/phase3/phase3_stats.py` (`Phase3Stats.uncued`),
  `host/tmaze/core/phase3/bundle_format.py` (`outcome_labels`, column doc),
  `host/tmaze/core/session/session_summary.py`, `host/tmaze/ui/phase3_controller.py`,
  and the analysis mirror `drieu_lab_analysis/src/tmaze_analysis/phase3_format.py`
  (`OUTCOME_LABELS`) + `bundle_data_dictionary.py`
- **Supersedes / Superseded by:** —

## Context

Phase 3 is a *cue-guided* task: on each trial a discriminative CHOICE tone
(HIGH/LOW → left/right) sounds while the rat is on the central stem, and the
rat's arm choice is scored right/wrong against that cue and rewarded
accordingly. The whole scientific point of a trial is that the animal heard the
cue and acted on it.

But the scorer (`phase3_task.py`) scored a choice purely on the shared
`trial_trip` credential (docs/decisions/0168/0169) — "a genuine hub→stem
excursion that committed an arm" — with **no check that the cue actually
sounded on that trip**. The cue is gated independently by position and
confidence (`cue_gate.py`, docs/decisions/0045): if the rat ran the stem too
fast, tracking dropped through the cue window, the confidence gate never
cleared, or (2b) it perched off-track, the CHOICE tone might never fire, yet the
rat could still enter an arm and the scorer would record a CORRECT/INCORRECT and
dispense reward. That is a silent data-integrity hole: a "trial" the animal was
never cued on is scored and rewarded as if it were, diluting accuracy and
contaminating the reward history the closed-loop scheduler and the analysis both
read.

There was no token for "the rat committed but was not cued", so such trips were
indistinguishable from real trials in the record.

## Decision

Add an **UNCUED** outcome and re-present the planned trial instead of consuming
it.

- The controller already tracks the choice-cue-onset stamp of the current trip
  (`_choice_cue_on_iso`, set when the tone sounds, cleared at the hub each lap).
  It passes `choice_cue_on = (self._choice_cue_on_iso is not None)` into
  `Phase3Task.update(frame, choice_cue_on)`.
- A `trial_trip` commit with `choice_cue_on` **False** is scored `UNCUED`: no
  reward, and — unlike a normal score — the trial index is **not advanced**. A
  new per-trip `_uncued_pending` latch (a sibling of `_awaiting_return`) flips
  the return lap from "advance" to "clear-and-re-run", and the decision carries
  `uncued_cleared=True` on that lap. `choice_cue_on` **defaults True**, so any
  caller/test that does not track cue state keeps the pre-0171 scoring behaviour;
  only an explicit False yields UNCUED.
- On `uncued_cleared`, the controller archives the uncued attempt with the
  existing archive-not-erase machinery (`Phase3Log.supersede_trial(idx,
  "uncued")`, docs/decisions/0119): the UNCUED row is preserved in the trial CSV
  (its own attempt, `edit="uncued"`) and the live row is cleared so the **same
  planned trial** — its cue identity — re-runs on the next clean cued trip. It
  emits an `auto:uncued` timeline note.
- **Visibility.** `UNCUED` is added to the manifest `outcome_labels` and the
  analysis `OUTCOME_LABELS` mirror + data dictionary (so a reader knows the
  token). A cumulative count is surfaced live: `Phase3Log.snapshot()` counts
  UNCUED over live **and** archived rows (so it survives the re-run overwriting
  the live row) into `Phase3StateSnapshot.uncued`; `phase3_stats.compute` passes
  it through to `Phase3Stats.uncued`; the session summary shows an `Uncued:` line
  (only when any occurred) and the JSON carries it. The controller keeps a live
  `self._uncued` tally for the status line and the trial-ribbon text marks an
  in-progress UNCUED row like ABANDONED.
- **No version bump.** `UNCUED` is an appended member of an existing enumerated
  column (`outcome`), not a new column or a semantic change to existing values,
  so the trial-CSV `PROTO_VERSION` and the manifest `FORMAT_VERSION` are
  unchanged; the golden fixture is re-issued so its `outcome_labels` list carries
  the new token. Scope is Phase 3 (the host-owned cued task) only.

"Never sounded" means never *started* this trip: any cue onset counts as cued
(a partial cue the rat acted on is still a cued trial); UNCUED is reserved for a
trip where the tone did not start at all.

## Alternatives considered

- **Score it anyway (status quo).** Rejected: it is the bug — an un-cued commit
  is not a cue-guided trial, and counting it corrupts accuracy, reward rate, and
  the closed-loop scheduler's evidence.
- **Abandon the trial and advance (like a Skip).** Rejected: that consumes the
  planned trial (its scheduled cue identity + any block context) for a trip the
  animal was never cued on, so the animal never actually runs that trial. Re-
  running preserves the schedule.
- **Deny silently and re-run without a token (like the ADR-0099 divider jump).**
  Rejected: a divider jump is not a genuine trip and leaves no trace by design; an
  UNCUED commit *is* a genuine trip and a real behavioural event (the rat ran and
  chose) that must be counted and preserved, or an operator/analyst can't see how
  often the rig failed to cue.
- **Overwrite the live row on re-run (no archive).** Rejected: the re-run's
  cued outcome would erase the UNCUED from the CSV, so the token would not
  reliably appear offline. Archiving (docs/decisions/0119) preserves every
  attempt and keeps the cumulative count honest.
- **Bump the format/proto version.** Rejected: adding a member to an existing
  enumerated field is backward-compatible (an old reader that hard-codes the
  three prior labels simply won't recognise `UNCUED`, the same as any new
  categorical value); a version bump is reserved for a new/removed column or a
  changed meaning of existing data.

## Consequences

- A trip the rat was never cued on no longer scores or rewards; its planned
  trial re-runs until the animal gets a real cued opportunity, and every uncued
  attempt is preserved in the CSV (`outcome==UNCUED`) and counted in the live
  readout.
- **A run with the CHOICE cue effectively never firing does not advance.** If
  the cue is disabled or the cue gate never clears (bad tracking, mis-tuned
  confidence), every commit is UNCUED and the trial index stays put — the session
  makes no progress and the climbing `Uncued:` count is the operator's signal
  that the rig is not cuing the animal. This is intended (an un-cued session is
  not a valid cued-task session), but it is a behaviour change from the old
  "always score" path; the operator should watch the uncued count.
- `phase3_stats` needed no change to its accuracy math: UNCUED is neither CORRECT
  nor INCORRECT, so the existing `completed` filter already excludes it (like
  ABANDONED); only the passthrough count was added.
- The re-run reuses the tested Repeat/Back archive path, so no new persistence
  code — the UNCUED attempt is a normal superseded row tagged `edit="uncued"`.
- Follow-up left open: the offline `phase3_log_reader` reconstructs a snapshot
  from a CSV and defaults `uncued=0` (it does not recount UNCUED rows); the
  authoritative offline count is the analysis repo reading `outcome==UNCUED`.

## Evidence / links

- Cue gating (why a trip can go un-cued): docs/decisions/0045; the off-track
  perch that can suppress the tone: docs/decisions/0170.
- Shared trial credential the commit is gated on: docs/decisions/0168, 0169.
- Archive-not-erase re-run machinery: docs/decisions/0119.
- Tests: `TestUncued` in `host/tests/test_phase3_task.py` (not scored/rewarded,
  return-lap clears without advancing, planned trial re-runs and scores once
  cued, cue-on default, UNCUED only on a real trip); `TestUncuedCount` in
  `host/tests/test_phase3_log.py` (live + archived cumulative count);
  `TestUncuedSurfaced` in `host/tests/test_phase3_stats.py`.
