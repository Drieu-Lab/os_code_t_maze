# ADR 0183: Per-client outbound queues in the broker (no slow client can delay the Due)

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/io/rig_broker.py`, `host/tests/test_rig_broker.py` (new)
- **Supersedes / Superseded by:** Extends ADR-0001 (single-owner broker), ADR-0062 (bounded serial writes / liveness)

## Context

The broker fans every line the Due prints out to every connected TCP client
(ADR-0001). The old `broadcast` did the send **inline on the serial-reader thread**:
it walked the client list under `clients_lock` calling `sock.sendall(...)` on each.
`sendall` is blocking, so a single client that stops draining (a wedged GUI, a
paused debugger, a saturated link) stalled the whole fan-out — and with it a
concrete physical-timing hazard (review finding **R1**):

> stuck client → `sendall` blocks under `clients_lock` → the serial reader stops
> draining the port → the Due's 128-byte UART TX buffer fills → `CONSOLE.print`
> blocks inside `readInputs` → `loop()` stalls → `updateValves()` / TTL pulses fire
> late.

So one slow socket on the PC could, worst case, delay a reward valve or an ephys
TTL on the Arduino. The broker must never let a consumer back-pressure the reader.

## Decision

Give **each client its own bounded outbound queue drained by its own writer
thread**, and make `broadcast` *append-only*:

- A new `Client` wraps the socket with `out = queue.Queue(maxsize=MAX_QUEUED_LINES)`
  (2,000 lines — many seconds of buffer at the rig's line rate) and a daemon
  `_writer_loop` that does the blocking `sendall`. The blocking now happens on
  **that client's** thread, never the reader's.
- `broadcast(line)` holds `clients_lock` only to `put_nowait` onto each queue (a
  fast, non-blocking append). A client whose queue is **full** — i.e. it has fallen
  `MAX_QUEUED_LINES` behind — is collected and dropped *outside* the lock via
  `drop_client`, which removes it from the fan-out, closes the socket (unblocking any
  in-flight `sendall`), and logs the peer + backlog. `drop_client` is idempotent and
  safe to call from the reader (disconnect), the writer (send failure), or `broadcast`
  (overflow).
- The periodic stats line now reports `max_backlog=<n>/2000`, so a client falling
  behind is visible **before** it hits the drop threshold.
- `import serial` is made soft (the module owns the COM port in production but the
  TCP fan-out logic needs no serial), so the fan-out is unit-testable headlessly;
  `open_serial()` raises a clear error if pyserial is genuinely missing.

## Alternatives considered

- **Non-blocking sockets serviced by the reader (`send` + `EWOULDBLOCK`, keep a
  per-client backlog buffer).** — Equivalent effect, but re-implements flow control
  and partial-write bookkeeping by hand; a queue + writer thread per client is simpler
  to reason about and to test.
- **A single shared send thread + one queue.** — One stuck socket's `sendall` would
  still block every other client's delivery. Per-client threads isolate the failure.
- **Just set a socket send timeout and drop on timeout.** — A timeout still stalls the
  reader for the timeout window on every broadcast to a slow client; the queue makes
  `broadcast` constant-time regardless.
- **Unbounded queues.** — A permanently stuck client would grow memory without limit;
  the bound converts "slow forever" into a clean, logged drop.

## Consequences

`broadcast` is now constant-time and never blocks on I/O, so no TCP consumer can
back-pressure the serial reader or, through it, the Due's control loop — the R1 chain
is cut on the host side (the firmware-side half, non-blocking `printSL` + valve-first
`loop()`, is Increment 5b at the next reflash). A client that can't keep up is dropped
at 2,000 lines with a logged backlog, and `max_backlog` in the stats line is the early
warning. Cost: one daemon thread + one bounded queue per connected client (a handful
in practice) and a small, isolated rewrite of the fan-out path.

## Evidence / links

- Review finding R1 and Increment 5a in `sensor_polarity_hand_check_5b3798c1.plan.md`.
- Single-owner broker: ADR-0001; bounded serial writes + liveness: ADR-0062.
- Code: `rig_broker.Client` / `broadcast` / `drop_client`.
- Tests: `host/tests/test_rig_broker.py` (slow client dropped, broadcast never blocks,
  healthy client still served, idempotent drop).
