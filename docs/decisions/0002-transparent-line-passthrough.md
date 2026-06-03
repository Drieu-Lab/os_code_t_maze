# ADR 0002: Keep the broker a transparent verbatim line passthrough

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rig_broker.py`, `host/rig_io.py`
- **Supersedes / Superseded by:** —

## Context

ADR 0001 introduced a broker process between the Due and its consumers. That
created a second hop (TCP) where messages could be reframed. We had to decide
what the broker's wire format should be.

Two facts constrained the choice:

1. The Due and the existing GUIs already share a working line protocol:
   newline-terminated ASCII, e.g. `S,<i>,<state>`, `L,<i>,<state>`,
   `SOL,<port>,<sol>` from the Due, and `R<port><sol>` to the Due.
2. The original GUIs each had their own line parser already written against
   that format.

Re-framing on the TCP hop (e.g. wrapping each line in JSON with metadata) would
have meant rewriting every client parser and maintaining a versioned message
schema — for no current functional need.

## Decision

The broker is a **fully transparent** byte/line relay. It does not reframe,
annotate, or restructure traffic:

- Each line read from the Due is decoded and forwarded to clients verbatim
  (only `\r`/`\n` trimmed), in `serial_reader_thread()` →`broadcast()`.
- Each line from a client is written to the Due verbatim, in
  `_forward_to_due()`.
- No JSON, no metadata, no timestamps, no source/client tags. The TCP wire
  protocol is byte-identical to the Due's serial protocol.

As stated in `host/rig_io.py`'s module docstring: "The wire protocol is
identical to what the Due itself speaks ... a near-drop-in replacement for the
prior pyserial reader/writer pattern."

## Alternatives considered

- **Structured/JSON framing with metadata** (message type, source client id,
  receive timestamp). Rejected: forces a rewrite of every client's parser,
  introduces a schema/version to maintain, and adds coupling — with no current
  consumer that needs the metadata. The GUIs already parse the raw format.
- **Broker-side timestamping of each line.** Rejected/deferred: experiment
  timing is better anchored at the firmware and at Open Ephys (which receives
  TTLs directly), not at a host process several buffers downstream. Listed as
  an explicit out-of-scope follow-up rather than built now.

## Consequences

- The GUI refactor was minimal: swap the pyserial reader/writer for a TCP
  reader/writer (`RigClient`); all existing line-parsing logic was reused
  unchanged.
- Any firmware that speaks the existing ASCII protocol works through the broker
  with no broker changes — including the testing firmware and, potentially,
  future sketches that emit the same `S,/L,/SOL,` style lines.
- Limitation accepted: there is **no per-line provenance**. The broker cannot
  tell clients which client issued a command, and if multiple clients send
  commands they interleave on the serial line with no attribution. This is fine
  for the current debug/monitor use but is the main reason we would revisit:
  add framing if we ever need provenance, host-side timestamps, or
  disambiguation among multiple command sources.

## Evidence / links

- Commit `38731a7` (2026-06-01, authored by Ravi) — broker and client added /
  refactored.
- Code: `host/rig_broker.py` — the verbatim decode in `serial_reader_thread()`
  (`line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")`) feeding
  `broadcast()`, and the verbatim write in `_forward_to_due()`; module
  docstring "The broker is fully transparent." `host/rig_io.py` — module
  docstring asserting protocol identity.
- No bench/scope artifacts apply (this is a framing decision). The protocol
  itself (the `S,/L,/SOL,/R` message set) originates in the firmware and is
  documented in the rig manual in the `os_code_t_maze_resources` repo.
