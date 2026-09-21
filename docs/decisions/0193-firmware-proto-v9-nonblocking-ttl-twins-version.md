# ADR 0193: Firmware protocol v9 — non-blocking serial, TTL serial twins, and a VERSION command (Reflash A)

- **Status:** Accepted
- **Date:** 2026-09-21
- **Author / Agent:** Agent (Cursor) on behalf of the operator
- **Affects:** `firmware/cue-guided-task/cue-guided-task.ino`, `firmware/common/rig_config.h`, `firmware/PROTOCOL.md`; host `core/protocol.py`, `ui/session_controller.py`, `core/provenance.py` (host half, same reflash)
- **Supersedes / Superseded by:** — (builds on ADR-0164/0183/0184/0165/0166/0046/0044)

## Context

Three review findings all point at the firmware↔host serial link, and all need a
reflash, so they land together as "Reflash A" (the polarity fix ADR-0164 already
shipped in v8; this is the software-only follow-up that was deferred to the next
flash).

- **R2 (robustness).** ADR-0183 gave the *broker* per-client outbound queues so a
  slow TCP client can't block the serial reader. But the firmware side of the same
  chain is still exposed: if the reader ever stalls, the Due's 128 B UART TX ring
  fills and a plain `CONSOLE.print` **blocks inside `readInputs()`**, stalling
  `loop()` — which is what services valves and Open Ephys TTL pulse-ends. A logging
  call must never be able to delay a physical valve or a TTL edge.
- **S7 (sync).** The choice / return-arm / tone TTLs are the hardware marks Open
  Ephys records, but they have **no firmware-clock twin in the serial log** (the
  choice/return TTLs have none at all; the tone TTLs only have the behavioural
  `CUE` line, and only for host-driven cues). Offline alignment therefore has to
  *infer* a TTL's onset by matching it back through beam edges and the (now
  corrected, but still debounced) `S`/`L` polarity — brittle. Two smaller doc
  bugs live here too: `PROTOCOL.md` claimed "the tone TTLs always follow
  `CUE_ON`/`CUE_OFF`" (false under `CUE,BEAM` auto-tone), and it never stated the
  5 ms debounce + one-loop lag on `S`/`L` lines.
- **P1/P6 (provenance).** A bundle records the firmware *protocol* version (the
  boot `0,PROTO,<n>` line) but not *which build* produced it. There is no way to
  ask the running firmware to identify itself.

## Decision

Bump `PROTO_VERSION` 8 → 9. All changes are **append-only / behavioural** — no
existing field changes meaning, so a type-dispatching parser is unaffected.

1. **Non-blocking hot-path writes + drop counter (R2).** A new
   `writeLineOrDrop(buf, cap, n)` writes a whole `snprintf`-built line only if it
   fits `CONSOLE.availableForWrite()` right now; on a shortfall it drops the
   **entire** line (never a half-line) and does `txDropped++`. `printSL`,
   `printLog`, `logTTL`, and the `CUE`/`SOL`/`REWARD`/`VERSION` emitters all route
   through it. A `STATUS` query now also emits a trailing `<ms>,TXDROP,<n>` line
   (kept a *separate shell line* rather than a new field inside the pure core's
   `STATUS` effect, so `task_core.h` and its unit tests are untouched).
   `updateValves()` + the four `updateTTLPulse(...)` also run at the **top** of
   `loop()` (before `readInputs`), so actuation is serviced before any logging.
   The boot / handshake / `SEQ`-dump lines deliberately **stay blocking**: they run
   at `setup()` with an empty buffer and are provenance we must not drop.

2. **TTL serial twins (S7).** Every TTL pulse start emits
   `<ms>,TTL,<CHOICE|RETURN|TONE_ON|TONE_OFF>,1` on its **rising edge**, using the
   *same* `millis()` that starts the pulse (a "shared `t`"), so the line's `<ms>`
   equals the TTL onset. Only the rising edge is logged (the ephys-relevant mark;
   the width is `TTL_PULSE_MS`). Alignment becomes "match `TTL` lines to TTL
   pulses". `PROTOCOL.md` gets the corrected tone-TTL wording and the `S`/`L`
   debounce-lag note.

3. **`VERSION` command (P1/P6).** `VERSION` → `<ms>,VERSION,<proto>,<fw_git_sha>,
   <build>`. The Arduino GUI build can't inject a git SHA, so `FW_GIT_SHA` is a
   macro in `rig_config.h` (default `"unknown"`; paste the short SHA before
   flashing when it matters) and the compile timestamp `__DATE__ " " __TIME__` is
   appended automatically, so a bundle always records *something*. The host sends
   `VERSION` at session start (after `PING`) and records the reply in the manifest
   provenance.

The unused firmware **Full-task FSM** gains a `DEPRECATED` comment at
`startFullTask()` (removal tracked in FW-0068).

## Alternatives considered

- **Keep blocking prints, rely only on the broker queues (ADR-0183).** Rejected:
  the broker fix stops *one* stuck client from stalling the reader, but any reader
  stall (or a genuinely saturated USB) still back-pressures into the firmware; the
  loop must be robust on its own. Dropping a log line is always preferable to
  delaying a valve.
- **Append `tx_dropped` inside the `STATUS` line.** Rejected: `STATUS` is built by
  the pure core (`task_core.h`), which doesn't know the shell's `txDropped`; a
  separate `TXDROP` shell line keeps the core pure and its tests intact and is
  equally watchable by the broker/host.
- **Also emit TTL falling-edge (`,0`) lines.** Rejected: doubles serial load for
  no alignment gain — the rising edge is the mark; the width is a known constant.
- **Compile-time git SHA via a build script.** Rejected: the rig builds through
  the Arduino GUI (ADR-0006 note), which has no injection hook; a pasted macro +
  automatic compile stamp is the low-friction honest option.
- **Guard *every* console write, including boot/`SEQ` lines.** Rejected: those run
  at setup with an empty buffer and are one-shot provenance; dropping them would
  lose the handshake/sequence for no timing benefit.

## Consequences

- Under a saturated link some `S`/`L`/event lines may be **absent by design**;
  `TXDROP` quantifies it and physical timing is preserved. Analysis must treat the
  serial log as best-effort under load (the full-capture bundle, ADR-0178, already
  records receipt gaps).
- Every ephys TTL now has a firmware-clock twin, simplifying the offline
  `clock_fit` / cross-stream sync work (the LED-fit path, ADR-0153/0188) — a second
  independent anchor beyond the LED barcode.
- `snprintf` line building adds a small per-line CPU cost vs streaming `print`s;
  negligible against the 80 kHz DAC ISR headroom.
- **Requires an on-rig re-verify after flashing** (every beam/lick/cue + a smoke
  session): confirm `S`/`L`/`CUE` still stream, `VERSION`/`TTL`/`TXDROP` land, and
  valves/TTLs still fire on time. The host half (send/capture `VERSION`, decode
  `TTL`/`TXDROP`) ships in the paired commit.

## Evidence / links

- Plan: `sensor_polarity_hand_check` roadmap, cluster 5 (5b/5f) + 3d `VERSION` +
  7e "Reflash A"; findings R2/S7/P1/P6.
- Builds on ADR-0183 (broker queues, host half of R2), ADR-0164 (polarity/v8),
  ADR-0165/0166 (`*_fw_ms` twins), ADR-0180 (provenance block), ADR-0046/0044
  (host-owned cue + TTLs).
