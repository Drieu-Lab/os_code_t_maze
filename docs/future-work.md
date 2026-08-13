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
| [FW-0009](#fw-0009-triggerdispenser-decomposition-of-the-well-reward-rule) | Trigger/Dispenser decomposition of the well-reward rule | Rules engine | Done (ADR-0068) |
| [FW-0010](#fw-0010-deeplabcut-live-pose-estimation-for-body-part-tracking) | DeepLabCut-Live pose estimation for body-part tracking | Markerless tracker | Proposed |
| [FW-0011](#fw-0011-richer-colour-model-for-clustered-fragments-multimodalper-part) | Richer colour model for clustered fragments (multimodal/per-part) | Markerless tracker | Proposed |
| [FW-0013](#fw-0013-free-cpu-cores-during-recording-nvenc-or-encode-thread-cap) | Free CPU cores during recording (NVENC or encode-thread cap) | Camera recording | Proposed |
| [FW-0014](#fw-0014-zone-entry-latency-floor-for-a-vision-triggered-cue-path) | Zone-entry latency floor for a vision-triggered cue path | Maze state + rules | Partially done |
| [FW-0015](#fw-0015-scoped-ratblocker-de-merge-restore-splitting-only-if-needed) | Scoped rat/blocker de-merge (restore splitting only if needed) | Markerless tracker | Proposed |
| [FW-0016](#fw-0016-migrate-trial-sequence--cue-identity-from-firmware-to-host) | Migrate trial-sequence / cue-identity from firmware to host | Firmware + protocol + host | Partially done (ADR-0046) |
| [FW-0017](#fw-0017-end-to-end-cue-onset-latency-from-the-firmware-cue-echo) | End-to-end cue-onset latency from the firmware CUE echo | Validation harness | Proposed |
| [FW-0018](#fw-0018-hardware-session-sync-fiducial-firmware-sync-ttl--camera-visible-led-barcode) | Hardware session sync fiducial (firmware `SYNC` TTL + camera-visible LED barcode) | Firmware + protocol + host session/camera | Proposed |
| [FW-0019](#fw-0019-one-reward-seam--rate-backstop-across-the-task-controllers) | One reward seam + rate backstop across the task controllers | Rules engine + Phase 3 host | Done |
| [FW-0020](#fw-0020-shared-choice-commit-edge-in-the-task-event-layer) | Shared choice-commit edge in the task-event layer | Task-event layer + task controllers | Done |
| [FW-0023](#fw-0023-shared-preview-producer-loop-helper) | Shared preview *producer* loop helper | Preview helpers + tab modules | Done |
| [FW-0024](#fw-0024-config-driven-phase-3-taskreward-parameters-parity-with-rules) | Config-driven Phase 3 task/reward parameters (parity with Rules) | Phase 3 host + config | Done (ADR-0070) |
| [FW-0025](#fw-0025-surface-the-brokers-link-health-in-the-gui) | Surface the broker's link health in the GUI | Broker + GUI status | Proposed |
| [FW-0026](#fw-0026-a-mid-session-gate-reopen-should-not-restart-the-session-and-overwrite-its-file) | A mid-session gate reopen should not restart the session and overwrite its file | Phase 3 host | Proposed |
| [FW-0027](#fw-0027-one-triggereffect-framework-behind-phases-1-2a-and-3) | One trigger/effect framework behind Phases 1, 2a and 3 | Rules engine + task-event layer + Phase 3 host | Proposed |
| [FW-0028](#fw-0028-scope-the-session-summary-to-the-startstop-window) | Scope the session summary to the Start/Stop window | Session controller + summary + stats cores | Proposed |
| [FW-0029](#fw-0029-real-water-timestamps--hours-since-water-in-the-daily-table) | Real water timestamps + hours-since-water in the daily table | Daily husbandry table + animal_state | Done (ADR-0101) |
| [FW-0030](#fw-0030-weekend-ad-libitum-water-accounting-in-the-daily-table--dashboard) | Weekend ad-libitum water accounting in the daily table + dashboard | Daily husbandry table + dashboard | Done (ADR-0100) |
| [FW-0031](#fw-0031-latching-toggle-on-note-tags-that-span-subsequent-trials) | Latching (toggle-on) note tags that span subsequent trials | Note tags + session notes + summary + Session panel | Proposed |
| [FW-0032](#fw-0032-per-port-leftright-reward-volume-calibration) | Per-port (left/right) reward-volume calibration | Reward calibration | Proposed |
| [FW-0033](#fw-0033-structural-cross-reference-hardening-for-the-coordinated-records) | Structural cross-reference hardening for the coordinated records | Records cores + Records Manager | Proposed |
| [FW-0034](#fw-0034-frozen-identity-columns-in-the-daily-table) | Frozen (pinned) identity columns in the Daily table | Records Manager Daily tab | Deferred |

## Entries

### FW-0001: ~60/40 same/switch transition bias in sequence generation

- **Status:** Deferred
- **Date added:** 2026-06-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/cue-guided-task/task_core.h` (`generateSequence`), `firmware/common/rig_config.h` (a new `SAME_SIDE_FRACTION` knob), `firmware/cue-guided-task/test/test_task_core.cpp`
- **Related ADRs:** [ADR-0009](decisions/0009-runtime-training-modes-and-logging.md) (added the anti-bias run-length cap; this is the deferred half of the same anti-bias goal), [ADR-0086](decisions/0086-phase3-debias-blocks.md) (a *sibling* host-side `debias_blocks` generator)

**Update (2026-08-09).** [ADR-0086](decisions/0086-phase3-debias-blocks.md) added a
selectable host-side `debias_blocks` generator (bias-triggered corrective *blocks*
that force the non-preferred side until the animal reliably switches). That is a
*different* generator from the ~60/40 transition-bias scheme sketched here — it
reacts to a developing bias in closed loop rather than baking a fixed transition
ratio into the open-loop schedule — so this item **remains open**. The generator
selector (`sequence.generator`) that ADR-0086 introduced is the natural place a
future `transition_bias` generator would slot in.

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

**Update (2026-08-09).** The closed-loop *bias-correction* half is now handled
**host-side** by [ADR-0086](decisions/0086-phase3-debias-blocks.md): a
`debias_blocks` generator runs a corrective block (forcing the non-preferred side,
with a 3-consecutive-correct exit + a length cap) when a directional-error bias
develops. It differs from the per-error "repeat the errored side once" idea below,
and lives in the host (where Phase 3 sequencing/scoring already lives, ADR-0046),
not the firmware. What REMAINS for FW-0008 is therefore the *firmware-native* path
only: per-error correction trials inside `task_core.h::generateSequence` and/or
folding the cue into the firmware trial FSM — relevant only if Phase 3 sequencing
ever moves back into firmware.

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

- **Status:** Done — implemented in [ADR-0068](decisions/0068-trigger-dispenser-well-reward-split.md)
  (2026-07-30): `RewardTrigger` + `WellDispenser` extracted into the pure
  `host/reward_dispenser.py`; `WellRewardWithExcursionRule` is now wiring, with
  `test_well_reward.py` passing unchanged and new `test_reward_dispenser.py`
  isolation tests. The notes below are kept for the record.
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

- **Status:** Done - implemented under
  [ADR-0055](decisions/0055-shared-choice-commit-edge.md). Added
  `TaskEventFrame.choice_committed` + `trip_id` (a once-per-outbound-excursion
  edge in `host/task_events.py`, re-armed on HUB presence, consumed on the first
  choice entry regardless of stem); the rules `choice`/`choice_via_stem`
  credentials, `Phase3Task` scoring, and `behavior_stats` now all read it, and
  each dropped its own private latch. Chose "Option A" on both open questions
  (fire regardless of stem; re-arm on HUB presence): the rules reward path and the
  readout are unchanged on the shipped profiles; Phase 3 changes only the rare
  non-stem-first-entry case (that trip yields no score, but the sequence trial is
  preserved, not burnt). Behavior pinned by tests before and after.
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

### FW-0023: Shared preview *producer* loop helper

- **Status:** Done - implemented directly (a mechanical UI-loop extraction, no
  ADR, as anticipated below). Added `preview.PreviewLoop`, a small opt-in driver
  taking `(frame, label, interval_ms, max_w, source, draw, is_closed)` that owns
  the `after`-based re-arm, the closed/viewable/`TclError` guards, the
  newest-frame fetch, the scale-to-`max_w`, the `PhotoImage` retention, and
  teardown (reusing `safe_after` + `render_bgr_to_label` unchanged). The three
  tabs with this loop - `tracker_view.py`, `maze_state_view.py`, `rules_view.py` -
  now build one `PreviewLoop` and pass a `_draw_preview(disp, scale)` overlay
  callback; their `_schedule_preview`/`_render_preview` pairs and per-tab
  `_photo_preview` refs are gone. Tracker's mask render folded into its draw
  callback (same per-frame `result`); rules recovers the full-frame size from
  `disp.shape / scale`. No visible change. **Phase 3 excluded:**
  `phase3_controller.py` has no live-preview loop to collapse (it consumes the
  frame *subscription* for task processing, never rendering frames to a Tk
  label), which answers this item's open question about the Phase 3 view.
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

- **Status:** Done — implemented in
  [ADR-0070](decisions/0070-config-driven-phase3-parameters.md) (versioned
  `host/phase3.json` + pure `host/phase3_config.py`; reward/cue editable with Save,
  scientific sequence/tones file-only + Reload, optional pinned seed). Started flat
  (no profiles), as recommended below.
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
task tabs. Update: [ADR-0069](decisions/0069-phase3-reward-topups-via-shared-dispenser.md)
added editable reward `pulse_ms` + `max_topups` Tk vars (applied on arm), but like
the other Phase 3 knobs they still reset every launch and aren't recorded in a
config file — so the persistence/reproducibility gap this item closes remains.

**Sketch of approach.** Add a versioned `host/phase3.json` with `reward` (port,
pulse_ms, max_topups, reward_incorrect), `sequence` (max_trials, block_size, max_same_run,
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

### FW-0025: Surface the broker's link health in the GUI

- **Status:** Proposed
- **Date added:** 2026-07-27
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/rig_broker.py` (would need a way to tell clients),
  `host/rig_io.py` (`RigClient`), a status indicator in `host/experiment_control.py` (Rig
  view bottom strip, near the Session panel)
- **Related ADRs:** [ADR-0062](decisions/0062-broker-bounded-writes-and-liveness-probe.md)
  (the detection this would display),
  [ADR-0002](decisions/0002-transparent-line-passthrough.md) (the passthrough rule
  any broker->client status message has to work around)

**Motivation / context.** ADR-0062 makes the broker notice a wedged Arduino within
~25 s, but it only says so in the **broker's own console window**. From the GUI a
dead rig still looks like a quiet one: the readouts simply stop updating. During
the 2026-07-27 lockouts (`#260727_SerialLockout`) the operator kept clicking
manual-feed buttons and only learned something was wrong on closing the GUI. An
on-screen indicator would have said so immediately.

**Sketch of approach.** The awkward part is that the broker is a verbatim line
passthrough (ADR-0002), so it has no channel for talking *about* the link. Options
worth weighing: a distinguishable out-of-band line (e.g. a `#`-prefixed
`#LINK,DEAD` that `protocol.parse` already rejects, at the cost of denting the
passthrough guarantee); a small side channel on a second TCP port; or keeping the
broker silent and having each client run its own `LivenessMonitor` over the lines
it receives — which needs no broker change at all, since `serial_liveness.py` is
already a reusable pure core, but duplicates the probe decision per client and
would have several clients writing `STATUS`. The client-side variant probably wins
if the client only *watches* (never probes) and treats the broker's probe traffic
as the stimulus.

**Evaluation / acceptance.** With the Due wedged on the bench (or its cable
pulled), a Rig-view indicator turns red within ~30 s and names the RESET remedy;
it stays green across a 10-minute healthy session with a resting rat (no false
alarms during legitimate quiet); and no extra `STATUS` traffic appears per
connected client.

**Effort / risk.** Small-to-medium. Main risk is eroding ADR-0002's passthrough
guarantee for a cosmetic gain — hence the preference for a watch-only client-side
monitor.

**Open questions.** Watch-only per client, or one authoritative verdict from the
broker? Where does the indicator belong (Rig view strip, Session panel, or the
window title)? Should a dead link also block/grey the manual actuation buttons, or
just warn?

**ADR needed?** Yes if the broker gains an out-of-band message (it narrows
ADR-0002); no if clients only watch the existing stream.

### FW-0026: A mid-session gate reopen should not restart the session and overwrite its file

- **Status:** Proposed
- **Date added:** 2026-07-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/phase3_controller.py` (`_process_frame` gate edges,
  `_arm_session`), `host/test_session_phase3_wiring.py`
- **Related ADRs:** [ADR-0066](decisions/0066-rebind-phase3-output-on-session-start.md)
  (surfaced this while fixing the neighbouring bug),
  [ADR-0058](decisions/0058-rig-view-trial-control-buttons.md) (the `_r<n>`
  restart tag this would reuse),
  [ADR-0047](decisions/0047-phase3-return-schedules-and-session-log.md) (the data
  file)

**Motivation / context.** Phase 3 treats the cue gate's rising edge as "start a
fresh session": `_arm_session` regenerates the sequence from the seed at trial 1
and re-binds the data-file path. That is right for the *first* arm, but the gate
also closes and reopens for mundane reasons mid-session — the operator pausing
tracking to reposition the rat or clear the camera view, or a brief firmware mode
change. When that happens **during** an active session, `_arm_session` runs with
`restart_index=0`, recomputes the same `phase3_<session_id>.csv` name, and
overwrites the session's file with a blank plan at trial 1. The trials already run
are lost from disk, and the operator gets no warning.

This is pre-existing behaviour, not introduced by ADR-0066, but that ADR made it
visible: now that Start reliably binds the file into the session folder, a reopen
clobbers the *session's* record rather than a stray `host/data/` file. It has not
been seen on the rig yet, which is why it was left out of that change.

**Sketch of approach.** Distinguish "first arm of this session" from "the gate
flickered". A rising edge that happens while a session context is set and a log
already exists for that same `session_id` should either (a) resume — keep the
existing task, sequence and file, and only redo the DAC-authority handshake
(`CUE,VISION`) plus the per-trip cue/gate reset that `_silence_and_rearm` already
does — or (b) restart explicitly, via the existing `_restart_count` path so the
prior run is preserved as `_r<n>` rather than overwritten. Option (a) matches what
an operator briefly pausing tracking expects; option (b) is safer if a reopen
should always mean a clean run. Either way the fix is confined to the edge
handling in `_process_frame`, and the file-clobbering half is a one-line
consequence of reusing `_restart_count`.

**Evaluation / acceptance.** Extend `test_session_phase3_wiring.py`: with a
session active and several trials run, drop and restore tracking, then assert the
session's CSV still contains the completed trials and (for option a) that the
trial index did not reset. On the rig, pause and resume tracking mid-session and
confirm the ribbon and the file both survive.

**Effort / risk.** Small. The risk is behavioural rather than technical: a resume
must not leave stale cue state sounding or skip the `CUE,VISION` handshake, and
whichever semantics is chosen needs to be the one the operator expects — getting
it wrong silently changes what a "session" means.

**Open questions.** Resume or explicit restart? Should a mid-session reopen be
surfaced in the status line / stats panel so the operator knows it happened? Does
a firmware mode change out of Free Roam mean something different from tracking
being toggled?

**ADR needed?** Yes - it redefines what a gate rising edge means mid-session,
which is exactly the kind of non-obvious semantics a future reader would query.

### FW-0027: One trigger/effect framework behind Phases 1, 2a and 3

- **Status:** Proposed
- **Date added:** 2026-07-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/task_events.py` (the shared predicate vocabulary),
  `host/rules_engine.py` + `host/rules.json` (the Phase 1/2a/2b rule templates),
  `host/phase3_task.py` + `host/phase3_controller.py` (the hand-written Phase 3
  state machine), `host/cue_gate.py` (the cue as one more gated effect)
- **Related ADRs:** [ADR-0026](decisions/0026-reward-shaping-phases.md) (the
  credential-gated well rule that unified Phases 1/2a/2b),
  [ADR-0046](decisions/0046-host-owned-phase3.md) (Phase 3 as a separate
  host-owned controller), [ADR-0055](decisions/0055-shared-choice-commit-edge.md)
  (the first predicate deliberately shared across both controllers),
  [ADR-0054](decisions/0054-unified-reward-dispatcher.md) (the reward *effect*
  already unified), [ADR-0061](decisions/0061-return-cue-requires-outbound-excursion.md)
  and [ADR-0067](decisions/0067-choice-cue-requires-hub-departure.md) (the two cue
  credentials that prompted this)
- **Related FW:** [FW-0009](#fw-0009-triggerdispenser-decomposition-of-the-well-reward-rule)
  (the Trigger/Dispenser split - the same seam seen from the rules side),
  [FW-0024](#fw-0024-config-driven-phase-3-taskreward-parameters-parity-with-rules)
  (config-driven Phase 3 - the prerequisite that lets a phase be *described*
  rather than coded)

**Motivation / context.** Celine's framing (2026-07-30, while fixing the choice
cue): the rules and conditions behind Phase 1, Phase 2a and Phase 3 should not be
separate implementations but **one framework, of which each phase is an
expression** - a phase being a selection of which blocks are active. Today that is
half true. Phases 1, 2a and 2b already are exactly that: three `rules.json`
profiles over one `WellRewardWithExcursionRule`, differing only in which
credential they demand (`well` vs `choice` vs `choice_via_stem`) and a few
numbers. Phase 3 is a **hand-written state machine** in `phase3_task.py` plus a
controller that hard-codes its own trial-start edge, and it cannot be expressed as
a rules profile at all.

The seams have been converging on their own, one ADR at a time, which is the
evidence that the unification is real rather than aesthetic: the reward *effect*
is now shared (ADR-0054), the choice-commit *predicate* is now shared (ADR-0055),
and both cue credentials now come from the same task-event vocabulary as the
reward (ADR-0061, ADR-0067). What is left unshared is the **wiring** - which
predicate triggers which effect in which phase. The cost of leaving it is
duplication that drifts silently: `phase3_controller._process_frame` still derives
its own `HUB -> CENTRAL_STEM` edge with a private `_prev_zone` to stamp
`trial_started` in the data file, which is now *literally the same edge* as
`task_events.left_hub_via_stem`, computed twice.

**Sketch of approach.** Name the three layers that already exist implicitly and
make the third one data:

1. **Predicates** - `task_events.py`, already the shared vocabulary
   (`at_well`, `outbound_used_stem`, `left_hub_via_stem`, `choice_committed`,
   `loop_clean`, the run counters). Growing.
2. **Effects** - dispense reward (`RewardDispatcher`, ADR-0054), start/stop a tone
   (`CueGate` + `CUE_ON/OFF`), advance a trial, write a log record. Mostly shared.
3. **Bindings** - "when predicate P holds, do effect E, subject to gates G". This
   is the part that is code in Phase 3 and JSON in Phases 1/2a/2b. A phase becomes
   a named set of bindings; Phase 1 enables the well binding, Phase 2a adds the
   choice credential, Phase 3 adds the cue and trial-advance bindings.

Sequence matters more than the destination here. FW-0024 (config-driven Phase 3)
comes first, since a phase cannot be *described* until its parameters are data.
FW-0009 (Trigger/Dispenser) is the same seam approached from the rules side and
should be reconciled with this rather than done independently. The cheap first
step is **done** (2026-07-31): the `_prev_zone` duplication above is retired - the
`HUB -> CENTRAL_STEM` departure now lives once as the `task_events.departed_hub_via_stem`
edge (the rising edge of `left_hub_via_stem`, mirroring `well_arrived`), which the
Phase 3 controller consumes to stamp `trial_started` instead of re-deriving the
edge with a private `_prev_zone`.

**Evaluation / acceptance.** The shipped Phase 1/2a/2b profiles behave
identically (the existing `test_well_reward.py` / `test_task_events.py` suites
pass unchanged) and a Phase 3 session produces the same trial sequence, cue
onsets, scores and data file as the hand-written controller - ideally checked by
replaying a recorded session through both and diffing the outputs. The win is
measured in what disappears: no predicate computed in two places, and a new phase
addable without a new controller.

**Effort / risk.** The revised CONTRIBUTING #6 splits this item cleanly, and the
two halves have opposite risk. Naming a shared concept - growing a predicate in
`task_events.py`, extracting an effect into a tested pure core - is **encouraged at
any caller count** (it gives a concept one clear home and *reduces* what a reader
holds in their head), so the predicate and effect layers are pulled-forward,
incremental work, done one small ADR/step at a time (as they have been). The
deferred half is the **bindings layer**: a generic, data-driven predicate->effect
engine / phase-config interpreter is an *indirection layer* - the exact
speculative-generality #6 says to defer until 2-3 real callers need it. A generic
rules engine that no one can read is worse than three readable controllers, so the
binding layer waits until a *fourth* phase or a second Phase 3 stage (FW-0008
correction trials) makes the pattern undeniable. The overall item stays Proposed
because only that binding engine is deferred; the incremental naming/extraction
steps (the `_prev_zone` retirement was the first) are ordinary work.

**Open questions.** The revised-#6 split gives a single test for each - "is this
naming a concept, or building an interpreter?" - which tips the answers toward
keeping code and only sharing vocabulary: (1) Phase 3's trial sequencing is
probably a state machine that should stay a named pure core (`phase3_task.py`) and
*consume* shared predicates, not be re-encoded as binding data (that would be the
interpreter). (2) The cue likely stays its own named effect - `CueGate`'s
fail-open/fail-safe onset behaviour (ADR-0045) is not a simple edge->impulse and
resists folding into the same table as reward. (3) One config file per phase vs one
table with a phase column is a property of the *deferred* binding layer, so it need
not be answered until that layer is warranted. The scoping question underneath all
of it: how much of this is real unification versus renaming the parts that are
already shared - and #6 answers that the renaming/naming part is worth doing on its
own merits, independent of the engine.

**ADR needed?** Yes - it would redefine how every phase is specified, and the
scoping decision (what stays code) is the substance of it.

### FW-0028: Scope the session summary to the Start/Stop window

- **Status:** Proposed
- **Date added:** 2026-07-31
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/session_summary.py`, `host/session_controller.py`
  (`_write_session_summary`), `host/phase3_stats.py` / `host/behavior_stats.py`
  (the stats cores the summary folds)
- **Related ADRs:** [ADR-0071](decisions/0071-session-behavioral-summary.md) (the
  end-of-session summary this refines), [ADR-0052](decisions/0052-phase-aware-behavior-readout-and-phase3-stats.md)
  / [ADR-0032](decisions/0032-live-behavior-readout.md) (the two stats cores),
  [ADR-0060](decisions/0060-session-button-owns-phase3-session.md) (Session Start/Stop
  vs Phase 3 arm), [ADR-0066](decisions/0066-rebind-phase3-output-on-session-start.md)
  (the file rebind at Start)

**Motivation / context.** The ADR-0071 summary is measured **"since Phase 3
arm"**, not "since Session Start": `published_state()` reflects the whole Phase 3
table since arm, and the locomotor accumulator resets on the Phase 3 arm edge. In
the normal arm-then-Start-then-Stop workflow that equals the session, but it
over-counts if the operator ran trials before pressing Start, or ran several
Start/Stop cycles on a single arm. The summary records its `scope` so a reader
isn't misled, but a summary that covered *exactly* the recording window would be
more faithful, especially for multi-block or paused sessions.

**Sketch of approach.** Two halves, matching the two data sources: (a) **cued
stats** - have `phase3_stats.compute` accept an optional `[t0, t_end]` wall-clock
window (from the manifest anchors) and count only trials whose `choice_time_iso`
(or `trial_start_iso`) falls inside it, so the cued group is Start/Stop-scoped;
(b) **locomotor stats** - reset the `BehaviorView._p3_stats` accumulator on
**Session Start** (a new reset seam driven by the Session panel) rather than on
Phase 3 arm, so its window is the recording. Keep the "since arm" behavior as a
fallback when no session window is available.

**Evaluation / acceptance.** With trials run before Start and after Stop, the
summary's completed/accuracy/compliance counts match only the in-window trials
(cross-checked against the CSV rows filtered by `t0`/`t_end`); the normal
workflow's numbers are unchanged; a paused/multi-block session sums correctly.

**Effort / risk.** Low-medium. Main risk is timestamp edge cases (a trial
straddling Start/Stop, missing stamps) and adding a Session->BehaviorView reset
seam without muddying the ADR-0052 phase-aware drive.

**Open questions.** Window a trial by choice time or by trial-start? How to treat
a trial straddling the boundary? Should the JSON carry both the windowed and the
since-arm totals for transparency?

**ADR needed?** Yes - it changes what window a session's reported behavior covers
(a small follow-up to ADR-0071).

### FW-0029: Real water timestamps + hours-since-water in the daily table

- **Status:** Done — implemented in
  [ADR-0101](decisions/0101-real-water-timestamps-hours-since-water.md)
  (2026-08-11). Deviations from the sketch below, all deliberate: the `water_on`/
  `water_off` columns stay **tolerant free text** (no hard Entry-sheet rejection —
  the real log has legitimate irregular entries), the time is derived
  best-effort, and `hours_since_water` is computed from the most recent parseable
  *prior* water-off (usually the previous evening) rather than the current row's
  own off-time. The historical messy cells are left for the operator (ADR-0097).
- **Date added:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `os_code_t_maze_resources/records/animal/daily_log.csv` +
  `build_daily_log.py` (the `water_on` / `water_off` columns + Entry-sheet
  validation), `host/daily_log.py` (parse), `host/animal_state.py`
  (`last_water_on` / `last_water_off` / `hours_since_water`)
- **Related ADRs:** [ADR-0076](decisions/0076-daily-husbandry-table-source-of-truth.md)
  (the daily table), [ADR-0077](decisions/0077-pre-session-animal-state-in-bundle.md)
  (the in-bundle animal state that would consume real timestamps)

**Motivation / context.** The daily husbandry table records when water was turned
on/off as **free text** ("6pm", "5:30pm", "Till Monday evening"), carried over
verbatim from the paper log. ADR-0077 therefore stores `last_water_on` /
`last_water_off` as strings and leaves `hours_since_water` **null** - even though
time-since-water is a direct measure of the animal's motivational state for a
water-rewarded task, and would be a valuable per-session covariate. Making it a
real number requires the underlying data to be real timestamps first.

**Sketch of approach.** Give the daily table real datetime fields for water
on/off (Entry-sheet date+time validation, ISO in the CSV), keeping a free-text
"note" for the irregular cases ("till Monday evening" -> an explicit datetime or a
flag). `daily_log.py` parses them to datetimes; `animal_state.from_daily_row`
computes `hours_since_water` as (session start - last water off), and Session
Start can fall back to a typed value in the dialog. Preserve backward
compatibility: rows with the old free-text form still load (hours stays null).

**Evaluation / acceptance.** For rows with real timestamps, `hours_since_water`
computes and lands in `animal_state.json`; free-text legacy rows still load with a
null; the Entry sheet rejects an un-parseable time; a unit test covers both forms.

**Effort / risk.** Low-medium. Main tension is human data-entry friction (typing a
precise time vs "6pm") and the irregular "till Monday" cases - needs a tolerant
format plus a clear convention, not a hard schema that the lab fights.

**Open questions.** One combined datetime or a date + time-of-day? How to encode
the "kept on over a weekend" case? Should the rig also stamp its own "water off at
session start" event to cross-check the table?

**ADR needed?** Yes - it changes the daily-table schema and how a motivational
covariate is derived (a small follow-up to ADR-0076/0077).

### FW-0030: Weekend ad-libitum water accounting in the daily table + dashboard

- **Status:** Done — implemented in [ADR-0100](decisions/0100-ad-lib-water-regime.md)
  (2026-08-11): a `water_regime` column (daily_log schema v1->v2) + dashboard
  interpretation + a Records Manager Regime control and "Mark ad-lib span"
  dialog. The chosen answers to the open questions below: a per-row
  `water_regime` value (not a span marker), the within-day on/off boundary reuses
  the existing `water_on`/`water_off` cells, and ad-lib days feed the % baseline
  trend but NOT the maintenance-water trace.
- **Date added:** 2026-08-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `os_code_t_maze_resources/records/animal/daily_log.csv` +
  `build_daily_log.py` (a way to mark ad-lib days), `host/tmaze/core/records/dashboard.py`
  (`water_ml_trend` / `not_logged_today` / `weight_status` interpretation),
  `host/tmaze/ui/records_manager.py` (dashboard rendering of ad-lib days)
- **Related ADRs:** [ADR-0087](decisions/0087-dashboard-trend-charts.md) (the
  water trace this qualifies), [ADR-0076](decisions/0076-daily-husbandry-table-source-of-truth.md)
  (the daily table), [ADR-0077](decisions/0077-pre-session-animal-state-in-bundle.md)
  (pre-session state)

**Motivation / context.** Water is available **ad libitum on weekends** (typically
Friday evening through Sunday afternoon) - the rats are off water restriction for
that span. The daily husbandry table and the dashboard don't model this. As a
result an ad-lib weekend day looks like either a "not logged / not weighed" alarm
(no restriction data to enter) or, once the optional water trace (ADR-0087) is on,
a spuriously large/absent water bar that distorts any read of the *maintenance*
water level (the amount that holds the animal at target during restriction). The
scientific point is that maintenance-level inference should be computed over
restriction days only; ad-lib days are a different regime.

**Sketch of approach.** Give the daily table an explicit way to mark a day (or a
span) as ad-lib / off-restriction (e.g. a `water_regime` column or a recognized
note flag), set once for the weekend rather than per row. Teach the dashboard to
treat ad-lib days distinctly: exclude them from the not-logged/not-weighed alarm
(they're expected blanks), and either omit them from the water-maintenance trace
or draw them in a visibly different style (so the eye doesn't read a weekend as a
restriction-day intake). Keep the % baseline trend showing them (weight is still
meaningful), just annotated as ad-lib.

**Evaluation / acceptance.** A marked Fri-evening->Sun weekend no longer raises a
not-logged alarm; the water trace either drops those days or renders them
distinctly; the % baseline line still plots them; a unit test covers the
ad-lib-day classification and its effect on the signals.

**Effort / risk.** Low-medium. Main tension is data-entry friction (marking a span
without a row per day) and picking a convention the lab will actually keep. Couples
to the water-entry clean-up and FW-0029 (real water timestamps).

**Open questions.** A per-row `water_regime` value or a span marker? Fixed
Fri-evening->Sun-afternoon default, or explicitly entered each weekend? Should
ad-lib days feed the maintenance-level estimate at all, or only the weight trend?

**ADR needed?** Yes - it changes the daily-table schema and how the dashboard
interprets welfare signals (a follow-up to ADR-0076/0087).

### FW-0031: Latching (toggle-on) note tags that span subsequent trials

- **Status:** Proposed
- **Date added:** 2026-08-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/records/note_tags.py` (a tag *kind*: point
  vs latching/interval, alongside the existing `counter` flag),
  `host/tmaze/core/session/session_notes.py` (represent an interval - an ON edge
  paired with an OFF edge, or an explicit span - not only point entries),
  `host/tmaze/ui/session_controller.py` (a toggle button that stays lit while
  active + stamps the ON/OFF edges; the running state must survive across trials),
  `host/tmaze/core/session/session_summary.py` (`_tag_tally` / notes rendering -
  summarize an interval tag as its span + the trial range it covered), optionally
  the Phase 3 per-trial data file (`phase3_log.py`) so downstream analysis can
  mark which trials fell under an active condition
- **Related ADRs:** [ADR-0074](decisions/0074-timestamped-note-entries.md)
  (timestamped note entries + the user-defined tag vocabulary this extends)

**Motivation / context.** Celine (2026-08-10): some interventions are a
**condition over a block of trials**, not a point-in-time event. The concrete
case is dissuading the animal from non-stem excursions: to do it she must **stand
by the rig for a block of trials and cannot be at the PC** to press a tag per
note, per trial. Two things follow. (1) The current tags (ADR-0074) are
point/one-press events (even a `counter` tag stores one stamped entry per press),
so marking "I was dissuading during these trials" means pressing once per trial -
which is exactly what standing at the rig prevents. (2) Even when she does *not*
actively dissuade on a given trial, **her standing beside the rig still influences
the animal's behavior**, so the whole span - not just the trials where she
intervened - is the scientifically relevant annotation. A tag that can be
**toggled ON and applies to all subsequent trials until toggled OFF** captures the
condition faithfully and hands-free.

**Sketch of approach.** Add a third tag *kind* to the vocabulary (today a tag is
`{name, counter}`): a **latching / interval** tag. Toggling it ON stamps an
interval-start entry (iso + monotonic + current Phase 3 trial index); toggling it
OFF stamps the matching end (or the session Stop closes any still-open interval).
`session_notes` gains a way to represent the interval (simplest: a paired
`tag_state` on/off on the existing timestamped entries, so an interval is
reconstructable from the ordered log and no schema-breaking span object is needed;
survives an unclean end because both edges are flushed like every other entry).
The Session-panel tag button for a latching tag becomes a **toggle** that stays
visibly lit while active, so the operator can flip it once from the PC and then
walk to the rig for the whole block. The summary renders an interval tag as its
duration and the **trial range it covered** (e.g. `dissuasion: trials 12-27,
4m10s`), and `_tag_tally` counts spans rather than presses. Optionally stamp an
"active conditions" set onto each Phase 3 trial row so the per-trial CSV can be
filtered by condition offline (the higher-value but larger half).

**Evaluation / acceptance.** Toggling a latching tag ON then OFF produces exactly
one reconstructable interval with correct start/end trial indices; a session
Stopped while a tag is still ON closes the interval at Stop (no dangling open
span); the summary shows the span + trial range; existing point/counter tags are
unchanged; pure-core tests cover the interval reconstruction and the summary
rendering (headless, like `test_note_tags` / `test_session_summary`). On the rig:
flip the toggle once, run a block hands-free at the rig, and confirm the whole
block is annotated.

**Effort / risk.** Medium. The pure-core pieces (tag kind, interval
reconstruction, summary rendering) are small and testable. Main risks: a dangling
ON interval if Stop/close doesn't close it (handle at `_stop_session`/`close`);
the UI toggle state drifting from the logged edges; and deciding whether the
per-trial CSV annotation is in scope (the larger, more valuable half) or deferred
to a follow-up. Keep it additive so existing tags and old logs still load.

**Open questions.** Represent an interval as paired on/off entries on the existing
timestamped log, or add an explicit span object (schema bump)? Should turning a
latching tag ON also allow free-text (why this block) on the ON edge? Should the
covered-trials range be computed from trial indices at the edges, or from
wall-clock against the trial timestamps? Is per-trial "active conditions" tagging
in the phase3 CSV part of this item or a separate one? Can multiple latching tags
be active at once (independent toggles)?

**ADR needed?** Yes - it adds a tag *kind* and an interval concept to the notes
model (a small follow-up to ADR-0074), and any per-trial condition column touches
the Phase 3 data file.

### FW-0032: Per-port (left/right) reward-volume calibration

- **Status:** Proposed
- **Date added:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/tools/reward_calibrate.py`, `host/reward_calibration.csv`, `host/tmaze/core/reward/reward_volume.py` (lookup already per-port)
- **Related ADRs:** [ADR-0091](decisions/0091-reward-volume-curve-per-liquid.md) (measured per-liquid curves + the per-`(port, liquid)` data file this would fill), [ADR-0007](decisions/0007-reward-at-central-port.md) (v1 rewards only the central port)

**Motivation / context.** ADR-0091 characterizes only the **central** spout today
(v1 delivers reward there). The calibration file, lookup, and tool are already
per-port, but the left/right reward valves have no measured curve, so a future
task that dispenses at the choice arms would fall back to the central-water curve
(possibly wrong for a different valve/line). When left/right reward comes into
use, measure their curves too - per liquid.

**Sketch of approach.** Run `python -m tmaze.tools.reward_calibrate --port left`
and `--port right` (per liquid) exactly as for center; the tool already fires
`R<port><sol>,<ms>` at the requested port and upserts rows keyed by
`(port, liquid, pulse_ms)`. No code change is expected - just measured data - but
confirm the reward-solenoid index and the physical valve mapping per port first
(rig_config.h `CONFIRM` tags), and check the left/right lines prime/settle like
the center one.

**Evaluation / acceptance.** `host/reward_calibration.csv` has left + right rows
per liquid; `reward_volume_ul(dur_ms, port="left"|"right", liquid=...)` returns
the measured curve rather than the central fallback; a spot-check volume at a
couple of widths matches the syringe.

**Effort / risk.** Small (mostly bench time). Risk: the left/right valve/line may
behave differently enough (dead volume, priming) that the same N-pulses method
needs a larger N or an extra settle time.

**Open questions.** Do left/right ever dispense in the actual task, or stay
monitor-only? If they never dispense reward, this may not be needed at all.

**ADR needed?** No - it fills existing per-port structure with data; ADR-0091
already covers the method. Add one only if a per-port structural change is found
necessary.

### FW-0033: Structural cross-reference hardening for the coordinated records

- **Status:** Proposed
- **Date added:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/session/session_registry.py`, `host/tmaze/core/records/{session_annotations,dashboard}.py`, `host/tmaze/ui/records_manager.py`
- **Related ADRs:** [ADR-0097](decisions/0097-records-integrity-guardrails.md) (the audit that surfaced these), [ADR-0075](decisions/0075-canonical-animal-identity.md) (loose id normalization), [ADR-0085](decisions/0085-records-manager-gui-reversible-surfaces.md) (annotations denormalization + deletions don't cross surfaces)

**Motivation / context.** The `#260810_RecordsIntegrity` audit (ADR-0097)
addressed the visible/active problems (non-real sessions, husbandry-gap and
water-column reporting, legacy/rig de-dup). Three *structural* cross-reference
weaknesses were found but deliberately left for later, being larger and lower
urgency (finding F of the audit):

- **No alias normalization at join time.** `session_registry.bundle_row` and the
  daily-table join match `(animal, date)` by exact stripped-string equality; only
  Session Start normalizes ids via `animals.normalize`. A bundle whose `animal`
  is a slug/alias (e.g. from the ADR-0075 unknown-id slug fallback) silently
  misses the `(animal, date)` daily-log join.
- **Denormalized annotation `animal`/`date`.** `session_annotations.csv` stores
  copies the Records Manager fills from the registry row (blank if the session
  isn't in a stale/never-regenerated registry); they could be derived from the
  `session_id` instead so they never disagree with the id.
- **Ghost annotations.** Per ADR-0085 deletions don't cross surfaces, so deleting
  a bundle leaves an annotation row with no live session; the GUI merges
  annotation-only rows back into the Sessions list, resurfacing ghosts.

**Sketch of approach.** Normalize `animal` through `animals.normalize` (falling
back to the raw id when unknown) at the registry join seam; derive annotation
`animal`/`date` from `session_id` (`_animal_date_from_id`) rather than the
registry copy; add a "ghost annotation" section to `records_check` (annotation
rows whose `session_id` is neither a live bundle nor ignored) so stale rows are
visible before deciding whether to prune them.

**Evaluation / acceptance.** A bundle with an alias animal id joins its daily-log
row; annotation `animal`/`date` always match the id; `records_check` lists ghost
annotation rows. No change to on-disk schemas (behaviour only), existing tests
still pass, new headless tests cover the normalization + ghost detection.

**Effort / risk.** Medium. Risk: normalizing at join time could change which
daily row a historical bundle matches; guard with tests and keep it a
fall-back-to-raw so an unknown id is never dropped.

**Open questions.** Should ghost annotations ever be auto-pruned, or only
reported (given ADR-0085's "never silently lose an edit")? Does deriving
annotation `animal`/`date` from the id break any session id that contains an
underscore in the animal part?

**ADR needed?** Yes for the join-time normalization (it changes a cross-reference
key's matching); the ghost-annotation report can fold under ADR-0097.

### FW-0034: Frozen (pinned) identity columns in the Daily table

- **Status:** Deferred
- **Date added:** 2026-08-13
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/ui/records_manager.py` (Daily tab table)
- **Related ADRs:** [ADR-0103](decisions/0103-daily-table-weekly-cues.md) (the
  Daily-table weekly cues this would extend), [ADR-0102](decisions/0102-dashboard-weekly-structure-view.md)

**Motivation / context.** The Daily table now has 13 columns (identity + derived
Day/delta + husbandry + notes). On a narrow window the operator may scroll right
and lose sight of which `animal_id`/`date`/`day` a cell belongs to. Pinning those
identity columns so they stay visible while the rest scrolls horizontally would
keep the context anchored. Deferred during Phase 2 of the weekly-structure work
(the operator chose to skip it for now; the identity columns are placed leftmost
as a lighter mitigation).

**Sketch of approach.** Tkinter's `ttk.Treeview` has no native frozen columns, so
the usual pattern is two side-by-side Treeviews: a left one holding the frozen
identity columns and a right one with the remaining columns, sharing one vertical
scrollbar (a `yview` proxy that drives both), with selection mirrored between the
two, row tints applied to both halves, and the double-click-to-load handler
working from either. The `_DAILY_TREE_COLUMNS` spec already lists the columns in
order, so it can be split at a "freeze index". An alternative is a third-party
widget (e.g. `tksheet`), which supports frozen columns natively but adds a
dependency.

**Evaluation / acceptance.** Horizontal scroll keeps `animal_id`/`date`/`day`
visible; row selection, tints, missing-entry placeholders, and double-click-load
behave identically to the single-table version; vertical scroll stays in sync
across both halves.

**Effort / risk.** Medium; mostly fragile GUI glue (scroll/selection sync, tint
mirroring) that is easy to get subtly wrong and adds comprehension debt for a
modest benefit - the reason it was deferred rather than built.

**Open questions.** Is the added GUI complexity worth it versus just trimming
columns, or adopting a table widget with native freeze support?

**ADR needed?** No (UI-only); a short note in a follow-up ADR or an update
referencing ADR-0103 suffices if it is built.

## Completed

Implemented items are removed from the active backlog above; their reasoning now
lives in the linked ADRs. This table is kept only so the `FW-NNNN` ids still
referenced from those ADRs resolve.

| ID | Title | Implemented in |
|----|-------|----------------|
| FW-0004 | Position-based task-rule engine over landmarks | [ADR-0023](decisions/0023-position-based-rules-engine.md) |
| FW-0007 | Maze-state consumers - rule-shaping + behavior stats | [ADR-0026](decisions/0026-reward-shaping-phases.md), [ADR-0032](decisions/0032-live-behavior-readout.md) |
| FW-0012 | Run tracking off the camera grab thread | [ADR-0040](decisions/0040-tracking-off-grab-thread.md) |
| FW-0021 | Single-parse serial fan-out in the GUI orchestrator | [ADR-0056](decisions/0056-single-parse-fanout-and-mode-reset-seam.md) |
| FW-0022 | Unified MODE-switch reset seam | [ADR-0056](decisions/0056-single-parse-fanout-and-mode-reset-seam.md) |
