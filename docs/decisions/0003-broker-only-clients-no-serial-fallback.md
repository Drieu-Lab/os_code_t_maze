# ADR 0003: Make GUI clients broker-only (no direct-serial fallback)

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rig_io.py`, `host/maze_gui.py`, `host/lick_tuner.py`
- **Supersedes / Superseded by:** —

## Context

When refactoring the GUIs to use the broker (ADR 0001), we had to decide
whether each GUI should keep the ability to open the serial port directly when
no broker is running, or whether it should rely on the broker exclusively.

This was an explicit fork put to the user. The two options offered were:

1. Keep standalone capability — a client opens COM4 directly if the broker is
   unreachable, otherwise uses the broker (more code, two code paths).
2. Broker-only — strip the direct-serial logic; the broker becomes a required
   dependency.

The user chose **broker-only**.

A related observation from the prior `lick_tuner.py` work also pushed this way:
an earlier design opened the port from a background thread with a silent retry
loop, which made a genuine failure indistinguishable from "still connecting."
We had already moved to opening up front and failing loudly; broker-only is the
natural continuation of that "fail fast, fail visibly" stance.

## Decision

GUI clients connect **only** through `RigClient` → broker. They do not open the
serial port directly and have no fallback path.

- `RigClient.start()` in `host/rig_io.py` performs one connection attempt and
  **raises** on first-connect failure (it does not silently retry at startup).
- Each GUI wraps `client.start()` in a try/except that, on failure, prints to
  stderr, shows a `messagebox.showerror` pointing the user at `start_rig.bat`,
  and calls `sys.exit(1)` — i.e. no attempt to grab the serial port itself.
- Once connected, automatic reconnection *is* provided for transient broker
  outages (`RigClient.RECONNECT_DELAY_S = 2.0`); "broker-only" is about not
  bypassing the broker, not about being fragile to a broker restart.

## Alternatives considered

- **Keep a direct-serial fallback** (open COM4 if the broker is down). Rejected
  by the user, and for a substantive reason: a GUI in fallback mode would itself
  grab the single-owner serial port, which is exactly the contention ADR 0001
  exists to eliminate. It would also double the code paths (serial *and* TCP) in
  every client and make "why isn't the broker getting data?" failures harder to
  diagnose.

## Consequences

- The GUIs got simpler: the direct `serial.Serial(...)` open, the 2 s settle,
  `reset_input_buffer()`, and the bespoke `serial_loop` reader threads were all
  removed in favor of `RigClient` callbacks.
- Single, unambiguous ownership of the serial port (only the broker opens it).
- Hard dependency accepted: nothing works unless the broker is running. This is
  mitigated by (a) the explicit, actionable error dialog, and (b) the launcher
  that starts the broker first (see ADR 0004).
- Would revisit if a use case appeared for running a single GUI against the Due
  with no broker (e.g. a minimal field-debug scenario) — but that would
  reintroduce the contention risk and should be a conscious new decision.

## Evidence / links

- Commit `38731a7` (2026-06-01, authored by Ravi) — GUI broker refactor.
- Code: `host/rig_io.py` — `RigClient.start()` (raises on first-connect failure;
  docstring: "Raises ... on initial failure so the caller can show a clean error
  dialog instead of silently retrying"). `host/maze_gui.py` and
  `host/lick_tuner.py` — the `client.start()` connect-or-die blocks
  (`messagebox.showerror` + `sys.exit(1)`), with no `import serial` remaining in
  either GUI.
- Decision provenance: the broker-only choice was made by the user in response
  to an explicit two-option question during the implementation session.
- No bench/scope artifacts apply.
