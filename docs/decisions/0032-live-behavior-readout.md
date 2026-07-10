# ADR 0032: Live Phase 2a behavior readout - a pure stats core over the task-event stream, shown in the Rig view tab

- **Status:** Accepted
- **Date:** 2026-07-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/behavior_stats.py` (pure core) + `host/behavior_view.py`
  (Tk shell) + `host/test_behavior_stats.py`; `host/experiment_control.py`
  (Rig view tab bottom strip split into left controls + right readout)
- **Supersedes / Superseded by:** implements the behavior-statistics half of
  [FW-0007](../future-work.md); consumes [ADR-0025](0025-maze-state-module.md)
  (maze state) and [ADR-0026](0026-reward-shaping-phases.md) /
  [ADR-0030](0030-prime-at-choice-entry.md) (task-event layer); reuses the scale
  from [ADR-0023](0023-position-based-rules-engine.md); —

## Context

Phase 2a shaping (ADR-0026/0030) trains *unidirectional* travel: the animal must
leave the hub up the central stem before entering a choice arm to earn the prime.
During a session the experimenter wants an at-a-glance answer to "is it working?"
without leaving the Rig view tab, where they already watch the schematic,
hand-reward via the solenoid buttons, and record video.

ADR-0025 deliberately built only the *producer* of the discrete `RatState`
stream, and ADR-0026 added `task_events.py` as the single definition of the task
vocabulary (`choice_entered`, `outbound_used_stem`, `same_side_choice_run`, ...)
precisely so a stats consumer could report the *same* events the reward rule
credits. That consumer (FW-0007's half (b)) had not been built.

The experimenter asked specifically for: the "core 5" scalar measures shown both
cumulatively and over a rolling window; a **Start/Stop** control and a **Reset**;
a **total distance run** using the existing px->cm calibration; and placement in
the empty bottom-right of the Rig view tab. A key correctness question was raised:
if tracking drops below confidence and re-acquires a distance X away, must X be
added to the distance total?

## Decision

1. **Pure core `behavior_stats.py`** (stdlib only, headless-testable, like
   `task_events.py`/`rules_engine.py`). `BehaviorStats` folds the
   `TaskEventFrame` stream (plus the smoothed centroid and a scale) into:
   stem-compliance rate, L/R choice balance (stem-gated only), non-stem
   (credit-denied) count, same-side run (current + max), trial throughput, and
   total distance. Every rate is kept **cumulatively** and over a **rolling
   window** of the most recent `N` choice entries (default `N = 10`).

2. **Reuse task-event predicates, but count one choice per outbound excursion.**
   The per-tick predicates are the shared ones - a choice is a `choice_entered`
   edge and stem use is `outbound_used_stem` - so the *definitions* of "choice"
   and "stem-gated" match the reward rule. But the readout's **counting unit is
   one choice per hub->...->hub trip**: only the first choice-arm entry after the
   animal last left the hub is counted. This fixes a double-count where a figure-8
   lap that goes out one arm and back the other (crossing the junction) enters two
   choice zones in a single trip - which per-entry counting would score as two
   choices, and, when neither used the stem, as two non-stem denials, deflating
   compliance (`stem/total`) and the L/R denominator. The same-side run is likewise
   recomputed here over per-excursion stem-gated choices rather than read from
   `same_side_choice_run`, so every readout measure shares the one "trip = choice"
   unit. The reward engine keeps its per-entry `task_events` semantics unchanged;
   only this observer de-duplicates.

3. **Distance reuses `rules_engine.Scale`** (`rig_scale.json`, the Rules tab's
   "Measure scale"; ADR-0023) via `distance_cm(p_px, q_px)` - the single
   canonical px<->cm conversion, homography-aware. No second calibration.

4. **Confidence gaps are discontinuities (the X question).** On any
   non-accumulated tick (invalid / below-confidence), the last-position anchor is
   dropped, so the first confident frame after a gap **re-seeds** without adding a
   segment. The re-acquisition jump X is therefore **never** added; distance
   during the gap is a deliberate **undercount** rather than a fabricated
   straight line across an untrusted stretch. A secondary per-tick **jump cap**
   (`DEFAULT_MAX_JUMP_PX = 200`) rejects a spurious hop while confident.

5. **Active-time denominators.** Throughput and distance-rate use summed *active*
   time (dt between `update()` calls), with a per-tick cap
   (`DEFAULT_MAX_DT_S = 0.5 s`) that drops - not clamps - an over-long gap, and a
   `mark_gap()` the shell calls on Start/resume so a paused stretch inflates
   nothing.

6. **Placement + Start/Stop, as a read-only observer.** `behavior_view.py` is a
   module (widget/apply/close) that runs its own `MazeStateEstimator` ->
   `TaskEvents` -> `BehaviorStats` poll loop on the shared tracker, independent of
   arming and of Free Roam vs Full task. In `experiment_control.py` the Rig view
   bottom strip is split into a **left** column (the existing solenoid / reward /
   lick / mode frames, unchanged) and a **right** column that fills the previously
   empty space with the readout. It **never actuates reward**; it only resets its
   counters on a firmware `MODE` line (matching the Reward/Lick tallies). It shows
   a quiet "waiting for tracking / zones" state, and "-- (needs scale)" for
   distance when uncalibrated.

## Alternatives considered

- **Add X (net displacement) across confidence gaps.** Rejected: a low-confidence
  stretch is exactly where the path is least trustworthy, and a straight A->B line
  ignores the real (curved) route; it would fabricate/ mis-measure distance more
  often than help. Undercounting is the defensible default.
- **Clamp an over-long inter-tick dt to the cap instead of dropping it.**
  Rejected: clamping adds up to `MAX_DT_S` of phantom active time on every resume;
  dropping it under-counts by at most one tick, which is preferable for a rate.
- **A separate always-on-top window or a new dedicated tab.** Rejected: the
  experimenter wanted the readout beside the manual-reward and recording controls
  they already use; the Rig view tab's dead bottom-right space fits it without
  shrinking the camera preview or schematic.
- **A live matrix / transition heatmap or full navigation fingerprint.**
  Deferred: for a first-order zone Markov matrix the maze topology dominates and
  the Phase 2a credential is path-dependent (non-Markovian); the scalar training
  readout is what answers "is shaping working" today. (Discussed with the
  experimenter; may return as offline analysis.)
- **Share one estimator with the Maze state / Rules tabs.** Deferred to keep the
  change small; the panel owns its own estimator and offers a "Reload zones/scale"
  button. Divergence risk is low (both load the same `zones.json`).

## Consequences

- The experimenter gets a live, glanceable "is Phase 2a working" panel (compliance
  trend + L/R + denied + perseveration + throughput + distance) on the tab they
  already run the session from, without any reward-actuation risk.
- Reported behavior and credited reward share one definition (`task_events.py`),
  so the numbers can be reconciled against the `rules_*.csv` rule log.
- Distance is a conservative **undercount** when tracking drops; if that matters
  for a session, a "tracking coverage" indicator (fraction of confident ticks)
  can be added later to surface the loss.
- The panel needs tracking enabled + `zones.json`; distance additionally needs
  `rig_scale.json`. It degrades gracefully otherwise.
- FW-0007's stats half is now done; the panel is a natural home for future
  metrics (dwell/latency, coverage) if wanted.

## Evidence / links

- Code: `host/behavior_stats.py` (`BehaviorStats`, `BehaviorSnapshot`),
  `host/behavior_view.py` (`BehaviorView`), `host/experiment_control.py`
  (Rig view bottom-strip left/right split + module registration).
- Tests: `host/test_behavior_stats.py` (25 cases) - compliance/L-R/non-stem,
  **per-excursion counting** (figure-8 non-stem cross counts one, within-trip arm
  cross after a stem entry counts one, return-via-stem counts one, two separate
  excursions count two), rolling window + resize, same-side run max + side +
  non-stem-doesn't-reset, active-time sum / large-gap drop / `mark_gap` pause /
  trials-per-min, distance with linear scale + homography + jump cap +
  **confidence-gap re-seed asserting the re-acquisition jump is not added** +
  no/invalid scale.
- Builds on ADR-0025 (maze state), ADR-0026 / ADR-0030 (task-event layer +
  Phase 2a), ADR-0023 (scale). Implements FW-0007 (stats half).
