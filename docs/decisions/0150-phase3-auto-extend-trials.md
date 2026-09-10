# ADR 0150: Auto-extend the Phase 3 trial plan (and never emit an identity-less cue)

- **Status:** Accepted
- **Date:** 2026-09-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/phase3_controller.py`
- **Supersedes / Superseded by:** extends ADR-0059 (append-only extension)

## Context

A host-owned Phase 3 session generates a fixed number of trials up front from the
seed (`TrialConfig.max_trials`, default 200; ADR-0046/0047). ADR-0059 added a
manual "+Trials" button so an operator can append a balanced block when the rat
out-paces the plan, and assumed that once the plan is exhausted "no more cues
sound".

On the rig that assumption turned out to be wrong, and the failure was silent and
bad for the animal. Once the last planned trial completes, `Phase3Task.complete`
is true, but the choice cue is driven by the rat's *tracked position*
(`cue_gate.py`), not by trial state - so the gate keeps firing a CHOICE onset on
every excursion. At that point the controller asked the task for the trial's cue
frequency, got `None` (the index is out of range), and fell back to sending a
bare `CUE_ON,CHOICE` with no `<hz>` and no `<gain>`. That degrades three ways at
once, all observed on the bench:

- **Wrong tone.** With the firmware trial FSM off (Free Roam) `correctArm` is
  unset (`-1`), so `cueFor(-1)` returns the LOW tone - the LOW cue plays on every
  excursion, independent of the last real trial.
- **Full blast.** No `<gain>` means the firmware plays at its 100 % default,
  undoing the operator's per-cue volume calibration (ADR-0125/0148).
- **No reward / no scoring.** `Phase3Task.update` returns early when `complete`,
  so a choice into the arm the stuck cue "indicated" is neither scored nor
  rewarded.

A rat that is still working should not be silently dropped into this state.

## Decision

Two changes in the thin controller shell (`_process_frame`), no core/protocol
change:

1. **Auto-extend one balanced block, one step ahead.** After folding the task
   frame each tick, if the current trial index has reached the last planned trial
   (`self._task.index >= len(self._seq) - 1`), call the existing
   `_extend_trials()` path (`TrialSequence.extend()` + `Phase3Log.extend_to()` +
   re-render + re-publish). Because an extend grows `len(seq)` by `block_size`,
   the condition is immediately false again and re-fires only after the animal
   advances a whole block later - one extend per boundary. This keeps the plan
   exactly one step ahead, so the next CHOICE cue always has a real identity and
   cues / volume / scoring / reward continue seamlessly. It reuses ADR-0059's
   append-only, seed-deterministic mechanism unchanged, so already-run rows are
   byte-identical and per-block L/R balance is preserved.

2. **Never send an identity-less cue.** When the resolved cue frequency is `None`
   (CHOICE or RETURN), start NO cue instead of sending a bare `CUE_ON,<kind>`.
   In host-owned Phase 3 a missing frequency only ever means "no valid trial", so
   a bare command is always the degenerate full-volume fallback above. This is a
   belt-and-suspenders guard: auto-extend keeps identity valid in normal running,
   and this ensures a completed / edge-case session stays silent rather than
   blasting a wrong cue.

`max_trials` therefore becomes the *initial* plan size; a session now ends when
the operator stops it (Stop Session / disarm the gate) or via the manual trial
controls, not at a fixed trial count. The "+Trials" button stays for pre-extending
by more than one block at a time.

## Alternatives considered

- **Clean stop at `max_trials` (fix only the cue guard).** Suppress the
  degenerate cue and require the operator to click "+Trials" to continue.
  Rejected: it still stops a working rat until the operator notices, which is the
  exact ergonomics ADR-0059's button already tried (and failed) to solve; the
  operator asked for the rat to keep running.
- **Auto-extend with a configurable hard cap.** A `phase3.json` absolute-max knob
  that clean-stops past it. Deferred as unnecessary for now (no scientific need
  for a hard cap surfaced); the cue guard already makes a stop safe, so a cap can
  be added later without redoing this.
- **Extend a whole block only when `complete` is reached.** Rejected: the cue for
  the trial that tips into completion would already have fired identity-less;
  extending one step *ahead* (on the last planned trial) closes that gap.
- **Sub-block extension to avoid trailing empty rows.** Rejected: ADR-0059
  requires whole blocks for exact L/R (and hi/lo) balance. One-block-ahead already
  caps trailing empty planned rows at `block_size - 1`, the same footprint as a
  fixed plan that ends mid-block.

## Consequences

- A rat that outpaces the plan keeps running with correct, calibrated cues,
  scoring, and reward; the operator no longer has to watch for the boundary.
- Every generated trial is still written to the CSV (planned columns filled, live
  columns blank for un-run trials, as before), so an auto-extended session ends
  with at most `block_size - 1` empty planned rows - unchanged from today.
- `cfg.max_trials` is now only the initial plan length; `len(sequence)` /
  the `planned_trials` header field remain the source of truth for the live
  length (ADR-0059), and reproducing a session's full plan from the seed requires
  knowing how many blocks were appended (recorded as `planned_trials`).
- A completed / disarmed session is silent instead of emitting a full-volume
  fallback cue.

## Evidence / links

- Extends ADR-0059 (append-only, seed-deterministic extension) and its
  `TrialSequence.extend` / `Phase3Log.extend_to`; builds on ADR-0046/0047
  (host-owned Phase 3, cue identity), ADR-0044 (`CUE_ON,<kind>[,<hz>[,<gain>]]`),
  and ADR-0125/0148 (per-cue volume / frequency calibration).
- Firmware fallback that produced the stuck cue: `cueFor(int8_t correctArm, ...)`
  in `firmware/cue-guided-task/task_core.h` (`-1` arm -> low tone) and
  `handleCueOn` in `firmware/cue-guided-task/cue-guided-task.ino`.
- Code refs: `Phase3ControlView._process_frame` and `_extend_trials`
  (`host/tmaze/ui/phase3_controller.py`).
- Tests: `host/tests/test_session_phase3_wiring.py` (plan grows past the
  boundary; no bare `CUE_ON` sent; scoring/reward resume) plus the existing
  `test_trial_sequence.TestExtend` / `test_phase3_log` extension coverage.
