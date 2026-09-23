# ADR 0190: Cap a Phase 3 session at N scored trials or M minutes (soft stop)

- **Status:** Accepted
- **Date:** 2026-09-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/phase3.json` (new `session_cap` section),
  `host/tmaze/core/phase3/session_cap.py` (new pure core),
  `host/tmaze/core/phase3/phase3_config.py` (`to_session_cap_config`, section
  + defaults + validation), `host/tmaze/core/phase3/phase3_task.py`
  (`awaiting_return` property), `host/tmaze/core/phase3/phase3_log.py`
  (`session_cap` header line), `host/tmaze/core/session/session_notes.py`
  (`AUTO_CAP_REACHED`), `host/tmaze/core/session/session_summary.py` (v7 -> v8,
  `session_cap` block), `host/tmaze/ui/phase3_controller.py` (boundary check,
  frozen branch, banner, seams), `host/tmaze/ui/session_controller.py`
  (manifest provenance + summary block); tests `test_session_cap`,
  `test_phase3_config`, `test_session_phase3_wiring`, `test_session_summary`
- **Supersedes / Superseded by:** extends ADR-0150 (auto-extend; which
  explicitly deferred "a configurable hard cap"), ADR-0059, ADR-0060; paired
  with ADR-0191 (per-animal reward volume); refined by ADR-0198 (the cap anchor
  is taken once per arm and survives a recording restart - see there for the
  points-1-and-4 amendments)

## Context

A Phase 3 session had no end condition of its own. `sequence.max_trials` is only
the initial plan size - ADR-0150 auto-extends the plan indefinitely - and the
recording ends when the operator clicks Stop, in practice when the rat
disengages after roughly an hour (130-230 trials at 2-5 trials/min).

Looking at the two weeks of sessions before this change (09-09 .. 09-18), the
three rats ran at 46-73 % correct and earned 4-11 mL per session at the
465-470 ms pulse (~90 uL per correct trial). Two facts about those numbers
motivated the cap:

- **Chance pays the whole ration.** An alternating or side-biased rat is right
  half the time. At 130+ trials, 50 % x 90 uL already exceeds the 6-9 mL a rat
  drinks in a day, so cue-following is never *needed* to be sated. Rat 600
  showed cue-following stints mid-week and collapsed to alternation on 09-18;
  the operator's Stop note that day reads "Already drank 7.1 ml and it just did
  alternation today" - a water ceiling was already being enforced by hand.
- **The last third of every session runs sated.** Because a session ends on
  disengagement, its final trials are run by an animal that no longer wants the
  reward - exactly where a cheap strategy like alternation is most attractive.

The cap fixes the number of *opportunities* per session so that earned water
becomes a function of strategy. Sizing the reward so that chance earns clearly
less than a day's need is the paired ADR-0191.

## Decision

A new `session_cap` section in `phase3.json`
(`{enabled: true, max_trials: 120, max_minutes: 45, water_at_chance_fraction: 0.5}`),
resolved into a frozen `SessionCapConfig` and evaluated by the pure
`session_cap.evaluate(completed, elapsed_s, cfg)`. The cap is reached when
EITHER limit is met; trials win a tie. Concretely:

1. **What is counted.** `max_trials` counts SCORED trials (correct + incorrect;
   UNCUED trips and abandoned trials don't count) since **Session Start**;
   `max_minutes` is wall-clock minutes since the same anchor (`session_t0.mono`,
   ADR-0124). Trials run while armed but before Start don't count, and an arm
   with no session at all is uncapped (there is no clock to count from).
2. **Trial boundary only.** The controller evaluates the cap only when no cue is
   sounding, no scored choice is awaiting its return lap
   (`Phase3Task.awaiting_return`), and the rat is not out on a genuine trial
   trip (`task_frame.trial_trip`). A trial in flight when the clock runs out
   therefore finishes and is rewarded normally - the rat heard the cue;
   withholding its reward would be an unearned punishment. The trial limit is
   naturally met on the advance that completes the Nth trial.
3. **Soft stop.** On reaching the cap the controller silences any tone, resets
   the cue gate, records `{reason, iso, trial_index, completed, elapsed_s}`,
   posts one `auto:cap_reached` note ("session cap reached (120 trials): 120
   trials in 33:40 - earned 5.50 mL - remove rat, Stop session"), and from the
   next tick takes the same frozen branch as the operator Pause (no cue, no
   score, no reward, auto-extend skipped). The banner reads `CAP REACHED (...)
   - remove rat, Stop session`. The recording is NOT auto-closed: Session
   Start/Stop stays operator-owned (ADR-0060) so the video keeps running until
   the rat is out of the maze, and Stop writes the summary as usual.
4. **The cap survives Stop.** Phase 3 stays armed across Stop (ADR-0060), so a
   capped rat still in the maze must not be cued again the moment the operator
   clicks Stop. `_cap_reached` clears only on a fresh arm, a Restart, or the
   next Session Start (which also re-takes the trial-count base).
5. **Provenance.** The cap is stamped into the trial-CSV header
   (`# session_cap session_cap_enabled= session_cap_trials= session_cap_minutes=`;
   additive tokens, no `format_version` bump), the manifest `provenance`
   (`session_cap`), and the summary (`task_params.session_cap` as archived plus a
   top-level `session_cap` block with `reached` = None or the hit; summary
   schema v7 -> v8, text line `Cap: 120 trials / 45:00 min   REACHED on trials:
   120 trials in 33:40`).
6. **Where it is edited.** `session_cap` is a scientific setting like
   `sequence`: hand-edited in `phase3.json`, shown read-only in the tab's
   sequence readout (`cap 120 trials / 45 min`), validated on load/Save even
   when disabled. The live banner shows a countdown (`cap: 87/120 · 22:10/45:00`)
   while recording.

Worked numbers for the cohort at 120 trials: rat 601 (3.3-4.8 trials/min) hits
the trial limit in 25-36 min; rat 600 (2.7-4.7/min) in 26-45 min; rat 602
(~2/min) is usually ended by the 45-min clock near 90 trials. That asymmetry was
accepted knowingly (the clock is a backstop, and `max_minutes` is one number in
the file); per-animal caps are future work.

## Alternatives considered

- **Hard cap: auto-Stop the session.** Would cut the video and finalise the
  bundle while the rat is still in the maze, and add a new seam from the Phase 3
  actuation thread into the Session controller. The soft stop reuses the Pause
  path and keeps ADR-0060's ownership.
- **A water-volume (mL) cap.** The operator's hand rule ("stop at ~7 mL"), but
  it does the opposite of the goal: an alternating rat simply takes longer to
  reach the same water. Fixing *opportunities* is what makes strategy matter.
- **Lower the reward volume only.** Shrinks what chance earns per hour, but the
  uncapped rat then runs more trials for longer, and the last third is still run
  sated. The volume lever is used *with* the cap (ADR-0191), not instead of it.
- **Evaluate the clock mid-trial.** Rejected: a cue that sounded would go
  unrewarded on a correct choice. Boundary-only costs at most one extra trial.
- **Count from arm (the stats scope, FW-0028).** Rejected: the clock has to be
  the Session Start anchor (the only shared instant), so the trial count starts
  there too; otherwise the two limits would measure different windows.
- **GUI-editable cap fields.** Deferred (FW-0070): the cap is a per-protocol
  decision, not a per-session knob, so it belongs with the hand-edited
  `sequence` params for now.
- **Per-animal cap overrides in `animals.json`.** Deferred (FW-0072): the
  per-animal lever chosen for this cohort is the reward volume (ADR-0191); if
  602's clock-bound sessions matter, a per-animal `max_trials`/`max_minutes`
  can follow the ADR-0186 override pattern.

## Consequences

- Sessions end while the rat is still motivated, and a chance-level strategy
  earns a predictable fraction of the day's water (ADR-0191 sets the fraction).
  The shortfall is supplemented in the home cage, several hours later, so the
  earned/free split is felt at session time.
- After the cap the operator has one job: remove the rat and click Stop. A
  further trip in the maze is silent and unscored (not recorded as UNCUED
  either - the frozen branch runs before the task fold).
- `sequence.max_trials` keeps its ADR-0150 meaning (initial plan size); the cap
  is a separate, smaller number. Auto-extend never fires past the cap.
- A pre-cap bundle has no `session_cap` header line / summary block; readers
  treat absence as "uncapped".
- The stats panel's "since Phase 3 arm" scope (FW-0028) and the cap's "since
  Session Start" window differ by the arm-before-Start trials; both are
  recorded (`pre_start_trials`) so a reader can reconcile.
- A time-cap hit is at most one trial late (the in-flight trial finishes).

## Evidence / links

- Session summaries 2026-09-09 .. 09-18 under the phase3 recordings root
  (trial counts, accuracy, `estimated_water`, `trials_per_min`); the 09-18 Stop
  note for rat 600 quoted above.
- `host/reward_calibration.csv` (center, 10 % sucrose): 470 ms ~ 91 uL.
- Code: `session_cap.evaluate` / `SessionCapConfig`;
  `Phase3ControlView._process_frame` (boundary check + frozen branch),
  `_on_cap_reached`, `_publish_status`, `set_session_context`
  (`_cap_base_done`), `cap_reached` / `resolved_session_cap` seams;
  `SessionControlView._session_cap_block`, `_start_session` provenance.
- Tests: `tests/test_session_cap.py` (arithmetic), `tests/test_phase3_config.py`
  (section + validation), `tests/test_session_phase3_wiring.py` (trial cap,
  time cap lets the in-flight trial finish, counts from Start / needs a session,
  survives Stop, disabled), `tests/test_session_summary.py` (v8 block + text).
- Related: ADR-0150 (deferred this cap), ADR-0059 (+Trials), ADR-0060
  (Start/Stop ownership), ADR-0124 (`session_t0`), ADR-0111 (auto notes),
  ADR-0191 (per-animal reward volume), FW-0028 (summary scope).
