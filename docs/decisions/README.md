# Decision log (ADRs)

This folder holds **Architecture Decision Records** — short, numbered,
immutable notes that capture *why* a non-obvious choice was made, not just
*what* changed. Git history already records the what/when/who; ADRs record the
reasoning, the alternatives considered, and the evidence, so a decision can be
understood (and re-justified or revisited) long after it was made.

On a behavioral rig this matters: timing constants, thresholds, reward volumes,
and protocol framing have scientific consequences, and the justification for a
value is often more important than the value itself.

## When to write an ADR

Write one when the *reasoning* isn't self-evident from the diff, e.g.:

- A protocol change (framing, message format, version bump).
- A timing / threshold / volume constant whose value is a deliberate choice.
- An architectural decision (process split, ownership of a resource, transport).
- Anything where a future reader would reasonably ask "why was it done this way?"

Skip ADRs for obvious, mechanical, or purely cosmetic changes.

## How to add one

1. Copy [`template.md`](template.md) to `NNNN-short-title.md`, where `NNNN` is
   the next zero-padded number (e.g. `0004-...`).
2. Fill in every section. Be concrete: name the constant and its file, link the
   commit/PR, and point to any bench data or scope photos (these may live in the
   `os_code_t_maze_resources` repo).
3. Set **Status** to `Accepted` once the change lands.
4. Add a row to the index below.
5. Reference the ADR from the code (`// see docs/decisions/0004`) and from the
   commit/PR body (`Refs ADR-0004`).

## Immutability

ADRs are append-only. Don't rewrite the reasoning in an existing ADR. If a
decision changes, write a **new** ADR, set the old one's status to
`Superseded`, and cross-link the two via the *Supersedes / Superseded by* field.
The chain of records is the audit trail.

## Index

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-serial-broker-tcp-fanout.md) | Use a single-owner serial broker with localhost TCP fan-out | Accepted | 2026-06-01 |
| [0002](0002-transparent-line-passthrough.md) | Keep the broker a transparent verbatim line passthrough | Accepted | 2026-06-01 |
| [0003](0003-broker-only-clients-no-serial-fallback.md) | Make GUI clients broker-only (no direct-serial fallback) | Accepted | 2026-06-01 |
| [0004](0004-launcher-external-conda-activation.md) | Start the rig via start_rig.bat, with conda activation done externally | Accepted | 2026-06-01 |
| [0005](0005-per-sketch-rig-config-copy.md) | Duplicate rig_config.h per sketch instead of a shared common/ header | Superseded | 2026-06-03 |
| [0006](0006-symlink-rig-config-per-sketch.md) | Symlink the shared rig_config.h into each sketch folder | Accepted | 2026-06-03 |
| [0007](0007-reward-at-central-port.md) | Deliver reward at the central port (v1 cue-guided task) | Accepted | 2026-06-03 |
| [0008](0008-wavetable-tone-isr.md) | Generate the tone cue from a wavetable, not sin() in the ISR | Accepted | 2026-06-03 |
| [0009](0009-runtime-training-modes-and-logging.md) | Runtime training modes (Free Roam / Full task), anti-bias sequencing, persistent logger | Accepted | 2026-06-23 |
| [0010](0010-manual-solenoid-control-in-task.md) | Full manual solenoid control + auto well-clean at trial start in cue-guided-task | Accepted | 2026-06-24 |
| [0011](0011-camera-tab-freerun-ffv1.md) | Add the Basler camera as an in-GUI tab (free-run, FFV1, decoupled grab/encode) | Accepted | 2026-06-24 |
| [0012](0012-pyav-multithreaded-ffv1.md) | Encode the camera's FFV1 video with PyAV slice multithreading | Accepted | 2026-06-24 |
| [0013](0013-selectable-lossless-lossy-recording.md) | Let the GUI pick lossless FFV1 or compressed H.264 per recording | Accepted | 2026-06-24 |
| [0014](0014-markerless-position-tracking.md) | Markerless rat position tracking (shared CV core + appearance model) | Accepted | 2026-06-29 |
| [0015](0015-undistort-at-source.md) | Apply lens-distortion correction at the camera source (preview, tracking, recording) | Accepted | 2026-06-29 |
| [0016](0016-quit-rig-session-tagging.md) | Tag rig processes with a session id so one "Quit rig" button can close them all | Accepted | 2026-06-30 |
| [0017](0017-live-scoring-tolerance-knobs.md) | Expose appearance-model tolerances as live, scoring-time knobs | Accepted | 2026-06-30 |
| [0018](0018-landmark-annotations.md) | Annotate reward wells and body sensors as image-space landmarks | Accepted | 2026-06-30 |
| [0019](0019-reward-volume-and-pulse-duration-logging.md) | Log reward pulse duration on the wire and track dispensed volume on the host | Accepted | 2026-06-30 |
| [0020](0020-save-background-image-with-recordings.md) | Save the empty-rig background image alongside recordings | Accepted | 2026-06-30 |
| [0021](0021-host-reads-rig-config-pin-map.md) | Read rig_config.h on the host as the single source of truth for the pin map | Accepted | 2026-07-01 |
| [0022](0022-verified-lick-port-pin-mapping.md) | Verified lick-port <-> pin/index mapping (left=L0/37, right=L1/36, center=L2/35) | Accepted | 2026-07-01 |
| [0023](0023-position-based-rules-engine.md) | Position-based rules engine (pure core + Free-Roam-gated executor) with cm-based spatial calibration | Accepted | 2026-07-02 |
| [0024](0024-shared-host-protocol-parser.md) | Centralize host-side serial-protocol parsing in protocol.py | Accepted | 2026-07-06 |
| [0025](0025-maze-state-module.md) | Discrete maze-state module (zones + direction + loops) over the tracker | Accepted | 2026-07-06 |
| [0026](0026-reward-shaping-phases.md) | Host-side reward-shaping phases via a task-event layer and a credential-gated well rule | Accepted | 2026-07-06 |
| [0027](0027-embed-acquisition-params-in-model.md) | Embed acquisition-time tracker settings in the saved model and restore them on load | Accepted | 2026-07-07 |
| [0028](0028-probabilistic-phase-fade.md) | Fade Phase 1 into Phase 2a with a probabilistic non-stem reward, not a hard switch | Accepted | 2026-07-08 |
| [0029](0029-comprehension-debt-guardrails.md) | Fight comprehension debt with advisory guardrails (CONTRIBUTING, env, tests, lint), not enforcement | Accepted | 2026-07-08 |
| [0030](0030-prime-at-choice-entry.md) | Deliver the Phase 2a/2b prime at choice-zone entry; redefine 2b as stem-gated choice + choice-side anti-bias | Accepted | 2026-07-08 |
| [0031](0031-gui-edit-rules-json.md) | Edit rule fields from the Rules tab and write back to the versioned rules.json | Accepted | 2026-07-09 |
| [0032](0032-live-behavior-readout.md) | Live Phase 2a behavior readout (pure stats core over the task-event stream) in the Rig view tab | Accepted | 2026-07-09 |
| [0033](0033-human-capture-inbox.md) | Human-owned bug/idea capture inbox (`docs/notes/`), separate from the agent-curated backlog | Accepted | 2026-07-10 |
| [0034](0034-parallel-blocker-tracking.md) | Track orange-taped blockers in parallel and subtract their footprint so they can't steal the rat's track | Accepted | 2026-07-10 |
| [0035](0035-blocker-footprint-per-frame.md) | Make the blocker footprint per-frame (clear when the tape isn't visible) so a removed blocker stops masking | Accepted | 2026-07-14 |
| [0036](0036-behavior-session-timer.md) | Session timer in the behavior readout (wall-clock span, separate from active time) | Accepted | 2026-07-10 |
| [0037](0037-cluster-fragmented-rat-blobs.md) | Track the rat as a cluster of nearby blobs (spatial + colour-membership merge) with decoupled registration | Accepted | 2026-07-15 |
| [0038](0038-once-per-excursion-well-credit.md) | Credit the well reward at most once per outbound excursion (per-excursion choice latch) | Accepted | 2026-07-10 |
| [0039](0039-blocker-containment-drop.md) | Exclude the blocker by dropping its blob in one pass (containment test), not pixel-subtracting + re-detecting, to cut per-frame tracking load | Accepted | 2026-07-21 |
| [0040](0040-tracking-off-grab-thread.md) | Run rat tracking on its own worker off the camera grab thread (newest-frame handoff + publisher), so tracking cost can't drop source frames | Accepted | 2026-07-22 |
| [0041](0041-shared-maze-state-pipeline.md) | One shared maze-state pipeline on the tracking worker (frame-rate estimator + drained fan-out) replacing the three per-tab estimators | Accepted | 2026-07-22 |
| [0042](0042-fast-actuation-maze-state-view.md) | A second "fast" (low-dwell) maze-state view + worker push seam for actuation, alongside the debounced display view | Accepted | 2026-07-22 |
| [0043](0043-rules-actuation-thread.md) | Move reward actuation onto a dedicated thread fed by fast-view pushes + routed lick edges (single-threaded engine, atomic gate snapshot) | Accepted | 2026-07-22 |
