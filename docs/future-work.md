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
| [FW-0006](#fw-0006-shared-base-class-for-still-frame-editors) | Shared base class for still-frame editors | Tracker tab UI | Done |
| [FW-0008](#fw-0008-phase-3-transition---firmware-correction-trials--spatially-gated-cue) | Phase 3 transition - correction trials + spatially-gated cue | Firmware + protocol | Deferred |
| [FW-0009](#fw-0009-triggerdispenser-decomposition-of-the-well-reward-rule) | Trigger/Dispenser decomposition of the well-reward rule | Rules engine | Deferred |
| [FW-0010](#fw-0010-deeplabcut-live-pose-estimation-for-body-part-tracking) | DeepLabCut-Live pose estimation for body-part tracking | Markerless tracker | Proposed |
| [FW-0011](#fw-0011-richer-colour-model-for-clustered-fragments-multimodalper-part) | Richer colour model for clustered fragments (multimodal/per-part) | Markerless tracker | Proposed |
| [FW-0013](#fw-0013-free-cpu-cores-during-recording-nvenc-or-encode-thread-cap) | Free CPU cores during recording (NVENC or encode-thread cap) | Camera recording | Proposed |
| [FW-0014](#fw-0014-zone-entry-latency-floor-for-a-vision-triggered-cue-path) | Zone-entry latency floor for a vision-triggered cue path | Maze state + rules | Partially done |
| [FW-0015](#fw-0015-scoped-ratblocker-de-merge-restore-splitting-only-if-needed) | Scoped rat/blocker de-merge (restore splitting only if needed) | Markerless tracker | Proposed |
| [FW-0016](#fw-0016-migrate-trial-sequence--cue-identity-from-firmware-to-host) | Migrate trial-sequence / cue-identity from firmware to host | Firmware + protocol + host | Partially done (ADR-0046) |
| [FW-0017](#fw-0017-end-to-end-cue-onset-latency-from-the-firmware-cue-echo) | End-to-end cue-onset latency from the firmware CUE echo | Validation harness | Proposed |
| [FW-0018](#fw-0018-hardware-session-sync-fiducial-firmware-sync-ttl--camera-visible-led-barcode) | Hardware session sync fiducial (firmware `SYNC` TTL + camera-visible LED barcode) | Firmware + protocol + host session/camera | Proposed |
| [FW-0019](#fw-0019-one-reward-seam--rate-backstop-across-the-task-controllers) | One reward seam + rate backstop across the task controllers | Rules engine + Phase 3 host | Done |
| [FW-0020](#fw-0020-shared-choice-commit-edge-in-the-task-event-layer) | Shared choice-commit edge in the task-event layer | Task-event layer + task controllers | Proposed |
| [FW-0021](#fw-0021-single-parse-serial-fan-out-in-the-gui-orchestrator) | Single-parse serial fan-out in the GUI orchestrator | GUI orchestrator + protocol | Proposed |
| [FW-0022](#fw-0022-unified-mode-switch-reset-seam) | Unified MODE-switch reset seam | GUI orchestrator + tab modules | Proposed |
| [FW-0023](#fw-0023-shared-preview-producer-loop-helper) | Shared preview *producer* loop helper | Preview helpers + tab modules | Proposed |
| [FW-0024](#fw-0024-config-driven-phase-3-taskreward-parameters-parity-with-rules) | Config-driven Phase 3 task/reward parameters (parity with Rules) | Phase 3 host + config | Proposed |

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
- **Related ADRs:** ADR-0014 (markerless tracking, planned in Phase 4), ADR-0037
  (blob clustering - provides the aggregated union axis and the learned lighter-tail
  colour this can use for disambiguation)

**Motivation / context.** The cue-guided task is about reward-directed *choices*
and the grant studies navigation/reactivation, so the animal's **head direction**
is a valuable behavioral (and neural-correlate) variable. The blob's major axis
is already available essentially for free from the second central moments used for
the shape model. Note the *left/right choice* itself is better read from
**position/ROI** (which arm the centroid enters) than from heading, so this is an
enrichment, not a prerequisite for scoring choices.

**Sketch of approach.** Emit `body_axis_deg` (0-180, from the moment
eigenvector; take it from the **clustered union** once ADR-0037 lands, so the axis
spans the reassembled animal rather than one fragment). Resolve the 180 deg
head/tail ambiguity using the **Kalman velocity** (assume forward motion: pick the
axis end aligned with travel), emitting `heading_deg` (0-360) plus a
`heading_valid` flag that is false when the blob is near-round (eccentricity below
a threshold -> axis ill-defined) or the speed is below a threshold (direction
unreliable). A stronger, **motion-independent** disambiguation is now available:
the end of the axis nearer the **lighter tail-coloured pixels** is the tail, the
other the head - reusing the tail colour the blocker/rat clustering already learns
(ADR-0037), which also fixes head/tail during slow turns / grooming when velocity
is unreliable. Smooth heading **circularly** (EMA on sin/cos, after resolving
body-axis flips), separate from the position Kalman. Heading is an OUTPUT only -
never a model feature (it is orientation-dependent by construction). Document that
it is in image space (post rot180 + undistort), not maze/world coordinates. (This
is the near-term, cheap, high-value precursor to full pose estimation, FW-0010.)

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
velocity-only disambiguation enough, or should the tail-colour cue (ADR-0037) be
the primary head/tail resolver, with velocity as a fallback? Should heading later
be mapped from image space into maze coordinates?

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
- **Related ADRs:** [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md) (the `ANIMAL` handshake + session/mode logging this extends), [ADR-0053](decisions/0053-gui-tab-streamlining.md) (the GUI regroup that surfaced the duplicated host-side id state)

**Update (2026-07-24, host-side consolidation done under ADR-0053 phase 4).**
The GUI-streamlining review (ADR-0053) found the animal id entered/held in
**two** host places with **two** copies of the `_sanitize` slug helper (the
Phase 3 tab entry and the Session panel), plus a third copy in `rig_logger.py`.
Phase 4 of ADR-0053 has now **landed** this consolidation: the new "Task" tab
(`task_view.py`) owns the **single** controller-agnostic Animal ID field, which
is the one host-side id source feeding both the Phase 3 data file and the session
manifest (the Session panel and Phase 3 read it via injected getters; it
auto-fills from the firmware `ANIMAL` handshake). The three `_sanitize` copies
collapsed to one shared helper, `protocol.sanitize_animal_id`. What **remains**
open for FW-0005 is only the firmware protocol work below (a Due `ANIMAL,<id>`
command + logger file-roll) so a GUI-typed id can also name the verbatim
`rig_logger.py` file and re-attribute mid-session.

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
(reuse `protocol.sanitize_animal_id`). Confirm a reflashed Due reports the command
in its handshake/version.

**Effort / risk.** Small-medium. Needs a Due reflash (protocol change). Main
risks: not losing lines across a file roll, and id sanitization.

**Open questions.** Allow changing the id mid-session (roll) or only when idle?
Persist the last id across reboots? Should the GUI also reflect the current id
from the `ANIMAL` line?

**ADR needed?** Yes - it changes the serial protocol and the logger's
file-naming behavior.

### FW-0006: Shared base class for still-frame editors

- **Status:** Done - implemented under
  [ADR-0053](decisions/0053-gui-tab-streamlining.md) phase 3 (the Setup-tab
  merge). Extracted `host/still_frame_editor.py` (`_StillFrameEditorBase`: the
  scaled canvas, photo ref, grabbed `Toplevel`, and modal `_close`); the three
  editors (`tracker_view._RoiEditor` / `_LandmarkEditor`,
  `maze_state_view._ZoneEditor`) now subclass it and keep only their own
  placement/commit semantics. No behavior change.
- **Date added:** 2026-07-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker_view.py` (`_RoiEditor`, `_LandmarkEditor`),
  `host/maze_state_view.py` (`_ZoneEditor`), `host/still_frame_editor.py` (new)
- **Related ADRs:** [ADR-0018](decisions/0018-landmark-annotations.md),
  [ADR-0025](decisions/0025-maze-state-module.md),
  [ADR-0053](decisions/0053-gui-tab-streamlining.md)

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

**Update (2026-07-22).** The **spatially-gated cue** half of this item is now
addressed by the host vision cue (ADR-0044 + ADR-0045): the tone is a function of
the rat's tracked position (the central-stem / return zones), so it is played only
within the spatially-stable window rather than for the whole beam-bounded
`CueRunning` interval. What REMAINS for FW-0008 is the **correction-trial**
sequencing below (and, if desired, folding the cue into the firmware trial FSM
rather than the host-driven path).

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

### FW-0010: DeepLabCut-Live pose estimation for body-part tracking

- **Status:** Proposed
- **Date added:** 2026-07-15
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** a new host module (e.g. `host/pose_dlc.py`) beside
  `host/tracker.py`; the position/rules feed (`rules_engine` consumes a
  `centroid_px` + `scale`, so a chosen keypoint can slot in); recording sidecar
  columns; the GUI overlay; new dependencies (`deeplabcut-live` **or** SLEAP, plus
  a trained model and likely a GPU)
- **Related ADRs:** [ADR-0014](decisions/0014-markerless-position-tracking.md)
  (single-blob markerless tracker this would sit beside/above),
  ADR-0037 (blob clustering - the classical predecessor + its tail-colour output),
  [ADR-0015](decisions/0015-undistort-at-source.md) (undistort applies before
  inference), FW-0002 (heading - subsumed once real pose keypoints exist)

**Motivation / context.** The stated end goal is **DeepLabCut-Live** running on the
rig: real-time per-body-part keypoints (nose, ears, body centre, tailbase, tail)
as behavioural and neural covariates - head direction, nose-at-port precision, and
pose during choices - supporting the grant's navigation / reactivation aims. The
classical pipeline (ADR-0014 + the ADR-0037 clustering) gives a robust centroid and
an aggregate body axis, but its blobs are background-subtraction / threshold
artifacts, **not stable anatomy**, so per-part *identity* from blobs is fragile
(blob count fluctuates; a part splits/merges frame to frame; a black rat is
low-texture so appearance barely separates parts). A learned pose estimator
sidesteps all of that by predicting keypoints directly.

**Sketch of approach.** Run DLC-Live (or SLEAP) inference per frame after
undistort-at-source (ADR-0015); output keypoints + per-keypoint confidence. Feed a
chosen keypoint (e.g. nose or body centre) into the existing position/ROI + rules
pipeline in place of the blob centroid, keeping the classical tracker as a fallback
for low-confidence frames / when no GPU is present. Add per-keypoint sidecar columns
and overlay. Budget inference latency against the closed-loop reward-timing
requirement (the reason cue/reward timing stays in firmware - see ADR-0001/0023).

**Evaluation / acceptance.** Keypoint accuracy vs hand-labelled frames; end-to-end
per-frame latency within the closed-loop budget; head-direction sanity on straight
runs; graceful, automatic fallback to the classical tracker when confidence is low
or the GPU is unavailable; identity stability across occlusions/crossings.

**Effort / risk.** High. A heavy ML dependency + trained model + GPU, and a
departure from the repo's classical-CV, low-comprehension-debt ethos - a
significant architectural decision, not just a feature. Main risks: real-time
latency for closed-loop actuation, model generalization across lighting/animals,
labeling/training workflow, and long-term maintenance.

**Open questions.** DLC-Live vs SLEAP? Which keypoint drives the rules (nose vs
body centre)? Is there a GPU on the rig PC, and what is the latency budget? Does
pose *replace* or *augment* the classical tracker (fallback)? A hand-built
constellation / pictorial-structures part model on the existing blobs was
considered and rejected as too fragile given unstable blob-to-part mapping - a
learned estimator is preferred; is that still the call?

**ADR needed?** Yes - a major architecture + external-dependency decision (likely
several: dependency/runtime, which keypoint feeds rules, fallback strategy).

### FW-0011: Richer colour model for clustered fragments (multimodal/per-part)

- **Status:** Proposed
- **Date added:** 2026-07-15
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker.py` (`RatModel` colour representation +
  `hist_score` / `in_support_fraction`; the clustering merge gate in
  `_cluster_detections`)
- **Related ADRs:** [ADR-0037](decisions/0037-cluster-fragmented-rat-blobs.md)
  (blob clustering - the pooled-histogram + membership design this would enrich),
  [ADR-0014](decisions/0014-markerless-position-tracking.md) (the appearance model),
  FW-0010 (learned pose estimation - the intended longer-term answer)

**Motivation / context.** ADR-0037 models the rat's colour as a **single pooled**
H-S histogram and gates fragment merges with a lenient "fraction of pixels in an
observed bin" membership test (`in_support_fraction`). A rat's parts are visibly
multi-modal in colour (black/white torso vs. a lighter tail vs. a head), so one
pooled histogram blurs those modes together: the membership gate can be too
permissive (any bin the rat ever showed passes, so a same-coloured intruder
fragment could sneak in) or, if tightened, too strict. A representation that knows
the rat has *several* colour modes could gate merges and score candidates more
precisely - and is a stepping stone toward per-part identity.

**Sketch of approach.** Replace/augment the pooled histogram with a **multimodal**
colour model - e.g. a small **GMM in H-S space**, or a handful of per-mode
histograms clustered from registration samples - and gate a fragment by its best
mode rather than the union support. Optionally attach a coarse **spatial prior**
per mode (tail tends to be peripheral/elongated) once ADR-0037's `members` are
being labelled. Keep it behind the existing knobs (fall back to the pooled
histogram when the model is thin). Note this is explicitly the "richer colour
model" alternative that ADR-0037 considered and deferred.

**Evaluation / acceptance.** On recorded real-rat clips: the merge gate keeps the
tail while rejecting a same-ish-coloured intruder fragment better than the pooled
membership test; no regression in single-blob tracking; registration stays
bootstrappable (no prior needed). Compare false-merge / false-split rates against
the ADR-0037 baseline on hand-labelled frames.

**Effort / risk.** Medium. Risk of over-engineering the classical model when the
stated end goal is a learned pose estimator (FW-0010); pursue only if the pooled
histogram proves insufficient in practice and pose estimation is not yet available.

**Open questions.** GMM vs. discrete per-mode histograms? How many modes, and set
by whom (auto vs. a knob)? Is this worth doing at all given FW-0010, or only as a
low-cost interim? Does it need the per-part labels the user can provide?

**ADR needed?** Yes - it changes the appearance model's representation and the
merge/scoring semantics.

### FW-0013: Free CPU cores during recording (NVENC or encode-thread cap)

- **Status:** Proposed
- **Date added:** 2026-07-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/camera_view.py` (`_VideoWriter` codec setup,
  `thread_count`)
- **Related ADRs:** [ADR-0011](decisions/0011-camera-tab-freerun-ffv1.md),
  [ADR-0012](decisions/0012-pyav-multithreaded-ffv1.md),
  [ADR-0013](decisions/0013-selectable-lossless-lossy-recording.md)

**Motivation / context.** The encoder is configured `thread_count = 0` (all
cores). It runs on its own thread (decoupled from grab by a bounded queue,
ADR-0011), but competes for the same cores the grab thread needs to finish
`process()` in time - a contributor to `#260720` when recording is on. The rig PC
has an NVIDIA RTX 4060 Ti whose **NVENC** block is separate silicon from both the
CPU and CUDA cores; PyAV in the `drieu_t_maze` env exposes `h264_nvenc` /
`hevc_nvenc` / `av1_nvenc`.

**Sketch of approach.** Either (a) switch H.264 recording from `libx264` to
`h264_nvenc` (GPU encode, frees CPU cores; add `nv12`/`yuv420p` pixel format + a
CQ/bitrate option), and/or (b) cap `thread_count` to leave cores for tracking.
Use the `track_ms` readout (added for #260720) to pick the operating point.
Keep FFV1 (lossless) on CPU; NVENC is for the compressed path.

**Evaluation / acceptance.** With recording on, `track_ms` and `src_drops` drop
toward their recording-off values; recorded file quality/size stays acceptable for
a behavioural record; no encode `queue_drops` regression.

**Effort / risk.** Low-medium. NVENC quality-per-bit is slightly below libx264 at
matched size; verify visual sufficiency. Adds a GPU-driver dependency for the
compressed path.

**Open questions.** Default to NVENC when available and fall back to libx264? Which
rate-control (CQ vs bitrate)? Expose as a GUI toggle or auto-detect?

**ADR needed?** Yes - it changes the recording codec/quality trade-off
(extends ADR-0013).

### FW-0014: Zone-entry latency floor for a vision-triggered cue path

- **Status:** Partially done
- **Date added:** 2026-07-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/maze_state.py` (`min_dwell_ticks`, commit logic),
  `host/maze_state_view.py` / `host/rules_view.py` (poll intervals), any future
  host->firmware cue command
- **Related ADRs:** [ADR-0025](decisions/0025-maze-state-module.md) (min-dwell
  hysteresis), [ADR-0023](decisions/0023-position-based-rules-engine.md)
- **Related FW:** [FW-0008](#fw-0008-phase-3-transition---firmware-correction-trials--spatially-gated-cue)
  (spatially-gated cue - would consume this)
- **Related ADRs (progress):** [ADR-0040](decisions/0040-tracking-off-grab-thread.md)
  (tracking off the grab thread),
  [ADR-0041](decisions/0041-shared-maze-state-pipeline.md) (one shared estimator
  ticking at the camera frame rate),
  [ADR-0042](decisions/0042-fast-actuation-maze-state-view.md) (fast low-latency
  view + worker push seam) and
  [ADR-0043](decisions/0043-rules-actuation-thread.md) (actuation off the Tk poll)

**Update (2026-07-22, latest).** The **host vision cue path itself now exists**
(ADR-0044 + ADR-0045): a host->firmware cue command (`CUE_ON/CUE_OFF` +
`CUE,VISION|BEAM`, PROTO_VERSION 5) and a pure `CueGate` (`host/cue_gate.py`) that
gates the tone on the fast-view zone (CHOICE = central stem up-stem, RETURN =
redrawn return zone), with **fail-open** on the choice cue and fail-safe on the
return, driven by the `cue_controller.py` Cue tab off the actuation-thread seam.
Onset is measured offline via `maze_state_validate.py --cue`. What REMAINS for
this item is only the beam+vision **fusion** for observability (committed-entry =
beam ENTRY *confirmed* by vision forward progress) - the current cue is
Celine's deliberate **vision-only** first cut ("ON/OFF just based on vision for
now ... eventually a fusion signal using both vision and beams"). Fusion would
add the beam fiducial back as a precise onset time while vision resolves
commitment-vs-reversal.

**Update (2026-07-22, earlier).** The **distinct fast/low-latency view** and the
**move off the Tk poll** called for below are now done: ADR-0042 added a second
low-`min_dwell` estimator (default 1 frame) with a worker push seam
(`subscribe_fast`), and ADR-0043 moved reward actuation onto a dedicated thread
fed by those pushes + routed lick edges, so rules react within ~1-2 frames instead
of a Tk poll. The fast push -> actuation-thread seam is what the cue path attached
to.

**Update (2026-07-22).** The *poll-rate* half of the floor below is gone: ADR-0041
moved the one estimator onto the frame-rate tracking worker, so `min_dwell_ticks`
now counts camera frames (~50-100 ms) instead of ~3 Tk polls (~300-450 ms). What
remains for this item is the beam+vision *fusion* for observability, a distinct
**fast/low-latency view** (lower `min_dwell`, evaluated on the worker) separate
from the debounced display/logging state, and confidence-dropout fail-open - i.e.
Phase 2b builds on this seam. The motivation below is kept for context but its
"evaluated only on the Tk poll" premise is now historical.

**Motivation / context.** Part of `#260720` is that zone **entry** registers late
and variably ("rat is well into the zone before entry commits"). This is largely
architectural, not CPU: entry is evaluated only on the Tk poll (100-150 ms) and a
new zone must persist `min_dwell_ticks = 3` consecutive polls before it commits -
a ~300-450 ms floor even at zero load. The *variability* comes from confidence
loss (returns `UNKNOWN` until re-lock) and dwell-counter resets on boundary
jitter. A future position-triggered auditory cue (none today; cues are firmware
beam-triggered) would inherit this floor, hurting the spatial consistency of cue
onset.

**Why beams alone are insufficient (the deeper reason for a vision cue path).** A
beam break is an *event*, not a *state*: a beam-only maze-state machine is **not
real-time observable**. Example (Celine, 2026-07-22): the rat breaks the central-
stem ENTRY beam, but can then either continue toward the EXIT beam *or* turn back
to the reward well - and beam-only, that branch is unobservable until a *future*
break (the EXIT sensor) resolves it. Many such break sequences are ambiguous, so
"which state is the rat in now" is only decidable in the context of a future
break. Beam-only therefore forces a hard trade: fire the cue early on the ENTRY
break (false cues when the rat reverses) or wait for a downstream break to be
certain (too late for an entry-timed cue). The firmware has exactly this gap today
- `task_core.h` plays the tone the whole time it is in `CueRunning` (between ENTRY
and EXIT), so a rat that breaks ENTRY then turns back keeps the tone until it
eventually trips EXIT. Vision's real role here is **observability**: it supplies
the continuous trajectory *between* beams so commitment-vs-reversal is answerable
*now*, not retrospectively.

**Sketch of approach.** Treat it as sensor fusion, not vision-vs-beam: **beams are
precise spatial/temporal fiducials** ("crossed this line, at this instant") and
**vision is the connective tissue that makes state observable between them**.
Define cue onset as *committed entry* = the beam ENTRY event **confirmed** by
vision showing sustained forward progress (the estimator already computes
`direction` up_stem/down_stem and `stem_progress` 0..1 - reuse them), evaluated on
the grab/tracking thread so it resolves within a few frames (tens of ms) rather
than waiting for the EXIT beam. Vision should **fail open** (defer to the beam when
tracking is unconfident) so a confidence dropout degrades gracefully instead of
dropping cues. Also expose a lower `min_dwell_ticks` + faster evaluation for this
path, distinct from the debounced display/logging state.

**Evaluation / acceptance.** Measured entry-commit latency (vs a beam-break ground
truth, cf. `maze_state_validate.py`) meets the cue's spatial-consistency budget
with acceptable false-edge rate; scripted turn-back sequences (ENTRY break then
reverse) do **not** fire/sustain the cue; a confidence dropout falls back to beam
behaviour rather than missing the cue.

**Effort / risk.** Medium-high. It is a fused host+firmware state estimator, not a
knob; couples tightly to FW-0008 (where the cue actually lives) and FW-0012 (the
thread model that makes the vision signal low-latency).

**Open questions.** Exact scientific definition of the cue-onset (and cue-*offset*)
event in commitment terms - does a mid-cue reversal abort the tone? What
`stem_progress`/`direction` threshold and frame-count define "committed"? Does the
fusion live in firmware (host sends a vision "committed" flag) or does the host own
the decision and command the tone? Per-zone settings?

**ADR needed?** Yes - it changes maze-state commit timing semantics
(extends ADR-0025).

### FW-0015: Scoped rat/blocker de-merge (restore splitting only if needed)

- **Status:** Proposed
- **Date added:** 2026-07-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tracker.py` (`RatTracker.process` blocker branch,
  `_footprint_overlap`), `host/test_tracker_blocker.py`
- **Related ADRs:** [ADR-0039](decisions/0039-blocker-containment-drop.md) (the
  containment-drop that accepted this trade-off), ADR-0034, ADR-0035

**Motivation / context.** ADR-0039 replaced per-frame pixel-subtraction with
dropping whole detections inside the blocker footprint, to cut per-frame load. The
accepted trade-off: a rat **fused** with the blocker into one contour is dropped
(brief COAST during contact) rather than split. If the blocker sits at a
decision-relevant spot and the rat contacts it often, that brief loss may matter.

**Sketch of approach.** Detect only the *fused* case cheaply - a single detection
whose bbox overlaps the footprint **and** has substantial area outside it - and do
pixel-subtraction + local re-detect **just for that one blob**, not a whole-frame
second pass. Everything else keeps the ADR-0039 single-pass drop.

**Evaluation / acceptance.** During scripted contact the rat stays tracked on its
own side (no steal onto the blocker) while `track_ms` stays near the ADR-0039
baseline for non-contact frames; existing blocker tests plus a restored contact
test pass.

**Effort / risk.** Low-medium. Gated on evidence from the `track_ms` /
reliability data that the contact case actually hurts an experiment.

**Open questions.** What outside-area fraction defines "fused"? Is local
re-detection cheap enough to be worth it vs just accepting the coast?

**ADR needed?** Yes if it changes the ADR-0039 behaviour (supersede the relevant
part); no if it is a guarded, evidence-driven addition.

### FW-0016: Migrate trial-sequence / cue-identity from firmware to host

- **Status:** Partially done - an initial host-owned realization LANDED as
  [ADR-0046](decisions/0046-host-owned-phase3.md) (host owns the sequence,
  identity, scoring, and reward in Free Roam with the firmware as an actuator;
  `host/trial_sequence.py` + `host/phase3_task.py` + `host/phase3_controller.py`,
  `CUE_ON,<kind>,<hz>` in PROTOCOL v6). What REMAINS open here is the
  firmware-authoritative variant (host AUTHORS + uploads the sequence, firmware
  stays the real-time trial-advance authority) for a Full-task deployment where
  reward timing must be on-Due - see ADR-0046's "Alternatives considered".
- **Date added:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/task_core.h` (`generateSequence`,
  `cueFor`, `correctArm`), `firmware/cue-guided-task/cue-guided-task.ino`,
  `firmware/PROTOCOL.md` (a sequence-upload line + handshake), `host/trial_sequence.py`
  (already exists; would author the uploaded schedule)
- **Related ADRs:** [ADR-0046](decisions/0046-host-owned-phase3.md) (the initial
  host-owned realization), [ADR-0044](decisions/0044-host-driven-cue-command.md) (the
  semantic `CUE_ON,<kind>` command chosen to stay valid across this migration),
  [ADR-0045](decisions/0045-vision-cue-zone-gate.md) (the host cue gate that
  consumes host-side identity), FW-0001 (transition-bias generator this would host)

**Motivation / context.** Today the firmware owns the trial sequence and the tone
identity: `generateSequence` builds the L/R schedule, `correctArm` is latched on
the `B_ENTRY` beam, and `cueFor()` maps it to a frequency. ADR-0044 kept the wire
command SEMANTIC (`CUE_ON,CHOICE`, not `TONE_ON,<hz>`) precisely so identity can
move to the host later without another protocol bump. Two forces push that way:
(a) ADR-0044 notes a latent race - the host decides the choice-cue ONSET from
vision while the firmware still resolves WHICH tone from the ENTRY beam; hosting
the sequence removes the split-brain; (b) richer sequence logic (FW-0001's
60/40 transition bias, correction trials in FW-0008) is easier to write and test
in Python than on the Due.

**Sketch of approach.** Add a pure host `trial_sequence` core (deterministic from
a seed, host-testable like `generateSequence`) that owns the L/R schedule and the
per-trial correct arm + tone identity. The host advances trials on the events it
already sees (beams / zone entries / reward) and resolves `CUE_ON,CHOICE` to a
specific tone; the firmware keeps synthesizing tones + TTLs. Decide the source of
truth for trial advancement (host vs firmware) and how they stay in sync.

**Evaluation / acceptance.** Host unit tests over long sequences (balance,
transition ratio, anti-bias cap, correction-trial rules); a bench check that
host-resolved identity matches the firmware's `cueFor` for the same schedule
during a transition period; no missed/duplicated trials across a mode switch.

**Effort / risk.** Medium-high. Splitting authority for trial state between host
and firmware is the main risk (drift/desync); needs a clear ownership decision and
probably a firmware "report current trial" line for reconciliation.

**Open questions.** Does the host or firmware remain the trial-advance authority?
How is the seed shared so both sides agree? Is this a hard cutover or a
dual-run reconciliation period? Does it subsume FW-0001's generator?

**ADR needed?** Yes - it moves a core behavioral-timing responsibility across the
host/firmware boundary.

### FW-0017: End-to-end cue-onset latency from the firmware CUE echo

- **Status:** Proposed
- **Date added:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/maze_state_validate.py` (a `load_cue_events` parser +
  a firmware-echo alignment pass), `host/protocol.py` (`cue_info`, already added)
- **Related ADRs:** [ADR-0045](decisions/0045-vision-cue-zone-gate.md) (the offline
  CueGate-replay latency this complements), [ADR-0044](decisions/0044-host-driven-cue-command.md)
  (the `<ms>,CUE,ON,...` echo this would parse)

**Motivation / context.** `maze_state_validate.py --cue` already measures the
**vision-decision** component of cue-onset spread (offline CueGate replay vs the
beams). It does NOT measure the **command/serial/firmware** component - the extra
latency from the host's `CUE_ON` send to the true acoustic onset. The firmware
emits `<ms>,CUE,ON,<kind>,<freq>` on every real (re)start (ADR-0044), and the
session log carries it with a wall-clock stamp, so the realized end-to-end onset
can be aligned to the beam ground truth offline - closing the budget breakdown the
mission asked for (decision -> tone).

**Sketch of approach.** Add `load_cue_events(session_csv)` (reuse `cue_info`) to
pull firmware `CUE,ON` echoes with their wall-clock time, then align them to the
cue-relevant beams like `cue_report` does. Handle the return-side ambiguity: the
`RETURN` echo carries no L/R side, so match a `RETURN` onset to the NEAREST of the
two return beams within the window (or infer side from the concurrent track).
Report realized-onset-vs-beam alongside the offline CueGate prediction so the
command-path delta is explicit.

**Evaluation / acceptance.** On a recorded session with the cue armed: the
realized `CUE,ON` onsets align to their beams within the window, and the
(realized - predicted) delta quantifies the command/serial/firmware latency;
consistent with a bench loopback measurement of `CUE_ON` -> `TONE_ON` TTL.

**Effort / risk.** Low. Pure offline harness addition; main subtlety is the
return-side disambiguation.

**Open questions.** Match `RETURN` echoes to the nearest return beam, or require a
track to disambiguate side? Should the harness ingest the TTL log directly if one
is exported, for a firmware-internal latency number?

**ADR needed?** No - it extends an existing measurement tool without changing
behavior.

### FW-0018: Hardware session sync fiducial (firmware `SYNC` TTL + camera-visible LED barcode)

- **Status:** Proposed
- **Date added:** 2026-07-23
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/cue-guided-task.ino` (+ `firmware/common/rig_config.h`, `firmware/PROTOCOL.md`, `PROTO_VERSION` bump) for a `SYNC` command that pulses an Open Ephys TTL and drives an LED in the camera FOV; `host/session_controller.py` (`mark_session_start` — the seam it attaches to), `host/session_log.py` (fiducial record), `host/camera_view.py` (detect/record the LED barcode in the sidecar), later `host/rig_logger.py`
- **Related ADRs:** [ADR-0048](decisions/0048-host-session-orchestration-and-manifest.md) (the host-only session orchestrator + the single fiducial seam this replaces the host-t0 anchor at), [ADR-0011](decisions/0011-camera-tab-freerun-ffv1.md) (free-run camera + the `cam_block_id`/`cam_timestamp` sidecar columns future-proofed for hardware triggering), [ADR-0002](decisions/0002-transparent-line-passthrough.md) (why a host marker must loop through firmware), [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md) (the serial log the TTL/echo would anchor)

**Motivation / context.** ADR-0048's session anchor is deliberately coarse: a
host-t0 in wall/monotonic clocks plus an optional `PING`→`PONG` in the serial
log, so cross-stream alignment is tens-of-ms and carries serial latency. Once
neural recording (Open Ephys) is integrated the camera will be hardware-triggered
or emit a per-frame TTL, and we will want **sub-ms, acquisition-grade** alignment
of video ↔ serial ↔ neural. A single hardware fiducial at session start (and
optionally periodically) that is simultaneously visible to *every* stream is the
standard way to get it: a TTL edge into Open Ephys (and the serial log via the
firmware echo) that co-occurs with a **camera-visible LED** flash, so the video
can be aligned to the electrical timeline without trusting any software clock.

**Sketch of approach.** Add a `SYNC` command the firmware accepts (alongside
`MODE`/`PING`/`CUE_ON`...) that (a) pulses a dedicated Open Ephys TTL line, (b)
flashes an LED positioned in the camera FOV, and (c) re-emits a stamped
`<ms>,SYNC,<seq>` line so it lands in the serial log (respecting ADR-0002 — the
host can't inject into other clients' streams, only the firmware re-emit reaches
`rig_logger.py`). Randomize the flash as a short **barcode** (a few bits of
on/off, à la Bpod/`labcams` sync) so each fiducial is uniquely identifiable and
robust to dropped frames. Have `session_controller.mark_session_start` call
`send("SYNC")` in addition to (or instead of) `PING`, and record the method in
the manifest `fiducial` field (`method: firmware-sync`, with the TTL line + LED
ROI). Detect the LED in `camera_view` (a fixed ROI intensity threshold) and log
the barcode + frame index to the sidecar so the video timestamp of each fiducial
is recovered offline; the same `SYNC` seq ties the serial-log line, the Open
Ephys TTL, and the video frame together.

**Evaluation / acceptance.** Bench: a scope/logic-analyzer shows the `SYNC` TTL
edge and the LED-on transition co-occur within the required tolerance; the LED
barcode is detected in the recorded video at the expected frames; the same seq
appears in the serial log, the sidecar, and the Open Ephys TTL events, and the
three timelines align to sub-ms after fitting. Regression: the host-t0 path still
works when the hardware is absent (graceful fallback — no firmware = `PING` only).

**Effort / risk.** Medium — needs a Due reflash (protocol change), a spare TTL
line + an LED wired into the FOV, and LED-detection code in the camera path.
Risks: LED placement that doesn't occlude behavior yet is reliably detected;
exposure/rolling-shutter effects on the barcode; keeping the fallback clean.

**Open questions.** One fiducial at start, or periodic re-syncs to bound clock
drift over a long session? Barcode length / bit rate vs the camera frame rate?
Which TTL line and LED GPIO on the Due? Should `camera_view` detect the LED live
or only offline from the recording? Does the fiducial also gate the future
hardware-triggered/per-frame-TTL camera mode?

**ADR needed?** Yes — it changes the serial protocol (Due reflash) and defines
the cross-stream synchronization mechanism, both non-obvious decisions.

### FW-0019: One reward seam + rate backstop across the task controllers

- **Status:** Done - implemented under
  [ADR-0054](decisions/0054-unified-reward-dispatcher.md). A shared
  `host/reward_dispatcher.py` (`RewardDispatcher`) now owns the global rate
  backstop (moved out of `rules_engine._apply_global`), the pulse/volume record,
  and the `R<port>` send; one instance is injected into both the Rules
  (`rules_view._dispatch_reward`) and Phase 3 (`phase3_controller._fire_reward`)
  controllers, so a Phase 3 reward is rate-limited + recorded like a rules reward.
  No on-wire framing change. Tests: `host/test_reward_dispatcher.py` (+ the
  relocated engine backstop assertion). The reward-parameter parity follow-up is
  FW-0024.
- **Date added:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/rules_engine.py` (`_apply_global`, the global rate
  backstop + LogEvent pairing), `host/phase3_controller.py` (`_send_cmd`, the
  direct `R<port>` fire at 451/522), `host/phase3_task.py` (reward config)
- **Related ADRs:** [ADR-0053](decisions/0053-gui-tab-streamlining.md) (the
  GUI regroup that surfaced this; it deliberately deferred the reward-core dedup),
  [ADR-0026](decisions/0026-reward-shaping-phases.md) / [ADR-0030](decisions/0030-prime-at-choice-entry.md)
  (reward-shaping seam the rules engine gates), [ADR-0019](decisions/0019-reward-volume-and-pulse-duration-logging.md)
  (pulse/volume logging any seam must preserve)
- **Related FW:** [FW-0009](#fw-0009-triggerdispenser-decomposition-of-the-well-reward-rule)
  (Trigger/Dispenser split of the well rule — the same reward plumbing viewed
  from the rules side)

**Motivation / context.** The two host task controllers dispense reward through
**two unrelated paths**. The Rules engine routes every `FireReward` intent
through `_apply_global` (`rules_engine.py` 815-838), which enforces a global
minimum-interval **rate backstop** and pairs each accepted fire with a
`LogEvent` (and a `"suppressed"` event when rate-limited). Phase 3 instead fires
reward directly (`phase3_controller._send_cmd`, `R<port>...`) with no shared
backstop and its own logging. So the anti-spam guarantee, the reward-volume
accounting (ADR-0019), and the log shape differ between the two task modes for
no principled reason — an accident of the tabs having grown separately.

**Sketch of approach.** Introduce one small **reward seam** (e.g. a
`RewardDispatcher`) that both controllers call: it owns the global rate backstop,
the pulse/volume log record, and the actual `R<port>` send, so a fire is
identical whichever task drove it. Rules keeps emitting `FireReward` intents into
it; Phase 3 emits its scored-choice reward into the same seam instead of building
the command inline. Fold this in after ADR-0053's shared vision-actuator skeleton
(refactor A) exists, since both controllers will already share a base.

**Evaluation / acceptance.** The existing rules reward tests
(`test_well_reward.py` / rules engine tests) pass unchanged; add a focused test
that a Phase 3 reward is rate-limited and logged identically to a rules reward;
no change to on-wire `R<port>` framing or reward volume accounting.

**Effort / risk.** Medium. Risk is coupling a behavioral seam (reward gating) to
the two task modes — do it only once the shared skeleton lands, and keep the
backstop interval configurable per mode if their timing budgets differ.

**Open questions.** Should the global backstop interval be shared or per-mode?
Does Phase 3 want the `"suppressed"` LogEvent semantics, or a lighter record?

**ADR needed?** Yes — it unifies a reward-gating/logging seam across two task
modes (a behavioral-timing decision), likely a small follow-up to ADR-0026/0053.

### FW-0020: Shared choice-commit edge in the task-event layer

- **Status:** Proposed
- **Date added:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/task_events.py` (task-semantic event layer over the
  maze-state estimator), `host/rules_engine.py` (`_earn_credential`
  `choice_via_stem` + the per-excursion latch), `host/phase3_task.py` (choice
  scoring + `_scored_this_trial` latch)
- **Related ADRs:** [ADR-0053](decisions/0053-gui-tab-streamlining.md) (deferred
  this event-layer dedup out of the regroup), [ADR-0026](decisions/0026-reward-shaping-phases.md)
  (the task-event layer), [ADR-0038](decisions/0038-once-per-excursion-well-credit.md)
  (per-excursion choice latch), [ADR-0025](decisions/0025-maze-state-module.md)
  (the maze-state stream both consume)
- **Related FW:** [FW-0019](#fw-0019-one-reward-seam--rate-backstop-across-the-task-controllers)
  (the reward seam this feeds)

**Motivation / context.** Both task controllers independently answer the same
question — *"has the rat just committed an outbound left/right choice, once per
trip?"* — with their own latch. The rules engine earns a `choice_via_stem`
credential and latches it per excursion (ADR-0038); Phase 3 scores the choice and
guards it with `_scored_this_trial`. Same maze-state input, same
"once-per-outbound-trip" predicate, two hand-rolled implementations that can
drift (e.g. different debounce or re-arm rules), which is exactly the kind of
silent behavioral divergence the comprehension-debt guardrails warn about.

**Sketch of approach.** Promote a single **choice-commit edge** into the shared
task-event layer (`task_events.py`): emit one `ChoiceCommitted(side, trip_id)`
event, latched once per outbound excursion, that both the rules credential logic
and the Phase 3 scorer subscribe to instead of each detecting the edge itself.
Keep the maze-state estimator as the sole upstream source.

**Evaluation / acceptance.** Pure refactor: rules `choice_via_stem` behavior and
Phase 3 scoring are unchanged on the shipped profiles (existing task-event and
well-reward tests pass); add a focused test that the shared edge fires exactly
once per outbound trip and re-arms on return.

**Effort / risk.** Medium. Risk is subtle timing differences between the two
current latches; capture both behaviors in tests before unifying.

**Open questions.** Do the two consumers agree on the exact commit point
(stem-gated vs zone-entry), or does unifying force a behavioral choice? Does the
edge belong in `task_events.py` or a thin layer above the estimator?

**ADR needed?** Yes if unifying changes either mode's commit semantics; no if it
is a pure extraction that both already agree on.

### FW-0021: Single-parse serial fan-out in the GUI orchestrator

- **Status:** Proposed
- **Date added:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/experiment_control.py` (`on_line`, 515-532),
  `host/protocol.py` (`mode_name` / `lick_edge` / `reward_info` parsers)
- **Related ADRs:** [ADR-0053](decisions/0053-gui-tab-streamlining.md) (the
  Operate-group review that surfaced this), [ADR-0024](decisions/0024-shared-host-protocol-parser.md)
  (the centralized parser this builds on), [ADR-0043](decisions/0043-rules-actuation-thread.md)
  (the actuation seam that also consumes serial lines)

**Motivation / context.** `on_line` (`experiment_control.py` 515-532) parses each
serial line **once** for the bottom-strip tallies (`protocol.mode_name`,
`lick_edge`, `reward_info`), then passes the **raw line** to every registered
module, each of which re-parses it with the same `protocol` helpers. On a busy
line rate this is N+1 parses of the same string, and it means the mode/lick/
reward classification lives in two layers that must be kept in agreement.

**Sketch of approach.** Parse once at the fan-out point into a small immutable
`ParsedLine` (mode/lick/reward/... plus the raw text) and hand **that** to
modules, so each module reads the pre-parsed fields instead of re-running the
parsers. Keep `protocol.py` as the single parser (ADR-0024); this is just about
not calling it repeatedly per line.

**Evaluation / acceptance.** No behavior change: modules react identically; a
micro-benchmark shows one parse per line instead of N+1; the `protocol` parser
API stays the single source of truth.

**Effort / risk.** Low-medium. Touches every module's `apply()` signature, so it
is best done alongside the ADR-0053 regroup when those modules are already being
edited.

**Open questions.** Do all modules only ever need the pre-parsed fields, or do
some still need the raw line (they can keep it — `ParsedLine` carries it)?

**ADR needed?** No — a mechanical fan-out/perf refactor within the ADR-0024 model.

### FW-0022: Unified MODE-switch reset seam

- **Status:** Proposed
- **Date added:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/experiment_control.py` (`on_line` resets the tallies
  on a MODE line, 519-522), `host/behavior_view.py` (`apply` resets on
  `protocol.mode_name`), and any other module that resets its own state on a
  MODE switch
- **Related ADRs:** [ADR-0053](decisions/0053-gui-tab-streamlining.md) (the
  review that surfaced the split resets), [ADR-0024](decisions/0024-shared-host-protocol-parser.md)
  (the shared MODE parse), [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md)
  (the `0,MODE,<name>` line that triggers the reset)

**Motivation / context.** A firmware MODE switch (`0,MODE,<name>`) means "new
run, drop stale state", but each consumer notices this independently: `on_line`
resets the reward/lick tallies (`experiment_control.py` 519-522), `behavior_view`
resets its stats on the same line, and other modules do their own thing. There is
no single "session/mode boundary" signal, so it is easy to add a new stateful
module and forget to reset it on a MODE change.

**Sketch of approach.** Emit one explicit **mode-changed** notification from the
fan-out point (naturally paired with FW-0021's single parse) that stateful
modules subscribe to, replacing the scattered per-module `if mode is not None:
reset()` checks with one seam. Modules that need no reset simply don't subscribe.

**Evaluation / acceptance.** No behavior change: tallies and the behavior readout
still clear on a MODE switch; add a test that a newly-registered stateful module
gets the reset callback without touching `on_line`.

**Effort / risk.** Low. Mostly a wiring change; pairs with FW-0021.

**Open questions.** Should the seam distinguish "MODE changed to X" (some modules
only reset for specific transitions) vs a blanket "boundary" signal?

**ADR needed?** No — a small internal wiring cleanup.

### FW-0023: Shared preview *producer* loop helper

- **Status:** Proposed
- **Date added:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/preview.py` (currently holds `safe_after` +
  `render_bgr_to_label`), the per-tab preview loops in `host/tracker_view.py`
  (`_schedule_preview` / `_render_preview`), `host/maze_state_view.py` (same
  pair), `host/rules_view.py`, and `host/phase3_controller.py`
- **Related ADRs:** [ADR-0053](decisions/0053-gui-tab-streamlining.md) (the
  Setup/Operate review that surfaced the remaining duplication),
  [ADR-0018](decisions/0018-landmark-annotations.md) / [ADR-0025](decisions/0025-maze-state-module.md)
  (the overlays the loops draw)
- **Related FW:** [FW-0006](#fw-0006-shared-base-class-for-still-frame-editors)
  (the modal still-frame editors — the *other* half of the tabs' shared UI
  scaffolding)

**Motivation / context.** `preview.py` already deduped the **render** step
(`render_bgr_to_label`) and the `after`-scheduling guard (`safe_after`), but each
tab still hand-writes the surrounding **producer loop**: a `_schedule_preview`
that re-arms via `after(PREVIEW_INTERVAL_MS, ...)`, grabs the newest frame from
the camera/tracker, computes the display scale to `PREVIEW_MAX_W`, calls a
per-tab `draw(disp, scale)` overlay, and tears down on close. That outer loop is
copied across at least four modules with only the draw callback differing.

**Sketch of approach.** Add a small **preview-loop driver** to `preview.py` that
owns the `after`-based re-arm, the newest-frame fetch, the scale computation, and
the teardown, taking `(interval_ms, max_w, source, draw)` — so each tab supplies
only its overlay `draw` callback and its source. Keep it opt-in (tabs with
unusual needs, e.g. the mask view, can stay bespoke). Best done while ADR-0053 is
already touching these modules (Setup merge).

**Evaluation / acceptance.** No visible change: each tab's live preview + overlay
looks and updates the same; the per-tab `_schedule_preview`/`_render_preview`
boilerplate collapses to a driver call + a draw callback; teardown still ignores
`TclError` on a closed widget.

**Effort / risk.** Low-medium. Risk is over-generalizing for tabs whose sources
differ (camera vs tracker publisher); keep the driver small and opt-in.

**Open questions.** One driver parameterized by source, or a thin base the tabs
subclass? Does the fast-view/actuation preview (Phase 3) fit the same driver or
stay separate?

**ADR needed?** No — a mechanical UI-loop extraction, like the original
`preview.py` split.

### FW-0024: Config-driven Phase 3 task/reward parameters (parity with Rules)

- **Status:** Proposed
- **Date added:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/phase3_task.py` (`Phase3Config`), `host/trial_sequence.py`
  (`TrialConfig` + tone Hz), `host/cue_gate.py` (`CueParams`),
  `host/phase3_controller.py` (load/apply + persist), a new `host/phase3.json`,
  optionally a pure config/editor core mirroring `rules_engine.py`'s editor support
- **Related ADRs:** [ADR-0026](decisions/0026-reward-shaping-phases.md) /
  [ADR-0031](decisions/0031-gui-edit-rules-json.md) (the profiles + GUI-edit-and-
  write-back pattern to mirror), [ADR-0046](decisions/0046-host-owned-phase3.md) /
  [ADR-0047](decisions/0047-phase3-return-schedules-and-session-log.md) (host-owned
  Phase 3 + session log), [ADR-0054](decisions/0054-unified-reward-dispatcher.md)
  (the unified reward dispatcher the reward params feed)
- **Related FW:** [FW-0019](#fw-0019-one-reward-seam--rate-backstop-across-the-task-controllers)
  (reward execution seam this parameterizes),
  [FW-0008](#fw-0008-phase-3-transition---firmware-correction-trials--spatially-gated-cue)
  (a second Phase 3 stage that would justify profiles),
  [FW-0016](#fw-0016-migrate-trial-sequence--cue-identity-from-firmware-to-host)
  (trial-sequence ownership this must not pre-empt)

**Motivation / context.** Phase 3's reward, sequence, and cue knobs are hardcoded
in frozen dataclasses (`Phase3Config` = port 2 / 300 ms / reward-incorrect) plus
ephemeral Tk vars (seed, cue params) that reset every launch, whereas Rules is
fully driven by a versioned `rules.json` with a GUI editor, validation, and
write-back (ADR-0031). So Phase 3 parameters can't be changed without editing
code, aren't reproducible, and the operator's mental model differs between the two
task tabs.

**Sketch of approach.** Add a versioned `host/phase3.json` with `reward` (port,
pulse_ms, reward_incorrect), `sequence` (max_trials, block_size, max_same_run,
tone Hz, seed policy), and `cue` (choice/return enabled, require_up_stem,
min_confidence) sections, loaded into `Phase3Config`/`TrialConfig`/`CueParams`
with defaults matching today's behavior. Put loading/coercion/validation in a pure
core (like `rules_engine`'s editor support) and keep the Tk shell thin. Start
**flat** (no profiles); add a named-profile map + phase dropdown only once a second
real Phase 3 stage exists (e.g. FW-0008 correction trials). Stamp the resolved
config into the Phase 3 data-file header / session manifest for reproducibility.

**Evaluation / acceptance.** Defaults reproduce current behavior with no
`phase3.json` present; edits persist across launches and are rejected if malformed
(reuse the ADR-0031 validate-before-write pattern); the resolved reward
`port`/`pulse_ms` flow through the shared `RewardDispatcher` (ADR-0054); the
session data file records the parameters used.

**Effort / risk.** Medium. Main risks: over-building profiles ahead of need
(CONTRIBUTING #6); putting scientific params (tone Hz, balancing) in JSON without
guarding them; and not pre-empting FW-0016's trial-sequence ownership decision.

**Open questions.** One shared task-config file or per-controller files? Fixed vs
random seed policy in config? Which sequence/cue params are safe to expose vs
remain ADR-gated? Flat now and profiles later, or profiles from the start?

**ADR needed?** Yes — it changes how Phase 3 is configured and persisted (a small
follow-up to ADR-0046, mirroring ADR-0031).

## Completed

Implemented items are removed from the active backlog above; their reasoning now
lives in the linked ADRs. This table is kept only so the `FW-NNNN` ids still
referenced from those ADRs resolve.

| ID | Title | Implemented in |
|----|-------|----------------|
| FW-0004 | Position-based task-rule engine over landmarks | [ADR-0023](decisions/0023-position-based-rules-engine.md) |
| FW-0007 | Maze-state consumers - rule-shaping + behavior stats | [ADR-0026](decisions/0026-reward-shaping-phases.md), [ADR-0032](decisions/0032-live-behavior-readout.md) |
| FW-0012 | Run tracking off the camera grab thread | [ADR-0040](decisions/0040-tracking-off-grab-thread.md) |
