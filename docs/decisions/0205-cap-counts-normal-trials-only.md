# ADR 0205: The session trial cap counts NORMAL trials only; probe blocks run on top

- **Status:** Accepted
- **Date:** 2026-09-25
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/phase3_controller.py` (new `_cap_scored`
  counter: set up in `__init__` / `_arm_session`, incremented in the
  `_process_frame` scoring fold, decremented on a hand-edit re-run; used for the
  cap anchor in `set_session_context`, the cap gate, and the live countdown;
  "normal trials" wording in the cap note, status banner and read-only Task-tab
  line); `host/tmaze/core/phase3/phase3_log.py` (`SupersedeResult.probe_block`);
  tests `test_session_phase3_wiring`
- **Supersedes / Superseded by:** refines ADR-0190 (what `max_trials`
  counts); builds on ADR-0198 (cap anchor), ADR-0115 / ADR-0134 (probe blocks)

## Context

ADR-0190 caps a Phase 3 session at `max_trials` (120) *scored* trials - any
correct or incorrect trial. An unrewarded **probe block** (ADR-0115 / ADR-0134)
is one correct-choice *trigger* trial whose reward is withheld plus
`block_len` (10) forced trials, all unrewarded. Under the old rule those 11
trials used up 11 of the 120, so a rat that got a probe block had only 109
chances at reward in that session, and a rat that did not had 120. The water
budget in ADR-0191 assumes the cap's trials are rewardable
(`daily_water_ml` is spread over `max_trials * 0.5` correct trials at chance),
so the probe block also quietly cut the rat's earnable water.

The operator asked (2026-09-25) that the probe block be *inserted into* the 120
normal trials rather than counted against them: the cap counts rewarded-type
trials, and the probe trials are counted separately. With intervention blocks
turned off for the past few days, almost every non-probe trial is a NORMAL one.

## Decision

1. **The trial limit counts NORMAL scored trials only.** A new counter
   `_cap_scored` goes up for each trial scored correct or incorrect whose
   `dec.probe` is False. `dec.probe` is True for the whole probe block, the
   trigger included, so the full unrewarded run (1 + 10 trials) sits **on top**
   of the 120; a session with one probe block runs about 131 scored trials.
2. **Scope is probe blocks only.** Side-bias (debias) and alternation-
   correction block trials are still rewarded trials of the task and still
   count. The operator chose this scope; incorrect normal trials also count
   (the rat had its chance at reward).
3. **Same anchoring as before.** The anchor taken at the first Session Start
   (ADR-0198) is now `_cap_base_done = _cap_scored`, and the cap gate and the
   live countdown both use `_cap_scored - _cap_base_done`. The time limit is
   unchanged: probe trials still take wall-clock time.
4. **Hand-edit re-runs stay consistent.** Re-running a scored trial from the
   table (ADR-0119) un-counts it. `SupersedeResult` gains `probe_block` (True
   for any probe-block trial, trigger included; the existing `probe` field
   excludes the trigger because it feeds probe accuracy), and the counter is
   decremented only when `probe_block` is False, mirroring how it was counted.
5. **Wording.** The cap note, the status banner and the read-only Task-tab
   line now say "normal trials" so the operator is not surprised when the
   total trial count passes 120. The Rig-view banner (ADR-0192) stays short and
   unchanged.

## Alternatives considered

- **Keep counting every scored trial** (the ADR-0190 rule). Simple, but a
  probe block takes reward chances and water away from exactly the sessions
  that carry the probe data. Rejected by the operator.
- **Count only rewarded trials** (correct ones). Would make session length
  depend on accuracy - a struggling rat would run far past 120 trials.
  Rejected; the operator chose "normal scored" (correct + incorrect).
- **Exclude every intervention-block trial too** (debias, alternation). Those
  trials are rewarded and are part of the task the rat is learning; excluding
  them would lengthen sessions for the rats that need correction most.
  Rejected for now (operator chose probe-only).
- **Exclude only the 10 forced trials, count the trigger.** The trigger's
  reward is withheld too, so it is not a rewarded chance either. Rejected.

## Consequences

- A session with a probe block runs about `max_trials + block_len + 1`
  scored trials (131 with today's settings) before the trial cap fires. The
  45-min time cap still applies and may now be the one that fires first.
- The countdown and the cap note count normal trials, so they no longer equal
  `correct + incorrect` once a probe block has run. The summary's trial totals
  are unchanged (they still count everything).
- No on-disk format change: `_cap_scored` is live state only. The recorded
  `session_cap.reached.completed` now means "normal trials", which is what the
  note text says; older bundles counted all scored trials.

## Evidence / links

- Code: `Phase3ControlView._cap_scored` (`__init__`, `_arm_session`,
  `_process_frame` scoring fold and cap gate, `_supersede_trial` undo path,
  `set_session_context`, `_publish_status`), `SupersedeResult.probe_block`.
- Tests: `tests/test_session_phase3_wiring.py`
  (`test_probe_block_runs_on_top_of_the_trial_cap`; updated wording check in
  `test_trial_cap_stops_cueing_and_scoring_and_is_recorded`).
- Related: ADR-0190 (session cap), ADR-0191 (water budget), ADR-0198 (cap
  anchor), ADR-0115 / ADR-0134 (probe blocks), ADR-0119 (hand-edit re-run).
