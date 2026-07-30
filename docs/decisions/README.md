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
| [0044](0044-host-driven-cue-command.md) | Host-driven positional audio cue command (CUE_ON/CUE_OFF + CUE,VISION\|BEAM source flag), PROTO_VERSION 5 | Accepted | 2026-07-22 |
| [0045](0045-vision-cue-zone-gate.md) | Host vision cue as a zone-membership gate (fast view + CueGate, fail-open choice / fail-safe return) with a redrawn return zone + offline onset measurement | Accepted | 2026-07-22 |
| [0046](0046-host-owned-phase3.md) | Initial host-owned Phase 3 (firmware as a real-time actuator): host owns sequence/identity/scoring off vision, `CUE_ON,<kind>,<hz>` + autonomous Free-Roam beam TTLs, PROTO_VERSION 6 | Accepted | 2026-07-22 |
| [0047](0047-phase3-return-schedules-and-session-log.md) | Deterministic balanced per-arm return-cue schedules (played by physical arm) + a self-describing, crash-safe Phase 3 session data file (plan matrix + choices + event timings) | Accepted | 2026-07-23 |
| [0048](0048-host-session-orchestration-and-manifest.md) | Host-only session orchestration ("Session" tab): one-click start/stop of the recorders + a shared `session_id`/manifest as the correlation index, with a single `mark_session_start` fiducial seam for the future firmware SYNC TTL + LED barcode | Accepted | 2026-07-23 |
| [0049](0049-per-session-recording-folder-tree.md) | Per-session recording folder tree (`<root>/<session_id>/{video,task,serial}/` + manifest) under `phase3_recordings`, via `out_dir` redirect seams, plus a filtered per-session serial file (sensor + central-port lines) written by the Session panel | Accepted | 2026-07-23 |
| [0050](0050-phase3-published-state-contract.md) | Publish an immutable Phase 3 state snapshot (`Phase3StateSnapshot`) from the controller via atomic reference, exposed cross-tab through the `phase3_ref` holder, so read-only consumers (stats panel, trial ribbon) never race the actuation thread's live table | Accepted | 2026-07-23 |
| [0051](0051-phase3-in-tab-progress-visualization.md) | In-tab Phase 3 progress visualization: a pure `phase3_ribbon.py` display-row helper (current trial + last/next few, clamped) feeding a compact trial ribbon + a scrollable all-N planned-trial list in the Phase 3 tab, read-only over the ADR-0050 snapshot on the Tk poll (change-gated) | Accepted | 2026-07-23 |
| [0052](0052-phase-aware-behavior-readout-and-phase3-stats.md) | Phase-aware behavior readout: a pure `phase3_stats.py` core (`compute` -> accuracy/per-cue/bias/reward/win-stay-lose-shift/RT over the ADR-0050 snapshot) feeding a switched Phase 3 sub-panel in the bottom-strip behavior view, shown while a Phase 3 session is armed | Accepted | 2026-07-23 |
| [0053](0053-gui-tab-streamlining.md) | Regroup the six experiment-control GUI tabs into three workflow tabs (Operate / Setup / Task), retire the Cue tab (== Phase 3 without scoring/reward/sequence), and extract a shared vision-actuator skeleton for Rules + Phase 3 | Proposed | 2026-07-24 |
| [0054](0054-unified-reward-dispatcher.md) | Unify host reward dispatch behind one shared RewardDispatcher (global rate backstop + pulse/volume record + R<port> send) across Rules + Phase 3 | Accepted | 2026-07-24 |
| [0055](0055-shared-choice-commit-edge.md) | Promote a single once-per-excursion choice-commit edge (`choice_committed` + `trip_id`) into the task-event layer, consumed by the rules credential, Phase 3 scoring, and the behavior readout | Accepted | 2026-07-24 |
| [0056](0056-single-parse-fanout-and-mode-reset-seam.md) | Parse each serial line once (`protocol.classify` -> `LineInfo`) and route MODE switches through one reset seam (`LineRouter` + `on_mode_changed`), replacing N+1 re-parses and scattered per-module resets | Accepted | 2026-07-24 |
| [0057](0057-trial-ribbon-on-rig-view.md) | Extract the Phase 3 trial ribbon into a shared `TrialRibbonView` widget (push/pull drive modes over the ADR-0050 snapshot) and show it on the Rig view too, so the current/upcoming trials are visible during a run without switching tabs | Accepted | 2026-07-24 |
| [0058](0058-rig-view-trial-control-buttons.md) | Rig-view trial-control buttons (Skip / Repeat / Back / +Trials / Restart / Pause) via a `("cmd", …)` actuation-thread command channel + a self-polling `TrialControlBar`, with an `ABANDONED` outcome excluded from the stats | Accepted | 2026-07-24 |
| [0059](0059-append-only-trial-sequence-extension.md) | Append-only, seed-deterministic trial-sequence extension (`TrialSequence.extend` continues the stored PRNG cursor by whole balanced blocks; `Phase3Log.extend_to` + `planned_trials` header) so a running session can grow without altering already-run trials | Accepted | 2026-07-24 |
| [0060](0060-session-button-owns-phase3-session.md) | The Session panel's Start/Stop button owns the Phase 3 session boundary: Start requires Phase 3 already armed (sets session context, no longer arms), Stop leaves Phase 3 running (no longer disarms), and the button's `SessionTimer` drives a session start/stop/duration line in the Phase 3 stats panel | Accepted | 2026-07-24 |
| [0061](0061-return-cue-requires-outbound-excursion.md) | Gate the return cue on the shared `outbound_used_stem` credential (stem-out excursion since the hub, the same predicate reward/clean-loop use), so it fires only on a genuine inbound return and not when a run starts up a return arm | Accepted | 2026-07-27 |
| [0062](0062-broker-bounded-writes-and-liveness-probe.md) | Make a wedged Due loud: bound the broker's serial writes (`write_timeout` + reader-thread-owned reopen) and probe a quiet port with `STATUS` via a pure `LivenessMonitor` (probe at 10 s, dead at 25 s) | Accepted | 2026-07-27 |
| [0063](0063-gate-the-tone-timer.md) | Run the 80 kHz tone-sample timer only while a tone sounds (`TC_Start` in `startToneAsync`, `TC_Stop` in `stopToneAsync`) instead of from boot to power-off | Accepted | 2026-07-27 |
| [0064](0064-broker-records-firmware-uptime.md) | Let the broker read each line's `<ms>,` prefix (`protocol.leading_ms`) so it can log firmware uptime at connect and the board's clock at death, making a lockup timestamp itself | Accepted | 2026-07-29 |
| [0065](0065-console-on-the-programming-port.md) | Move the firmware console to the Due's Programming port (`DUE_USE_NATIVE_USB 0`, host defaults to `COM3`), working around the ~1030 s Native-USB lockout at the cost of reset-on-open and a real 115200 UART | Accepted | 2026-07-29 |
| [0066](0066-rebind-phase3-output-on-session-start.md) | Re-point the Phase 3 data file at the session on Start (a `("cmd", "rebind")` on the actuation thread, since the gate opened before the session existed), release the context on Stop, and refuse any stream path outside the session tree in the manifest (`session_log.path_within_session`) | Accepted | 2026-07-29 |
| [0067](0067-choice-cue-requires-hub-departure.md) | Gate the choice cue on a new shared `left_hub_via_stem` credential (this trip departed the hub up the stem), so a lap run backwards is silent crossing the stem on its way home; held for the whole trip, so a post-choice reversal deliberately re-hears the tone | Accepted | 2026-07-30 |
| [0068](0068-trigger-dispenser-well-reward-split.md) | Split the well-reward rule into a pure `RewardTrigger` (why/when: credential + fade + debias, owns `armed`) and `WellDispenser` (how: port/pulse, visit lifecycle, prime, lick top-ups) in `reward_dispenser.py`; the rule becomes wiring (behaviour-preserving, implements FW-0009) | Accepted | 2026-07-30 |