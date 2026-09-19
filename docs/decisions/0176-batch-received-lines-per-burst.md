# ADR 0176: Batch received serial lines into one Tk callback per burst

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/io/rig_io.py` (`RigClient._dispatch_line`, new `_drain`/`_deliver`, pending queue), `host/tests/test_rig_io.py`
- **Supersedes / Superseded by:** —

## Context

`RigClient`'s reader thread carves newline-terminated lines out of each
`socket.recv()` chunk and hands each one to the Tk main thread with
`root.after(0, cb, line)` - a fresh Tk event per line. That is correct (Tk is
touched only on its own thread) but wasteful: the firmware emits lines in bursts
(a rat crossing a beam fires several `S`/`L`/`SOL`/`REWARD`/`CUE` lines back to
back, and the broker can hand the client a chunk holding many at once), so one
`recv` of N lines schedules N separate `root.after` callbacks. Under load that is
N trips through the Tk event queue, each re-entering the whole single-parse
fan-out (`experiment_control.on_line` -> `router.dispatch`), competing with the
frame poll and the redraws. This is review finding R3: coalesce a burst into one
scheduled callback.

The receipt-stamp work (ADR-0175) had just moved to carrying `(recv_wall,
recv_mono, line)` across this exact hop, so the two changes touch the same seam;
batching must preserve the per-line stamps and the arrival ORDER (consumers - the
tallies, the Phase 3 gate feed, the serial capture - assume lines arrive in the
order the firmware sent them).

## Decision

Queue received lines and schedule at most ONE Tk drain per burst.

- `RigClient` gains a `_pending` deque of `(recv_wall, recv_mono, line)` (arrival
  order), a `_pending_lock`, and a `_drain_scheduled` flag.
- `_dispatch_line` appends the line under the lock and schedules
  `root.after(0, self._drain)` only if a drain is not already pending; every
  other line in the same burst just appends. So a burst of N lines costs ONE
  `after`, not N.
- `_drain` runs on the Tk thread and pops one line at a time, delivering each via
  `_deliver` (which still prefers the stamped callback, else the plain one,
  ADR-0175). It clears `_drain_scheduled` only once the queue is empty, so a line
  that arrives MID-drain (either a fresh `recv`, or a callback that re-enters) is
  delivered in the SAME pass rather than stranded, and the NEXT genuine burst
  schedules a fresh drain. If a callback raises, any still-queued lines are
  re-scheduled before the exception propagates, so one bad line never strands the
  rest of the burst (and the error still surfaces to Tk, as before).

Order is preserved (FIFO deque, single drainer on the Tk thread). The status
callback keeps its own per-message `after` (status changes are rare, not a hot
stream).

## Alternatives considered

- **Keep one `root.after` per line** — the status quo; N Tk events per burst, the
  overhead R3 flags.
- **Drain a fixed-size batch then re-schedule** — needless; the deque already
  bounds a pass to what has arrived, and popping until empty handles a burst of
  any size in one pass.
- **Snapshot-and-clear the whole deque under the lock, then deliver** — simpler,
  but a callback that re-enters (`_dispatch_line` from within delivery) would land
  in a fresh deque and need a second `after`; popping one-at-a-time folds those
  into the current pass and keeps `_drain_scheduled` honest.
- **Deliver on the reader thread** — violates the Tk-thread rule the whole client
  is built around.

## Consequences

- A burst of N firmware lines now costs one Tk hop instead of N, cutting event-
  loop pressure during the busy moments (a rat working the ports) without changing
  what any consumer sees: same lines, same order, same per-line receipt stamps.
- The reader thread never blocks on Tk (it only appends under a short lock and
  maybe schedules once); the Tk thread does the fan-out, as before.
- Lines queued but not yet drained at shutdown are dropped (the client is
  stopping); the hot data is the rig_logger's verbatim stream, not this fan-out.
- Pairs with ADR-0175: all lines in a burst share that burst's single receipt
  stamp, so batching does not blur the stamp.

## Evidence / links

- Review finding R3 (coalesce serial-line Tk callbacks).
- `docs/decisions/0175` (receipt stamps on the same seam), `0056` (single-parse
  fan-out the drain feeds).
- Tests: `host/tests/test_rig_io.py` `TestBurstBatching` (one drain per burst,
  order preserved, mid-drain re-entry folded in, plain path batched too).
