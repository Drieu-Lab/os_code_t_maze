# ADR 0058: Rig-view trial-control buttons via an actuation-thread command channel

- **Status:** Accepted
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/trial_control_bar.py` (new), `host/phase3_controller.py`, `host/phase3_task.py`, `host/phase3_log.py`, `host/phase3_stats.py`, `host/trial_ribbon_view.py`, `host/experiment_control.py`, `host/tooltip_text.py`
- **Supersedes / Superseded by:** the Repeat/Back *data-erasure* semantics
  (`Phase3Log.reset_trial` on the edit path) are superseded by ADR-0119 (edits
  now preserve history append-only); the rest of this ADR stands.

## Context

During a host-owned Phase 3 run (docs/decisions/0046) the operator watches the
Rig view, where the trial ribbon (docs/decisions/0057) and the live-stats panel
(docs/decisions/0052) already show progress. But there was no way to *intervene*:
if the rat abandons a trial, pokes the wrong way, or the operator wants to pause,
the only recourse was disarming (which cuts the tone and restores firmware
autonomy) and re-arming from scratch. The operator asked for buttons on the Rig
view to abandon the current trial (stop the cues, advance, and wait for the rat
to get back to the hub before the next trial starts), plus a few related
controls.

The wrinkle is ownership. All Phase 3 task state - the trial index, the
`TrialSequence`, and the `Phase3Log` table - is owned by the controller's single
**actuation thread** (the ADR-0043 skeleton in `vision_actuator.py`); the Tk
thread only hands it work through a bounded queue and reads an atomic snapshot. A
button handler runs on the Tk thread, so it must not touch task state directly.

## Decision

Add a small **command channel** to the existing actuation queue and a thin Tk
widget that drives it.

- The queue already carries `("frame", …)` and `("lick", …)` items; add
  `("cmd", name)`. `Phase3ControlView._process_act_item` routes `"cmd"` to a new
  `_process_command`, which runs on the actuation thread and is therefore the
  sole mutator of the task cores - the same guarantee frames and licks rely on.
- Public Tk-thread seams enqueue commands: `skip_current_trial`,
  `repeat_current_trial`, `step_back_trial`, `extend_trials`, `restart_session`.
  Pause is a plain Tk-thread bool (`set_paused` / `toggle_paused` / `is_paused`)
  folded into the atomic `_Phase3Snapshot` as `paused`, since it is a steady
  state, not a one-shot event.
- The six buttons live in a new reusable widget, `trial_control_bar.TrialControlBar`
  (module contract `widget()`/`apply()` no-op/`close()`), a vertical column that
  self-polls the published `Phase3StateSnapshot` (ADR-0050) to enable/disable
  buttons and switch Pause↔Resume. It owns no task state; each button calls a
  callback wired to the live controller through the `phase3_ref` holder.

Command semantics (all cut any sounding tone and reset the per-trip cue/gate
state via `_silence_and_rearm`, so the next trial is "pending start" - its choice
cue only re-arms on a fresh hub→up-stem run):

- **Skip** - `Phase3Log.trial_abandoned` marks the current row with a distinct
  `ABANDONED` outcome, then `Phase3Task.abandon_current` advances the index.
- **Repeat** - `Phase3Log.reset_trial` clears the current row's live fields so it
  records cleanly, then `Phase3Task.repeat_current` stays on the same index.
- **Back** - `Phase3Task.step_back` decrements the index (floored at 0), then
  `reset_trial` clears the row it lands on for a clean re-run.
- **Restart** - re-runs `_arm_session` from the current seed into a *fresh* data
  file (an `_r<n>` restart tag preserves the aborted run's file); confirmed by a
  dialog first.
- **Pause** - the actuation thread silences the tone and freezes progress while
  `paused`, without disarming.

`ABANDONED` is a new outcome sentinel in `phase3_task.py`; `phase3_stats.compute`
counts a trial as "completed" only when its outcome is `CORRECT`/`INCORRECT`, so
a skip never dilutes accuracy or reward rate, and the ribbon draws it as a
neutral "SKIP" cell.

Placement: the Rig-view right column is split so the ribbon + live-stats stack in
a left sub-column and the button bar fills the empty area to their right (the
operator pointed at that dead space, which spans both panels).

## Alternatives considered

- **Mutate task state from the button handler (Tk thread).** Rejected: the task
  cores are single-threaded by design and mutated only by the actuation thread
  (ADR-0043); a Tk-thread write would race the frame/lick path and need locks on
  the hot path.
- **Drive the buttons off firmware serial lines.** Rejected: these are host-side
  task decisions (the firmware is a dumb actuator in Phase 3, ADR-0046); there is
  no firmware event to hang them on.
- **Put the buttons inside `TrialRibbonView`.** Rejected: the operator asked for
  the space that spans *both* the ribbon and the stats panels; a separate
  self-polling bar keeps the shared ribbon widget unchanged and lets the column
  layout own the placement.
- **Reuse `outcome` values (mark skips INCORRECT) instead of a new `ABANDONED`.**
  Rejected: it would corrupt accuracy/strategy stats and mislead analysis; a skip
  is not a wrong choice.

## Consequences

- The operator can steer a run (skip / repeat / back / extend / restart / pause)
  from the Rig view without disarming, and the abandoned trials are recorded
  distinctly rather than lost or miscounted.
- One more `("cmd", …)` item type on the shared queue; the actuation thread stays
  the sole task-state mutator, so no new locking.
- A second self-poll runs on the Rig view (the button bar, alongside the ribbon's
  pull-mode poll); it is a cheap 150 ms enable/disable pass and is cancelled in
  `close()`.
- Repeat/Back are destructive to the affected trial's row (they clear it for a
  clean re-run); Restart discards the current run's progress (its file is kept via
  the `_r<n>` tag) and is confirm-gated.
- Sequence *extension* is its own decision - see docs/decisions/0059.

## Evidence / links

- Builds on ADR-0043 (actuation thread + snapshot), ADR-0046 (host-owned Phase 3),
  ADR-0050 (published snapshot), ADR-0051/0057 (ribbon), ADR-0052 (stats panel).
- New/extended pure-core tests: `test_phase3_task.TestManualControls`,
  `test_phase3_log.TestExtendAndAbandon`, `test_phase3_stats.TestAbandonedExcluded`.
  `python -m unittest discover -p "test_*.py"` from `host/` stays green (340 tests,
  17 skipped).
- Code refs: `TrialControlBar` (`host/trial_control_bar.py`),
  `Phase3ControlView._process_command` / the operator-button seams
  (`host/phase3_controller.py`), and the Rig-view right-column split in
  `host/experiment_control.py`.
