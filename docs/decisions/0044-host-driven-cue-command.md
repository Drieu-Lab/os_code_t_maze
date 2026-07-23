# ADR 0044: Host-driven positional audio cue command (CUE_ON/CUE_OFF), PROTO_VERSION 5

- **Status:** Accepted
- **Date:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/common/rig_config.h` (`PROTO_VERSION`, `DEFAULT_CUE_SOURCE_VISION`),
  `firmware/cue-guided-task/task_core.h` (`TaskConfig.cueSourceVision`, `Phase::CueRunning`),
  `firmware/cue-guided-task/cue-guided-task.ino` (command parsing + `CUE` emit),
  `firmware/cue-guided-task/test/test_task_core.cpp`, `firmware/PROTOCOL.md`,
  `host/protocol.py` (`cue_info`), `host/test_protocol.py`
- **Supersedes / Superseded by:** —

## Context

The audio cue must become a function of the rat's CURRENT tracked position (a
spatial window), not of past beam events, to drive the hippocampal
place x auditory-cue interactions the BRAINK99 grant targets. Only the HOST has
position (vision runs on the tracking worker; the Due sees only beam edges and
serial). So the host must own the ON/OFF decision, while the firmware keeps
owning tone synthesis and the hardware-synchronous Open Ephys TTLs (the TTL
faithfully marks true acoustic on/off regardless of command latency).

Today there is NO host->firmware tone command. The firmware plays the tone the
whole time it is in `Phase::CueRunning` (between the `B_ENTRY` and `B_EXIT`
beams; see `task_core.h`), so a rat that breaks ENTRY then reverses keeps the
tone until it eventually trips EXIT. That beam-driven auto-tone is exactly what
the positional cue replaces. The accepted host commands were `ABORT`/`STOP`,
`MODE,FREE|FULL`, `R<port><sol>[,ms]`, `PING`, `STATUS` at `PROTO_VERSION 4`.

## Decision

Add host->firmware cue commands and a firmware log line, and bump
`PROTO_VERSION` 4 -> 5. Reflashing the Due is required.

Commands (host -> firmware; newline-terminated, not part of the `ms,TYPE`
schema), handled shell-side in `drainSerial()` like `R<port>`:

- `CUE_ON,CHOICE` — start the choice tone. Frequency = `cueFor(current trial
  correctArm, cfg)` (the firmware still owns tone identity; the host owns *when*).
- `CUE_ON,RETURN` — start the return tone. Frequency = a hi/lo pick from the
  session PRNG (identity randomized in firmware, per the task design).
- `CUE_OFF` — stop the tone.
- `CUE,VISION` / `CUE,BEAM` — select the cue source; default `BEAM`
  (`DEFAULT_CUE_SOURCE_VISION false`). Under `VISION` the firmware suppresses its
  own `CueRunning` auto-tone so the host is the single DAC authority; all other
  FSM/scoring/reward/TTL behavior is unchanged. `BEAM` preserves v4 behavior.

`startToneAsync`/`stopToneAsync` already pulse the `TONE_ON`/`TONE_OFF` TTLs, so
the hardware onset mark follows automatically. For the behavioral record the
firmware also emits a serial line reflecting the ACTUAL acoustic state (only on a
real (re)start / stop, never a no-op):

- `<ms>,CUE,ON,<CHOICE|RETURN>,<freq_hz>`
- `<ms>,CUE,OFF`

The host parses it via a new pure helper `cue_info()` in `host/protocol.py`.

The command grammar is intentionally SEMANTIC (`CUE_ON,CHOICE`) rather than an
explicit frequency (`TONE_ON,<hz>`). This keeps the trial sequence / tone
identity / PRNG where they live today (firmware) and, critically, stays valid if
the trial sequence later migrates to the host (a deferred, separate task): the
host would then resolve identity but the wire command is unchanged, so no further
`PROTO_VERSION` bump is needed.

## Alternatives considered

- **Explicit `TONE_ON,<hz>` / `TONE_OFF`** — host picks the frequency. Rejected
  for now: it forces tone-identity/sequence logic onto the host before that
  migration is scoped, and duplicates `cueFor()` there. The semantic command
  keeps identity in one place and is forward-compatible with the migration.
- **Keep the firmware beam-driven auto-tone and add nothing** — rejected: a beam
  is an event, not a state; a beam-only machine can't tell commitment from
  reversal until a downstream beam, which is too late for a spatially-consistent
  onset (see FW-0014).
- **Firmware self-gates the tone on beam proximity** — impossible: the Due has no
  position; proximity is a host-only predicate.
- **A per-frame host "in-window" flag streamed to firmware** — rejected: puts a
  soft-real-time position signal on the serial link every frame; ON/OFF edges are
  enough.
- **New `MODE,FULL_VISION`** — rejected as heavier than a `CUE,VISION|BEAM`
  runtime flag; the trial FSM is identical, only the tone source differs.

## Consequences

- One DAC authority at a time: under `CUE,VISION` the firmware never auto-starts
  the tone, so host and firmware can't both drive the DAC. `ABORT`/timeout still
  force the tone off (safety), and a mode switch now silences a playing tone.
- The choice cue's identity is still resolved on the Due from the current trial's
  `correctArm`, which is set on the `B_ENTRY` beam. Because ENTRY is low on the
  stem, it is crossed before vision reaches the choice window, so `correctArm` is
  set in time. This is a latent race that the deferred sequence-to-host migration
  removes; documented in `docs/future-work.md`.
- `CUE_ON`/`CUE_OFF`/`CUE,*` are session-independent (work in any mode, like
  `R<port>`), so the tone can be exercised from Free Roam for bring-up. In
  Free Roam `correctArm` is -1 and `cueFor()` falls back to the low tone.
- A protocol change now touches `PROTOCOL.md`, `rig_config.h` (`PROTO_VERSION`),
  and `host/protocol.py` + its tests; the Due must be reflashed.

## Evidence / links

- `firmware/PROTOCOL.md` (v5) — the wire contract this implements.
- ADR-0008 (wavetable tone ISR), ADR-0042/0043 (fast view + actuation thread the
  host cue path attaches to), ADR-0025 (maze state), ADR-0024 (host parser).
- `docs/future-work.md` FW-0014 (vision cue path) and FW-0008 (Phase-3 cue).
- `firmware/cue-guided-task/test/test_task_core.cpp` — covers the `CueRunning`
  suppression under `cueSourceVision`.
