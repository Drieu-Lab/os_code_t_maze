# ADR 0046: Initial host-owned Phase 3 (firmware as a real-time actuator), PROTO_VERSION 6

- **Status:** Accepted
- **Date:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/cue-guided-task.ino` (`handleCueOn`
  explicit `<hz>`, larger `cmdBuf`, autonomous Free-Roam beam TTLs),
  `firmware/common/rig_config.h` (`PROTO_VERSION`, `CUE_HZ_MIN`/`CUE_HZ_MAX`),
  `firmware/PROTOCOL.md`, new host cores `host/trial_sequence.py` +
  `host/phase3_task.py` (+ tests), new host shell `host/phase3_controller.py`
  (Phase 3 tab) + `host/experiment_control.py` wiring
- **Supersedes / Superseded by:** builds on ADR-0044 (host-driven cue command),
  ADR-0045 (vision cue zone gate), ADR-0026 (host reward-shaping phases /
  task-event layer); —

## Context

Phase 3 (the full cued two-alternative task) has lived in firmware for its
real-time cue timing and the hardware-synchronous Open Ephys TTLs (ADR-0026
kept it there while the shaping phases 1/2a/2b moved host-side). But the audio
cue is now a function of the rat's tracked POSITION, which only the host has
(ADR-0044/0045): the host already decides cue onset from vision and drives
`CUE_ON`/`CUE_OFF`. ADR-0044 also documented a latent split-brain race: the host
decides cue onset from vision while the firmware still resolves WHICH tone from
the `B_ENTRY` beam and its own trial sequence.

The question this ADR answers (an initial cut of FW-0016): for a first working
Phase 3, where should the trial logic live? The observation that drives the
choice: the firmware's trial FSM and the host's vision-based task view are two
authorities that must be kept in sync, and the hardest failure mode is drift
between them. If the firmware FSM is simply turned OFF, there is exactly one
authority and no sync problem at all - and the host already reconstructs the
task-relevant structure better than beams can (`maze_state` / `task_events`,
ADR-0025/0026), and already owns reward in Free Roam (ADR-0023/0026).

The hard constraint that cannot move: closed-loop cue/reward timing and the
neural-recording TTLs must stay hardware-real-time. So "the host owns the task"
must NOT mean "the host times the hardware."

## Decision

Run an **initial host-owned Phase 3 in the firmware's Free Roam posture** (trial
FSM off), with the Due reduced to a real-time actuator. The host owns the entire
task; it commands the Due only to turn the cue on/off and to dispense reward.

Ownership split:

- **Host owns (decision + state):** the L/R trial sequence, cue identity,
  choice scoring, and trial advancement - all driven off VISION
  (`task_events` zone/loop edges), not raw beams. New pure cores:
  - `host/trial_sequence.py` - the seed-deterministic schedule + `cueFor`
    identity, ported from `task_core.h` (the future home of FW-0001 / FW-0008).
  - `host/phase3_task.py` - a pure trial FSM folding `TaskEventFrame`s into the
    current trial, correct arm, outcome, reward decision, and advancement.
  - The thin `host/phase3_controller.py` (a "Phase 3" tab) composes the existing
    `CueGate` (WHEN the cue sounds, positionally) with `Phase3Task` (WHAT tone,
    scoring, reward), on a dedicated actuation thread (the ADR-0043 pattern).
    Gated to Free Roam + tracking + a valid zone map; it owns and logs the seed.
- **Due keeps (real-time / hardware):** tone synthesis, the reward valve pulse
  (fired on the existing `R<port>` command), and the hardware TTL event marks.

Protocol (bump `PROTO_VERSION` 5 -> 6; reflash):

- `CUE_ON,<CHOICE|RETURN>,<hz>` - the cue command gains an OPTIONAL explicit
  frequency. Without it the firmware resolves identity as in v5 (back-compatible);
  with it the host supplies identity directly, clamped to `[CUE_HZ_MIN,
  CUE_HZ_MAX]`. This is required because with the FSM off `correctArm` is unset,
  so the firmware's own `cueFor` would fall back to the low tone. The command
  stays otherwise identical, honoring ADR-0044's forward-compatible design.
- **Autonomous Free-Roam beam TTLs** - the choice / return-arm TTLs fire directly
  on their beam edges while in Free Roam, so the Open Ephys event marks are
  preserved with the FSM (which fires them in Full task) off. No double-fire: in
  Free Roam the FSM never reaches `AwaitChoice`/`AwaitReturn`. The host never
  commands a TTL (that would put a neural fiducial behind serial latency).

## Alternatives considered

- **Firmware stays the trial-advance authority; host authors + uploads the
  sequence.** The lower-risk long-term shape (one real-time authority, host
  authors the plan), but heavier for a FIRST cut: it needs a sequence-upload
  protocol + handshake and keeps the ADR-0044 identity race. Deferred (still the
  candidate if reward timing ever needs to be on-Due); see FW-0016.
- **Host becomes the trial authority but pre-arms the firmware to fire reward
  locally on the choice beam.** Preserves on-Due reward timing, but reintroduces
  a per-trial host->firmware arming contract and a second real-time authority.
  Overkill for the initial cut given reward is collected at the center well.
- **Explicit `TONE_ON,<hz>` instead of extending `CUE_ON`.** Rejected in ADR-0044
  for the beam era; here we keep the semantic `CUE_ON,<kind>` and only APPEND an
  optional `<hz>`, so v5 senders are unaffected and the kind still tags the log.
- **Run Phase 3 in Full task (FSM on) and overlay the host.** Rejected: two
  authorities, the exact split-brain this ADR removes by turning the FSM off.

## Consequences

- One authority, no host/firmware trial drift - the firmware FSM is simply not
  running. Phase 3 becomes a continuation of the ADR-0026 host-task-rule model.
- Reward now takes a serial round-trip (vision -> host -> `R<port>` -> Due)
  instead of firing on the Due at the choice beam. Behaviorally acceptable: v1
  reward is delivered at the CENTER well and collected on the way back, so it is
  a contingency, not a sub-100 ms-timed event. It IS a regression from on-Due
  firing and should be measured once on the bench.
- Loss of firmware autonomy: if the host crashes or serial drops, no cue/reward
  fires. Acceptable for bring-up with the experimenter present.
- The `Cue` tab (ADR-0044/0045) remains the FIRMWARE-FSM cue path (Full task);
  the new Phase 3 tab is the HOST-OWNED path (Free Roam). They are mutually
  exclusive by firmware mode, so only one ever drives the DAC / reward.
- Auto well-clean (rinse/drain at trial start), which the FSM did, is not carried
  over in this initial cut; it can be added later as host `R23`/`R24` sends.
- A protocol change touches `PROTOCOL.md`, `rig_config.h` (`PROTO_VERSION`), and
  the host; the Due must be reflashed. The `.ino` changes are shell-level (not
  the pure core), so `test_task_core.cpp` is unaffected; identity parity with the
  firmware generator is covered by `test_trial_sequence.py`, and the `.ino`
  behavior is bench-verified.

## Evidence / links

- `firmware/PROTOCOL.md` (v6) - the wire contract this implements.
- ADR-0044 (semantic `CUE_ON` chosen to stay valid across this migration),
  ADR-0045 (the `CueGate` this reuses), ADR-0026 (task-event layer + host reward),
  ADR-0043 (the actuation-thread pattern the Phase 3 tab mirrors), ADR-0008
  (wavetable tone ISR that bounds the sane `CUE_HZ` band).
- `docs/future-work.md` FW-0016 (this is its initial host-owned realization),
  FW-0008 (Phase 3 transition), FW-0001 (60/40 generator, now a host change).
- Tests: `host/test_trial_sequence.py`, `host/test_phase3_task.py`.
