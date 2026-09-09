# Rig serial protocol v7

Both firmware sketches (`cue-guided-task`, `tmaze_test`) and the host tools
(`maze_gui.py`, `lick_tuner.py`, `rig_logger.py`) speak this protocol. v7 adds a
per-cue playback volume: `CUE_ON,<CHOICE|RETURN>,<hz>` gains an optional trailing
percent, `CUE_ON,<CHOICE|RETURN>,<hz>,<gain>` (0-100 % of full DAC amplitude,
clamped to `[CUE_GAIN_PCT_MIN, CUE_GAIN_PCT_MAX]`; absent == 100 == the v6 level,
so it is append-only and backward compatible). The firmware scales the tone in
its wavetable ISR and echoes the applied gain on the `CUE,ON` line. This lets the
host equalize the HIGH and LOW cues on a speaker whose response is not flat -
attenuating the louder cue - without touching tone identity or timing (see
docs/decisions/0125).

v6 lets
the HOST own the cue tone identity: `CUE_ON,<CHOICE|RETURN>` gains an optional
trailing frequency, `CUE_ON,<CHOICE|RETURN>,<hz>`. Without `<hz>` the firmware
resolves identity as in v5; with it the host supplies the tone directly (clamped
to `[CUE_HZ_MIN, CUE_HZ_MAX]`). This is used by the host-owned Phase 3, where the
firmware trial FSM is off (Free Roam) so `correctArm` is unset and the host is
the sole task authority. v6 also fires the choice / return-arm TTLs autonomously
on their beam edges **in Free Roam**, so those Open Ephys event marks are
preserved while the FSM (which fires them in Full task) is inactive (see
docs/decisions/0046).

v5 adds the host-driven positional audio cue: the commands `CUE_ON,<CHOICE|RETURN>`,
`CUE_OFF`, and the cue-source flag `CUE,VISION` / `CUE,BEAM`, plus a firmware
`CUE` log line (`CUE,ON,<type>,<freq>` / `CUE,OFF`). The HOST decides tone
ON/OFF from the rat's tracked position; the firmware synthesizes the tone and
fires the `TONE_ON`/`TONE_OFF` TTLs. Under `CUE,VISION` the firmware suppresses
its beam-driven `CueRunning` auto-tone so only one thing drives the DAC; `BEAM`
(the default) keeps the v4 behavior (see docs/decisions/0044).

v4 adds a trailing pulse-duration field (`<dur_ms>`) to the `cue-guided-task`
reward/solenoid lines (`REWARD,CENTER,<dur_ms>`, `REWARD,<port>,MANUAL,<dur_ms>`,
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
| `0,PROTO,7` | protocol version (assert this on the client) |
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
| `S` | `<i>,<state>` | body-beam `i` edge; `state` **0 = beam broken (rat crossing), 1 = beam clear (resting)**. Measured on recorded logs 2026-09-08: the `1` spans run for tens of seconds, the `0` spans 50–400 ms, and the board's channel LED goes dark during `0` (`docs/design/cross-stream-sync-design.md` §3.7). Earlier revisions of this row said the opposite; the firmware source comment and the host consumers of the flag are being reconciled under `docs/future-work.md` FW-0058. |
| `L` | `<i>,<state>` | lick `i` edge; `state` **0 = tongue contact (lick), 1 = idle**. Same inversion and same evidence as `S`: over a full session `1` runs unbroken for minutes while `0` never exceeds 0.3 s (median 92 ms); the lick LED lights during `0`. See FW-0058. |
| `MODE` | `FREEROAM\|FULLTASK` | training mode entered (at boot or on a `MODE` command) |
| `CHOICE` | `LEFT\|RIGHT` | arm chosen |
| `OUTCOME` | `CORRECT\|INCORRECT` | choice correctness |
| `REWARD` | `CENTER,<dur_ms>` | automatic reward valve pulsed on a correct choice (central port; see ADR-0007). `<dur_ms>` is the valve-open time (`REWARD_PULSE_MS`); the host maps it to a calibrated dispensed volume (see ADR-0019). |
| `REWARD` | `<port>,MANUAL,<dur_ms>` | manual reward-spout fire (`R<port>` / `R<port>1`): the experimenter delivered reward at `port` (0=left,1=right,2=center) for `<dur_ms>` ms. The Free Roam reward path; emitted alongside the `SOL` ack. |
| `SOL` | `<port>,<sol>,<dur_ms>` | a solenoid was pulsed for `<dur_ms>` ms: a manual `R<port><sol>` fire, or the automatic well-clean (`SOL,2,4,<rinse_ms>` rinse then `SOL,2,3,<drain_ms>` drain) at trial start. Drives the Rig-view marker flash. |
| `RETURN_ARM` | `ENTRY` | return-arm beam entered; trial advances |
| `CUE` | `ON,<CHOICE\|RETURN>,<freq_hz>[,<gain_pct>]` | a host-driven cue tone started (v5): `<type>` is the cue, `<freq_hz>` the tone actually played (CHOICE = the trial's `correctArm` mapping; RETURN = a random hi/lo). v7 appends `<gain_pct>`, the volume applied (0-100 % of full amplitude; 100 when none was sent). Emitted only on a real onset, not a no-op. The `TONE_ON` TTL marks the acoustic onset. A parser that only reads the first three fields is unaffected by the appended gain. see docs/decisions/0044, docs/decisions/0125 |
| `CUE` | `OFF` | a host-driven cue tone stopped (v5). Emitted only when a tone was actually playing. The `TONE_OFF` TTL marks the acoustic offset. |
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
signals, not serial lines. In Full task the choice / return-arm TTLs fire from
the trial FSM; in Free Roam (v6) they fire autonomously on the choice / return
beam edges so the host-owned Phase 3 keeps those event marks while the FSM is
off (see docs/decisions/0046). The tone TTLs always follow `CUE_ON`/`CUE_OFF`.

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
| `PING` | cue-guided-task | -> `PONG`. Answered in ANY session state, including after a session has completed, so the broker's liveness probe keeps working post-session (see docs/decisions/0123). |
| `STATUS` | cue-guided-task | -> `STATUS,<count>,<index>,<active>`. Answered in any session state; after completion `<active>` reports `0` (see docs/decisions/0123). |
| `CUE_ON,CHOICE[,<hz>[,<gain>]]` / `CUE_ON,RETURN[,<hz>[,<gain>]]` | cue-guided-task | start the host-driven cue tone. Without `<hz>` (v5) the frequency is chosen in firmware: `CHOICE` from the current trial's `correctArm` (`cueFor`), `RETURN` a random hi/lo from the session PRNG. With an explicit `<hz>` (v6) the host supplies identity directly, clamped to `[CUE_HZ_MIN, CUE_HZ_MAX]` - used by the host-owned Phase 3 (Free Roam, no firmware trial), see docs/decisions/0046. A trailing `<gain>` (v7) sets the playback volume as a percent 0-100 of full amplitude (clamped to `[CUE_GAIN_PCT_MIN, CUE_GAIN_PCT_MAX]`; absent == 100), applied by scaling the wavetable in the ISR - used to equalize HIGH/LOW on a non-flat speaker, see docs/decisions/0125. Session-independent (works in any mode; in Free Roam `CHOICE` without `<hz>` falls back to the low tone). Fires the `TONE_ON` TTL and emits `CUE,ON,<type>,<freq>,<gain>` (the tone actually played). No-op if a tone is already playing. see docs/decisions/0044 |
| `CUE_OFF` | cue-guided-task | stop the host-driven cue tone (v5). Fires the `TONE_OFF` TTL and emits `CUE,OFF`. No-op if no tone is playing. |
| `CUE,VISION` / `CUE,BEAM` | cue-guided-task | select the cue source (v5). `VISION`: the host drives the tone via `CUE_ON`/`CUE_OFF` and the firmware suppresses its `CueRunning` beam auto-tone (single DAC authority). `BEAM` (default): the v4 beam-driven auto-tone. All other trial FSM / scoring / reward / TTL behavior is unchanged. |

## Notes for analysis

- The `SEED` line plus the threaded PRNG make a Full task session's trial
  sequence reconstructable offline.
- `MODE` lines segment a recording into sessions; the firmware `ms` clock resets
  at each one. `rig_logger.py` prepends a host wall-clock column to every line,
  which is what anchors events on an absolute timeline across mode switches.
- `read_csv` will not work directly (rows are ragged); parse line-by-line,
  dispatch on the `TYPE` token, then apply the per-type layout above.
