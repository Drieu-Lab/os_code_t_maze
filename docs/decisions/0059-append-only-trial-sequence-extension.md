# ADR 0059: Append-only, seed-deterministic trial-sequence extension

- **Status:** Accepted
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/trial_sequence.py`, `host/phase3_log.py`, `host/phase3_controller.py`
- **Supersedes / Superseded by:** —

## Context

A host-owned Phase 3 session generates a fixed number of trials up front from the
seed (`TrialConfig.max_trials`, default 200; docs/decisions/0046/0047). Once the
rat runs past the last planned trial, `Phase3Task.complete` is true and no more
cues sound - but a rat that is still working shouldn't be stopped just because it
out-paced the plan. The operator asked for a "generate more trials" button
(docs/decisions/0058).

The constraint: the trials already run - and their recorded rows - must not
change. Their planned columns (correct arm, choice cue, both return-arm cues) and
their filled-in outcomes/timestamps are the scientific record. So the extension
has to be **append-only**, and it should stay deterministic from the seed so a
session is still reproducible.

`TrialSequence` builds three seed-deterministic schedules threaded off one PRNG:
the balanced L/R choice schedule (a faithful port of the firmware generator,
ADR-0009), then a balanced hi/lo schedule for each physical return arm
(ADR-0047). Each is generated in whole balanced blocks with the LCG state threaded
forward; the object retains the advanced state as `self._rng`.

## Decision

Add `TrialSequence.extend(n_more=None)` that **continues the stored `self._rng`
cursor** to append more trials to all three schedules in place, then advances and
stores the new cursor:

- It appends whole `block_size` blocks (rounding `n_more` up), so every appended
  segment keeps the same exact per-block L/R (and hi/lo) balance as the initial
  plan; `None` adds one block (what the button sends).
- Because it threads the *existing* PRNG state, the already-generated prefix is
  left byte-identical (append-only), and the result is deterministic per seed +
  the sequence of `extend` calls made.
- The one relaxation: the anti-bias `max_same_run` cap is enforced only *within*
  each appended block, not across the join with the existing tail (the generator
  validates each block against its own prefix). Extension is an overflow/shaping
  aid, so a possible run of up to `2*max_same_run` straddling one join is
  acceptable.

`Phase3Log.extend_to(sequence)` appends planned rows for the new indices only
(existing rows untouched), and the CSV header now prints `planned_trials=<len>`
alongside the original `max_trials` so the actual plan length is visible after an
extension. `Phase3ControlView._extend_trials` calls both and republishes the
snapshot; a task that had completed resumes on the next frame.

## Alternatives considered

- **Regenerate a longer sequence from the seed in place.** The choice schedule's
  prefix would survive (blocks are generated in order), but the return schedules
  are threaded *after* the choice pass, so a longer choice pass changes the PRNG
  state feeding them - every return-arm cue, including already-run trials, would
  shift. Rejected: it violates append-only.
- **Restructure generation to interleave the three schedules block-by-block** so a
  single cursor extends cleanly. Rejected: it would change the seed→schedule
  mapping for the choice schedule too and break firmware parity (ADR-0009); the
  stored-cursor continuation gets the same append-only guarantee without touching
  initial generation.
- **Draw the extension from a fresh sub-seed** (e.g. `seed ^ index`). Rejected as
  unnecessary: continuing the existing threaded cursor is already deterministic
  and reproducible, and keeps a single PRNG lineage per session.
- **End the session at `max_trials`.** Rejected: that is the behaviour the button
  exists to fix.

## Consequences

- The operator can extend a running session by a balanced block at a time; the
  rat keeps working and every already-run trial is preserved exactly.
- `cfg.max_trials` becomes a "how many were planned initially" record rather than
  the live length; `len(sequence)` / the `planned_trials` header field are the
  source of truth for the actual plan length.
- A single same-side run of up to `2*max_same_run` can straddle an extension join
  (not the within-plan guarantee). If that ever matters, revisit by validating the
  first appended block against the existing tail.
- Reproducing a session's full plan from its seed now also requires knowing the
  extension calls made (how many blocks were added), which the data file's
  `planned_trials` records.

## Evidence / links

- Builds on ADR-0009 (balanced anti-bias generator + firmware parity), ADR-0047
  (per-arm return schedules + session data file), and ADR-0058 (the button that
  drives this).
- Tests: `test_trial_sequence.TestExtend` (append-only prefix invariant, whole-
  block rounding, determinism) and `test_phase3_log.TestExtendAndAbandon`
  (`extend_to` append-only + `planned_trials` header). Full host suite green
  (340 tests, 17 skipped).
- Code refs: `TrialSequence.extend` (`host/trial_sequence.py`),
  `Phase3Log.extend_to` (`host/phase3_log.py`),
  `Phase3ControlView._extend_trials` (`host/phase3_controller.py`).
