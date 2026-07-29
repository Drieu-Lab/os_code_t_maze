# ADR 0065: Run the Due's console on the Programming port

- **Status:** Accepted
- **Date:** 2026-07-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/cue-guided-task.ino` (`DUE_USE_NATIVE_USB`),
  `host/rig_broker.py` (`DEFAULT_PORT`), `host/start_rig.bat` (`RIG_PORT`),
  `PROGRAMS.md`, `host/README.md`, `docs/glossary.md`
- **Supersedes / Superseded by:** — (the port name in ADR-0001 is now `COM3`; that
  ADR's decision, the broker + TCP fan-out, is untouched)

## Context

Since 2026-07-27 the rig has been unable to hold a serial link for a full
session. The Due stops talking mid-run, the manual feed and speaker buttons go
dead, and every reopen fails with `PermissionError(13, 'A device attached to the
system is not functioning.', None, 31)` until somebody presses RESET on the
board. Sessions need 30-60 minutes; the link lasted about 17.

Eight instrumented runs (`docs/notes/Bugs.md` `#260727_SerialLockout`) narrowed
it to one number. Death comes **~1030 s after the host opens the port**, and the
countdown is rearmed by every open:

| Run | Port opened | Firmware clock at death | Seconds since open |
| --- | --- | --- | --- |
| 6 | 10:50:11 | 1,030,039 ms | ~1030 |
| 7 | 11:35:23 | 1,036,321 ms | ~1030 |
| 8 | 12:04:21 (a reopen) | 1,462,023 ms | ~1030 |

Excluded along the way: board uptime and the 2^20 ms boundary (run 6 sailed past
it and died 4.7 minutes later), the firmware session clock (three unrelated
values above), message count (~103 vs ~515 exchanges, same deadline), the
always-on 80 kHz tone timer (ADR-0063), rig load of every kind (an idle board
with one `STATUS` every 10 s is enough), USB contention with the camera (it is
GigE), the USB 3 hub's "turn off this device to save power" setting, and a bug
inside the broker process (after a wedge a *fresh* process still cannot open the
port until RESET, so the device itself is in the bad state).

That left two suspects, both at the port-open boundary: the SAM3X8E's own USB
device stack, or the Windows driver instance created by the open. The Due's two
USB sockets separate them, because they share only the host half of the path. The
Native port is the SAM3X being its own USB device. The Programming port carries
the same bytes out of the SAM3X's UART into a separate ATmega16U2, which does the
USB talking - different silicon, different firmware, different endpoints, but the
same `usbser.sys`, hub and cable.

Experiment E6 ran the unchanged rig firmware with its console on the Programming
port: **37 minutes, ~1130 probes answered one-for-one, zero failures** - more than
twice the deadline that killed every native-port run.

## Decision

Run the firmware's console on the **Programming port**, and move the host
defaults with it.

- `DUE_USE_NATIVE_USB 0` in `cue-guided-task.ino`, so `CONSOLE` is `Serial`
  (SAM3X UART0 -> ATmega16U2) instead of `SerialUSB`.
- `DEFAULT_PORT = "COM3"` in `rig_broker.py` and the same default for `RIG_PORT`
  in `start_rig.bat`.
- Uploads use the **Arduino Due (Programming Port)** board entry, FQBN
  `arduino:sam:arduino_due_x_dbg`.

This is a workaround with a known cost, not a root-cause fix. The lockout stays
open in `docs/notes/Bugs.md`; what changes is that experiments can run while it
is being chased.

## Alternatives considered

- **Stay on the Native port and recover automatically.** Rejected: there is
  nothing to recover with. Error 31 outlives the process, so reopening cannot
  help, and only a physical RESET clears it. ADR-0062's watchdog makes the
  failure loud and immediate, which is worth having, but it cannot keep a session
  alive.
- **Close and reopen the Native port every ~15 minutes, before the deadline.**
  The countdown does rearm per open, so this might work, but it drops data for
  the ~2 s settle on each cycle, and one missed window wedges the board
  mid-session with an animal in the maze. Untested, and fragile in exactly the
  situation it exists to protect.
- **Hunt the root cause first, keep the rig down.** Rejected as sequencing, not
  as work: the hunt continues either way, and this change is a one-line compile
  flag that is trivial to revert if the answer arrives tomorrow.
- **Try a USB 2 port on the motherboard, or another PC.** Still cheap and still
  worth doing to characterize the fault, but E6 now points at the device side, so
  neither is likely to restore a working link today.

## Consequences

- **Opening the port reboots the board.** The ATmega16U2 pulls the SAM3X's reset
  line when the host asserts DTR - the mechanism that makes uploads work - so
  every broker start now begins a fresh firmware session with a freshly seeded
  trial sequence. Confirmed on the bench: the session clock read 3791 ms shortly
  after an open that followed a 30 s-old upload. Do not restart the broker
  mid-session expecting to rejoin one. `open_serial`'s existing 2 s settle sleep
  already covers the reboot. The Native port behaved the opposite way (a reopen
  rejoined a session that had been running for 433 s), so this is a real change
  in operating procedure.
- **The board no longer waits for a host.** `while (!SerialUSB)` is compiled out,
  so `setup()` finishes and a Free Roam session starts at boot, with output going
  into a UART nobody is reading. Lines emitted before the broker attaches (the
  boot banner, `SEED`, the trial-sequence dump) are lost rather than buffered. In
  practice the open resets the board anyway, so those lines are re-emitted and
  land in the settle window - where `reset_input_buffer()` may still discard
  them. Anything that must be seen should be requested, not assumed.
- **115200 baud is now real.** The Native port ignored it and ran at USB speed;
  UART0 does not, giving ~11.5 kB/s. The rig's steady-state line rate is far
  below that, but `Serial.write` blocks once the 128-byte TX ring fills, so a
  large burst (the trial-sequence dump is the biggest) can stall `loop()` for a
  fraction of a second. That burst happens at session start, before any solenoid
  pulse is pending, so it costs nothing today - but a future high-rate log line
  would need a `availableForWrite()` guard.
- **`COM4` disappears.** `SerialUSB.begin()` is never called, so the Native CDC
  device stops enumerating entirely. Anything still pointing at COM4 will fail to
  open rather than fail mysteriously.
- **The watchdog still earns its keep.** ADR-0062's bounded writes and liveness
  probe and ADR-0064's clock logging are port-agnostic; they now guard the UART
  path, and they are how we will notice if this port has a deadline of its own.
- **Revisit if:** the Programming port ever wedges, throughput approaches the
  UART ceiling, or the native-stack root cause is found and fixed (in which case
  flip the flag back and keep everything else).

## Evidence / links

- E6 soak (2026-07-29): uploaded with the IDE's bundled `arduino-cli`
  (`compile/upload --fqbn arduino:sam:arduino_due_x_dbg -p COM3`), then
  `python rig_broker.py --port COM3 --probe-quiet 2 --probe-interval 2
  --probe-dead 8`. Opened 12:36:05, `firmware session clock at connect: 3791 ms`,
  ~5 probes answered per 10 s with no gap, no `write failed`, no
  `DUE NOT RESPONDING`, still healthy at 13:13:53.
- Runs 6-8 and the exclusions above: `docs/notes/Bugs.md` `#260727_SerialLockout`
  occurrences 6, 7 and 8.
- While the board sat wedged at 12:31, the Native port was **absent** from the
  device tree and a phantom `Unknown USB Device (Device Descriptor Request
  Failed)` sat at its hub location (`USB\VID_0000&PID_0002\5&30741CDD&0&9`, the
  location shared by `Arduino Due (COM4)`), which is what error 31 looks like
  from the driver's side. Windows reports every absent device as "Unknown", so
  that node cannot be dated with confidence.
- The console has been a compile-time switch since the sketch was written
  (`DUE_USE_NATIVE_USB`, mirroring `firmware/tests/tmaze_test`), so this change
  is one digit plus the host defaults.
- ADR-0001 (broker + TCP fan-out), ADR-0062 (bounded writes + liveness probe),
  ADR-0063 (gated tone timer), ADR-0064 (firmware clock in the broker log).
