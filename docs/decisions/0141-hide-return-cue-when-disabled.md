# ADR 0141: Hide the return-cue readout when the return cue is disabled

- **Status:** Accepted
- **Date:** 2026-08-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_log.py`,
  `host/tmaze/core/phase3/phase3_ribbon.py`,
  `host/tmaze/ui/trial_ribbon_view.py`, `host/tmaze/ui/phase3_controller.py`
- **Supersedes / Superseded by:** —

## Context

Every planned Phase 3 trial carries two return-arm cues
(`return_cue_left` / `return_cue_right`), and the trial ribbon strip and the
"Planned trials" list both render them as a `ret L:.. R:..` line
(docs/decisions/0047, 0051, 0135). But whether the return cue is actually PLAYED
is a separate operator knob, `cue.return_enabled` (`CueParams.return_enabled`),
which currently defaults to `false` in `host/phase3.json`.

The planned return-cue columns are always generated and recorded (they belong to
the trial's identity), so the readout showed a HIGH/LOW value for a cue the rig
never plays - misleading the operator into thinking a return cue was in effect.
The on/off flag was not carried into the published `Phase3StateSnapshot`
(docs/decisions/0050) that both displays read, so the views had no way to know.

## Decision

Thread `return_enabled` from config into the published snapshot and suppress the
return-cue readout when it is off, in both displays:

- `Phase3StateSnapshot` gains a `return_enabled: bool = True` field; `Phase3Log`
  takes a `return_enabled` constructor arg (default `True`) and stamps it into
  each snapshot. The controller passes `snap.params.return_enabled` at arm time.
- The pure `RibbonRow` gains `return_enabled` (set in `_make_row` from the
  snapshot), keeping the "what to show" decision in the headless-testable core.
- `TrialRibbonView._fill_cell` drops the `ret` label with `pack_forget()` when
  the flag is off (so the cell shrinks) and re-shows it before the outcome label
  when on. `phase3_controller._seq_line` omits the `ret ...` segment likewise.

Default `True` preserves today's behavior for any caller that doesn't set it, so
existing snapshots/tests are unaffected.

## Alternatives considered

- **Blank the line's text instead of removing it** — leaves an empty row that
  keeps the cell tall for no information; `pack_forget` reads cleaner.
- **Tell the view directly at construction rather than via the snapshot** — the
  Rig-view ribbon (pull mode, docs/decisions/0135) reads only the published
  snapshot, so routing through the snapshot keeps both ribbons consistent from
  one source of truth.
- **Leave the readout as-is** — rejected: it advertises a cue that is not played.

## Consequences

The operator no longer sees a return-cue value when none is played, and the
Phase 3 tab, the Rig view, and the planned-trials list stay in agreement. The
CSV log and the planned schedule are unchanged (return-cue columns are still
generated and recorded); this is display suppression only. `progress_signature`
is left alone because `return_enabled` is fixed for a session (a settings change
re-arms and rebuilds the snapshot). Revisit if the return cue ever becomes
togglable mid-session, which would require the signature to react to it.

## Evidence / links

- Config knob: `host/phase3.json` `cue.return_enabled` (currently `false`).
- Test: `host/tests/test_phase3_ribbon.py::TestReturnEnabled`.
- Precedent for suppressing a misleading readout: docs/decisions/0094
  (reward-incorrect suppresses the reward-rate line).
