# Future work backlog

A running list of **candidate code changes** that have been deliberately deferred
- ideas worth doing later but out of scope for the change that surfaced them.
The goal is that a future reader (human or agent) can reconstruct the idea, its
motivation, and where it fits without the original context in the room.

## How this differs from other docs

- **ADRs (`docs/decisions/`)** record decisions *already made* - immutable,
  "why we did X." This backlog is the opposite: *candidates not yet decided*.
  When a backlog item is implemented, capture the reasoning in a new ADR and
  **remove the entry from this file** (or mark it Done with a link to the ADR).
- **Planning / Gantt (in the `os_code_t_maze_resources` repo)** is project and
  timeline planning. This file is narrower: concrete code changes (which module,
  which files, how to validate). If an item is really a project task, put it in
  the planning workbook instead.

## How to add an entry

1. Copy the **Entry template** below to the bottom of the **Entries** section.
2. Give it the next `FW-NNNN` id and fill in every field you can.
3. Add a row to the **Index** table.
4. Keep it proportional - a small idea is a few lines; a big one gets the full
   template.

**Status values:** `Proposed` (captured, not evaluated), `Deferred` (evaluated,
intentionally postponed), `Blocked` (waiting on something), `Done` (implemented
- link the ADR and consider removing).

## Entry template

```
### FW-NNNN: <short title>

- **Status:** Proposed | Deferred | Blocked | Done
- **Date added:** YYYY-MM-DD
- **Author / Agent:** <name, or "Agent (Cursor) on behalf of <human>">
- **Area / modules:** <files or subsystems this would touch>
- **Related ADRs:** <ADR links, or "—">

**Motivation / context.** Why this matters (scientific rationale, references,
the observation that prompted it).

**Sketch of approach.** Enough detail to reconstruct the idea.

**Evaluation / acceptance.** How we would validate it (tests, bench checks,
criteria).

**Effort / risk.** Rough size and the main risks or tensions.

**Open questions.** What still needs deciding.

**ADR needed?** Yes/No - and why.
```

## Index

| ID | Title | Area | Status |
|----|-------|------|--------|
| [FW-0001](#fw-0001-6040-sameswitch-transition-bias-in-sequence-generation) | ~60/40 same/switch transition bias | Trial-sequence generation | Deferred |
| [FW-0002](#fw-0002-head-vs-body-orientation-heading-from-the-tracker) | Head-vs-body orientation (heading) | Markerless tracker | Deferred |
| [FW-0003](#fw-0003-multi-animal-association-and-irlow-saturation-tracking) | Multi-animal association + IR/low-saturation | Markerless tracker | Proposed |
| [FW-0005](#fw-0005-in-gui-rat-id-entry-firmware-animal-command--logger-file-roll) | In-GUI rat id entry (firmware `ANIMAL` command + logger file-roll) | Firmware + protocol + logger + GUI | Deferred |
| [FW-0006](#fw-0006-shared-base-class-for-still-frame-editors) | Shared base class for still-frame editors | Tracker tab UI | Deferred |
| [FW-0008](#fw-0008-phase-3-transition---firmware-correction-trials--spatially-gated-cue) | Phase 3 transition - correction trials + spatially-gated cue | Firmware + protocol | Deferred |
| [FW-0009](#fw-0009-triggerdispenser-decomposition-of-the-well-reward-rule) | Trigger/Dispenser decomposition of the well-reward rule | Rules engine | Deferred |

## Entries

### FW-0001: ~60/40 same/switch transition bias in sequence generation

- **Status:** Deferred
- **Date added:** 2026-06-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/task_core.h` (`generateSequence`), `firmware/common/rig_config.h` (a new `SAME_SIDE_FRACTION` knob), `firmware/cue-guided-task/test/test_task_core.cpp`
- **Related ADRs:** [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md) (added the anti-bias run-length cap; this is the deferred half of the same anti-bias goal)

**Motivation / context.** Rats have a strong innate **spontaneous-alternation**
tendency (natural foraging: don't return to a just-depleted source), so they
switch sides by default. A balanced-random sequence has ~50% same-side / ~50%
switch *transitions* between consecutive trials, which lets a pure-alternation
strategy score above chance without learning the cue->side association. Holleman
et al. 2019 (*Sci Rep* 9:12589) found the alternation drive must be *actively
discouraged* by making same-side transitions more common than switches, and that
a ratio of ~**60% same / 40% switch** was most conducive to learning. The
firmware already implements the companion rule from that paper - no more than 3
consecutive same-side trials (`MAX_SAME_RUN`, ADR-0009) - but not the transition
bias itself.

**Sketch of approach.** The current generator enforces *exact per-block L/R
balance* (counterbalancing), which naturally pulls transitions toward ~50/50, so
the 60/40 target can't just be layered on top - it needs a different generator:
a **transition-sampling** scheme. Start on a random side; for each subsequent
trial pick "same" with p approx `SAME_SIDE_FRACTION` (default ~0.6) else
"switch"; force a switch whenever a same-side run would exceed `MAX_SAME_RUN`;
and nudge toward overall L/R balance within a tolerance (relax the strict
per-block halves to an approximate global balance). Keep it deterministic from
the threaded PRNG seed and host-testable, as `generateSequence` is today.

**Evaluation / acceptance.** Add unit tests that, over a long generated
sequence, assert: (a) realized same-side transition fraction within tolerance of
the target; (b) overall L/R balance within tolerance; (c) `maxRunOk` still holds;
(d) **strategy simulations** - win-stay, win-shift, spontaneous-alternation, and
constant-side strategies all score <= ~60% on the generated sequence (the
paper's bar that no simple heuristic beats using the cue).

**Effort / risk.** Medium. Main tension is balance-vs-ratio: exact per-block
balance and a 60/40 transition ratio are mutually constraining, so this likely
relaxes block balance to a global tolerance - a behavioral-design change worth an
ADR. Low hardware risk (sequence generation only).

**Open questions.** Is approximate global balance acceptable instead of strict
per-block halves? Should `SAME_SIDE_FRACTION` be per-stage once more training
stages exist? What tolerances make the strategy-simulation tests stable yet
meaningful?

**ADR needed?** Yes - it changes how trial sequences are generated and relaxes
the per-block balance guarantee from ADR-era behavior.

### FW-0002: Head-vs-body orientation (heading) from the tracker

- **Status:** Deferred
- **Date added:** 2026-06-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker.py` (`Detection`/`RatModel` already compute the
  body axis via 2nd-moment elongation; add a heading output + disambiguation),
  `host/blob_tracker_prototype.py` (overlay + CSV), later the GUI overlay/sidecar
- **Related ADRs:** ADR-0014 (markerless tracking, planned in Phase 4)

**Motivation / context.** The cue-guided task is about reward-directed *choices*
and the grant studies navigation/reactivation, so the animal's **head direction**
is a valuable behavioral (and neural-correlate) variable. The blob's major axis
is already available essentially for free from the second central moments used for
the shape model. Note the *left/right choice* itself is better read from
**position/ROI** (which arm the centroid enters) than from heading, so this is an
enrichment, not a prerequisite for scoring choices.

**Sketch of approach.** Emit `body_axis_deg` (0-180, from the moment
eigenvector). Resolve the 180 deg head/tail ambiguity using the **Kalman
velocity** (assume forward motion: pick the axis end aligned with travel),
emitting `heading_deg` (0-360) plus a `heading_valid` flag that is false when the
blob is near-round (eccentricity below a threshold -> axis ill-defined) or the
speed is below a threshold (direction unreliable). Smooth heading **circularly**
(EMA on sin/cos, after resolving body-axis flips), separate from the position
Kalman. Optionally add a morphology tie-break (head end bulkier than the tapering
tail) once real video is available. Heading is an OUTPUT only - never a model
feature (it is orientation-dependent by construction). Document that it is in
image space (post rot180 + undistort), not maze/world coordinates.

**Evaluation / acceptance.** Validate against **recorded real-rat video** (the
trigger for deferring this): check `body_axis_deg` is rotation-stable on straight
runs; that velocity-based disambiguation matches the visible nose direction on
runs; that `heading_valid` correctly goes false during rearing/grooming and when
stationary; and that circular smoothing has no wrap artifacts. Add the fields to
the prototype CSV for offline scoring.

**Effort / risk.** Low-medium. Body axis is nearly free; the real work and risk
are head/tail disambiguation and validity gating, which is exactly why it waits on
real video. Low hardware risk (output only).

**Open questions.** Eccentricity/speed thresholds for `heading_valid`? Is
velocity-only disambiguation enough, or is a morphology tie-break needed for slow
turns? Should heading later be mapped from image space into maze coordinates?

**ADR needed?** Fold into ADR-0014 (tracking architecture) when implemented, or a
small follow-up ADR if the disambiguation strategy is non-obvious.

### FW-0003: Multi-animal association and IR/low-saturation tracking

- **Status:** Proposed
- **Date added:** 2026-06-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker.py` (`_associate`, `RatModel`, state machine),
  `host/tracker_view.py` (per-animal controls), recording sidecar columns
- **Related ADRs:** [ADR-0014](decisions/0014-markerless-position-tracking.md)
  (single-rat design these defer), [ADR-0015](decisions/0015-undistort-at-source.md)

**Motivation / context.** ADR-0014 deliberately tracks a **single** rat and leans
on a **colour** histogram as a strong cue. Two foreseeable needs break those
assumptions: (a) tracking **more than one animal** at once (e.g. social/again-two-
in-rig scenarios), which the current "pick one best blob" association can't do;
and (b) **IR or low-light** imaging, where saturation collapses and the colour
histogram becomes nearly useless, so association must lean on geometry/size/shape.

**Sketch of approach.** *Multi-animal:* keep one `RatModel` per identity and run a
proper assignment per frame (Hungarian / linear-sum assignment on a cost matrix of
gated detections vs. tracks, cost = blend of distance + appearance + form), with
per-track Kalman states and birth/death (ACQUIRING/LOST) logic; emit per-identity
sidecar columns. *IR/low-saturation:* detect the low-saturation regime (or a
manual toggle) and **re-weight** the score toward `w_dist`/`w_form` and away from
`w_hist` (optionally swap the H-S histogram for an intensity/texture descriptor);
the FORM Gaussian (size+shape) already works in greyscale.

**Evaluation / acceptance.** Multi-animal: on clips with two animals, identities
stay stable across crossings within tolerance (few ID swaps), validated against
hand-labeled frames. IR: with saturation artificially reduced, tracking + intruder
rejection still hold using geometry/size/shape; confirm the re-weighting kicks in.

**Effort / risk.** Medium-high (multi-animal association + ID management is the
bulk). IR re-weighting is small. Main risk is ID swaps during occlusion/crossings
- the classic multi-object-tracking failure mode.

**Open questions.** Is multi-animal actually in scope for this task, or only
single-rat + occasional experimenter hand (already handled)? Auto-detect the IR
regime from saturation stats, or expose a manual "low-saturation" mode? Per-
identity vs. shared models?

**ADR needed?** Yes - multi-object association and the appearance-weighting change
are non-obvious enough to warrant their own ADR (or an extension of ADR-0014).

### FW-0005: In-GUI rat id entry (firmware `ANIMAL` command + logger file-roll)

- **Status:** Deferred
- **Date added:** 2026-07-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/cue-guided-task.ino` (new `ANIMAL,<id>` command + re-emit), `firmware/common/rig_config.h` + `firmware/PROTOCOL.md` (`PROTO_VERSION` bump), `host/rig_logger.py` (roll file on a new `ANIMAL` line), `host/experiment_control.py` (rat-id field), a new ADR
- **Related ADRs:** [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md) (the `ANIMAL` handshake + session/mode logging this extends)

**Motivation / context.** The log filename comes from the firmware's
`0,ANIMAL,<id>` handshake, but `ANIMAL_ID` is hard-coded (`RAT001`), so every
rat's session lands in `data/RAT001_<time>.csv` unless overridden. The interim
fix (2026-07-01) is a `start_rig.bat` prompt that passes `--animal` to
`rig_logger.py` - but that is set once at launch and can't be changed from the
running GUI. The goal is to type (or change) the rat id directly in
`experiment_control` and have new sessions attribute correctly.

**Sketch of approach.** Add an `ANIMAL,<id>` command the firmware accepts
(alongside `MODE`/`PING`/...): store the id and emit a fresh `0,ANIMAL,<id>`
line. The broker fans that out, so `rig_logger.py` (which today opens one file
per process and ignores later `ANIMAL` lines) rolls to a new
`data/<id>_<time>.csv` on each new `ANIMAL` line. `experiment_control` gets a
small rat-id entry + Apply button that sends the command. Bump `PROTO_VERSION`
(v5) and document the command in `PROTOCOL.md`. Note the broker constraint
([rig_broker.py](../host/rig_broker.py)) that forces this through the firmware:
it forwards a client's line to the Due but does **not** echo it to the other
clients, so a GUI-typed id can't reach the logger except via the firmware
re-emitting the `ANIMAL` line.

**Evaluation / acceptance.** Set an id in the GUI -> the logger opens
`data/<id>_<time>.csv` and subsequent lines go there; changing the id rolls to a
new file without losing buffered lines; blank/invalid ids are rejected/sanitized
(reuse `rig_logger._sanitize`). Confirm a reflashed Due reports the command in
its handshake/version.

**Effort / risk.** Small-medium. Needs a Due reflash (protocol change). Main
risks: not losing lines across a file roll, and id sanitization.

**Open questions.** Allow changing the id mid-session (roll) or only when idle?
Persist the last id across reboots? Should the GUI also reflect the current id
from the `ANIMAL` line?

**ADR needed?** Yes - it changes the serial protocol and the logger's
file-naming behavior.

### FW-0006: Shared base class for still-frame editors

- **Status:** Deferred
- **Date added:** 2026-07-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker_view.py` (`_RoiEditor`, `_LandmarkEditor`),
  `host/maze_state_view.py` (`_ZoneEditor`)
- **Related ADRs:** [ADR-0018](decisions/0018-landmark-annotations.md),
  [ADR-0025](decisions/0025-maze-state-module.md)

**Motivation / context.** During the host dedup refactor, `_RoiEditor` and
`_LandmarkEditor` in `tracker_view.py` share still-frame setup (scale to
`EDITOR_MAX_W`, `Toplevel`, `grab_set`, `_close`) but diverge enough in their
interaction model (polygon vs. landmark list) that a shared base class was
deferred as low payoff for the complexity it would add. A **third** such editor
has now appeared - `maze_state_view._ZoneEditor` (multi-region polygon + point
targets, ADR-0025) - which re-copies the same modal still-frame scaffolding, so
the case for extracting a base is now stronger.

**Sketch of approach.** Extract a `_StillFrameEditorBase` holding the scaled
canvas, photo ref, and modal lifecycle; subclasses implement placement/commit
semantics only.

**Evaluation / acceptance.** No behavior change; both editors still work for ROI
and landmark definition after the extraction.

**Effort / risk.** Low-medium. Risk is over-abstracting two editors that may
continue to diverge.

**Open questions.** A third still-frame editor has appeared
(`maze_state_view._ZoneEditor`); is now the time to extract, or wait to see if
it keeps diverging?

**ADR needed?** No - mechanical UI refactor only if pursued.

### FW-0008: Phase 3 transition - firmware correction trials + spatially-gated cue

- **Status:** Deferred
- **Date added:** 2026-07-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/task_core.h` (correction-trial
  sequencing), `firmware/cue-guided-task/cue-guided-task.ino`,
  `firmware/PROTOCOL.md` (any new events / `PROTO_VERSION` bump)
- **Related ADRs:** [ADR-0026](decisions/0026-reward-shaping-phases.md)
  (reward-shaping phases 1/2a/2b this follows), [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md)
  (anti-bias run-length cap), [ADR-0008](decisions/0008-wavetable-tone-isr.md) (cue)

**Motivation / context.** The host-side shaping phases (ADR-0026) end at Phase 2b
(stem-gated choice with choice-side anti-bias, primed at the choice; ADR-0030).
Phase 3 is the full cue-guided task,
which stays in firmware for real-time cue timing and neural-recording TTLs. The
bridge from 2b to 3 needs two behavioral pieces not yet built: **correction
trials** (repeat the same cue/side after an error until the animal gets it,
without letting a side-bias re-emerge) and confirming the cue is played only
while the animal is within the spatially-stable window between the stem entry and
exit beams (to drive hippocampal place x auditory-cue interactions - the grant's
rationale).

**Sketch of approach.** Extend the firmware sequence generator with an optional
correction-trial mode (on error, re-present the same side, bounded so it does not
defeat the anti-bias cap); keep it deterministic + host-testable like
`generateSequence`. Verify/implement the spatial gate for cue onset from the
stem entry/exit beam window.

**Evaluation / acceptance.** Host unit tests over generated sequences (correction
trials repeat the errored side, bounded run length, overall balance preserved);
bench check that the cue only sounds within the stem window.

**Effort / risk.** Medium; firmware + protocol change (Due reflash), so an ADR is
warranted.

**Open questions.** How many correction repeats before forcing a switch? Does
2b's `uniform` debias carry into 3, or does 3 use a different balancing scheme
(cf. FW-0001)?

**ADR needed?** Yes - correction-trial semantics and any protocol change are
behavioral-design decisions.

### FW-0009: Trigger/Dispenser decomposition of the well-reward rule

- **Status:** Deferred
- **Date added:** 2026-07-08
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/rules_engine.py` (`WellRewardWithExcursionRule`),
  `host/test_well_reward.py`
- **Related ADRs:** [ADR-0030](decisions/0030-prime-at-choice-entry.md) (the
  in-place change that surfaced this), [ADR-0026](decisions/0026-reward-shaping-phases.md)
  (the rule this refactors)

**Motivation / context.** `WellRewardWithExcursionRule` now bundles three
concerns that grew together: *when the credential is earned* (the excursion +
`nonstem_reward_prob` fade + `debias` cap), *when the prime fires* (`prime_on`:
well-arrival / well-lick / at-the-credential), and *how it dispenses* (port,
pulse, top-up count + cooldown, freshness/anti-camp gating). ADR-0030 added the
third `prime_on` mode in place rather than extracting an abstraction, because
CONTRIBUTING #6 ("no abstraction until 2-3 real callers") was not yet met: the
only other rule template, `prime_and_lick_topup`, is kept dormant. As more reward
variants appear (e.g. the Phase-3 firmware bridge, FW-0008, or per-side spouts),
the single method will get harder to follow.

**Sketch of approach.** Split the rule into a small **Trigger** (decides *if/when*
to arm and to prime, emitting `armed`/`credit_denied`/`debias_withhold` and a
"prime now" signal) and a **Dispenser** (owns the port/pulse, the top-up counter +
cooldown, the well-visit lifecycle, and the confidence/freshness/anti-camp gates).
`prime_on` becomes which Trigger edge the Dispenser listens to. Credential logic
(`_earn_credential`) is already a separate method, so it can move to the Trigger
largely intact; the state machine in `tick`/`on_lick` moves to the Dispenser.

**Evaluation / acceptance.** Pure refactor: the full `test_well_reward.py` +
`test_task_events.py` suites pass unchanged; add focused tests for the
Trigger/Dispenser seam. No behavior change for the shipped phase1/2a/2b profiles.

**Effort / risk.** Medium. Risk is over-abstracting for a single live caller
(exactly what was deferred here); revisit only when a second real consumer exists.

**Open questions.** Does the dormant `prime_and_lick_topup` get retired or
migrated onto the Dispenser? Do per-side spouts (dispense at the entered arm)
arrive and justify the split on their own?

**ADR needed?** Yes if the seam changes rule semantics or config; no if it is a
pure internal refactor.

## Completed

Implemented items are removed from the active backlog above; their reasoning now
lives in the linked ADRs. This table is kept only so the `FW-NNNN` ids still
referenced from those ADRs resolve.

| ID | Title | Implemented in |
|----|-------|----------------|
| FW-0004 | Position-based task-rule engine over landmarks | [ADR-0023](decisions/0023-position-based-rules-engine.md) |
| FW-0007 | Maze-state consumers - rule-shaping + behavior stats | [ADR-0026](decisions/0026-reward-shaping-phases.md), [ADR-0032](decisions/0032-live-behavior-readout.md) |
