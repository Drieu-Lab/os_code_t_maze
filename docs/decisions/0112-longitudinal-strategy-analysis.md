# ADR 0112: Longitudinal cross-session strategy analysis (interpretable-first)

- **Status:** Accepted
- **Date:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/tmaze/analysis/` layer (`bundle_reader`,
  `choice_signals`, `rolling_metrics`, `intervention_response`, `mode_label`,
  `choice_model`), `host/notebooks/animal_progress.ipynb`, `host/tests/test_analysis_*.py`,
  `host/requirements.txt`, `PROGRAMS.md`, `docs/glossary.md`
- **Supersedes / Superseded by:** —

## Context

A single per-session accuracy number badly misrepresents what the animals are
actually doing. Watching training, an animal cycles through distinct behavioural
*modes* - fully disengaged, side-stem excursions, mid-stem with a side bias,
mid-stem alternating, occasional lose-shift/win-stay, and only rarely anything that
looks like cue-guided choice. The complexity is increasing over sessions, so we need
a cross-session view of how the strategy repertoire shifts, not just whether the
score went up.

The raw material already exists on disk (no new logging): the per-trial `task/`
CSVs (choice, cue, outcome, the return-cue matrix, timestamps, and the closed-loop
intervention columns from ADR-0086/0107) and, once ADR-0109 ships to recorded
sessions, the per-excursion log. The live `phase3_stats` core (ADR-0052/0093)
already defines the per-trial strategy signals for one session; what was missing is
an offline, cross-session reduction and a way to see *strategy* and its *dynamics*.

Two design forks had to be settled before coding (decided with the operator):
delivery format (a **Jupyter notebook** over a tested core), and modelling ambition
(**interpretable-first**, deferring a latent-state model).

## Decision

Add a read-only `tmaze.analysis` layer (headlessly tested modules) plus a thin
`animal_progress` notebook front-end. It is a NEW top-level layer, deliberately not
under `tmaze.core`: the analysis leans on `pandas`/`numpy`/`statsmodels`, the
data-science deps ADR-0083 keeps out of the pure core (and `test_architecture`
enforces that - numpy/matplotlib are forbidden core imports). Key choices:

1. **Interpretable-first, no fitting for the modes.** Rolling-window rate traces +
   a rule-based behavioural-mode decomposition (explicit `ModeThresholds`, fixed
   priority order) + a per-session logistic **choice model** (`statsmodels`) of
   choice on cue and history. Every number traces back to one checkable arithmetic
   - the comprehension-debt rule applied to analysis. A latent-state GLM-HMM is
   explicitly deferred (FW-0040) and this rule-based ribbon is its future validation
   baseline.

2. **Intervention blocks are KEPT as first-class labelled epochs, never dropped.**
   Each trial is `free` (NORMAL, counterbalanced) / `in_block` (INTERVENTION) /
   `post_block` (a NORMAL trial within `post_window` of a block exit), derived from
   `sched_state`/`block_id`/`block_kind`/`block_trigger`. The animal's *choice* is
   always free; a block manipulates the *schedule*, and blocks often instigate mode
   switches - so we partition rather than discard. Baseline-strategy measures (side
   bias, alternation, and the choice-model cue coefficient) use **free** trials only
   (a block deliberately breaks counterbalancing, which would confound the cue
   term); the animal's response is measured **in-block**; carryover **post-block**.

3. **Peri-intervention transition summary is restricted to AUTO blocks.** An auto
   block fires from the animal's own drift, so its pre-onset window is a genuine
   baseline; a manual (operator-chosen) block would bias the "did the block cause
   the switch" reading, so manual blocks appear only as timeline markers. Per-block
   response metrics (compliance, escape-via-cue, in-block accuracy, trials-to-exit)
   are still computed for all blocks.

4. **Return-cue carryover is a first-class hypothesis.** Lag-1 features test whether
   trial N-1's *played* return cue (`return_cue_played`) - by identity/implied side
   or by congruency with N-1's choice cue - biases the choice/switch at N, both
   descriptively and as a term in the choice model. Documented caveat:
   `return_cue_played` is set by `return_arm_used`, so it is partly endogenous; the
   model controls for prior choice/reward but the result stays observational (a
   randomised return-cue schedule would be the causal follow-up).

5. **Graceful degradation for missing excursion data.** No `*_excursions.csv` exists
   for any current recording (ADR-0109 is new), so engagement uses an inter-trial-gap
   proxy and the `non-stem` mode reads "no data" until those logs exist; both
   auto-activate once present.

Mode threshold defaults are deliberate starting points surfaced in one notebook
config cell for tune-and-re-run against real data, not tuned values.

## Alternatives considered

- **Latent-state GLM-HMM now.** More powerful for "cycles through modes," but adds a
  modelling dependency, is harder to explain to a non-CS reader, and needs more
  data. Deferred to FW-0040; the rule-based ribbon ships value now and validates it
  later.
- **Drop/ignore intervention-block trials.** Rejected: they follow task rules and
  are often the instigator of the transitions we most want to see; partitioning
  keeps them without letting them confound baseline measures.
- **A per-animal PDF/PNG report tool (like the reward-calibration figure).** Good
  for reproducibility, but the operator wanted interactive tune-and-re-run; a
  notebook over a tested core keeps the logic reusable without burying it in cells.
- **Pool auto + manual blocks in the transition analysis.** Rejected: a manual
  block's pre-window isn't a spontaneous baseline (operator chose the moment).

## Consequences

- New analysis-only deps (`pandas`, `statsmodels`) added to
  `host/requirements.txt`, guarded so the live rig tools are unaffected
  (`choice_model` reports "not installed" without statsmodels).
- The cores are pure and tested (`host/tests/test_analysis_*.py`), so the notebook
  stays thin and the measures can be reused (e.g. by a future report tool or the
  deferred GLM-HMM).
- Reader tolerates schema drift (older CSVs lacking the intervention columns are
  back-filled) and restarts (`_r<n>` files concatenated).
- Follow-ups: FW-0040 (GLM-HMM) and FW-0041 (reward-pressure mode-transition
  characterization, incl. the free-block adverse-alignment driver and a
  transition-hazard model).

## Evidence / links

- Validated end-to-end against the real recordings root (48 sessions / 9600 trials
  across 600/601/602): traces, blocks, mode occupancy, per-block response,
  auto-block peri-transitions (600: 9 transitions, 78% changed dominant mode), and
  per-session choice-model coefficients all render; a couple of sessions already
  show a significant return-cue-carryover term (p ~ 0.007 / 0.035).
- Builds on ADR-0052 (`phase3_stats`), ADR-0093 (win-stay/lose-shift adjacency),
  ADR-0081 (session registry), ADR-0086/0090/0107 (intervention blocks + per-trial
  columns), ADR-0109 (excursion log), ADR-0083 (package layout / pure-core pattern).
