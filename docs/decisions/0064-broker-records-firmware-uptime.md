# ADR 0064: Let the broker read the `<ms>,` prefix so a lockup timestamps itself on the Due's own clock

- **Status:** Accepted
- **Date:** 2026-07-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rig_broker.py` (`last_ms`, `last_ms_at`, `_last_line_note`,
  the reader thread's decode step, both failure log lines), `host/protocol.py`
  (`leading_ms`) + `host/test_protocol.py`
- **Supersedes / Superseded by:** — (narrows ADR-0002 again, this time in the
  Due->host direction: the broker still forwards every line untouched, but it
  now reads the timestamp prefix in passing)

## Context

The lockout of `#260727_SerialLockout` has now happened five times, and
ADR-0062's probe made it measurable. Three idle runs put death in a
**1027-1030 s window measured from the moment the host opened the port**, and
the fifth run — probing every 2 s instead of every 10 s, so ~515 exchanges
instead of ~103 — died at the same 17 minutes. Message count is excluded; the
trigger is a clock.

Which clock is the open question, and the host cannot answer it:

- **2^20 ms is 1048.576 s**, about 18 s past the observed window. Suspicious
  enough to chase, but the wall clock cannot close an 18 s gap: the operator's
  RESET press is known only to within a few seconds, and Windows takes an
  unknown further moment to notice the board re-enumerating.
- The two candidate clocks — time since boot and time since the host attached —
  have been the *same instant* in every run, because `setup()` in
  `cue-guided-task.ino` blocks on `while (!SerialUSB) { delay(10); }` before it
  configures anything. The board does nothing at all until a host opens the
  port, so no run so far has separated them.

The number that settles both is already on the wire and being thrown away. Every
firmware line is `<ms>,<TYPE>,...` and a `STATUS` reply goes out through the same
`printLog`, so **each probe reply carries the Due's own `millis()`**. The broker
relays lines verbatim (ADR-0002) and never looks at them; run 5 ran with
`clients=0`, so nothing captured it and the run yielded only wall-clock times.

## Decision

The broker reads the timestamp prefix off each line and reports it at the two
moments that matter.

`protocol.leading_ms(line)` returns the integer before the first comma, or
`None`. It lives in `protocol.py` because that module is the single owner of
line-format knowledge (ADR-0024), and it is deliberately *not* `parse()`: it
scans to the first comma instead of splitting the whole line, so the relay hot
path pays almost nothing for a field no client asked for.

`rig_broker.py` keeps `last_ms` and `last_ms_at`, written only by
`serial_reader_thread` (the same single-owner discipline as `liveness`), and
emits two lines:

- **`firmware uptime at connect: <ms> ms`** on the first line after each open.
  Because the firmware waits for the host, this states how much of the board's
  life we were absent for — the one measurement that separates "dead at N ms of
  uptime" from "dead N s after the host attached".
- **`; last firmware line: ms=<N> (board up <N/1000> s), <age> s ago`** appended
  to both failure paths: the `serial write failed` line (which is what actually
  fires on the rig) and `DUE NOT RESPONDING`. With a 2 s probe the age is under
  two seconds, so this pins the board's clock at death an order of magnitude
  tighter than the ambiguity it replaces.

`last_ms` resets to `None` on each successful open, so a reopen re-reports
uptime rather than carrying a stale board's clock forward.

## Alternatives considered

- **Attach `rig_logger.py` and read the CSV.** The data is genuinely there —
  the logger records every line and flushes each one — but it depends on
  remembering to start it, which is exactly what cost us the number in run 5
  (`clients=0`), and it makes the operator cross-reference two files by
  timestamp to answer one question. Kept as the fallback, not the mechanism.
- **Call the existing `protocol.parse()` on every line.** Rejected: it splits
  the whole line and builds a `ParsedLine` to reach one field, on the path whose
  defining property is that it does not interpret lines.
- **Emit an explicit uptime or heartbeat line from the firmware.** Rejected for
  now: it needs a reflash, and it modifies the firmware in the middle of an
  investigation into that firmware. The prefix already carries the value.
- **Put uptime in the periodic `stats:` line.** Rejected: a number on every
  10 s line, in every run, for a fault that appears rarely — the same reasoning
  that keeps `probes=` hidden until it is non-zero.
- **Keep doing wall-clock arithmetic.** Rejected: it is precisely what is stuck,
  18 s short of a decisive answer.

## Consequences

- A lockup is now self-documenting: the broker console alone gives uptime at
  connect and the board's clock at death, with no second process and no CSV.
- It makes the delayed-connect experiment (E1b in `docs/notes/Bugs.md`) a
  one-run question. Reset, wait 5 minutes, then connect: `uptime at connect`
  near 300000 ms confirms the wedge between the clocks, and death near
  ms 1030000 means absolute `millis()` while death near ms 1330000 means the
  clock starts when the host attaches and `loop()` begins.
- The broker interprets a line prefix, so ADR-0002's "verbatim passthrough" is
  now narrowed in both directions (ADR-0062 did the host->Due side). What is
  *forwarded* is still byte-for-byte unchanged; only the broker's own logging
  differs.
- Cost is one `str.partition` per line, against a relay that already decodes,
  copies and sends each line to every client.
- The failure logger can run on a client thread and reads these globals without
  a lock. An int read is atomic in CPython and the value is at most a moment
  stale, which affects a log line and nothing else.
- If the firmware ever stopped prefixing `ms`, the notes would quietly vanish
  rather than break; the tests guard the parser, not the firmware's format.

## Evidence / links

- Run 5 (2026-07-28, fast probe): RESET pressed with the broker running at
  ~12:13:55 (`ClearCommError failed (PermissionError(13, 'The device does not
  recognize the command.', None, 22))` as the board re-enumerated, then a clean
  reopen), `serial open` 12:13:58, `serial write failed forwarding 'STATUS' from
  liveness probe: Write timeout` 12:31:08 — 17 min 10 s, with ~515 probes
  answered 1:1 until the last two seconds.
- Runs 3 and 4 for the window this intersects: 17 min 11 s and 17 min 15 s.
- `cue-guided-task.ino` `setup()`: `while (!SerialUSB) { delay(10); }` — the
  reason boot and attach have been indistinguishable.
- `task_core.h` answers `Command::Status` via `logN2`, so the reply carries the
  `ms` prefix like every other line (verified for Free Roam in ADR-0062).
- `host/test_protocol.py::TestLeadingMs` — prefix reads, the full 32-bit
  unsigned range, and the malformed inputs that must return `None` rather than
  raise.
- ADR-0002 (verbatim passthrough), ADR-0024 (`protocol.py` as the one parser),
  ADR-0062 (bounded writes + liveness probe), `docs/notes/Bugs.md`
  `#260727_SerialLockout`.
