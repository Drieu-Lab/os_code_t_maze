# ADR 0086: Closed-loop side-bias correction via a `debias_blocks` Phase 3 generator

- **Status:** Accepted
- **Date:** 2026-08-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/debias.py` (new), `trial_sequence.py`,
  `phase3_task.py`, `phase3_config.py`, `phase3_log.py`, `phase3_ribbon.py`,
  `phase3_stats.py`; `host/tmaze/ui/phase3_controller.py`, `trial_control_bar.py`,
  `experiment_control.py`, `behavior_view.py`, `tooltip_text.py`;
  `host/tmaze/core/session/session_summary.py`; `host/phase3.json`;
  tests `test_debias.py` (new) + `test_phase3_stats.py` / `test_phase3_log.py` /
  `test_phase3_ribbon.py`
- **Supersedes / Superseded by:** Bias-signal normalization + the `min_samples`
  trigger gate superseded by [ADR-0090](0090-debias-bias-normalized-by-errors.md);
  the ribbon's provisional-display half refined by
  [ADR-0096](0096-debias-provisional-display-alignment.md) (the rest of this ADR
  stands).

## Context

All three rats currently run Phase 3 with a heavy **left** bias: they pull left
regardless of the cue, which scores ~50% (chance on a two-choice task) without
learning the cue->side association. The existing schedule
(`trial_sequence.generate_balanced`: exact per-block L/R balance + a
`max_same_run` cap; ADR-0009) is **open-loop** — it is fixed from the seed before
the session and never reacts to what the animal actually does, so it cannot
undo a bias once one develops.

We wanted a way to counteract a developing bias **in closed loop**, with a
*structured, predetermined* framework so the intervention doesn't introduce
experimenter subjectivity (no ad-hoc "I'll force some right trials today"
decisions). The operator's requirements, refined over discussion:

- The counterbalanced schedule stays the **default**; intervene **only** when a
  bias actually develops.
- The intervention is a **run of trials whose correct side is the animal's
  non-preferred side** (so an "always-preferred" strategy earns no reward),
  continuing until the animal reliably chooses that side — not a single "repeat
  after error" correction trial.
- A clean, **predetermined exit**: end after **3 consecutive correct** choices to
  the non-preferred side.
- A welfare **failsafe**: cap the block length, auto-revert to counterbalanced,
  and flag it.
- Selectable as a **new generator option** alongside the existing one (not a
  change to the default), and **tunable from the GUI**.

This is distinct from the two deferred backlog items it sits near:
[FW-0001](../future-work.md#fw-0001-6040-sameswitch-transition-bias-in-sequence-generation)
(a *transition-bias* generator, still open) and
[FW-0008](../future-work.md#fw-0008-phase-3-transition---firmware-correction-trials--spatially-gated-cue)
(firmware, per-error correction trials). Ours is a host-side, bias-triggered
*block* generator — a sibling schedule, not a replacement for either.

## Decision

Add a **`debias_blocks`** trial-sequence generator, selected by a new
`sequence.generator` field (`"balanced"` default, unchanged; or
`"debias_blocks"`). It is the counterbalanced schedule **plus** a small
closed-loop state machine:

```
NORMAL (counterbalanced spine) --| |b| >= trigger_bias |--> INTERVENTION (block)
INTERVENTION --| 3 consecutive correct, or max_block_len cap |--> NORMAL
```

- **Bias signal — directional errors.** Over a sliding window of the last
  `window` scored trials, `b = (left_pull - right_pull) / len(window)` in
  `[-1, 1]`, where a *left-pull* is (cued RIGHT, chose LEFT) and a *right-pull* is
  (cued LEFT, chose RIGHT); `b > 0` means left-biased. A block forces the side the
  animal over-pulls *away from* (b > 0 -> force RIGHT correct).
- **State machine (`debias.py`, pure core).** `DebiasBlockController.decide()`
  fixes the next trial's context (may transition state; returns the forced side in
  a block, else `None` = "use the spine"); `.record(cued, chosen)` folds a scored
  trial back. On block exit the window is **cleared**, so a re-trigger needs fresh
  evidence. Parameters live in a frozen `DebiasBlockConfig` (`window=20`,
  `min_samples=10`, `trigger_bias=0.3`, `exit_consecutive=3`, `max_block_len=15`).
- **Stateful sequence (`trial_sequence.py`).** In `debias_blocks` mode the seed's
  balanced schedule becomes the **spine** (the pool of NORMAL-trial sides); the
  correct side of each trial is decided **lazily, in order**, the first time it is
  committed (`commit(index)`), so it can depend on realized choices. NORMAL trials
  consume the spine; block trials insert a forced side without consuming it.
  `max_same_run` still governs NORMAL trials and is naturally suspended inside a
  block (the block *is* the deliberate same-side run).
- **Manual override.** `force_block()` / `end_block()` let the operator start/stop
  a block from the Rig-view control bar; a manual block is flagged `manual`,
  otherwise `auto`, and still ends on the normal criteria.
- **GUI-tunable (extends ADR-0070).** ADR-0070 made `reward`+`cue` GUI-editable
  and the whole `sequence` block file-only. We carve the **`generator` selector +
  the `debias_blocks` params** out of that file-only rule into the Save-able GUI
  set (a dropdown + five spinboxes on the Phase 3 tab), because these are knobs an
  operator will want to tune per animal mid-training. The scientific schedule
  params (trials/block/tones/seed) stay file-only.
- **Data format (`phase3_log.py`, `proto=6` -> `proto=7`).** Four per-trial
  columns appended: `sched_state` (NORMAL/INTERVENTION), `block_id`,
  `block_trigger` (auto/manual), `bias` (signed `b` at the trial's decision). The
  header records the generator + `debias_blocks` params **and a caveat that for a
  closed-loop session the seed alone no longer reproduces the schedule** — it also
  depends on the realized choices + any operator overrides. Blank for `balanced`.
- **Stats partitioning (`phase3_stats.py`).** A forced block would distort
  learning measures, so all learning measures (accuracy, per-cue, side bias,
  win-stay/lose-shift, RT) are computed over **out-of-block** trials only; in-block
  trials are summarized separately (in-block accuracy/reward-rate/fraction), plus a
  headline **directional-error bias** and **block-efficacy** metrics (`n_blocks`,
  `blocks_success`, `blocks_capped`, `block_mean_len`). For a `balanced` session
  the in-block set is empty, so every measure equals the pre-debias readout.
- **Ribbon + panel.** Corrective-block trials get a distinct outline in the trial
  ribbon; the behavior panel gains a debias line (state, live bias vs threshold,
  block tally, in-block accuracy), and the end-of-session summary gains two guarded
  lines (balanced sessions unchanged).

## Alternatives considered

- **Graded adaptive probability shift** (nudge P(non-preferred correct) up with
  the bias, clamped to `[p_min, p_max]`). Rejected: the operator explicitly wanted
  counterbalancing to remain the default with a clean predetermined exit, not a
  continuously-perturbed schedule; and `max_same_run` interacts badly with a
  probability clamp (it intermittently *forces* the preferred side back, briefly
  reinforcing the bias). Kept as a possible future variant.
- **Textbook per-error correction trials** (repeat the errored side once after
  each error; the FW-0008 firmware idea). Rejected as the *primary* mechanism: it
  reacts to single errors rather than a *sustained* bias, and doesn't give the
  "keep going until it reliably switches" behavior the operator wanted. The block's
  3-consecutive-correct exit encodes "reliably switched".
- **Modify the default `balanced` generator in place.** Rejected: the operator
  asked for a *selectable* option so counterbalanced sessions stay bit-for-bit
  unchanged and comparable; the generator selector keeps the old path untouched
  (guarded by tests).
- **Keep the params file-only (strict ADR-0070).** Rejected: these are
  per-animal tuning knobs; a file-only round-trip (edit JSON + Reload) is too slow
  for mid-training adjustment. The carve-out is documented above.

## Consequences

- **Reproducibility changes shape for debias sessions.** A `balanced` session is
  still fully reproducible from the seed. A `debias_blocks` session is reproducible
  only from *(seed + realized choices + operator overrides)* — which the data file
  captures — and the header says so. This is inherent to any closed loop.
- **Stats are now partitioned.** Downstream that reads the summary's `cued_task`
  block (the session registry, ADR-0081; the resources training log, ADR-0082)
  keeps working unchanged: it reads fields by name, the new fields flow in
  automatically (`session_summary._stats_dict` uses `vars`), and the learning
  fields it already reads now mean "out-of-block" for debias sessions (identical
  for balanced). No `proto`/schema break for those consumers.
- **Bigger change surface.** Touches core + config + GUI + stats + docs. Landed
  behind a default-off selector with the pure cores + tests separable from the
  GUI wiring, per the repo's cap-change-size rule.
- **Revisit if:** the fixed `trigger_bias`/`exit_consecutive`/`max_block_len`
  defaults prove too twitchy or too slow on the bench (they are GUI-tunable, so
  this is data-gathering, not a redesign), or if a graded variant is wanted.

## Evidence / links

- Core + behavior: `host/tmaze/core/phase3/debias.py` (state machine),
  `trial_sequence.py` (`GEN_DEBIAS_BLOCKS`, lazy `commit`), `phase3_task.py`
  (`record_outcome`, decision-surfacing), `phase3_stats.py` (partition).
- Tests: `host/tests/test_debias.py` (state machine + simulated stubborn /
  cue-following agents + determinism), and the debias cases added to
  `test_phase3_stats.py` / `test_phase3_log.py` / `test_phase3_ribbon.py`.
- Related: ADR-0009 (anti-bias run-length cap — the open-loop half),
  ADR-0046/0047 (host-owned Phase 3 + session log), ADR-0052 (`phase3_stats`),
  ADR-0070 (config split this extends), ADR-0083 (package layout);
  [FW-0001](../future-work.md#fw-0001-6040-sameswitch-transition-bias-in-sequence-generation)
  and [FW-0008](../future-work.md#fw-0008-phase-3-transition---firmware-correction-trials--spatially-gated-cue)
  (sibling / firmware-side approaches, still open).
