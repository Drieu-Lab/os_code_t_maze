# ADR 0062: Make a wedged Due loud — bound the broker's writes and probe a quiet port with STATUS

- **Status:** Accepted
- **Date:** 2026-07-27
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rig_broker.py` (`WRITE_TIMEOUT_S`, `LIVENESS_PROBE_CMD`,
  `reopen_request`, `_write_to_due`, `_check_liveness`, the `stats:` line), new
  `host/serial_liveness.py` + `host/test_serial_liveness.py`
- **Supersedes / Superseded by:** — (narrows ADR-0002's "verbatim passthrough":
  the broker now emits one line of its own, a periodic `STATUS` while quiet)

## Context

On 2026-07-27 the Arduino Due stopped talking to the host twice. The board stayed
enumerated in Device Manager with no error code, the Windows system log recorded
nothing, and a CPU RESET revived it without touching the cable — the signature of
a hung `loop()`, since the SAM3X services its USB CDC bulk endpoints from
`loop()`. The firmware cause is still open (see ADR-0063 for the first
suspect fixed).

What this ADR is about is that the **host did not notice**, twice, for a long
time. Two separate blind spots in `rig_broker.py`:

1. **The port was opened with a read timeout but no write timeout.** pyserial's
   default `write_timeout=None` blocks forever, and `_forward_to_due` held
   `serial_lock` across the write. In occurrence 1 one write failed at 13:45:14
   and the next blocked for **~17.5 minutes** — during which every client's
   commands were frozen and **not one log line** was printed. The backlog only
   drained at 14:02:49 when the operator pressed RESET.
2. **A wedged Due makes `readline()` return empty rather than raise**, and empty
   reads were treated as normal (`if not raw: continue`). So the broker kept
   printing healthy-looking `stats: ... from_due=0/10s` lines throughout. In
   occurrence 2 the board died somewhere inside a **19-minute window** (last good
   command 14:06:17, first failed one 14:25:35) and the log cannot narrow it
   further, which is exactly the data a firmware bisection needs.

A plain silence timeout cannot fix (2): the same log shows over five minutes of
**legitimate** quiet while perfectly healthy (13:35:29-13:40:39, all
`from_due=0/10s`), because a resting rat breaks no beams. Any threshold long
enough to avoid false alarms would be uselessly slow.

## Decision

Two changes, one purpose — a dead port becomes loud and timestamped within
seconds.

**Bound the write.** `WRITE_TIMEOUT_S = 1.0`, passed as `write_timeout` to
`serial.Serial`. A 32-byte command is ~2.8 ms of wire time at 115200 baud, so 1 s
is ~350x headroom: it cannot false-trip on a healthy port, yet a wedged one is
caught on the first command. On failure the writer logs and sets a module-level
`reopen_request` event; `serial_reader_thread` checks that event each pass and
does the close/reopen itself. The reader thread stays the **single owner** of the
port lifecycle — closing from the writer would re-enter `serial_lock`, which
`_close_serial()` also takes, and deadlock. All writes now go through one
`_write_to_due()`, so client commands and probes get identical treatment.

**Probe a quiet port.** After 10 s of silence the broker sends `STATUS`, repeats
every 5 s, and declares the port dead at 25 s of unbroken silence — three
unanswered probes, at 10, 15 and 20 s. One lost byte or a momentarily busy host
must not condemn a working port; 25 s is still two orders of magnitude better
than the 17.5 minutes it replaces. Death logs a loud
`DUE NOT RESPONDING: ...` line naming the RESET remedy, and sets the same
`reopen_request`. A `probes=` field appears in the periodic `stats:` line only
when non-zero, so the onset of trouble can be read straight off the broker log.

The timing decision lives in a new pure core, `host/serial_liveness.py`
(`LivenessParams` + `LivenessMonitor.reset/note_rx/step`), stdlib-only with time
**injected** as a `now` argument, per the pure-core-plus-thin-shell convention in
`CONTRIBUTING.md`. Its tests therefore walk a hand-written clock instead of
sleeping. The broker drives it from the reader thread, which already wakes ~10x a
second on the 0.1 s read timeout, so no new thread is needed.

**Probe with `STATUS`, not `PING`.** `PONG` stays reserved for the ADR-0048
session-start fiducial; periodic `PONG`s would blur that anchor by up to the probe
interval and degrade cross-stream alignment that is currently good to tens of
milliseconds. Verified before relying on it: `task_core.h` handles `Status`
*before* the `if (cfg.mode == Mode::FreeRoam) return n;` early exit, so it is
answered in the mode Phase 3 runs in; `protocol.parse` decodes the reply as an
ordinary `<ms>,STATUS,...` line that no per-type helper claims; and
`session_serial.is_sensor_or_central()` drops it, so probe traffic never pollutes
the per-session serial files (ADR-0049). The full `rig_logger.py` stream does
record it, which is wanted.

## Alternatives considered

- **Close and reopen the port directly from the failed write.** Rejected: the
  writer holds `serial_lock` and `_close_serial()` acquires it, so this
  deadlocks — the exact failure mode being fixed, in a new place.
- **A plain silence watchdog (declare death after N seconds of quiet).**
  Rejected on the rig's own data: >5 minutes of healthy silence is normal, so N
  would have to exceed that and detection would be as slow as the problem.
- **Probe with `PING`.** Rejected to protect the ADR-0048 fiducial (above).
- **A new dedicated watchdog thread.** Rejected as unnecessary: the reader thread
  already wakes ~10x/s, and adding a thread would mean sharing the port with yet
  another writer.
- **Auto-recovery (reset the board in software).** Not available: the only remote
  reset path on the Due's native port is the 1200-baud bootloader touch, which
  erases the sketch. So this ADR deliberately delivers **detection, not
  self-healing**.
- **Probing all the time, not only when quiet.** Rejected: it would add a
  needless line every 5 s to every log for no diagnostic gain.

## Consequences

- A wedged Due is now reported within ~25 s, with a timestamp, instead of
  silently ending a session. That timestamp is what makes the firmware
  bisection in the notes tractable.
- **Not self-healing.** Reopening a wedged Due fails with
  `PermissionError(13, 'A device attached to the system is not functioning.',
  None, 31)` — `SetCommState` needs a CDC `SET_LINE_CODING` reply the wedged
  stack never sends. The operator still presses RESET; the broker will retry the
  open every 2 s until they do.
- Neither change can make the lockup worse: both are host-side and only add a
  timeout and an occasional extra command.
- The broker is no longer *strictly* a verbatim passthrough in the host->Due
  direction (ADR-0002). One self-originated `STATUS` while quiet is the whole
  exception, and it is documented in the module docstring.
- The GUI is still blind to a wedge — readouts simply stop updating. Surfacing
  the broker's verdict in the GUI is left for later.
- Revisit if the rig ever legitimately goes >10 s quiet in a state where an extra
  `STATUS` would be harmful (none exists today: it is session-independent and
  changes no firmware state).

## Evidence / links

- Broker console, occurrence 1: last Due line `S,3,1` at 13:43:46.774 (firmware
  uptime 972401 ms), first `serial write failed forwarding 'CUE_ON,CHOICE,8000'
  ... Write timeout` at 13:45:14, ~20 queued commands draining as failures at
  14:02:49 after RESET.
- Broker console, occurrence 2 (idle rig, no tones since boot, nothing
  recording): good manual feed `SOL,2,1,150` at 14:06:17 (uptime 206420 ms),
  `serial write failed forwarding 'R21,150' ... Write timeout` at 14:25:35.
- Healthy-silence baseline: 13:35:29-13:40:39, every `stats:` line
  `from_due=0/10s`.
- Ruled out with evidence: stale process holding COM4 (error 31 comes from
  `SetCommState`; a busy port gives error 5), USB bus events (empty Windows
  system log), dead USB hardware (still enumerated, revived by CPU reset), USB
  contention with the camera (it is GigE, not USB), host CPU/disk load
  (occurrence 2 was idle with no traffic in either direction).
- `host/test_serial_liveness.py` — probe schedule, the 25 s dead threshold,
  traffic resets, and sticky-dead-until-reset.
- ADR-0001/0002 (broker ownership + passthrough), ADR-0048 (`PONG` fiducial),
  ADR-0049 (per-session serial capture), ADR-0063 (the tone-timer suspect),
  `docs/notes/Bugs.md` `#260727_SerialLockout`.
