# ADR 0194: Reset the Due only on the broker's first serial open, not on reopens

- **Status:** Accepted
- **Date:** 2026-09-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/io/rig_broker.py` (`open_serial`, `serial_reader_thread`),
  `host/tests/test_rig_broker.py`
- **Supersedes / Superseded by:** refines the "opening the port reboots the board"
  consequence of ADR-0065 (that ADR's Programming-port decision is untouched)

## Context

The Due's console is on the USB Programming port (ADR-0065). Opening that port
asserts DTR, which the ATmega16U2 turns into a **reset of the SAM3X** - so *every*
time the broker opened the port it rebooted the board. At broker launch that is
exactly what we want: a clean, known state and a fresh `0,PROTO,9` handshake,
which is what sets the bundle's `sensor_polarity` (ADR-0164).

But the broker also reopens the port mid-life on its own recovery path (a write
timeout / dead-port verdict / cable hiccup; ADR-0065/0120). Under the old
behaviour that recovery *rebooted a session that was still running* - the
firmware `millis()` clock (the `*_fw_ms` twins, ADR-0165/0166) restarted at 0 and
the DAC ownership cleared, i.e. the exact failure Increment 5e now detects and
survives (ADR-0184). We would rather the reconnect not reboot the board at all.

A DTR-low open avoids the reset. Verified on the rig 2026-09-21: opening COM3 with
`dtr=False` produced **no boot handshake and the `millis()` clock kept counting**
(the board was left running). The only cost of adopting `dtr=False` blindly is
that we would also lose the launch handshake - and with it the honest
`sensor_polarity` at startup.

## Decision

**Reset on the FIRST open since the broker started; leave the board alone on every
reopen after that.** `open_serial(port, baud, reset=True)`:

- `reset=True` (the first open): open the normal DTR-asserting way and keep the
  existing 2 s settle, so the Due reboots and re-emits `0,PROTO,9`. Unchanged from
  before - so `sensor_polarity` stays honest at launch and needs no fallback.
- `reset=False` (every reopen): construct the port, set `dtr = False` **before**
  `open()` (the reset is triggered by the DTR transition at open time, so
  deasserting afterwards would be too late), and skip the settle (there is no
  reboot to wait out). A reconnect now rejoins the running board instead of
  rebooting it.

`serial_reader_thread` carries a `first_open` flag: the first successful open
passes `reset=True`, every open after it passes `reset=False`. A failed open
leaves the flag set, so we keep trying to reset until we succeed once.

## Alternatives considered

- **Adopt `dtr=False` on every open.** Rejected: it also removes the launch
  handshake, so `format.json`'s `sensor_polarity` would read `unknown` at startup
  and we'd need a fallback (declared proto, or an early `VERSION`) to keep it
  honest. The hybrid keeps the handshake for free.
- **Keep resetting on every open (status quo).** Rejected: a mid-session
  reconnect reboots a running session - the hazard 5e exists to paper over.
- **Send `VERSION` early and resolve proto from the reply instead of the
  handshake.** More wiring for no benefit here, since the first-open reset already
  yields the handshake.

## Consequences

- A mid-session USB hiccup that only drops the *data* link (board stays powered)
  now recovers **without** rebooting the Due - the session clock and DAC ownership
  survive. If the board genuinely lost power, it reboots on its own and 5e handles
  the resulting handshake.
- **Restarting the broker process** mid-experiment still resets the Due, because
  that new process's first open uses `reset=True`. This is identical to the old
  behaviour (and arguably desirable - you get a clean handshake), and it is
  5e-survivable, so it is not a regression. "Do not restart the broker mid-session
  expecting to rejoin" (ADR-0065) still holds for a *process* restart; it no longer
  holds for the broker's own in-process reconnect.
- Reconnects are also slightly faster (no 2 s settle on the no-reset path).
- The broker log now says which kind of open happened ("reset the Due" vs "no
  reset; left the running board alone").
- **Revisit if:** a DTR-low open ever fails to hold the line on this hardware
  (it was verified once), or a future board/driver resets regardless of DTR.

## Evidence / links

- On-rig `dtr=False` probe 2026-09-21: `serial.Serial()` with `s.dtr=False` then
  `s.open()` on COM3 - no `0,PROTO` line, and `readline()` returned mid-run frames
  (e.g. `b'565575,S,4,1\n'`), i.e. the board was not reset.
- `host/tests/test_rig_broker.py::OpenSerialResetTest` - `reset=True` leaves DTR
  asserted and settles 2 s; `reset=False` deasserts DTR before open and does not
  settle.
- ADR-0065 (console on the Programming port; the reset-on-open mechanism),
  ADR-0120 (reopen on write timeout), ADR-0184 (survive a mid-session firmware
  reset), ADR-0164 (`sensor_polarity` from the handshake proto).
