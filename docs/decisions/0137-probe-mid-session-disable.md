# ADR 0137: Probe blocks can be disabled (and re-enabled) mid-session

- **Status:** Accepted
- **Date:** 2026-08-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/probe_blocks.py` (runtime arm gate:
  `set_armed`/`armed`/`ever_disarmed`, `on_scored` gated on armed, `reset` re-arms),
  `trial_sequence.py` (`set_probe_armed` + `probe_armed`/`probe_ever_disarmed`),
  `phase3_task.py` (`set_probe_armed` passthrough), `phase3_log.py`
  (`Phase3StateSnapshot.probe_armed`; a `probe_blocks` header line + a manual-disable
  caveat note), `host/tmaze/core/session/session_notes.py`
  (`auto:probe_disabled` / `auto:probe_enabled` tags); `host/tmaze/ui/`
  (`phase3_controller.py` `toggle_probe_armed` + `probe_toggle` command + `[PROBE OFF]`
  status tag, `trial_control_bar.py` On/Off toggle, `experiment_control.py`,
  `tooltip_text.py`); `host/tmaze/analysis/bundle_reader.py` (a `notes` frame + the
  `probe_planned`/`probe_disabled` session flags); `host/tests/`
  (`test_phase3_probe_blocks.py`, `test_phase3_log.py`, `test_analysis_bundle_reader.py`)
- **Supersedes / Superseded by:** extends ADR-0134 (correct-choice-in-window trigger)
  and ADR-0115 (why probes exist / the reward gate). Nothing superseded.

## Context

Under ADR-0134 the decision to run probes is fixed at session start: `phase3.json`
`sequence.probe_blocks` arms one or more windows, and the only runtime control is
"End probe block", which aborts a probe **that is already running**. There was no way
to decide, after a session had started, that no probe should fire at all - e.g. the
animal is behaving unusually, or the experimenter wants a clean reinforced session
after seeing the early trials. Editing `phase3.json` mid-session is not an option
(config is loaded once), and the abort button can't help before a probe has triggered.

A second, subtler problem: even if probes are simply "not wanted today", the archived
config still says `enabled: true` with windows. A reader looking at the config (or the
task CSV, which did not mention probes at all) could not tell a **deliberately
disabled** session apart from one where a planned window simply never had a qualifying
correct trial. The manual decision has to be recoverable from the saved data.

## Decision

Add a runtime **arm** gate to the probe feature, controllable mid-session, and make
the decision self-describing in the saved bundle.

1. **Runtime arm flag, separate from `enabled`.** `ProbeBlockConfig.enabled` stays the
   load-time build switch (no probe machinery when False). `ProbeBlockController` gains
   a runtime `_armed` (default True) that `on_scored` now also requires, plus
   `set_armed(bool)`, an `armed` property, and an `ever_disarmed` property.
   `reset()` (Restart) re-arms and clears `ever_disarmed` - a Restart is a fresh
   session, so a prior disable does not carry over.

2. **"Off" means no probe, cleanly.** Because the GUI exposes this as a single
   persistent On/Off toggle (see 4), `set_armed(False)` both **disarms future window
   triggers** and **aborts a running block** (`_manual_end`), and records
   `ever_disarmed`. `set_armed(True)` re-arms: already-fired windows stay fired, but
   any remaining un-fired window can trigger again.

3. **Plumbing.** `TrialSequence.set_probe_armed` / `probe_armed` /
   `probe_ever_disarmed` delegate to the controller; `Phase3Task.set_probe_armed`
   passes through; `Phase3StateSnapshot` carries `probe_armed` for the UI.
   `Phase3ControlView.toggle_probe_armed()` enqueues a `probe_toggle` command resolved
   on the actuation thread from the CURRENT armed state (not the possibly-stale
   snapshot), so a double-press can't desync.

4. **GUI: repurpose the one probe button into an On/Off toggle.** The former abort-only
   "End probe block" control-bar button becomes a persistent toggle, live whenever
   probes were built for the run (`probe_enabled`), labelled "Disable probes" while
   armed and "Enable probes" once off. The status line shows `[PROBE OFF]` when
   disarmed (mirroring `[PROBE ACTIVE]`). Turning off subsumes the old abort (off also
   aborts a running block), so a single button covers both needs.

5. **Discoverability in the saved data.** Turning probes off/on emits a timestamped
   auto note (`auto:probe_disabled` / `auto:probe_enabled`) on the session timeline,
   so it lands in `summary/notes_<id>.jsonl` and is folded into
   `session_summary_<id>.json`. Additionally the task CSV header now records the probe
   plan (`# probe_blocks probe_enabled=True probe_block_len=N probe_windows=...`) and,
   when the operator disabled at any point, a `# note:` caveat that a planned window
   may not have fired. Offline, `bundle_reader` reads the notes file into a new
   `notes` frame and derives `sessions.probe_planned` / `sessions.probe_disabled`, so
   the analysis stack can tell the two cases apart without opening the notes by hand.

## Alternatives considered

- **Disable only future triggers; keep a separate abort button.** Rejected together
  with keeping two buttons: with a single repurposed toggle, "off" that left a running
  block going would strand it with no way to stop it. Folding abort into "off" keeps
  one clear control (chosen with the experimenter).
- **A second, separate on/off checkbox by the sequence settings.** Rejected: the
  control bar already has the probe button and the operator watches it during a run;
  a second control split the state across two places.
- **Record the disable only in the notes.** Rejected as insufficient: the notes are
  saved, but the config/CSV a reader (or `bundle_reader`) looks at first would still
  imply a probe was planned. Hence the CSV-header flag + the analysis `notes` frame.
- **Encode the mid-session disable back into the archived `phase3.json` snapshot.**
  Rejected: the config snapshot is meant to be the definitions that *shaped* the run
  (ADR-0072), not a runtime event log; the timeline (notes) is the right home for a
  timestamped operator action, with the CSV header carrying the boolean summary.

## Consequences

- The experimenter can call off probes at any time in a session and be sure nothing
  fires; a Restart brings them back (fresh session), matching the window-fired reset.
- A disabled session is unambiguous offline: `sessions.probe_disabled == True` and the
  CSV caveat line, versus `probe_planned == True, probe_disabled == False` with no
  probe rows (a window that never fired).
- The new `notes` frame surfaces every existing auto event (manual reward, debias/alt
  starts-ends, skips/backs) to analysis for free, not just probe enable/disable.
- `AnimalData` gains a fourth field, defaulted to an empty frame, so existing
  `trials`/`excursions`/`sessions` consumers (the notebooks, `rolling_metrics`) are
  unaffected. `load_bundle` now returns a 4-tuple (internal callers updated).
- No log proto bump: `probe_armed` is published-snapshot state, and the header lines
  are `#` comments the reader already tolerates (the `probe_`-prefixed tokens are new
  but additive; the caveat is a skipped `# note:` line).

## Evidence / links

- `host/tests/test_phase3_probe_blocks.py`: a disarmed controller never fires in a
  window; re-arm lets a later window fire; `set_armed(False)` aborts a running block
  and sets `ever_disarmed`; `reset()` re-arms and clears it; `TrialSequence` /
  `Phase3Task` passthrough gate the trigger.
- `host/tests/test_phase3_log.py`: the snapshot carries `probe_armed`; the CSV header
  shows the `probe_blocks` plan line and the manual-disable caveat only after a disarm.
- `host/tests/test_analysis_bundle_reader.py`: an `AUTO_PROBE_DISABLED` note yields a
  non-empty `notes` frame and `sessions.probe_disabled == True`; a planned-but-not-
  disabled bundle reads `probe_planned == True, probe_disabled == False`; a bundle with
  no notes file still loads (empty `notes` frame).
- Extends ADR-0134 (trigger) and ADR-0115 (probe rationale / reward gate); builds on
  ADR-0074 (timestamped notes), ADR-0071 (session summary), ADR-0072 (config
  snapshot), ADR-0050 (published snapshot), ADR-0083 (pure-core + thin shell),
  ADR-0113/0131 (bundle discovery / attempted-trials frames).
