# ADR 0115: Unrewarded probe blocks (manual, latent-knowledge read-out)

- **Status:** Accepted
- **Date:** 2026-08-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/tmaze/core/phase3/probe_blocks.py` +
  `host/tests/test_phase3_probe_blocks.py`;
  `host/tmaze/core/phase3/trial_sequence.py` (probe controller wired into the
  lazy-commit path), `phase3_task.py` (reward gate + passthrough),
  `phase3_config.py` (`sequence.probe_blocks`), `phase3_log.py` (a `probe`
  column + snapshot `probe_enabled` / `probe_state`, proto 7->8),
  `phase3_stats.py` (probe vs reinforced partition), `phase3_traces.py` +
  `phase3_traces_view.py` (probe spans + shading/overlay/toggle);
  `host/tmaze/ui/` shells (`phase3_controller.py`, `trial_control_bar.py`,
  `experiment_control.py`, `trial_ribbon_view.py`, `tooltip_text.py`);
  `host/tmaze/core/session/session_notes.py` (`auto:probe_start/end`);
  `host/tmaze/analysis/` (`bundle_reader.py`, `choice_signals.py`,
  `choice_model.py`, `rolling_metrics.py`) + `host/notebooks/animal_progress.ipynb`;
  `host/phase3.json`; `PROGRAMS.md`, `docs/glossary.md`, `docs/future-work.md`
- **Supersedes / Superseded by:** the **trigger mechanism** (decision 5, manual +
  boundary-aligned start) is superseded by ADR-0134 (automatic correct-choice
  window trigger); the rest of this ADR still holds.

## Context

Reward-directed accuracy conflates two things: what the animal KNOWS (the
cue->action map it has acquired) and what it chooses to EXPRESS when it is being
paid. A well-trained rat that stops working for water late in a session looks
"inaccurate", but may still know the map perfectly - it is just disengaged.
Drieu et al. 2025 (*Nature* 641, s41586-025-08730-8; the grant's motivating
work, `os_code_t_maze_resources/research_question/`) separate these with short
UNREWARDED **probe** blocks interspersed in an otherwise normal session:
accuracy under a probe reads out latent knowledge, independent of the
reward-driven performance around it (the acquisition/expression dissociation).

We want the same read-out on this rig, run near disengagement onset (the operator
watches the live ITI trace, ADR-0114, creeping toward the 45 s line). Two rig
facts shape the design:

- The Phase 3 schedule is a seed-deterministic, block-counterbalanced L/R spine
  (`trial_sequence`, ADR-0047). The closed-loop debias / de-alternation
  interventions (ADR-0090 / ADR-0107) already INSERT forced trials on top of that
  spine WITHOUT consuming it, decided lazily in order at commit time.
- Reward is a host decision made per scored choice in `phase3_task.update`.

So a probe block is naturally "another kind of insert" - but orthogonal to the
corrective interventions (it is not a reaction to bias, and it should work under
any generator, including plain `balanced`), and it must suppress reward.

Where and when to place probes is an open experimental question. The operator
wants to PILOT placement by hand before committing to any automatic rule.

## Decision

Add manual, unrewarded probe blocks as a pure core + thin shell (ADR-0083).

1. **Pure core `probe_blocks.ProbeBlockController`** (mirrors the
   `DebiasBlockController` contract so `TrialSequence` drives it the same way): a
   small manual state machine with `force_block()` / `end_block()` operator
   latches and one `decide(spine_pos, block_size, intervention_active)` per
   upcoming trial. A pending block starts only at a counterbalanced block
   boundary (`spine_pos % block_size == 0`) with NO intervention active; once
   active it hands out an internally counterbalanced L/R plan of `block_len`
   trials (default **10**, 5/5), then auto-ends. The plan is seeded off the
   session seed plus a distinct salt and the block ordinal, so the k-th probe of
   a session is deterministic given the seed (see decision 5 on placement).

2. **Inserted on top of the spine, priority over the intervention stack.**
   `TrialSequence` gains a `_lazy_commit` flag = `_closed_loop OR probe enabled`,
   so the lazy in-order commit machinery (previously debias-only) also turns on
   for a probe-enabled `balanced` session. In `_commit_one`, an active probe is
   checked FIRST: a probe trial forces its own counterbalanced side, does NOT
   consume the spine, and does NOT poll the intervention stack (so no
   intervention block advances during a probe - it is suspended, not
   interrupted). A probe never starts on top of an active intervention; it waits
   for the next boundary after the block ends. Scored probe trials are still fed
   to `record_outcome` so the intervention detectors' windows stay warm across
   the probe.

3. **Reward gate in `phase3_task`.** The trial's `trial_meta` carries a `probe`
   flag; `Phase3Decision` gains `probe: bool`. On a probe trial the cue plays and
   the choice is scored CORRECT/INCORRECT exactly as normal, but
   `reward = (is_correct or reward_incorrect) and not probe` - no prime fires (so
   no lick top-ups either) and `reward_incorrect` shaping is ignored.

4. **Explicit `probe` marker end-to-end**, not overloaded onto the intervention
   fields. A probe trial keeps `sched_state == NORMAL` (the stack was suspended)
   and is flagged only by the new `probe` column in `phase3_log` (proto 7->8) and
   `probe_enabled` / `probe_state` on the published snapshot (ADR-0050). Stats,
   live traces, and the offline analysis all partition on this flag so probe
   trials are EXCLUDED from reinforced accuracy / strategy and reported
   separately as the latent-knowledge accuracy (per-block, e.g. the diamond
   markers on the Metrics-tab accuracy subplot, ADR-0114).

5. **Manual only, for now; automatic triggers deferred.** The controller has no
   trigger evaluation - a probe starts solely because the operator pressed the
   "Start probe block" toggle (new in `trial_control_bar`, live only when
   `probe_enabled`). Because the trigger is a live operator action, probe
   PLACEMENT is not reproducible from the seed alone - only the within-block 5/5
   pattern is; the CSV `probe` column records where each probe actually landed,
   and `auto:probe_start` / `auto:probe_end` session notes stamp it. The
   scaffolding for automatic triggers (fixed trial position / elapsed time /
   cumulative reward volume) is captured as future work so the operator can first
   pilot where a probe is worth placing.

6. **Config surface.** `sequence.probe_blocks = {enabled, block_len}` in
   `phase3_config`, resolved to a `ProbeBlockConfig` and threaded into
   `TrialConfig.probe`. It defaults DISABLED in `default_config()` so
   `to_trial_config(default_config())` still equals a bare `TrialConfig()` (the
   no-drift invariant `test_phase3_config` guards); the rig turns it on in
   `host/phase3.json`.

## Alternatives considered

- **Model a probe as a debias/alternation block kind.** Rejected: interventions
  are closed-loop CORRECTIONS keyed to bias, force a particular side, and are
  meaningful only under the intervention generators. A probe is orthogonal
  (no side preference beyond its own 5/5 balance, must run under `balanced`), and
  folding it in would entangle the reward gate with the correction logic.
- **Overload the existing `block_kind` / `sched_state` to say "probe".**
  Rejected: a probe is NOT an intervention (`sched_state` stays NORMAL), and every
  reader (`phase3_stats`, `phase3_traces`, the analysis epoch labeller) needs to
  keep probe trials out of the reinforced sets - a dedicated boolean is clearer
  and cannot be confused with a corrective block. Cost: one new CSV column
  (append-only, back-fills to "" for old sessions).
- **Consume the counterbalanced spine for probe trials.** Rejected: that would
  desync the seed-deterministic schedule and make the surrounding reinforced
  block no longer balanced. Inserting on top (like interventions) keeps the spine
  and its reproducibility intact.
- **Ship an automatic trigger now** (e.g. "probe at trial 40 and at 6-8 ml
  consumed"). Deferred: the right position/threshold is unknown and worth
  piloting; a wrong auto-rule would waste probe trials. Manual first, auto later
  (FW).

## Consequences

- A probe reads out latent knowledge without perturbing the reinforced schedule:
  the spine, its counterbalancing, and seed-reproducibility are unchanged; only
  reward is suppressed on probe trials.
- Probe placement is not seed-reproducible (it is operator-triggered). This is
  intentional for the piloting phase; the CSV + session notes make each probe's
  actual position recoverable offline.
- Enabling probe blocks turns on the lazy-commit path even for the `balanced`
  generator. That path is already exercised by the intervention generators and is
  covered by tests; the plain-balanced side (no probe) is untouched.
- New column bumps the log proto to 8; older CSVs back-fill `probe` to "" and are
  read fine (`bundle_reader` default).
- Automatic triggers, and any richer probe-vs-reinforced contrast in the offline
  model, are deferred (see future-work).

## Evidence / links

- `host/tests/test_phase3_probe_blocks.py` (15 tests): the controller state
  machine (pending-until-boundary, never-over-intervention, 5/5 balance, block
  length, id increment, seed determinism, early end / pending cancel), the
  `TrialSequence` insert (probe consumes no spine, `probe` flag set, spine resumes
  after), and the `phase3_task` reward gate (correct probe not rewarded,
  `reward_incorrect` ignored under probe, reinforced trial after a probe pays
  out). Full host suite green (850 passed / 17 skipped) incl. `test_architecture`
  and the proto-8 / column-order updates in `test_phase3_log`.
- Builds on ADR-0047 (counterbalanced spine + seed), ADR-0090 / ADR-0107
  (closed-loop insert model this mirrors), ADR-0046 (host-owned reward decision),
  ADR-0050 (published snapshot), ADR-0052 (`phase3_stats`), ADR-0114 (live
  Metrics traces the probe overlay rides on), ADR-0083 (pure-core + thin shell).
- Motivating work: Drieu et al. 2025, *Nature* 641 (s41586-025-08730-8);
  `os_code_t_maze_resources/research_question/BRAINK99_Drieu.pdf`.
