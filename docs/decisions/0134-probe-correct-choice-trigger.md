# ADR 0134: Probe blocks trigger on a correct choice inside a context window

- **Status:** Accepted
- **Date:** 2026-08-27
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/probe_blocks.py` (rewritten controller) +
  `host/tests/test_phase3_probe_blocks.py`;
  `host/tmaze/core/phase3/trial_sequence.py` (`probe_trigger_on_score`,
  `advance()` in `_commit_one`, `probe_trigger` in `trial_meta`, manual start
  dropped), `phase3_task.py` (trigger at scoring + reward gate,
  `Phase3Decision.probe_trigger`), `phase3_config.py`
  (`sequence.probe_blocks.windows`), `phase3_log.py` (a `probe_trigger` column,
  proto 9->10), `phase3_stats.py` / `phase3_traces.py` (exclude the trigger from
  probe accuracy); `host/tmaze/ui/` shells (`phase3_controller.py`,
  `trial_control_bar.py`, `experiment_control.py`, `tooltip_text.py`);
  `host/tmaze/analysis/` (`bundle_reader.py`, `choice_signals.py`,
  `rolling_metrics.py`); `host/phase3.json`; `PROGRAMS.md`, `docs/glossary.md`,
  `docs/future-work.md`
- **Supersedes / Superseded by:** supersedes the **trigger mechanism** of ADR-0115
  (manual, boundary-aligned start). The rest of ADR-0115 (why probes exist, the
  insert-on-top model, the reward gate, the `probe` flag partition) still holds.

## Context

ADR-0115 added unrewarded **probe** blocks - short runs where the cue plays and
the choice is scored but no water is given - to read out latent cue->action
knowledge separately from reward-driven performance (Drieu et al. 2025). It shipped
them **manual only**: the operator pressed a "Start probe block" button and the
block began at the next counterbalanced boundary. That was deliberate scaffolding
for piloting *where* a probe is worth placing.

Piloting settled a placement rule the operator now wants automated. The scientific
target is the moment the animal DEMONSTRATES it knows the map inside a chosen phase
of the session: rather than probing at a fixed clock position, start the probe the
first time the rat makes a CORRECT choice inside a configured trial-number window.
That correct choice is itself withheld (turned into the first unrewarded trial),
and an unrewarded block follows - the design used in Duvelle et al. 2024 (*Current
Biology* S0960-9822(24)00457-3), where a block of unrewarded trials follows the
triggering trial.

Two rig facts still shape the design (unchanged from ADR-0115): the schedule is a
seed-deterministic counterbalanced L/R spine that interventions INSERT on top of
without consuming (ADR-0047/0090/0107), and reward is a host decision per scored
choice in `phase3_task.update`. The new fact is that the trigger needs the trial's
OUTCOME, which is only known at SCORING time - after the trial was committed.

## Decision

Replace the manual/boundary trigger with an automatic **correct-choice-in-window**
trigger, evaluated at scoring time. Keep everything else about probes from ADR-0115.

1. **Config: context windows.** `sequence.probe_blocks` gains
   `windows: list[[start, end]]` - inclusive, 1-based overall-trial-number ranges
   (e.g. `[[40,60],[120,160]]`), validated `1 <= start <= end`. Each window is
   auto-armed for the session and fires **at most one** probe. `block_len` (default
   **10**) and `enabled` are unchanged; the manual start knob is gone.

2. **Trigger = first correct choice on a NORMAL trial inside an un-fired window.**
   `ProbeBlockController.on_scored(trial_no, correct, intervention_active)` fires iff
   enabled, no probe already running, the choice was correct, it was NOT an
   intervention-block trial, and `trial_no` lands in a window not yet fired. An
   incorrect choice does not trigger; a correct choice during an intervention block
   does not trigger. So windows can be generous - a run of wrong choices, or a
   window spent entirely in an intervention, simply means no probe fires there.

3. **The trigger trial is unrewarded and marked `probe_trigger`.** It is an ordinary
   spine trial the animal already chose (it consumed the spine); at scoring
   `TrialSequence.probe_trigger_on_score` reclassifies it as a probe trial
   (`probe` + `probe_trigger`) and `phase3_task` re-reads its meta and withholds
   reward. Being correct BY CONSTRUCTION, the trigger is the gating event, not part
   of the *measured* block: stats, live traces, and offline analysis exclude
   `probe_trigger` trials from probe accuracy (but still count them as unrewarded
   probe trials - shaded, reward withheld).

4. **A fresh `block_len` counterbalanced block FOLLOWS the trigger.** Once fired, the
   controller builds an internally 5/5-balanced `block_len` plan (same balanced
   generator + seed+salt+ordinal as ADR-0115) and `_commit_one` hands out those
   forced sides via `advance()` - inserted on top of the spine, priority over the
   intervention stack, exactly as before. Reward is suppressed on the trigger AND all
   `block_len` following trials, so the unrewarded run is `block_len + 1` (default
   **11**) trials. The measured latent accuracy is over the `block_len` block.

5. **No manual start; manual abort kept.** The "Start probe block" button is removed
   (the trigger is automatic). A single **"End probe block"** button remains to abort
   a running probe early; it is live only while a probe is ACTIVE. The `PENDING`
   probe state is gone. `auto:probe_start` is emitted from the trigger (marking where
   the probe actually landed) and `auto:probe_end` on a manual abort.

6. **Log + reproducibility.** A `probe_trigger` column is appended to `phase3_log`
   (proto 9->10; back-fills to "" for older CSVs). The *within-block* 5/5 pattern of
   the k-th probe is still seed-deterministic, but PLACEMENT is not reproducible from
   the seed - it depends on when the animal is first correct in a window; the CSV
   records where each probe landed (as under ADR-0115, now window- rather than
   operator-driven).

## Alternatives considered

- **Keep the manual button, add auto as a second path.** Rejected: the operator
  finished piloting and wants the window rule to be the behaviour; two live start
  paths add state (`PENDING`) and UI for no benefit. The abort button is enough.
- **Trigger at commit time (before the outcome).** Impossible for a
  *correct-choice* trigger - the outcome isn't known until scoring. Evaluating at
  scoring and reclassifying the already-committed trial is the minimal change.
- **Make the triggering correct trial the FIRST of a `block_len` block (10 total).**
  Rejected in favour of a `block_len` block that FOLLOWS the trigger (11 total),
  matching the cited precedent (Duvelle et al. 2024) and keeping the measured block
  independently 5/5-counterbalanced regardless of whether the trigger was L or R.
- **Count trigger trials in probe accuracy.** Rejected: the trigger is correct by
  construction, so including it would inflate the latent-knowledge read-out. A
  dedicated `probe_trigger` flag lets every reader exclude it while still shading it
  as an unrewarded probe trial.
- **Fire multiple probes per window / re-fire.** Rejected for now: one probe per
  window keeps the read-out clean and the session budget predictable; more windows
  are the way to get more probes.

## Consequences

- Probe placement is now tied to a behavioural event (a correct choice) inside an
  operator-chosen window, not the wall clock or a button press - closer to the
  scientific intent and repeatable across sessions via the config.
- The trigger trial "costs" one correct choice its reward. That is intended (it is
  the first unrewarded probe trial); the animal experiences a `block_len + 1` run.
- Generous windows tolerate interventions and wrong-choice runs by simply not firing
  - a window with no qualifying correct trial yields no probe that session. The CSV
  makes the actual (or absent) placement recoverable offline.
- New column bumps the log proto to 10; older CSVs back-fill `probe_trigger` to ""
  and read fine (`bundle_reader` default), so a pre-0134 probe session keeps all its
  probe trials in the accuracy read-out (it had no trigger to exclude).
- The `probe` flag partition, the insert-on-top model, the reward gate, and the
  seed-reproducible within-block pattern from ADR-0115 are all unchanged.

## Evidence / links

- `host/tests/test_phase3_probe_blocks.py` (rewritten): the controller
  (fires-in-window, incorrect/intervention/out-of-window don't fire, one probe per
  window, multiple windows, 5/5 balance + length, id increment + seed determinism,
  no-fire-while-active, early abort), the `TrialSequence` trigger (trigger trial
  reclassified + consumes spine; following block inserts without consuming; reset),
  and the `phase3_task` gate (trigger flagged + unrewarded, incorrect first choice
  doesn't trigger, whole block unrewarded then reinforced trial pays out,
  `reward_incorrect` ignored under probe). Full host suite green (981 passed / 17
  skipped) incl. the proto-10 / column-order updates in `test_phase3_log`.
- Supersedes the trigger of ADR-0115 (which it otherwise builds on); builds on
  ADR-0047 (spine + seed), ADR-0090/0107 (insert model), ADR-0046 (host reward
  decision), ADR-0050 (published snapshot), ADR-0052 (`phase3_stats`), ADR-0114
  (live traces), ADR-0083 (pure-core + thin shell).
- Motivating precedent for a block FOLLOWING the trigger: Duvelle et al. 2024,
  *Current Biology* S0960-9822(24)00457-3. Broader rationale: Drieu et al. 2025,
  *Nature* 641 (s41586-025-08730-8);
  `os_code_t_maze_resources/research_question/BRAINK99_Drieu.pdf`.
