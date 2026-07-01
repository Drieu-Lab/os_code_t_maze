# Rig serial protocol v4

Both firmware sketches (`cue-guided-task`, `tmaze_test`) and the host tools
(`maze_gui.py`, `lick_tuner.py`, `rig_logger.py`) speak this protocol. v4 adds a
trailing pulse-duration field (`<dur_ms>`) to the `cue-guided-task` reward/
solenoid lines (`REWARD,CENTER,<dur_ms>`, `REWARD,<port>,MANUAL,<dur_ms>`,
`SOL,<port>,<sol>,<dur_ms>`) so the host can map each pulse to a calibrated
dispensed volume (see docs/decisions/0019). The field is appended, so a
type-dispatching parser that ignores trailing fields it does not need is
unaffected; only consumers that want the volume read it.

v3 added training modes (the `MODE` command and the `0,MODE,<name>` line),
manual-reward logging (`REWARD,<port>,MANUAL`), and the end-of-session
`SESSION_SUMMARY` line (see docs/decisions/0009); plus full manual solenoid
control in `cue-guided-task` (`R<port><sol>[,<ms>]` -> `SOL` ack) and an
automatic central-well rinse/drain at trial start (docs/decisions/0010).

## Line format

Every line emitted by the firmware is newline-terminated ASCII:

```
<ms>,<TYPE>,<payload...>
```

- `<ms>` is an unsigned integer: milliseconds since the session clock started
  (startup/handshake lines use `0`).
- `<TYPE>` is an uppercase token matching `[A-Z_]+`.
- `<payload...>` is zero or more comma-separated fields whose meaning depends on
  `<TYPE>` (see tables below).

Every line validates against this regex:

```
^(\d+),([A-Z_]+)(,.*)?$
```

A parser dispatches on `<TYPE>` (field 1) and then applies the per-type field
layout. The format is intentionally not a single fixed-width table, but the
`ms,TYPE` prefix is uniform across all lines.

## Startup handshake (cue-guided-task)

Emitted at boot, in order, before the session begins:

| Line | Meaning |
|------|---------|
| `0,PROTO,4` | protocol version (assert this on the client) |
| `0,ANIMAL,<id>` | configured animal id |

The firmware then starts in its default **training mode** (Free Roam unless
`DEFAULT_MODE` is changed) and emits the per-mode session block below. The same
block is re-emitted whenever the mode is switched at runtime via `MODE,...`, so a
log can contain several of them.

**Free Roam** (no trials): just a mode marker.

| Line | Meaning |
|------|---------|
| `0,MODE,FREEROAM` | session is in Free Roam (no cues/trials; reward is manual) |

**Full task**: the trial sequence is generated, dumped, then the mode marker.

| Line | Meaning |
|------|---------|
| `0,SEED,<v>` | PRNG seed used to build the trial sequence (session is reproducible from it) |
| `0,SEQ_START,<total>` | trial-sequence dump begins; `<total>` trials follow |
| `0,SEQ,<n>,LEFT\|RIGHT` | planned correct side for trial `<n>` (1-based) |
| `0,SEQ_END` | trial-sequence dump ends |
| `0,MODE,FULLTASK` | session is in the Full cued task |

## Events emitted by cue-guided-task

| TYPE | Payload | Meaning |
|------|---------|---------|
| `TRIAL` | `<index>,LEFT\|RIGHT` | trial `<index>` (1-based) started; correct side |
| `S` | `<i>,<state>` | body-beam `i` edge; `state` 1=broken, 0=clear |
| `L` | `<i>,<state>` | lick `i` edge; `state` 1=active, 0=idle |
| `MODE` | `FREEROAM\|FULLTASK` | training mode entered (at boot or on a `MODE` command) |
| `CHOICE` | `LEFT\|RIGHT` | arm chosen |
| `OUTCOME` | `CORRECT\|INCORRECT` | choice correctness |
| `REWARD` | `CENTER,<dur_ms>` | automatic reward valve pulsed on a correct choice (central port; see ADR-0007). `<dur_ms>` is the valve-open time (`REWARD_PULSE_MS`); the host maps it to a calibrated dispensed volume (see ADR-0019). |
| `REWARD` | `<port>,MANUAL,<dur_ms>` | manual reward-spout fire (`R<port>` / `R<port>1`): the experimenter delivered reward at `port` (0=left,1=right,2=center) for `<dur_ms>` ms. The Free Roam reward path; emitted alongside the `SOL` ack. |
| `SOL` | `<port>,<sol>,<dur_ms>` | a solenoid was pulsed for `<dur_ms>` ms: a manual `R<port><sol>` fire, or the automatic well-clean (`SOL,2,4,<rinse_ms>` rinse then `SOL,2,3,<drain_ms>` drain) at trial start. Drives the Rig-view marker flash. |
| `RETURN_ARM` | `ENTRY` | return-arm beam entered; trial advances |
| `SESSION_SUMMARY` | `<trials>,<correct>,<incorrect>` | emitted before `SESSION_COMPLETE` for a Full task session |
| `STATUS` | `<count>,<index>,<active>` | reply to `STATUS` command |
| `PONG` | (none) | reply to `PING` |
| `SESSION_COMPLETE` | (none) | session ended (max trials, timeout, or abort) |
| `FX_OVERFLOW` | (none) | diagnostic: per-tick effect buffer saturated (should never occur) |

When `AUTO_DRAIN_ON_TRIAL` is set, every `TRIAL` after the first is immediately
followed by `SOL,2,4,<rinse_ms>` (rinse) and `SOL,2,3,<drain_ms>` (drain) on the
central well; the drain energizes after the rinse finishes (non-blocking).
see docs/decisions/0010

TTL pulses to Open Ephys (choice, return-arm, tone on/off) are hardware
signals, not serial lines.

## Events emitted by tmaze_test

| TYPE | Payload | Meaning |
|------|---------|---------|
| `PROTO` | `2` | protocol version |
| `READY` | (none) | setup finished |
| `S` | `<i>,<state>` | body-sensor edge |
| `L` | `<i>,<state>` | lick edge |
| `SOL` | `<port>,<sol>` | solenoid pulsed (ack of an `R` command) |

## Commands accepted (host -> firmware)

Commands are newline-terminated and are NOT part of the `ms,TYPE` line schema.

| Command | Target | Meaning |
|---------|--------|---------|
| `ABORT` / `STOP` | cue-guided-task | end the session now |
| `MODE,FREE` / `MODE,FULL` | cue-guided-task | switch training mode and (re)start the session (Free Roam / Full task) |
| `R<port><sol>[,<ms>]` | cue-guided-task, tmaze_test | pulse solenoid `[port][sol]` (port 0=left,1=right,2=center; sol 0-2 reward, 3 drain, 4 rinse); optional `<ms>` overrides `REWARD_PULSE_MS` (clamped <=5000). Both sketches ack with `SOL,<port>,<sol>` (`cue-guided-task` appends the pulse `<dur_ms>`: `SOL,<port>,<sol>,<dur_ms>`; `tmaze_test` does not). In `cue-guided-task` the pulse is non-blocking, works in any mode (independent of an active session), and a reward-spout fire (`<sol>` omitted or `=1`) also logs `REWARD,<port>,MANUAL,<dur_ms>`. A bare `R<port>` fires that port's reward spout. |
| `PING` | cue-guided-task | -> `PONG` |
| `STATUS` | cue-guided-task | -> `STATUS,<count>,<index>,<active>` |

## Notes for analysis

- The `SEED` line plus the threaded PRNG make a Full task session's trial
  sequence reconstructable offline.
- `MODE` lines segment a recording into sessions; the firmware `ms` clock resets
  at each one. `rig_logger.py` prepends a host wall-clock column to every line,
  which is what anchors events on an absolute timeline across mode switches.
- `read_csv` will not work directly (rows are ragged); parse line-by-line,
  dispatch on the `TYPE` token, then apply the per-type layout above.
