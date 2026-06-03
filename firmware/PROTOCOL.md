# Rig serial protocol v2

Both firmware sketches (`cue-guided-task`, `tmaze_test`) and the host tools
(`maze_gui.py`, `lick_tuner.py`) speak this protocol. It is strict v2: there is
no backward/dual-format support, so all devices and GUIs must be on v2 together.

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

Emitted once, in order, before the session begins:

| Line | Meaning |
|------|---------|
| `0,PROTO,2` | protocol version (assert this on the client) |
| `0,ANIMAL,<id>` | configured animal id |
| `0,SEED,<v>` | PRNG seed used to build the trial sequence (session is reproducible from it) |
| `0,SEQ_START,<total>` | trial-sequence dump begins; `<total>` trials follow |
| `0,SEQ,<n>,LEFT\|RIGHT` | planned correct side for trial `<n>` (1-based) |
| `0,SEQ_END` | trial-sequence dump ends |

## Events emitted by cue-guided-task

| TYPE | Payload | Meaning |
|------|---------|---------|
| `TRIAL` | `<index>,LEFT\|RIGHT` | trial `<index>` (1-based) started; correct side |
| `S` | `<i>,<state>` | body-beam `i` edge; `state` 1=broken, 0=clear |
| `L` | `<i>,<state>` | lick `i` edge; `state` 1=active, 0=idle |
| `CHOICE` | `LEFT\|RIGHT` | arm chosen |
| `OUTCOME` | `CORRECT\|INCORRECT` | choice correctness |
| `REWARD` | `LEFT\|RIGHT\|START` | reward valve pulsed |
| `RETURN_ARM` | `ENTRY` | return-arm beam entered; trial advances |
| `STATUS` | `<count>,<index>,<active>` | reply to `STATUS` command |
| `PONG` | (none) | reply to `PING` |
| `SESSION_COMPLETE` | (none) | session ended (max trials, timeout, or abort) |
| `FX_OVERFLOW` | (none) | diagnostic: per-tick effect buffer saturated (should never occur) |

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
| `R<port>` (`R0`/`R1`/`R2`) | cue-guided-task | manual valve flush (port 0=left,1=right,2=start) |
| `R<port><sol>` | tmaze_test | pulse solenoid `[port][sol]` (cue-guided-task ignores the `<sol>` digit) |
| `PING` | cue-guided-task | -> `PONG` |
| `STATUS` | cue-guided-task | -> `STATUS,<count>,<index>,<active>` |

## Notes for analysis

- The `SEED` line plus the threaded PRNG make a whole session's trial sequence
  reconstructable offline.
- `read_csv` will not work directly (rows are ragged); parse line-by-line,
  dispatch on the `TYPE` token, then apply the per-type layout above.
