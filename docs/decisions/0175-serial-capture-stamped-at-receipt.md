# ADR 0175: Stamp the per-session serial capture at line RECEIPT, not at Tk write time

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/io/rig_io.py` (`RigClient.on_line_stamped`, `_dispatch_line`, reader loop), `host/tmaze/core/protocol.py` (`LineInfo.recv_wall`/`recv_mono`), `host/tmaze/ui/experiment_control.py` (fan-out), `host/tmaze/ui/session_controller.py` (`_write_serial`, serial header), `host/tests/test_rig_io.py`, `host/tests/test_session_phase3_wiring.py`
- **Supersedes / Superseded by:** —

## Context

The per-session filtered serial capture (`serial/sensors_<id>.csv`, ADR-0049/0124)
records every body-beam / central-port line the session sees, one row of
`wall_clock_iso,firmware_line`. That host wall-clock column is the ONLY host-time
anchor on those raw firmware events, so offline work uses it to place beams and
licks on the host timeline (and, via the LED clock-fit, on the neural timeline).

But the stamp was `datetime.now()` taken **inside the Tk callback that writes the
row** - i.e. after the line had already made the hop from the reader thread to the
Tk main thread via `root.after`. Every consumer of a serial line goes through one
fan-out point (`experiment_control.on_line` -> `router.dispatch`, the single-parse
seam of ADR-0056/FW-0021), which runs on the Tk thread. Under GUI load (a redraw,
a modal, a slow frame) that hop can be delayed by tens of milliseconds and, worse,
by a VARIABLE amount - so the recorded stamp carried the GUI's scheduling jitter,
not when the bytes actually reached the host. This is review finding S3: the serial
host-clock column should mark receipt, the tightest host-side instant we can get,
not an incidental write time downstream of a busy event loop.

The reader thread already sits right at the socket; it knows the receipt instant
exactly. Nothing was carrying that instant across the Tk hop.

## Decision

Capture the receipt stamp on the reader thread and thread it through the existing
single-parse fan-out to the serial writer.

- **`RigClient`** takes the stamp the instant `sock.recv()` returns
  (`recv_wall = time.time()`, `recv_mono = time.monotonic()`); every line carved
  out of that chunk shares it (they arrived together), and a line split across
  chunks is stamped when its terminating newline arrives. A new
  `on_line_stamped(cb)` callback fires `cb(recv_wall, recv_mono, line)` on the Tk
  thread and TAKES PRECEDENCE over the plain `on_line`. `on_line` stays the
  fallback so the other clients (`rig_logger`, `lick_tuner`, `maze_gui`,
  `reward_calibrate`, ...) are untouched.
- **`LineInfo`** (protocol.py) gains two defaulted optional fields `recv_wall` /
  `recv_mono`. `classify()` never sets them; the fan-out attaches them with
  `dataclasses.replace` after classify, so the one parsed line carries its own
  receipt stamps to every module.
- **`experiment_control`** registers the stamped callback and does the
  `replace`; nothing else in the fan-out changes.
- **`session_controller._write_serial`** uses `info.recv_wall` verbatim
  (`datetime.fromtimestamp(recv_wall)`), falling back to `datetime.now()` only
  when a line carried no stamp (e.g. one re-classified on another thread). The
  serial-file header comment now states the column is receipt time.

`recv_mono` (a monotonic partner to the wall stamp) is captured and carried now
even though the serial writer only needs the wall value - it is the jitter-free
clock a later host<->firmware fit would prefer, and adding it here is free.

## Alternatives considered

- **Leave `datetime.now()` at write time** — the status quo; records GUI
  scheduling jitter as if it were event time, the exact defect S3 names.
- **Stamp per-line inside the split loop instead of once per `recv`** — the lines
  in one chunk genuinely arrived together at the socket; a per-line `time.time()`
  in the tight decode loop would differ only by loop overhead and imply a
  precision we don't have. One stamp per `recv` is the honest receipt instant.
- **A new dedicated stamped stream / second socket** — the fan-out already hands
  every consumer one parsed line; piggy-backing two optional fields on `LineInfo`
  reuses that seam with no new plumbing and no second parse.
- **Bump a format/proto version** — nothing about the bundle's column set or the
  wire protocol changed; only WHICH host instant fills an existing column. The
  header comment documents the meaning; no version bump.

## Consequences

- The serial host-clock column now reflects when the host received each line, to
  reader-thread resolution, independent of Tk load - a better anchor for the
  offline host/neural alignment.
- `on_line`-only clients are unaffected (the plain path is unchanged); only
  `experiment_control` opts into the stamped callback.
- The stamp is still a HOST clock and still carries serial-transport latency from
  the firmware `millis()` (which the ADR-0165/0166 `*_fw_ms` twins and the LED
  clock-fit address); this change removes only the added, variable Tk-hop jitter,
  it does not make the serial column a hardware clock.
- Pairs with the burst-batching seam (its own ADR): with one Tk callback per
  `recv` burst, all lines in a burst still carry the burst's single receipt stamp,
  so batching does not blur the stamp.

## Evidence / links

- Review finding S3 (serial host stamp should be receipt time).
- `docs/decisions/0056` (single-parse fan-out), `0049`/`0124` (the serial
  capture), `0165`/`0166` (firmware-clock twins), `0173` (truthful send on the
  same seam).
- Tests: `host/tests/test_rig_io.py` (stamped dispatch + precedence),
  `host/tests/test_session_phase3_wiring.py`
  (`test_filtered_serial_stamps_line_at_receipt_not_write_time`,
  `test_filtered_serial_falls_back_to_now_without_a_receipt_stamp`).
