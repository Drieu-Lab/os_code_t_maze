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
| [FW-0027](#fw-0027-one-triggereffect-framework-behind-phases-1-2a-and-3) | One trigger/effect framework behind Phases 1, 2a and 3 | Rules engine + task-event layer + Phase 3 host | Proposed |
| [FW-0028](#fw-0028-scope-the-session-summary-to-the-startstop-window) | Scope the session summary to the Start/Stop window | Session controller + summary + stats cores | Proposed |
| [FW-0029](#fw-0029-real-water-timestamps--hours-since-water-in-the-daily-table) | Real water timestamps + hours-since-water in the daily table | Daily husbandry table + animal_state | Done (ADR-0101) |
| [FW-0030](#fw-0030-weekend-ad-libitum-water-accounting-in-the-daily-table--dashboard) | Weekend ad-libitum water accounting in the daily table + dashboard | Daily husbandry table + dashboard | Done (ADR-0100) |
| [FW-0031](#fw-0031-latching-toggle-on-note-tags-that-span-subsequent-trials) | Latching (toggle-on) note tags that span subsequent trials | Note tags + session notes + summary + Session panel | Proposed |
| [FW-0032](#fw-0032-per-port-leftright-reward-volume-calibration) | Per-port (left/right) reward-volume calibration | Reward calibration | Proposed |
| [FW-0033](#fw-0033-structural-cross-reference-hardening-for-the-coordinated-records) | Structural cross-reference hardening for the coordinated records | Records cores + Records Manager | Proposed |
| [FW-0034](#fw-0034-frozen-identity-columns-in-the-daily-table) | Frozen (pinned) identity columns in the Daily table | Records Manager Daily tab | Deferred |
| [FW-0035](#fw-0035-alternation-intervention-block-step-1-toward-an-adversarial-schedule) | Alternation intervention block (step 1 toward an adversarial schedule) | Phase 3 host (`core/phase3/`) | Done ([ADR-0107](decisions/0107-phase3-alternation-intervention-stack.md)) |
| [FW-0036](#fw-0036-retire-the-reward_incorrect-phase-3-shaping-aid-candidate-for-removal) | Retire the `reward_incorrect` Phase 3 shaping aid (candidate for removal) | Phase 3 host + config | Proposed |
| [FW-0037](#fw-0037-zone-dwell--consummatory-well-time-from-tracking--licks) | Zone dwell + consummatory well time from tracking + licks | Behavior stats + session summary | Proposed |
| [FW-0038](#fw-0038-join-the-days-baseline--water-context-into-the-session-summary) | Join the day's %baseline / water context into the session summary | Session summary + records cores | Proposed |
| [FW-0039](#fw-0039-firmwarehost-git-sha-provenance-stamp-in-the-session-data) | Firmware/host git-SHA provenance stamp in the session data | Session log + data-file headers | Proposed |
| [FW-0040](#fw-0040-latent-state-glm-hmm-model-of-behavioural-mode) | Latent-state (GLM-HMM) model of behavioural mode | Cross-session analysis (`analysis/`) | Deferred |
| [FW-0041](#fw-0041-reward-pressure--mode-transition-characterization) | Reward-pressure / mode-transition characterization | Cross-session analysis (`analysis/`) | Deferred |
| [FW-0042](#fw-0042-live-excursion-based-engagement-traces) | Live excursion-based engagement traces (trips/min, non-stem fraction) | Phase 3 host + live trace panel | Proposed |
| [FW-0043](#fw-0043-automatic-probe-block-triggers) | Automatic probe-block triggers (position / elapsed time / reward volume) | Phase 3 host (`core/phase3/probe_blocks.py`) | Done (ADR-0134) |
| [FW-0044](#fw-0044-closed-loop-scheduler-rollback-on-a-manual-scored-re-run) | Closed-loop scheduler rollback on a manual scored re-run | Phase 3 closed-loop schedulers (`core/phase3/`) | Deferred |
| [FW-0045](#fw-0045-in-fov-event-led-visual-fiducial-for-videoserial-alignment) | In-FOV event-LED visual fiducial (sensor/solenoid) for video<->serial alignment | Camera vision + sidecar + rig LED wiring | Proposed |
| [FW-0046](#fw-0046-live-core-metrics-tab-fixes-engagement-definition--others) | Live-core Metrics-tab fixes (engagement definition + others) | Phase 3 live metrics (`core/phase3/phase3_traces.py`, `phase3_stats.py`) | Deferred |
| [FW-0047](#fw-0047-schedule-aware-side-bias-score-inside-intervention-blocks) | Schedule-aware side-bias score inside intervention blocks | Phase 3 closed-loop schedulers (`core/phase3/debias.py`, `intervention_stack.py`) | Proposed |
| [FW-0048](#fw-0048-carve-the-analysis-layer-into-its-own-repo) | Carve the analysis layer into its own repo | Cross-session analysis (`tmaze.analysis`) + packaging | Done (ADR-0146) |
| [FW-0049](#fw-0049-relative-stream-paths-in-the-session-manifest) | Relative stream paths in the session manifest (archive-drive portability) | Session manifest (`core/session/session_log.py`, `session_controller.py`) + analysis readers | Proposed |
| [FW-0050](#fw-0050-golden-fixture-bundle--schema-migration-tooling) | Golden fixture bundle + schema-migration tooling for the bundle contract | `host/tests/` fixtures + analysis readers/exporters | Partly done (ADR-0146: fixture shipped; migration tooling still open) |
| [FW-0051](#fw-0051-productionize-nwb-export--dandiephys-integration) | Productionize NWB export + DANDI / Neuropixels integration | Offline exporters (`tmaze.analysis.export_nwb`) + ephys | Deferred |
| [FW-0052](#fw-0052-video-storage-footprint--retention-policy) | Video storage-footprint / retention + derived-preview policy | Camera video (`ui/camera_view.py`) + recordings storage | Proposed |
| [FW-0053](#fw-0053-author-the-rigor-guidance-spokes--slim-the-agentsmd-hubs) | Author the rigor guidance spokes + slim the `AGENTS.md` hubs | Agent guidance (`AGENTS.md` both repos, `docs/guidance/`) | Proposed |
| [FW-0054](#fw-0054-measurement-rigor-checklist--grounded-uncertainty-norm-in-the-workflow) | Measurement-rigor checklist + grounded-uncertainty norm in the workflow | Agent guidance (`CONTRIBUTING.md`, `AGENTS.md`) | Proposed |
| [FW-0055](#fw-0055-glossary-terms-for-scientific--epistemic-rigor) | Glossary terms for scientific + epistemic rigor | `docs/glossary.md` | Proposed |
| [FW-0056](#fw-0056-camera-visible-cue-leds-on-spare-optocoupler-channels) | Camera-visible cue LEDs on spare optocoupler channels (hardware + firmware + `LEDTEST` roll-call) | Rig electronics board + `firmware/cue-guided-task` (`handleCueOn/Off`, `rig_config.h`, `PROTOCOL.md`) | Proposed |
| [FW-0057](#fw-0057-live-alt-veto-readout-for-the-suppressed-debias-trigger) | Live `[ALT-VETO]` readout for the suppressed debias trigger | Phase 3 live readout (`intervention_stack.py` -> snapshot -> `phase3_stats.py` -> `behavior_view.py`) | Proposed |
| [FW-0058](#fw-0058-reconcile-the-beam--and-lick-state-polarity-si1--li1-are-the-resting-states-not-broken--active) | Reconcile the beam- and lick-state polarity (`S,<i>,1` and `L,<i>,1` are the resting states in recorded logs; `PROTOCOL.md` rows corrected 2026-09-08, firmware comment + consumer audit open) | `firmware/PROTOCOL.md`, `cue-guided-task.ino` sensor reads, host consumers of the `S`/`L` `active` flag | Proposed |

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
- **Design doc (2026-09-02):** this entry is now **Phase 3** of
  [`docs/design/cross-stream-sync-design.md`](design/cross-stream-sync-design.md)
  (§5), which retargets the sketch below in three ways. (1) **Acquisition
  target:** the neural recording will be **Neuropixels over PXI** (SpikeGLX or
  Open Ephys Neuropix-PXI), not the Open Ephys acquisition board's I/O BNCs. Those
  systems already lock the probes to the NI digital-input card via the imec
  basestation's 1 Hz SMA sync, so the Due only has to reach **one NI digital-in
  line** (3.3 V TTL; confirm the card's threshold). (2) **Two rates from one
  sequence:** the standard Allen / Open Ephys 32-bit, 10 ms-per-bit barcode is
  right for NI (existing decoders) but invisible at ~30 fps, so a second Due pin
  drives a camera-facing LED - via a **spare optocoupler-module channel** on the
  electronics board, like the cue LEDs (FW-0056) - with a slow pattern (bits of
  >= 3 frame periods, e.g. 100 ms; 8-bit code every ~10 s). Both are generated
  from the same PRNG sequence number so a camera-decoded and an NI-decoded code
  name the same `seq`; the simpler one-slow-pattern-for-both variant is the bench
  alternative. (3) **Relation to Phase 1:** the board-LED fit (FW-0045) already
  gives video<->Arduino from natural events; this line adds the Arduino<->NI leg
  and a behaviour-independent, drift-proof fiducial, and its LED is a guaranteed
  time-labelled anchor for the constellation registration and the session-start
  roll-call. Everything below (the `SYNC` command, `<ms>,SYNC,<seq>` echo,
  `mark_session_start` seam, manifest `fiducial` record, graceful fallback) still
  applies.

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

### FW-0035: Alternation intervention block (step 1 toward an adversarial schedule)

- **Status:** Done — implemented as [ADR-0107](decisions/0107-phase3-alternation-intervention-stack.md)
  (`intervention_blocks` generator: `alternation.py` + `intervention_stack.py`,
  concurrent with the side-bias `debias_blocks`). One change from the sketch
  below: the detector is **cue-aware** (condition on repeat-cued trials), not the
  cue-independent switch rate first sketched — that resolves the "false positives
  on a real cue-user" open question. Exit is the relaxed in-block `alt_rate`
  (+ welfare cap), as preferred below.
- **Date added:** 2026-08-17
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/phase3/` (a new sibling to `debias.py`, e.g. `alternation.py`); wired through `trial_sequence.py` (`decide`/`record_outcome`, `sequence.generator` selector); `phase3_config.py` + `host/phase3.json` (config dataclass); `phase3_log.py` (per-trial `sched_state`/block columns); `host/tests/` (headless test mirroring `test_debias.py`)
- **Related ADRs:** [ADR-0086](decisions/0086-phase3-debias-blocks.md) (closed-loop side-bias correction via `debias_blocks` — this is the same idea aimed at a different habit), [ADR-0090](decisions/0090-debias-bias-normalized-by-errors.md) (error-count evidence gate, the pattern the switch-rate gate mirrors)

**Motivation / context.** Rats on the cued T-maze climb a ladder of "cheat"
strategies — side arms → side bias → alternation → [cue use] — and stall at
**alternation**, never stepping up to actually using the tone. The eventual fix
is an *adversarial* schedule that predicts the rat's next choice from its own
history and rewards the side it is predicted **not** to pick, so no history-based
habit wins and the (history-independent) cue becomes the only reliable way to
earn reward. The existing side-bias `debias_blocks` (ADR-0086) is that idea for
one habit (order-0: side bias). This item is the **next single rung**: an
intervention block that counters **alternation** (order-1: the switch habit),
built as a near-twin of `DebiasBlockController` so we can **beta-test the
intervention-block mechanism and its welfare tolerance on rats before building
the full adversarial predictor**. Success for this step is *not* "rats start
using the cue" (one block only closes one rung); it is "the block reliably
detects alternation, changes behaviour, and is welfare-tolerable."

**Sketch of approach.** A sibling pure-core controller (`AlternationBlockController`),
composed alongside — not merged into — the existing side-bias controller:

- *Detector (order-1 statistic).* Over a rolling window of the rat's **chosen**
  sides (cue-independent), compute the switch rate
  `p_switch = #switches / #transitions`. This is the maximum-likelihood estimate
  of P(switch | previous choice); the alternation strategy is the corner
  `p_switch → 1` (chance = 0.5, perseveration ≈ 0). Auto-trigger a block when a
  `min_transitions` evidence gate is met **and** `p_switch >= trigger_switch_rate`
  (≈ 0.8). A high threshold keeps a genuine cue-follower (whose switch rate
  inherits the schedule's ~0.5, bounded by `max_same_run`) from tripping it.
- *Intervention (adversarial placement).* Predict the alternator's next choice =
  **opposite of its last choice**, so place reward where it *won't* go: **forced
  correct side = the rat's last choice** (it must *repeat* to be rewarded). Unlike
  the fixed-side debias block, this target is **recomputed each trial** from the
  last scored choice (still fits the `decide()`/`record()` contract).
- *Exit.* Prefer **"switch rate returned to normal" (+ `max_block_len` welfare
  cap)** over "`exit_consecutive` correct in a row." Key finding: because a
  correct trial here *means* the rat repeated, and the next forced side is that
  same side, N-consecutive-correct is by construction N repeats on **one** side —
  so the naive exit *guarantees* the escape hatch is one-sided perseveration, and
  ~half the time on the rat's already-preferred side (reinforcing the very bias we
  fight). Decoupling exit from perseveration avoids this.
- *Guarding the bias slide via composition.* A cornered alternator's cheapest
  escape is to slide to win-stay/perseveration (a side bias). Rather than
  entangle two contingencies in one block, **run the existing `debias_blocks`
  side-bias controller concurrently** so it catches any resulting bias on the next
  window. This keeps each controller single-responsibility and independently
  testable, and doubles as the first test of the "stack the experts" composition
  that the full adversarial scheduler needs.

**Evaluation / acceptance.** Headless unit test mirroring `test_debias.py`
(synthetic alternator → block triggers, forces repeats, exits on normalized
switch rate or cap; balanced/cue-following stream → never triggers). Beta in
training sessions: log per-trial `p_switch`, block enter/exit reason, and where
the rat goes next (cue vs. new habit vs. disengage). Wins to look for: switch
rate drops inside/after blocks; blocks exit on the success criterion more than
the welfare cap; late-session drop-off stays bounded.

**Effort / risk.** Small-to-medium (a focused twin of an existing, tested core).
Risks: (a) **welfare** — an adversarial block pushes a committed alternator's
reward toward zero, not 50%, so the `max_block_len` cap + a manual-water floor
are load-bearing; (b) **false positives on a real cue-user** if the threshold is
set too low or the counterbalanced spine has a locally-alternating stretch;
(c) **closed-loop non-reproducibility** — like debias, the session is
reproducible only from (seed + realized choices + operator overrides), so the log
is the record of truth.

**Open questions.** Exit criterion — switch-rate-normalized (preferred) vs.
consecutive-correct vs. the self-contained two-phase "chain into a bias-guard
debias block" variant (considered, deferred in favour of concurrent composition).
Concurrency-vs-single-controller. Thresholds (`window`, `min_transitions`,
`trigger_switch_rate`, `max_block_len`). Whether to also make the detector
explicitly cue-independent (discount alternation that coincides with correct
cue-following).

**ADR needed?** Yes — when built, as a sibling/generalisation of ADR-0086
(record the switch-rate detector, the dynamic last-choice forced side, the
exit-criterion choice, and the concurrent-composition decision).

### FW-0036: Retire the `reward_incorrect` Phase 3 shaping aid (candidate for removal)

- **Status:** Proposed
- **Date added:** 2026-08-17
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/phase3.json` (`reward.reward_incorrect`), `host/tmaze/core/phase3/phase3_config.py` (`to_phase3_config`), `host/tmaze/core/phase3/phase3_task.py` (`Phase3Config.reward_incorrect` + the scoring/reward branch), `host/tmaze/core/phase3/phase3_stats.py` + the session summary (the reward-rate-hiding special case), any Phase 3 tab control
- **Related ADRs:** [ADR-0046](decisions/0046-host-owned-phase3.md) (host-owned Phase 3), [ADR-0070](decisions/0070-config-driven-phase3-parameters.md) (config-driven Phase 3 params), [ADR-0094](decisions/0094-trim-misleading-phase3-readouts.md) (added the reward-rate-hiding when this aid is on)

**Motivation / context.** `reward_incorrect` rewards every completed trial
regardless of correctness - an early-shaping aid to keep a not-yet-engaged rat
running the maze. It is **unused in practice** (the operator runs correct-choice-
gated reward), it pins the reward rate near 100% (so it needs a special case in
the stats/summary to avoid a misleading readout, ADR-0094), and it is
conceptually at odds with the intervention / adversarial-schedule work ([FW-0035](#fw-0035-alternation-intervention-block-step-1-toward-an-adversarial-schedule)),
whose whole mechanism is *withholding* reward from a habit-driven choice - with
this aid on, an intervention block has no teeth. It is one of several
experimental features added during development that the codebase will eventually
want pruned to limit comprehension debt (see `AGENTS.md`). Kept for now because it
is a plausible early-shaping tool for future cohorts, not obviously obsolete -
this entry just tracks the decision so it is not forgotten.

**Sketch of approach (if/when removed).** Drop `reward.reward_incorrect` from the
`phase3.json` default + `to_phase3_config`; remove the `reward_incorrect` field
and its scoring/reward branch in `phase3_task`; remove the reward-rate-hiding
special case in `phase3_stats` and the session summary (and any GUI toggle);
confirm nothing else keys on it. Record the reasoning in a new ADR, superseding
the `reward_incorrect`-specific part of ADR-0094.

**Evaluation / acceptance (criteria to actually remove).** Still unused after the
next 1-2 training cohorts and no early-shaping need arises for upcoming animals;
removal then validated by the Phase 3 tests passing and the stats/summary
remaining correct without the special case.

**Effort / risk.** Small-to-medium; low technical risk (isolated flag, not a hot
path). Main risk is losing a shaping tool a future animal might need - mitigated
by git history + the ADR.

**Open questions.** Any early-shaping scenario for the next animals that still
wants it? Should its removal be bundled into a broader "prune experimental
Phase 3 features" audit rather than done piecemeal?

**ADR needed?** Yes at removal time (record why; supersede the relevant part of
ADR-0094). None needed to merely keep tracking it here.

### FW-0037: Zone dwell + consummatory well time from tracking + licks

- **Status:** Proposed
- **Date added:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/behavior_stats.py`,
  `host/tmaze/core/phase3/phase3_stats.py`,
  `host/tmaze/core/session/session_summary.py`; the tracker/maze-state pipeline
  (zone occupancy) + the center-well lick stream
- **Related ADRs:** [ADR-0108](decisions/0108-cue-zone-dwell-readout.md)
  (relative cue-zone dwell),
  [ADR-0052](decisions/0052-phase-aware-behavior-readout-and-phase3-stats.md)
  (Phase 3 stats),
  [ADR-0048](decisions/0048-host-session-orchestration-and-manifest.md)
  (host timeline)

**Motivation / context.** ADR-0108 surfaced a RELATIVE cue-zone dwell derived
from two host-clock actuation stamps (`choice_cue_on_iso` -> `choice_time_iso`),
explicitly NOT a true time-in-zone: it carries uncharacterized actuation latency
and only measures cue-on -> choice, not how long the animal actually sat in the
cue zone or at the reward well. The tracker already computes zone occupancy each
frame and the center well already logs lick edges, so a *geometry-grounded* dwell
(frames the body centroid spent inside a named zone) and a *consummatory* well
time (first-to-last lick within a reward visit) are both recoverable and would be
more directly interpretable than the actuation-stamp proxy.

**Sketch of approach.** In the maze-state pipeline / `behavior_stats`, accumulate
per-zone dwell from frame occupancy (enter/exit edges x frame dt) and expose
per-trial or aggregate zone-dwell measures; derive a consummatory well time from
the center-well lick timestamps bracketing each reward visit (reuse the top-up
lick stream the `WellDispenser` already sees). Surface aggregates through
`phase3_stats` -> the summary alongside the ADR-0108 relative dwell, clearly
labeled as tracking-derived (frame-rate limited) vs actuation-stamp-derived.

**Evaluation / acceptance.** For a recorded session, the tracking-derived cue-zone
dwell should track the ADR-0108 relative dwell in shape but differ in absolute
value; the well time should match hand-scored video for a few trials. Headless
tests over synthetic occupancy/lick sequences.

**Effort / risk.** Medium. Risk: frame-rate + tracking dropout bound the
resolution; zone definitions must be stable (config snapshot, ADR-0072). Keep the
core calibration/geometry-free where possible (occupancy in, dwell out).

**Open questions.** Per-trial vs session-aggregate first? Which zones matter
(cue zone, choice arms, well)? Reconcile with the excursion log
([ADR-0109](decisions/0109-phase3-excursion-event-log.md)) so dwell attaches to
excursions rather than a parallel accounting?

**ADR needed?** Yes (a new measure with a geometry/latency interpretation to
pin down; cross-link ADR-0108).

### FW-0038: Join the day's %baseline / water context into the session summary

- **Status:** Proposed
- **Date added:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/session/session_summary.py`,
  `host/tmaze/core/records/daily_log.py` / `animal_state.py`; the session
  registry builder (`os_code_t_maze_resources` `build_session_training_log.py`)
- **Related ADRs:** [ADR-0071](decisions/0071-session-behavioral-summary.md)
  (the summary),
  [ADR-0076](decisions/0076-daily-husbandry-table-source-of-truth.md) (daily
  table source of truth),
  [ADR-0081](decisions/0081-session-registry-analysis-entry-point.md) (session
  registry), [ADR-0100](decisions/0100-ad-lib-water-regime.md) (water regime),
  [ADR-0104](decisions/0104-water-method-field.md) (water method)

**Motivation / context.** The behavioral summary reports what happened in the
task, but not the welfare context that frames it - the animal's weight as a
percent of baseline that day, whether it was on restriction vs ad-lib, and how the
day's water was delivered (timed access / measured bottle / task-earned). Those
live in the daily husbandry table (`daily_log.csv`, keyed by (animal, date)) and
are already read at Session Start to prefill `animal_state.json`. Joining them
into the summary (or the registry row) would let an analyst read task performance
against the day's water state without a manual cross-reference - e.g. "accuracy
dipped on the ad-lib day."

**Sketch of approach.** At Stop, read the already-loaded `animal_state` (or
re-read the day's `daily_log` row) and copy the relevant context fields
(`percent_baseline`, `water_regime`, `water_method`, weight/baseline) into an
additive `welfare_context` block in the summary JSON - a passthrough, no
recompute (ADR-0071). Alternatively (or additionally) join them in the registry
builder so the per-session view carries them. Keep the daily table the source of
truth; the summary only snapshots.

**Evaluation / acceptance.** A session summary for a known day shows the same
%baseline / regime / method as that day's `daily_log` row; an ad-lib day is
labeled as such. Headless test over a synthetic daily row + session.

**Effort / risk.** Small-to-medium. Risk: the daily row may be absent or edited
after the session - snapshot what was known at Stop and note it may lag a later
correction. Additive JSON only.

**Open questions.** Summary block, registry column, or both? Snapshot-at-Stop vs
join-at-read (the latter always reflects later corrections but couples the summary
to the resources repo)? Which fields are worth freezing?

**ADR needed?** Yes (records which fields are snapshotted vs joined, and the
snapshot-vs-live trade-off).

### FW-0039: Firmware/host git-SHA provenance stamp in the session data

- **Status:** Proposed
- **Date added:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/session/session_log.py` (manifest),
  `host/tmaze/core/phase3/phase3_log.py` /
  `host/tmaze/core/phase3/excursion_log.py` (CSV headers); a small host
  version-probe helper
- **Related ADRs:** [ADR-0072](decisions/0072-per-session-config-snapshot.md)
  (config snapshot / reproducibility),
  [ADR-0048](decisions/0048-host-session-orchestration-and-manifest.md)
  (manifest)

**Motivation / context.** A session is reproducible in its *config* (ADR-0072
snapshots the JSON that shaped the run) but not in its *code version*: nothing in
the bundle records which host commit produced the data or which firmware build was
flashed. When behavior shifts after a code change, there is no in-bundle way to
attribute it. A git-SHA (host) + firmware build id, stamped into the manifest and
the data-file headers, would make each session self-describing.

**Sketch of approach.** Add a best-effort host version probe (`git rev-parse
HEAD` + dirty flag, or a packaged version when not a git checkout) and record it
in the manifest (`provenance` block) and in the `phase3_*` / `_excursions` CSV
header comments (which already carry session_id/animal/clock caveats). For
firmware, capture the build id the firmware reports on handshake (add a `VERSION`
echo if none exists - possibly a separate firmware change) into the same block.
Purely additive; a missing SHA (not a checkout) leaves the field blank.

**Evaluation / acceptance.** A fresh session's manifest + CSV headers carry the
current host SHA; a dirty tree is flagged; the firmware build id appears when the
firmware reports one. Headless test over the probe with a stubbed git output.

**Effort / risk.** Small on the host side (a subprocess probe, guarded); the
firmware `VERSION` echo is a separate, larger firmware/protocol change if it does
not already exist. Risk: don't let the probe block or throw on non-checkout
machines.

**Open questions.** Host SHA only first, deferring firmware build id until a
`VERSION` protocol message exists? Where canonically - manifest only, or also the
CSV headers (duplication vs self-containment)? Include the dirty-tree flag?

**ADR needed?** Yes (records the provenance schema + the best-effort/blank-on-
failure policy; cross-link ADR-0072).

### FW-0040: Latent-state (GLM-HMM) model of behavioural mode

- **Status:** Deferred
- **Date added:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/analysis/` (a new `state_model.py` beside
  `choice_model.py`), `host/notebooks/animal_progress.ipynb`, a new
  `host/tests/test_analysis_state_model.py`; new dep (`ssm` or a small hand-rolled
  EM), analysis-only
- **Related ADRs:** [ADR-0112](decisions/0112-longitudinal-strategy-analysis.md)
  (the interpretable-first analysis this extends; its rule-based mode decomposition
  is the validation baseline)

**Motivation / context.** ADR-0112 ships an interpretable, *rule-based* mode
decomposition (thresholds + priority order) — great for explainability and as a
first pass, but the cut-points are hand-set and a trial belongs to exactly one mode.
The animals visibly *cycle* through modes, which is exactly what a latent-state
model captures principledly: a Generalized-Linear-Model Hidden Markov Model
(GLM-HMM, aka a "state-space" choice model) infers K hidden states, each with its
own choice policy (its own logistic weights on cue/history), plus the
trial-to-trial transition probabilities between states. It would give a
data-driven, probabilistic version of the same picture (per-trial state posteriors,
a state-occupancy timeline, dwell times), and the transition matrix is a natural
home for the deferred reward-pressure question (FW-0041).

**Sketch of approach.** Reuse the `choice_signals` design matrix (cue, prev choice,
win-stay/lose-shift, return-cue). Fit a K-state GLM-HMM per animal (start with the
Ashwood et al. 2022 formulation; `ssm` library or a compact EM/Baum-Welch over
logistic emissions). Select K by cross-validated held-out log-likelihood. Output
per-trial state posteriors, a MAP state timeline (overlay on the ADR-0112 mode
band), per-state weight vectors, and the transition matrix. Validate the states
against the rule-based labels (confusion/adjusted-Rand) so the interpretable layer
remains the sanity check. Fit on FREE trials first; optionally allow blocks to
influence transitions (ties to FW-0041).

**Evaluation / acceptance.** Held-out log-likelihood beats the single per-session
logit (ADR-0112) and a 1-state baseline; recovered states are stable across seeds
and correspond recognisably to the rule-based modes; runs headlessly on synthetic
data with known states (recovers them). Keep it optional/guarded like
`choice_model` so the base analysis has no new hard dep.

**Effort / risk.** Medium-large. Risks: label-switching/identifiability across
fits, choosing K, a heavier dependency, and over-interpreting states on
short/among-session-nonstationary data. Mitigate by anchoring to the interpretable
baseline and reporting uncertainty.

**Open questions.** Per-animal vs hierarchical (partial pooling across 600/601/602)?
Include an explicit engagement/disengaged state driven by the ADR-0109 excursion
signals once they exist? Pool sessions or model session as a covariate?

**ADR needed?** Yes (records the model choice, K-selection policy, and how it
relates to the rule-based decomposition).

### FW-0041: Reward-pressure / mode-transition characterization

- **Status:** Deferred
- **Date added:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/analysis/intervention_response.py` (extend),
  a new `transition_model.py`, `host/notebooks/animal_progress.ipynb`, tests
- **Related ADRs:** [ADR-0112](decisions/0112-longitudinal-strategy-analysis.md)
  (peri-intervention transitions are descriptive there; this makes the driver
  explicit), [ADR-0107](decisions/0107-phase3-alternation-intervention-stack.md) /
  [ADR-0086](decisions/0086-phase3-debias-blocks.md) (the intervention blocks whose
  reward pressure is the candidate driver)

**Motivation / context.** The operator's observation is that mode switches are often
*instigated* by reward pressure — a stretch where the animal's current strategy
stops paying off (its side-bias/alternation runs against the schedule, or an
intervention block actively withholds reward for the current habit). ADR-0112
surfaces peri-intervention transitions descriptively (dominant mode pre vs post, for
auto blocks) but deliberately does NOT model what *drives* a transition. This item
is that lightweight causal-ish characterization: quantify how the recent
reward/error history and the block context predict a mode switch on the next window.

**Sketch of approach.** Define a per-window "reward-pressure" signal (e.g. rolling
reward-rate deficit vs the animal's own baseline, plus a count of
strategy-vs-schedule mismatches — how often the current dominant mode's predicted
choice would have been wrong). Then either (a) a transition-hazard / survival model
(probability of leaving the current mode as a function of accumulated pressure and
block onset), or (b) if FW-0040 lands, regress the GLM-HMM transition probabilities
on the pressure covariate. Also characterize the *free-trial* adverse alignment
(when counterbalancing alone punishes a bias) separately from the *coerced* block
pressure. Include the return-cue-congruency covariate (ADR-0112) as a candidate
short-timescale nudge.

**Evaluation / acceptance.** Pressure predicts transitions above chance on
held-out data; block onset adds predictive power beyond free-trial pressure;
results are robust to window size. Headless tests on synthetic sequences with a
built-in pressure->switch rule recover it.

**Effort / risk.** Medium. Risks: endogeneity (pressure and mode are mutually
determined), few transitions per animal (low power), and defining "pressure"
without baking in the conclusion. Mitigate with pre-registered signal definitions
and reporting effect sizes with uncertainty.

**Open questions.** Best pressure definition (reward deficit vs explicit
strategy-schedule mismatch vs both)? Hazard model vs HMM-transition regression?
Pool animals or per-animal?

**ADR needed?** Yes (records the pressure definition and the transition-model
choice).

### FW-0042: Live excursion-based engagement traces

- **Status:** Proposed
- **Date added:** 2026-08-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/ui/phase3_controller.py` (publish excursion
  data on the snapshot), `host/tmaze/core/phase3/phase3_traces.py`,
  `host/tmaze/ui/phase3_traces_view.py`
- **Related ADRs:** [ADR-0114](decisions/0114-live-session-metric-traces.md)
  (the live trace panel), [ADR-0109](decisions/0109-phase3-excursion-event-log.md)
  (the excursion event log), [ADR-0050](decisions/0050-phase3-published-state-contract.md)
  (the published snapshot)

**Motivation / context.** The live "Metrics" trace panel (ADR-0114) derives its
engagement signal (rolling inter-trial interval) from the completed-trial table
only. But disengagement often shows first in *off-task* behaviour - the rat
making outbound trips that never commit up the stem, or fewer trips per minute -
which the excursion log already captures per event (ADR-0109). The offline
analysis (ADR-0112) plans the same excursion-based measures (trips/min,
non-stem fraction) but they're not computed live because the excursion rows are
written to CSV only, not published on the `Phase3StateSnapshot` the panel reads.

**Sketch of approach.** Publish the excursion events (or a rolling reduction of
them) on the cross-thread snapshot the same way the trial table is (ADR-0050) -
either a bounded tail of `ExcursionRow`s or pre-reduced counts - then add two
series to `phase3_traces.build_traces` (rolling `trips_per_min` from event
timestamps and `nonstem_frac` from `type`) and a subplot/toggle to the view.
Keep the reconciliation caveat from ADR-0109 (the controller's actuation-thread
`TaskEvents` is the authoritative per-event source, distinct from the behavior
panel's debounced view).

**Evaluation / acceptance.** Headless tests over a synthetic snapshot carrying
excursion data: trips/min tracks an injected trip cadence; non-stem fraction
rises when denied trips are injected; the series stay empty (not crashing) when
no excursion data is present. The subplot toggle hides cleanly.

**Effort / risk.** Small-to-medium. Main risk is snapshot bloat / churn if the
full excursion list is published every poll - prefer a bounded tail or a
pre-reduced rolling summary so the publish stays cheap.

**Open questions.** Publish raw events vs. a pre-reduced rolling summary? Window
by trials or by wall-clock minutes for trips/min? Share the subplot with the ITI
engagement trace or give it its own?

**ADR needed?** Yes (records the snapshot-publishing choice and the trace
definitions), likely folded into a follow-up to ADR-0114.

### FW-0043: Automatic probe-block triggers

- **Status:** Done — the automatic trigger shipped as a CORRECT-CHOICE-in-WINDOW
  rule (see [ADR-0134](decisions/0134-probe-correct-choice-trigger.md)): after
  piloting, the operator wanted probes to start when the animal demonstrates it
  knows the map inside a chosen phase, so `ProbeBlockController.on_scored` fires on
  the first correct choice inside a config `sequence.probe_blocks.windows` range
  (evaluated at scoring, since it needs the outcome). The other flavours this entry
  originally sketched (fixed spine-boundary position, elapsed wall-clock, cumulative
  reward volume) were NOT built - the behavioural trigger addressed the need
  directly; re-open a fresh backlog entry if a time/volume trigger is later wanted.
- **Date added:** 2026-08-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/phase3/probe_blocks.py` (a trigger
  evaluator on `ProbeBlockController`), `host/tmaze/core/phase3/phase3_config.py`
  (`sequence.probe_blocks` trigger parameters), `host/tmaze/core/phase3/phase3_task.py`
  (feed the evaluator the per-trial context it needs), `host/tmaze/ui/phase3_controller.py`
- **Related ADRs:** [ADR-0134](decisions/0134-probe-correct-choice-trigger.md)
  (the correct-choice window trigger that resolved this),
  [ADR-0115](decisions/0115-phase3-unrewarded-probe-blocks.md)
  (probe blocks - originally manual only), [ADR-0114](decisions/0114-live-session-metric-traces.md)
  (the disengagement signal a time/volume trigger would key off),
  [ADR-0110](decisions/0110-per-trial-reward-magnitude.md) (per-trial reward
  magnitude, the source for a cumulative-volume trigger)

**Motivation / context.** Probe blocks shipped MANUAL-only on purpose (ADR-0115,
decision 5): the operator presses "Start probe block" so they can pilot WHERE in
a session a probe is worth spending. The original request (from the paper's
design, Drieu et al. 2025) was to run probes near disengagement onset - "a couple
of blocks in from the start" and again "around the one-hour mark or 6-8 ml
consumed". Once piloting settles good placements, those should be automatable so a
session doesn't depend on the operator watching the clock.

**Sketch of approach.** Add an optional trigger evaluator to
`ProbeBlockController` (kept separate from the manual `force_block` latch so the
button always works). Candidate triggers, config-driven under
`sequence.probe_blocks`: (a) **fixed position** - fire at spine block boundary K
(e.g. after 2 counterbalanced blocks); (b) **elapsed wall-clock** - fire once past
T minutes into the session; (c) **cumulative reward volume** - fire once dispensed
volume crosses V ml (sum the per-trial magnitudes, ADR-0110). Each trigger is
one-shot (arms the pending latch once, then disarms) with a minimum spacing so two
triggers can't stack. The controller already gates the actual START on a
counterbalanced boundary with no intervention active, so a trigger only needs to
set `pending` - the existing `decide()` placement rules are reused unchanged.
Feed the evaluator the session clock / cumulative volume from `phase3_task` (which
already sees each scored trial).

**Evaluation / acceptance.** Headless tests: a position trigger fires exactly once
at the configured boundary; a time trigger fires on the first trial past the
threshold and not again; a volume trigger sums per-trial magnitudes and fires at
the crossing; a manual `force_block` still works alongside an armed auto-trigger;
minimum-spacing suppresses a second auto-probe too soon after the first. Placement
determinism note from ADR-0115 still holds for position triggers (seed-derivable)
but NOT for time/volume triggers (depend on the animal's pace) - assert the CSV
records the realized placement either way.

**Effort / risk.** Small-to-medium (the placement/insert machinery already
exists; this is a trigger layer on top). Main risk is a poorly chosen threshold
wasting probe trials on an already-disengaged animal - which is exactly why manual
piloting comes first.

**Open questions.** Which trigger(s) are actually useful once piloting is done -
position, time, volume, or a disengagement-signal trigger keyed off the live
rolling ITI (ADR-0114)? Should an auto-trigger be suppressed if the animal is
already clearly disengaged (probe would be uninformative)? How many probes per
session is too many (they cost unrewarded trials)?

**ADR needed?** Yes - a follow-up to ADR-0115 recording the trigger semantics and
the chosen thresholds/defaults.

### FW-0044: Closed-loop scheduler rollback on a manual scored re-run

- **Status:** Deferred
- **Date added:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/phase3/` (`debias.py`, `alternation.py`,
  `intervention_stack.py`, `trial_sequence.py`); wired from
  `phase3_task.py`/`phase3_controller.py`
- **Related ADRs:** [ADR-0119](decisions/0119-trial-edit-append-only-history.md),
  [ADR-0086](decisions/0086-phase3-debias-blocks.md),
  [ADR-0107](decisions/0107-phase3-alternation-blocks.md)

**Motivation / context.** ADR-0119 made trial edits (Repeat/Back) preserve the
animal's real behavior append-only, and un-counts the accuracy tallies for a
preserved scored attempt. But in a **closed-loop** run (debias / alternation /
intervention / probe schedulers) the trial's outcome was already fed to the
scheduler via `TrialSequence.record_outcome` → `controller.record(...)` on the
tick it was scored. Re-running that trial feeds a *second* outcome for the same
index, so the scheduler's bias / alternation-rate windows and block counters
double-count the edited trial and can fire (or fail to fire) a corrective block
differently than they would have. ADR-0119 currently only surfaces an operator
note ("auto-schedule may be affected") when a scored trial is re-run in a
closed-loop session; the schedule itself is not reconciled.

**Sketch of approach.** Two candidate designs:
1. **Single-step undo.** Give each controller (`DebiasBlockController`,
   `AlternationBlockController`, the `intervention_stack`) an `undo_last()` that
   pops the most recent `record(...)` from its window/counters, and call it from
   the edit path before the re-run re-feeds. Precise but stateful and easy to get
   subtly wrong (window edges, block-length counters, forced/probe interactions).
2. **Replay from history.** On a scored Repeat/Back, `reset_debias()` then replay
   the current live table's recorded `(cued, chosen)` pairs for trials `< index`
   in order (skipping superseded attempts, honouring forced/probe blocks). Reuses
   existing methods and is deterministic, but must faithfully reproduce operator
   forced-block/probe actions that aren't in the per-trial data, and re-commits
   the spine.

**Evaluation / acceptance.** A headless test where a debias/alternation session
is scored, a mid-block scored trial is Repeated, and the scheduler state
(bias/window/active block) matches what it would be had that trial simply been
scored with the re-run's outcome from the start. Full host suite green.

**Effort / risk.** Medium-high: touches the closed-loop cores that drive live
behavior, so a regression could mis-schedule corrective blocks. Low urgency —
only bites when an operator manually edits a *scored* trial *inside an auto
block*, which is rare; the header already warns the closed-loop schedule is not
seed-reproducible.

**Open questions.** Undo vs replay? How to reconstruct operator forced-block /
probe actions during a replay (persist an operator-action log, or snapshot
controller state per trial and restore)? Should a manual edit inside an auto
block be disallowed or warned-and-allowed (current behavior)?

**ADR needed?** Yes - a follow-up to ADR-0119 recording the chosen mechanism.

### FW-0045: In-FOV event-LED visual fiducial (sensor/solenoid) for video<->serial alignment

- **Status:** Proposed
- **Date added:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/vision/` (a detector), `host/tmaze/ui/camera_view.py`
  (sidecar columns), the rig's LED wiring, and offline alignment tools
  (`host/tmaze/tools/maze_state_validate.py`)
- **Related ADRs / work:** [ADR-0124](decisions/0124-self-anchoring-session-bundle.md)
  (software self-anchoring; this is the middle rung between it and the hardware
  fiducial), FW-0018 (hardware `SYNC` TTL + LED barcode), FW-0056 (cue LEDs on
  spare optocoupler channels), FW-0017 (cue-onset latency from firmware echoes)
- **Design doc (2026-09-02):** this entry is now **Phase 1** of
  [`docs/design/cross-stream-sync-design.md`](design/cross-stream-sync-design.md)
  (§3), which supersedes the sketch below where they differ. Key refinements
  agreed there: (1) the video clock to fit is the sidecar's `cam_timestamp`
  (Basler hardware ticks, written every frame), bridged to the host via
  `host_unix_s`, not the host clock itself; (2) LED positions are NOT hard-coded
  pixel ROIs but derived each session by **constellation registration** - the
  board is flat and the camera fixed, so the LED pattern moves as one rigid
  shape; a max-minus-median image over the first minutes yields the blinking
  blobs, which are matched (Procrustes/RANSAC similarity, homography if tilted)
  to a stored board-frame constellation in a new `host/led_map.json` (snapshotted
  into `config/` like `zones.json`), with the Arduino-driven LEDs and the
  always-lit power LED as time-labelled anchors that break the four-identical-
  channels-per-module ambiguity; (3) LED->event bindings and polarities are
  learned once by cross-correlating blob traces against serial event trains in a
  no-rat mapping session (`tmaze_test`), then verified passively every session
  with a per-LED verdict `matched / silent / displaced` (no manual beam
  triggering); (4) the fit is a robust straight line per MODE segment (firmware
  clock resets on MODE, ADR-0064) with residuals reported as the measured
  camera+serial offset - the number that answers `#260720_DataVideoSync`;
  (5) outputs land in the bundle as a new `sync/` stream
  (`sync/led_events_<id>.csv`, `sync/clock_fit_<id>.json`, manifest
  `fiducial.method: led-fit`), written by a rig-repo runnable
  `tmaze.tools.sync_fit` (modelled on `maze_state_validate.py`) over a pure core
  `tmaze/core/sync/` + `tmaze/vision/led_detector.py`; the analysis repo only
  READS `clock_fit`. Offline first; a live preflight in `camera_view` is a
  follow-on (no generic per-frame detector hook exists - ADR-0040 pattern).
  Retroreflective markers were considered and rejected: the LEDs are
  self-illuminating landmarks in a dark room.
- **Feasibility measured (2026-09-04, design doc §3.7; exploratory runnable
  `tmaze.tools.led_sync_prototype`).** On one real H.264 (CRF 18) session
  (`600_20260903_134552`, 39 min): 11 board LEDs read cleanly (contrast 90-185
  grey levels vs a ~25 noise floor); every one was bound to its serial event from
  the rat's own session (reward `SOL`, beams S2-S5, lick detector) by matching
  lit/dark spans at both ends; the `cam_timestamp <-> firmware_ms` fit on 3,500
  pairs has residual MAD **9.5 ms** (p5-p95 within one 40 ms frame), flat over
  the session. Numbers the production tool must reproduce and that answer
  `#260720_DataVideoSync` today: the host-clock join (`host_unix_s` vs the
  serial line's wall stamp) carries a **+59 ms systematic bias** (frame stamped
  ~1.5 frames after the serial line; MAD 10 ms) and the camera and Arduino
  clocks differ by **27 ppm (~97 ms/hour)**, so the fit needs the rate term.
  `cam_timestamp` ticks at 1.0000 GHz. Refinements adopted for the build:
  interval (both-ends) matching with a >= 60 ms visible-span floor,
  the sensors' measured active states (`S<i>=0`, `L<i>=0`; FW-0058), local-maximum blob detection
  (channel LEDs sit ~8-12 px apart and bloom), min-max hysteresis levels (a 1 %
  duty reward LED defeats percentiles), and one fit offset per edge polarity
  (LED turn-off registers one frame later than turn-on). **Open rig checks:**
  beams S0/S1 (ENTRY/EXIT) showed *no* visible blinking LED in the board crop -
  hidden by wiring or facing away - so they need to be located (or accepted as
  camera-invisible) before the LED map is finalised.

**Motivation / context.** The rig has LEDs in the camera's field of view that
flash on body-sensor and solenoid activations - i.e. the video ALREADY contains
visible markers that correspond one-to-one to serial events (`S,...` sensor
edges, `SOL,...`/`REWARD,...` at the central port). Detecting those flashes in
the recorded frames and time-stamping them against the same events in the serial
log would give a *direct* video<->serial alignment (and thus video<->firmware-ms)
WITHOUT the Open Ephys hardware that FW-0018 needs - a practical middle path
between ADR-0124's software self-anchoring (host-clock, ~ms + serial latency) and
FW-0018's acquisition-grade hardware fiducial. It also cross-checks the host-clock
alignment: the LED flash frame vs the serial event's host-wall stamp measures the
camera's exposure->grab offset and the serial path latency empirically.

**Sketch of approach.** Add a small vision detector that watches a
configured in-FOV region (or auto-locates the LEDs from the empty-rig
background) for on/off transitions per frame; write the detected LED state (or a
per-LED on/off) as new sidecar column(s) alongside `cam_block_id`/`host_unix_s`.
Offline, match LED-on frames to the corresponding `S`/`SOL`/`REWARD` serial
events (nearest in host wall time, then refine) to fit `cam_block_id -> firmware_ms`
per mode-segment. Keep it self-contained: no change to the firmware or the task
FSM; the LEDs are already wired to the sensor/solenoid lines.

**Evaluation / acceptance.** On a recorded session, LED-on frames align to the
matching serial events within one frame period after the fit; the residual
(flash frame time - serial host-wall time) is reported as the measured
camera+serial offset/jitter. Regression: sessions without the LEDs (or with the
detector off) behave exactly as today.

**Effort / risk.** Medium: a new vision detector + sidecar columns + an offline
matcher, plus deciding LED placement/colour so flashes are unambiguous and don't
confuse the rat tracker. Low urgency but high payoff for behavior<->video
alignment before neural recording lands. **Owner note:** the user asked to keep
this as a separate task for a dedicated agent - do not fold it into the ADR-0124
software groundwork.

**Open questions.** One LED per event class or a small bank encoding which
sensor/port fired? Fixed ROI vs background-difference auto-location? How to keep
the flash out of the tracker's way (colour channel, brightness, placement)?

**ADR needed?** Yes - when built (detector + sidecar schema + alignment method).

### FW-0046: Live-core Metrics-tab fixes (engagement definition + others)

- **Status:** Deferred
- **Date added:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/phase3/phase3_traces.py`,
  `host/tmaze/core/phase3/phase3_stats.py`, and the Metrics-tab views
  (`host/tmaze/ui/phase3_traces_view.py` and siblings)
- **Related ADRs / work:** [ADR-0114](decisions/0114-live-session-metric-traces.md)
  (live traces; defines the dead-time ITI), [ADR-0132](decisions/0132-offline-baseline-out-of-block.md)
  (offline/GUI baseline reconciliation - this is its deferred companion),
  FW-0042 (live excursion-based engagement)

**Motivation / context.** Reconciling the offline analysis against the live GUI
(ADR-0132) surfaced live-core metric issues the operator wants handled together
as one dedicated task, rather than piecemeal. The clearest is the ENGAGEMENT
definition: the Metrics tab uses the DEAD-TIME inter-trial interval
`trial_start[N] - trial_end[N-1]` (time not spent initiating the next trial;
ADR-0114), while the offline layer uses the START-TO-START gap
`trial_start[N] - trial_start[N-1]`. The offline start-to-start gap is the
intended engagement signal (it captures the whole between-choice interval,
including the return/consummatory leg), so the live and offline disengagement
lines are not measuring the same thing and the fixed 45 s threshold means
different things on each. The operator has flagged additional Metrics-tab metric
problems to review in the same pass.

**Sketch of approach.** Audit the live Metrics-tab metric definitions against the
offline `tmaze.analysis` twins and the scientific intent; change the live
engagement ITI to the start-to-start gap (or make the definition explicit and
consistent across live + offline), re-derive the disengaged threshold for the
chosen definition, and fix the other flagged metric issues. Keep live and offline
in step so a future divergence is caught by the round-trip symmetry the analysis
layer already relies on.

**Evaluation / acceptance.** Updated `test_phase3_traces` ITI cases; the live
disengagement line and the offline engagement proxy agree in definition on the
same recorded session; the other flagged metrics validated case by case.

**Effort / risk.** Medium: touches the live core (used on the bench during
sessions), so changes need care and a matching test update; low scientific risk
once the definition is pinned in an ADR.

**Open questions.** Exactly which additional Metrics-tab metrics the operator wants
changed (to be enumerated when the task is picked up); whether to wait for the
excursion-based engagement (FW-0042) rather than refine the gap proxy.

**ADR needed?** Yes - a live-core metrics ADR when built (it changes an on-bench
readout definition and would refine/relate to ADR-0114).

### FW-0047: Schedule-aware side-bias score inside intervention blocks

- **Status:** Proposed
- **Date added:** 2026-08-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/phase3/debias.py` (the bias signal),
  `host/tmaze/core/phase3/intervention_stack.py` (fan-out / composition),
  `host/tests/`
- **Related ADRs:** [ADR-0136](decisions/0136-freeze-side-bias-during-dealternation.md)
  (the freeze stopgap this would replace),
  [ADR-0090](decisions/0090-debias-bias-normalized-by-errors.md) (the current
  directional-error bias), [ADR-0129](decisions/0129-phase3-de-alternation-two-sided-exit.md)
  (why the de-alternation block's cued side is non-counterbalanced)

**Motivation / context.** The side-bias score is a signed *directional-error*
bias (left-pull `(cued RIGHT, chose LEFT)` vs right-pull `(cued LEFT, chose
RIGHT)`, normalised by the error count; ADR-0090). It is only valid when the cued
side is drawn from the COUNTERBALANCED spine. Inside a de-alternation block the
cued side is forced from the animal's own choices (last choice / bridge arm,
ADR-0129) and can repeat on one arm, so an UNBIASED alternator's ~50% errors all
land on the same pull and read as a growing bias - which fired a debias block
right after the de-alt block and cascaded on the bench (ADR-0136). ADR-0136 is a
stopgap: it FREEZES the side-bias detector during a de-alternation block, so no
bias evidence is gathered there at all. A real bias that develops during a long
block is therefore invisible until the block exits. A schedule-aware score would
let those in-block trials contribute correctly instead of being discarded.

**Sketch of approach.** Make the bias signal condition on the trial's SCHEDULE
context, not just `(cued, chosen)`. Options to evaluate: (a) weight or normalise
each error by how "available" each pull was given the forced cued side over the
window (e.g. condition the directional-error rate on the cued side, so a run of
one-sided cues can't manufacture a one-sided pull count); (b) measure bias from
the CHOICE stream corrected for the forced schedule (the balanced-spine
expectation), so alternation reads as ~0 bias even under one-sided forcing;
(c) keep the current score for out-of-block trials but add a separate, in-block
estimator the stack can consult. Whichever is chosen, replace the ADR-0136 freeze
in `InterventionStackController.record` so the side-bias detector can safely see
in-block trials again.

**Evaluation / acceptance.** Headless tests: a pure alternator inside a
de-alternation block yields ~0 bias (no spurious trigger / no cascade), while a
genuinely biased animal inside a block is still detected. Re-run
`test_intervention_stack` / `test_debias` / `test_alternation`; add a simulated
alternator-in-block case. Bench check: the 8/27-style cascade does not recur and a
real bias is still corrected.

**Effort / risk.** Medium: it changes a core scientific signal used on the bench,
so it needs careful design and a matching test update; the main risk is a score
that over- or under-corrects at the block boundary.

**Open questions.** Which formulation (a/b/c above) best matches the intended
"is the animal side-biased?" semantics; whether the in-block score should also
feed triggering (it currently can't, since only one child owns the active block);
whether to unify with the alternation detector's cue-aware conditioning.

**ADR needed?** Yes - it would supersede ADR-0136 and refine ADR-0090.

### FW-0048: Carve the analysis layer into its own repo

- **Status:** Done — executed 2026-08-30 (see [ADR-0146](decisions/0146-split-analysis-into-separate-repo.md)).
  The layer now lives in the separate `drieu_lab_analysis` repo
  (`git@github.com:ravipjk/drieu_lab_analysis.git`, package `tmaze_analysis`);
  the rig keeps the writer-side contract (`core/phase3/bundle_format.py`) + the
  golden-fixture generator. Original context kept below for the record.
- **Status (original):** Deferred
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/analysis/` (the whole package + its
  `host/tests/test_analysis_*.py`), `host/notebooks/` (analysis notebooks),
  `pyproject.toml` / `host/requirements.txt` (packaging + deps), `PROGRAMS.md`
- **Related ADRs:** [ADR-0142](decisions/0142-analysis-core-decoupling-data-contract.md)
  (the decoupling this builds on), [ADR-0112](decisions/0112-longitudinal-strategy-analysis.md)
  (the analysis layer), [ADR-0083](decisions/0083-host-tools-become-tmaze-package.md)
  (package layout)

**Motivation / context.** The analysis code grows on a different cadence and risk
profile from the stabilizing rig/task code, and leans on heavy
`pandas`/`numpy`/`statsmodels`/`matplotlib` deps the lean rig env doesn't need.
ADR-0142 already severed the code coupling: `tmaze.analysis` imports nothing from
`tmaze.core`/`tmaze.paths`, and the bundle is self-describing (`config/format.json`
+ version guards). The only remaining coupling is the on-disk bundle FORMAT, which
is now explicit and versioned - so an independent repo is a mechanical move, not a
redesign.

**Sketch of approach.** Pre-decided direction (see ADR-0142 Consequences): rename
the import root to a top-level package `tmaze_analysis` (mechanical
`from tmaze.analysis.X` -> `from tmaze_analysis.X` sweep across modules, tests,
notebooks); a FRESH copy into the new repo (no `git filter-repo`/history
preservation required); the human creates/authorizes the remote (a future agent
only prepares the local repo). At the split: delete the temporary
`test_analysis_phase3_format.TestCoreParity` (the new repo can't import
`tmaze.core`; the `format_version`/`proto`/`zones.version` guards become the sole
contract), move the analysis notebooks + their `PROGRAMS.md` rows and the heavy
analysis deps into the new repo, and exclude `tmaze.analysis` from this repo's
`pyproject.toml` package discovery.

**Environment / installation split (revisit at the split).** Today everything lives
in one env: `tmaze.analysis` installs with the rig via `pip install -e .` from the
repo root, and its deps sit in `host/requirements.txt` (the always-on `pandas` /
`statsmodels`, plus the OPTIONAL, import-guarded `pyarrow` / `pynwb` for the
docs/decisions/0143 exporters, currently listed commented-out). At the split:

- **New analysis repo owns its own deps.** Move the analysis-only deps out of
  `host/requirements.txt` into the new repo's `pyproject.toml` / requirements:
  `pandas`, `numpy`, `statsmodels` (+ Jupyter for the notebooks), with `pyarrow`
  and `pynwb` as OPTIONAL extras (e.g. an `[export]`/`[interchange]` extra) so the
  two-tier exporters (`export_parquet` / `export_nwb`) install on demand, not by
  default. `host/requirements.txt` then slims to the live-rig deps only.
- **Two separate conda envs.** `drieu_t_maze` = rig only (no pandas/pyarrow/pynwb);
  a separate analysis env (Python 3.13, matching the rig at the time of writing)
  does `pip install -e .` IN THE NEW REPO + the export extras. The analysis env no
  longer needs the rig package installed at all.
- **Launch paths + doc references move.** If the import root becomes
  `tmaze_analysis`, update the exporter/notebook run commands (currently
  `python -m tmaze.analysis.export_parquet` / `export_nwb`) and repoint the
  `PROGRAMS.md` rows, the ADR-0143/0144 command references, and
  `docs/design/data-format-review.md` at the new home (the ADRs/design doc stay
  here as the decision record; they just link out).
- **Move the exporter test + fixtures.** `host/tests/test_analysis_exporters.py`
  and the shared `bundle_data_dictionary.py` travel with the analysis repo; the
  skip-if-missing guards keep them green whether or not the export extras are
  installed. A golden fixture bundle (FW-0050) is what lets those tests + the
  readers run without importing `tmaze.core`.
- **Cross-repo format handshake.** ADR-0144's units/dtype enrichment touches
  `bundle_format.py`, which stays in the RIG repo; a `format_version` bump then
  becomes a two-repo step (rig bumps what it writes, analysis bumps what it
  accepts). The version guards make a mismatch fail loudly, so this is safe but
  must be coordinated.

**Evaluation / acceptance.** The analysis repo installs + tests standalone
(`pip install -e . && pytest`) with no `tmaze` (rig) dependency; the rig repo's
tests still pass and `grep tmaze.analysis` in rig `core/io/ui/tools` stays empty;
the notebooks run against real bundles resolved via `TMAZE_RECORDINGS_DIR`.

**Effort / risk.** Medium (mostly mechanical). Main risk is silent format drift
once the parity test is gone - mitigated by the bundle version guards and by
bumping `format_version`/`proto` on any rig-side format change.

**Open questions.** Repo host (GitHub org vs. Cursor-hosted); whether to publish
the analysis package to an internal index or keep it editable-install only.

**ADR needed?** Yes - a short ADR in the NEW repo establishing it, plus a note in
this repo's ADR log that analysis moved out.

### FW-0049: Relative stream paths in the session manifest

- **Status:** Proposed
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Area / modules:** `host/tmaze/core/session/session_log.py` (the `streams`
  map), `host/tmaze/ui/session_controller.py` (writer), the analysis readers that
  resolve stream paths
- **Related ADRs:** [ADR-0048](decisions/0048-session-orchestration-and-manifest.md)
  (manifest as correlation index), [ADR-0113](decisions/0113-multi-location-recordings.md)
  (archive roots), [ADR-0142](decisions/0142-analysis-core-decoupling-data-contract.md)
  (bundle-as-contract), [ADR-0143](decisions/0143-two-tier-data-model-offline-interchange.md)

**Motivation / context.** The manifest `session_<id>.json` records every stream
with an ABSOLUTE Windows path (e.g.
`C:\Users\Celine\...\phase3_recordings\<id>\task\phase3_<id>.csv`). When a bundle
is moved to an archive drive (ADR-0113) or copied to another machine, those
absolute paths no longer resolve. Analysis works around this today by reconstructing
paths from the bundle directory + known filenames rather than trusting the manifest
`streams` map, so the map is effectively decorative for a moved bundle.

**Sketch of approach.** Store each stream path RELATIVE to the bundle root (e.g.
`task/phase3_<id>.csv`) alongside (or instead of) the absolute path; a reader joins
it to the bundle directory it actually found. Keep back-compat: prefer the relative
path when present, fall back to the absolute path, and (as today) fall back to
filename reconstruction. Bump the manifest `schema_version` if the shape changes.
Small change, but it touches the live write path, so it is out of scope for the
data-format review task.

**Evaluation / acceptance.** A bundle copied to a new drive resolves every stream
from the manifest alone; old bundles with absolute-only paths still load.

**Effort / risk.** Low. Risk: a reader that trusted absolute paths; mitigated by
the prefer-relative-then-absolute fallback.

**ADR needed?** Minor - can ride under ADR-0143/0048; note the `schema_version`
bump in the commit.

### FW-0050: Golden fixture bundle + schema-migration tooling

- **Status:** Partly done (2026-08-30, [ADR-0146](decisions/0146-split-analysis-into-separate-repo.md)).
  The GOLDEN FIXTURE half shipped with the split: `tools/make_golden_bundle.py`
  regenerates a committed `host/tests/fixtures/` bundle from the real writer, this
  repo's `test_golden_bundle` diffs it, and the analysis repo round-trips it. The
  SCHEMA-MIGRATION tooling half (a `format_version` upgrade path / migration
  helpers) is still open. Original context kept below.
- **Status (original):** Proposed
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Area / modules:** `host/tests/` (a small checked-in reference bundle),
  `host/tmaze/analysis/` (readers + the ADR-0143 exporters)
- **Related ADRs:** [ADR-0142](decisions/0142-analysis-core-decoupling-data-contract.md)
  (version guards), [ADR-0143](decisions/0143-two-tier-data-model-offline-interchange.md)
  (exporters), [ADR-0144](decisions/0144-enrich-bundle-data-dictionary.md)
  (data dictionary), [ADR-0112](decisions/0112-longitudinal-strategy-analysis.md)

**Motivation / context.** There is no checked-in reference ("golden") bundle; the
disk-based tests build synthetic temp bundles inline. Once analysis is a separate
repo (FW-0048), it can no longer render CSVs via `Phase3Log.render_csv()` to
fabricate fixtures, and the only thing pinning the contract will be the bundle
FORMAT. A tiny, real (or realistically-rendered) golden bundle - one short session
with a handful of trials, an excursions file, a few sidecar rows, notes, and a
`config/format.json` - would let both repos test the reader/exporter contract, and
would give schema-migration work something concrete to migrate.

**Sketch of approach.** Commit a minimal bundle under `host/tests/fixtures/`
(small enough to version: no real `.mp4`; a truncated sidecar). Add tests that load
it via `bundle_reader` and round-trip it through the ADR-0143 exporters. Pair it
with a small migration helper that upgrades an older bundle to the current
`format_version` (or asserts the version guards behave), so a future format bump
(e.g. ADR-0144's v1->v2) has a tested upgrade path.

**Evaluation / acceptance.** The analysis repo's tests pass against the golden
bundle with no `tmaze.core` import; a deliberately-old fixture exercises the
legacy-inference path (missing `format.json`).

**Effort / risk.** Low-medium. Risk: fixture drift as the format evolves;
mitigated by regenerating it from the writer in the rig repo and version-stamping
it.

**ADR needed?** No - test/tooling change; reference ADR-0142/0143/0144.

### FW-0051: Productionize NWB export + DANDI/ephys integration

- **Status:** Deferred
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Area / modules:** a new analysis-repo `preprocess_session` pipeline built on
  `export_nwb.py` / `export_parquet.py` (POC -> production), the Neuropixels
  acquisition pipeline, packaging (`pynwb` dep), the golden fixture (FW-0050)
- **Related ADRs:** [ADR-0145](decisions/0145-nwb-primary-derived-format-preprocessing-pipeline.md)
  (NWB primary + the pipeline; the direction this implements),
  [ADR-0143](decisions/0143-two-tier-data-model-offline-interchange.md)
  (two-tier + NWB target), [ADR-0124](decisions/0124-self-anchoring-session-bundle.md)
  (session anchor), [ADR-0144](decisions/0144-enrich-bundle-data-dictionary.md)
  (units feed `TimeSeries.unit`)

**Motivation / context.** ADR-0143 shipped proof-of-concept exporters that map a
finished bundle into `.nwb` (behaviour: trials table + position + cue/lick events,
anchored at the manifest `t0`) and Parquet. The lab has decided NWB is the adopted
sharing format (ADR-0145), and wants a **preprocessing step** that turns a raw
bundle into a per-session NWB (+ optional Parquet) with optional preprocessing
applied. The scientific payoff - unifying behaviour with the Neuropixels recordings
in one standard file and sharing via DANDI (a K99/R00 expectation) - lands once
ephys recording begins and the mapping is firmed up.

**Sketch of approach.** Build an offline, analysis-repo pipeline
`preprocess_session(bundle) -> session.nwb (+ optional .parquet)` (ADR-0145):

- **Read-only + regenerable**, importing only the bundle contract (`bundle_reader`
  / `phase3_format` / `bundle_data_dictionary`), never the rig core. A batch driver
  runs it over the recordings roots.
- **Declarative registry of OPTIONAL steps**, each deterministic given
  `(bundle, step_config)` (e.g. trajectory smoothing, derived per-trial features,
  spike-time alignment when ephys lands). Default = the current POC mapping.
- **Text provenance sidecar:** stamp applied steps + params + tool/code version +
  bundle `format_version` into a JSON sidecar next to the outputs (so it is
  value-comparable and reproducible for sharing, not buried inside the `.nwb`).
- **Idempotency keyed on INPUTS:** a skip-if-fresh cache hashes the input bundle
  content + step config, never the output bytes (NWB/Parquet embed library
  versions, compression, and an NWB creation timestamp, so bytes vary run-to-run).
- **Firm up the mapping:** trial columns, cue/lick events (already the modern
  `EventsTable`), `SpatialSeries` with real spatial units once ADR-0144 lands; add
  the electrical-series / probe geometry when Neuropixels data exists; validate with
  the NWB Inspector; add a DANDI upload path.

**Golden-fixture implications (from the analysis-repo-separation Phase 2 plan;
FW-0050).** The committed golden bundle is the pipeline's primary regression anchor,
and its design constrains the pipeline:

- **Fixture coverage tracks the steps.** It must carry every stream a step reads
  (trial CSV incl. the ISO event columns, manifest `t0`, `config/format.json` +
  `zones.json`, a truncated sidecar; optionally excursions + notes). A step that
  reads a new field makes that field mandatory in the fixture.
- **Cross-repo fixture handshake (mirrors `format_version`).** The authoritative
  generator (`make_golden_bundle.py`) lives in the RIG repo; the analysis repo gets
  a copy. Adding a step that needs a new bundle field means: extend the generator ->
  regenerate the fixture in the rig repo -> copy it into the analysis repo.
- **Determinism discipline in tests.** Never byte-compare `.nwb`/`.parquet`;
  round-trip (write, re-read) and assert derived VALUES, and byte/JSON-compare only
  the text dictionary + provenance sidecars.
- **Keep the synthetic builder for edge cases** (legacy/no-`format.json`) alongside
  the real-artifact golden fixture.

**Evaluation / acceptance.** A session's `.nwb` passes NWB Inspector, opens in
`matnwb`, and (with ephys) aligns spikes to the behavioural trials table on one
clock; the pipeline is deterministic per `(bundle, step_config)` and idempotent
(skip-if-fresh on inputs); its tests pass against the golden fixture (value-level +
text-sidecar equality); a test dandiset validates.

**Effort / risk.** Medium-high (depends on the ephys pipeline). Risks: timing
alignment across behaviour/video/ephys clocks (leans on ADR-0124 + FW-0018); and
fixture/generator drift across the two repos (mitigated by the cross-repo handshake
above + the version guards).

**ADR needed?** Direction is set by ADR-0145; add a follow-up ADR only if the
production mapping makes a non-obvious modelling choice (e.g. how ephys + behaviour
share the clock) beyond what ADR-0145 records.

### FW-0052: Video storage-footprint / retention policy

- **Status:** Proposed
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Area / modules:** `host/tmaze/ui/camera_view.py` (codec/CRF), recordings
  storage + archive workflow (ADR-0113)
- **Related ADRs:** [ADR-0011](decisions/0011-camera-tab.md)/[ADR-0013](decisions/0013-camera-codec-choice.md)
  (camera recording + codec choice), [ADR-0020](decisions/0020-save-background-image.md),
  [ADR-0113](decisions/0113-multi-location-recordings.md)

**Motivation / context.** A single session's `.mp4` dominates the bundle (~12 GB
in the inspected example); the rest of the bundle is a few tens of MB. Across many
sessions/animals this drives the archive-drive workflow and backup cost, and it
makes casual copying/sharing of a bundle impractical.

**Sketch of approach.** Options to weigh (not mutually exclusive): a documented
retention policy (which sessions keep full video vs. a compressed proxy); an
optional lower-bitrate H.264 "preview" derivative generated offline (Tier 2)
alongside Parquet/NWB for quick review; codec/CRF defaults tuned for size vs.
tracking fidelity; and confirming the tracking sidecar (the analysis-relevant part)
is always retained even when raw video is archived/pruned.

**Evaluation / acceptance.** A clear rule for what video is kept where; a
size-vs-quality comparison for any proposed codec/CRF change; the analysis path
never depends on the full-resolution `.mp4`.

**Effort / risk.** Low-medium. Risk: over-compressing loses tracking/behaviour
detail; mitigated by keeping the sidecar + validating tracking on a proxy.

**ADR needed?** Yes if a codec/CRF default or a retention rule changes; a note
otherwise.

### FW-0053: Author the rigor guidance spokes + slim the `AGENTS.md` hubs

- **Status:** Proposed
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Area / modules:** `AGENTS.md` (both repos), a new `docs/guidance/` folder,
  `CONTRIBUTING.md` (unchanged content, becomes an explicit spoke), the resources
  repo `AGENTS.md` Change Log
- **Related ADRs:** ADR-0146 (to be written - agent-guidance hub-and-spoke, per
  [`docs/design/agent-guidance-rigor-review.md`](design/agent-guidance-rigor-review.md));
  builds on the reasoning in ADR-0029 (advisory guardrails), ADR-0083 (package
  layout precedent for "one home per concern")

**Motivation / context.** The agent frameworks are strong on software-engineering
craft and thin on *scientific / data / reproducibility / epistemic* rigor. The
design review [`agent-guidance-rigor-review.md`](design/agent-guidance-rigor-review.md)
diagnoses the gaps (measurement validity, confounds, clocks/units, reproducibility,
grant grounding, welfare, agent epistemics) from concrete ADR/bug evidence and
recommends a hub-and-spoke restructuring rather than growing the monolith.

**Sketch of approach.** Keep each repo's `AGENTS.md` as a thin always-loaded *hub*
that routes to scoped *spokes* read on demand. Author four new spokes under
`docs/guidance/`: `scientific-rigor.md` (measurement + statistics; the
*is-this-number-what-it-claims* checklist; descriptive-vs-inferential; confound/causal
discipline), `data-and-reproducibility.md` (the bundle as a contract; declare
clock+units; seeds/provenance/idempotent rebuilds; cross-repo records sources of
truth), `experiment-and-welfare.md` (grant/hypothesis grounding + the welfare
invariant), and `agent-collaboration.md` (ask-before-assuming + grounded uncertainty).
Cross-reference the data/records and welfare spokes from the resources repo `AGENTS.md`
so the discipline is shared, not duplicated. Write each spoke worked-example-first from
the ADRs the review cites. Land as small stages: ADR-0146 first, then the spokes, then
the hub slim-down.

**Evaluation / acceptance.** Each spoke is short, plain-language (glosses/links
`docs/glossary.md`), and cites real ADRs as examples; the hubs read as accurate
routers with a one-line-per-spoke table; no guidance is duplicated across docs; the
resources repo Change Log records the change.

**Effort / risk.** Medium (writing, not code). Risk: bloating the always-loaded hub
(mitigated - the point is to *shrink* it) or duplicating content (mitigated by the
single-source-of-truth/routing rule).

**ADR needed?** Yes - ADR-0146 records the restructuring decision (named but not yet
written; the review is recommend-only).

### FW-0054: Measurement-rigor checklist + grounded-uncertainty norm in the workflow

- **Status:** Proposed
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Area / modules:** `CONTRIBUTING.md` ("Before you commit"), `AGENTS.md`
  (autonomy/collaboration guidance), the `agent-collaboration.md` + `scientific-rigor.md`
  spokes (FW-0053)
- **Related ADRs:** ADR-0146 (to be written); evidence in ADR-0092/0093/0094/0095
  (misleading stats) and the epistemics discussion in
  [`agent-guidance-rigor-review.md`](design/agent-guidance-rigor-review.md) §4.1, §4.8

**Motivation / context.** The `#260720_BehaviorStatError` cluster shows agents
repeatedly shipped numbers whose definition/denominator/units didn't match their
label; and two human-adopted practices (checking whether the agent has questions;
the agent stating grounded certainty and flagging work that needs checking) have no
home in the frameworks.

**Sketch of approach.** Add a short *is-this-number-what-it-claims* checklist to the
pre-commit reminders (numerator/denominator + set; descriptive vs inferential; name
matches definition; clock/units honest; caveat travels with the number). Add an
ask-before-assuming rule (surface open questions + key assumptions on consequential/
ambiguous forks; don't over-ask on reversible defaults) and a grounded-uncertainty
rule (mark verified vs inferred vs recalled/guess; flag unverified claims and name
what would verify them; no numeric self-confidence). Keep the canonical wording in
the spokes and only *nudge* from `CONTRIBUTING.md`/`AGENTS.md`.

**Evaluation / acceptance.** The checklist is short enough to actually run; the
uncertainty norm is grounding-based (not a percentage); both are referenced from the
workflow docs without duplicating the spoke text.

**Effort / risk.** Low. Risk: checklist fatigue (mitigated by keeping it to ~5
questions) or over-asking (mitigated by the "reversible defaults" carve-out).

**ADR needed?** Covered by ADR-0146 (part of the same guidance decision).

### FW-0055: Glossary terms for scientific + epistemic rigor

- **Status:** Proposed
- **Date added:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Area / modules:** `docs/glossary.md`
- **Related ADRs:** ADR-0146 (to be written); supports FW-0053/FW-0054

**Motivation / context.** The rigor spokes (FW-0053) introduce recurring terms that
should be defined once and pointed to, per the "point to the glossary instead of
re-defining" rule in `AGENTS.md`.

**Sketch of approach.** Add short, plain-language entries for: *measurement
provenance* (a number stating what it is, its units, and its caveats), *grounded
uncertainty / verification status* (verified vs inferred vs recalled - the reliable
alternative to a numeric confidence), *descriptive vs inferential* (observing vs
drawing a statistical conclusion that needs an uncertainty), and *hub-and-spoke* (a
thin always-loaded index routing to scoped docs). Reuse the existing *endogeneity*
and *source of truth* entries rather than re-defining them.

**Evaluation / acceptance.** Each entry is a sentence or two, links a relevant ADR
where one exists, and is referenced (not re-explained) by the spokes.

**Effort / risk.** Low.

**ADR needed?** No - covered by ADR-0146; glossary additions are mechanical.

### FW-0056: Camera-visible cue LEDs on spare optocoupler channels

- **Status:** Proposed
- **Date added:** 2026-09-02
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** the rig electronics board (two spare MT-301R4P-N channels,
  wired from free Due pins); `firmware/common/rig_config.h` (new
  `PIN_LED_CUE_LOW` / `PIN_LED_CUE_HIGH`, optional `PIN_LED_CUE_RETURN`);
  `firmware/cue-guided-task/cue-guided-task.ino` (`handleCueOn`, `handleCueOff`,
  the MODE-reset path); `firmware/PROTOCOL.md` + `PROTO_VERSION` and
  `host/tmaze/core/protocol.py` if the optional `LEDTEST` command is added;
  `host/led_map.json` (FW-0045) gains the two LEDs; the rig manual's pin table in
  `os_code_t_maze_resources/rig-hardware/generate_rig_manual.py`
- **Related ADRs / work:** [ADR-0044](decisions/0044-host-driven-cue-command.md)
  / [ADR-0046](decisions/0046-host-owned-phase3.md) (host-driven `CUE_ON`, cue
  identity as a frequency), [ADR-0063](decisions/0063-gate-the-tone-timer.md)
  (TONE_ON/OFF TTLs next to the DAC), FW-0045 (Phase 1 board-LED detection this
  feeds), FW-0018 (Phase 3 sync line, which reuses the same wiring trick);
  design: [`docs/design/cross-stream-sync-design.md`](design/cross-stream-sync-design.md) §4

**Motivation / context.** The electronics board's optocoupler indicator LEDs
already flash for every beam break and solenoid pulse, so those events can be
read out of the video (FW-0045). Cues cannot: the cue is an analog waveform on
`DAC0`, and the only digital trace of it is a 5 ms TONE_ON TTL that no camera at
~30 fps can see. A camera-visible marker per cue tone closes that gap and gives
the constellation registration a time-labelled anchor that fires without the rat.

**Sketch of approach.** *Hardware:* one wire per cue tone from a free Due pin to
a spare input channel on an existing MT-301R4P-N 4-channel optocoupler module;
the module's own indicator LED is the marker and the isolated output stays free
(a future spare TTL). The Due is 3.3 V and sources only a few mA, so a bare LED
must not hang off a pin - the module input is exactly the load it can drive.
Checklist: count spare channels (two, three with a return-cue LED; OPI104-HS is
the fallback), confirm the input side accepts 3.3 V logic and its polarity, wire
common ground, use pins 8/9(/10) (unused; 0-1 are the console UART, 4-7 the
TTLs), photograph, update the rig manual pin table (which also still omits TTL
pins 4-7). *Firmware:* a **level held for the whole tone**, not a pulse. Cue
identity in firmware is a frequency (low/high tone), not a side - `handleCueOn`
resolves `freq` from the correct arm, the return-cue coin flip, or a host Hz - so
pick the LED by which band `freq` is nearer (`cfg.lowToneHz` vs
`cfg.highToneHz`), set it HIGH where `startToneAsync()` is called, LOW in
`handleCueOff()` next to `stopToneAsync()`, and clear both in the MODE-reset path
so a mid-tone mode switch cannot leave an LED stuck on. *Optional `LEDTEST`
command* (`LEDTEST,<mask>,<ms>`) drives the LED pins without the DAC for a
silent session-start roll-call and echoes `<ms>,LEDTEST,<mask>` so the serial
log carries it and it is never mistaken for a real cue (protocol change: bump
`PROTO_VERSION`, document in `PROTOCOL.md`, teach `tmaze.core.protocol`).

**Evaluation / acceptance.** Bench: the LED-on frame in the recording is the
first frame whose exposure overlaps the `CUE,ON` serial line (within one frame
period), and the LED goes dark within one frame of `CUE,OFF`; low- and high-tone
cues light different LEDs; an `LEDTEST` echo appears in the serial log and is
excluded from cue counts by every consumer. Regression: tone timing, the
TONE_ON/OFF TTLs, and the DAC output are unchanged (the pin writes sit outside
the 80 kHz ISR).

**Effort / risk.** Small firmware diff plus an afternoon of wiring. Risks: no
spare channel or a 5 V-only input (fallback: OPI104-HS breakout); an LED stuck on
after an abnormal stop (mitigated by the MODE-reset clear); confusing the LEDs
with the cue *side* - they mean low/high *tone*, which is the identity the
firmware reliably has.

**Open questions.** Two LEDs or three (return cue)? Is the `LEDTEST` roll-call
worth a protocol bump now, or can the roll-call live on rinse/drain alone until
the Phase 3 sync LED exists? Should the host ever pass an explicit LED index on
`CUE_ON` instead of the band split?

**ADR needed?** Yes for the firmware/protocol part (new pins with a deliberate
level-not-pulse choice, and the `LEDTEST` command if added). The hardware
install is documented in the resources repo's rig manual, not an ADR.

### FW-0057: Live `[ALT-VETO]` readout for the suppressed debias trigger

- **Status:** Proposed
- **Date added:** 2026-09-03
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `host/tmaze/core/phase3/intervention_stack.py` (already
  exposes `debias_vetoed`), `host/tmaze/core/phase3/trial_sequence.py` (surface
  it), `host/tmaze/core/phase3/phase3_log.py` (`Phase3StateSnapshot` field),
  `host/tmaze/core/phase3/phase3_stats.py`, `host/tmaze/ui/behavior_view.py`
  (the "Side-bias b=.../trigger" line)
- **Related ADRs:** [ADR-0149](decisions/0149-debias-veto-when-switch-dominant.md)
  (the veto this makes visible), [ADR-0107](decisions/0107-phase3-alternation-intervention-stack.md)

**Motivation / context.** ADR-0149 makes the stack *silently* refuse to auto-start
a debias block while the animal is switch-dominant (or during the post-de-alt
grace). Per trial nothing is logged - no block starts - so an operator watching
the live readout sees a bias over `trigger_bias` with `errors >= min_errors` and
no block, and has to know about the veto to understand why. Post hoc it is
reconstructable (header `alt_veto_rate` + per-trial `bias` / `alt_rate` /
`sched_state`), but the live panel should say so.

**Sketch of approach.** Thread the stack's existing `debias_vetoed` bool through
`TrialSequence` into `Phase3StateSnapshot` -> `phase3_stats` (a `debias_vetoed`
field, false when the stack/veto is absent) and append a short tag to the
side-bias readout line in `behavior_view.py`, e.g. `[ALT-VETO alt=0.71]`, with a
tooltip pointing at the `Alt veto rate` knob. Optionally also a
`auto:debias_vetoed` timeline note the first trial the veto engages (rate-limited
so a long vetoed stretch is one note, not one per trial). No CSV column or
protocol change.

**Evaluation / acceptance.** Headless: a snapshot from a switch-dominant armed
stack reports `debias_vetoed=True`; a camper reports `False`. Bench: the tag
appears exactly on the trials where a bias over threshold is not starting a
block, and disappears once the alt window refills / the rat drops to camper
levels.

**Effort / risk.** Small (one bool through five files). Risk: readout clutter -
keep it a terse tag.

**ADR needed?** No - a pure observability surface for a decision already recorded
in ADR-0149.

### FW-0058: Reconcile the beam- and lick-state polarity (`S,<i>,1` / `L,<i>,1` are the resting states, not "broken" / "active")

- **Status:** Proposed
- **Date added:** 2026-09-08
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Area / modules:** `firmware/PROTOCOL.md` (the `S` and `L` rows), `firmware/cue-guided-task/cue-guided-task.ino`
  (`raw = digitalRead(sensorPins[i]) == LOW`, and the lick read), `firmware/common/rig_config.h`
  (sensor wiring notes), and every host consumer of the `S`/`L` edge's `active`
  flag: `core/protocol.py` (`sensor_edge`, `lick_edge`), `core/session/session_serial.py`,
  `core/serial_liveness.py`, `core/phase3/intervention_stack.py`,
  `core/rules_engine.py` (`_lick_port_of`: takes `L,<i>,1` as "a lick"),
  `ui/rig_view.py`, `ui/maze_gui.py` (sensor labels green when `active`),
  `ui/lick_tuner.py`, `ui/tooltip_text.py`,
  `tools/maze_state_validate.py`; the analysis repo where it reads `S`/`L` lines
- **Related ADRs / work:** FW-0045 (where this was found),
  [`docs/design/cross-stream-sync-design.md`](design/cross-stream-sync-design.md) §3.7

**Update (2026-09-08, Slice 1 of FW-0045).** Two things done, the rest still
open. (a) The **lick detector has the same inversion**: over the full session
`600_20260903_134552`, `L2=1` runs unbroken for up to 257 s (1582 s in total)
while `L2=0` never exceeds 0.3 s (median 92 ms), and the lick LED lights during
`0`. So `L,<i>,0` is the tongue contact and `L,<i>,1` idle. (b) The `S` and `L`
rows of `PROTOCOL.md` were **corrected to the measured polarity** (with the
evidence and a pointer here), and `tmaze/core/sync/spans.py` hard-codes the
active states (`BEAM_ACTIVE_STATE = LICK_ACTIVE_STATE = "0"`) with a fixture
test that checks them against the real log
(`tests/test_sync_fixture.py::test_active_states_are_the_brief_ones`). Still
open: the hand check on the rig, the firmware source comment, and the consumer
audit - note that `core/rules_engine.py` reads `L,<i>,1` as the lick, which
under the measured polarity is the *release* edge (~90 ms after contact; the
reward still follows a lick, just late), and `ui/maze_gui.py` / `ui/lick_tuner.py`
show "active" while the sensors are idle.

**Motivation / context.** `PROTOCOL.md` documents body-beam edges as
`S,<i>,<state>` with `state 1 = broken, 0 = clear`. The recorded serial logs say
otherwise: in session `600_20260903_134552` the `1` spans of every beam reach
tens of seconds (p90 26-74 s) while the `0` spans never exceed ~0.4 s (median
50-230 ms), and the board's channel indicator LEDs go **dark** during the `0`
spans. So the beam's *resting* state is reported as `1` and the rat's crossing
as `0`. The firmware sets state 1 when the pin reads LOW; the likely cause is the
Banner sensors running in light-operate mode (output on while the beam is
received) behind the MT-301R4P-N stage, so "input active" means "beam intact".
Whether any host or firmware logic is actually wrong depends on whether it
reads the flag as *a transition happened* (fine) or as *the beam is broken now*
(inverted). The firmware FSM in Full task uses these edges to drive trials, so
this needs a definitive answer, not a guess.

**Sketch of approach.** (1) On the rig, with no rat, break each beam by hand and
watch the `S` lines - one minute settles the physical polarity. (2) Grep the
consumers above for places that interpret `active` as "broken" (as opposed to
"edge of either sign") and list them. (3) Decide where to fix: flip the label in
`PROTOCOL.md` + firmware comments (if every consumer only wants edges), or invert
in firmware (`== HIGH`, a protocol-visible change -> `PROTO_VERSION` bump and an
ADR), or make consumers polarity-agnostic. Old logs keep the old meaning either
way, so the analysis layer needs a per-`PROTO_VERSION` (or per-date) rule. (4)
Whatever the outcome, the LED map (FW-0045) records the *measured* polarity per
LED, so the sync fit is unaffected.

**Evaluation / acceptance.** The `S` row in `PROTOCOL.md` matches what a hand
break produces on the rig; every consumer's reading of the flag is documented
as edge-only or state-aware; recorded-log interpretation is stated per protocol
version; `tests/test_protocol.py` pins the convention.

**Effort / risk.** Small to verify, small-to-medium to reconcile. Risk: a firmware
flip silently inverts the Full-task FSM's beam logic - do the consumer audit
before touching firmware.

**ADR needed?** Yes if the firmware/protocol meaning changes; otherwise a doc
fix referencing this entry.

## Completed

Implemented items are removed from the active backlog above; their reasoning now
lives in the linked ADRs. This table is kept only so the `FW-NNNN` ids still
referenced from those ADRs resolve.

| ID | Title | Implemented in |
|----|-------|----------------|
| FW-0004 | Position-based task-rule engine over landmarks | [ADR-0023](decisions/0023-position-based-rules-engine.md) |
| FW-0026 | Mid-session gate reopen must not restart/overwrite the session | [ADR-0120](decisions/0120-gate-reopen-resumes-session.md) |
| FW-0007 | Maze-state consumers - rule-shaping + behavior stats | [ADR-0026](decisions/0026-reward-shaping-phases.md), [ADR-0032](decisions/0032-live-behavior-readout.md) |
| FW-0012 | Run tracking off the camera grab thread | [ADR-0040](decisions/0040-tracking-off-grab-thread.md) |
| FW-0021 | Single-parse serial fan-out in the GUI orchestrator | [ADR-0056](decisions/0056-single-parse-fanout-and-mode-reset-seam.md) |
| FW-0022 | Unified MODE-switch reset seam | [ADR-0056](decisions/0056-single-parse-fanout-and-mode-reset-seam.md) |
