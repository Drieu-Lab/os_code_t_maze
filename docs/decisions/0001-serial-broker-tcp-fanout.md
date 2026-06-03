# ADR 0001: Use a single-owner serial broker with localhost TCP fan-out

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rig_broker.py`, `host/rig_io.py`, `host/maze_gui.py`, `host/lick_tuner.py`, `host/start_rig.bat`
- **Supersedes / Superseded by:** —

## Context

Only one OS process can hold the Arduino Due's serial port (COM4) at a time.
On Windows, a second opener gets `PermissionError(13, 'Access is denied')`.
This was observed directly on the bench during the 2026-05-13 session:
`lick_tuner.py` failed to open COM4 while another `python.exe` (a stale,
orphaned process, PID 18120) still held it, even though `maze_gui.py` and the
Arduino IDE Serial Monitor had been closed.

The operational need was concrete: the user wanted to run **both** the
monitor/control GUI (`maze_gui.py`) **and** the lick-tuning plot
(`lick_tuner.py`) at the same time to debug the rig, and to keep adding tools
later (CSV logger, video sync, experiment control). The single-owner serial
constraint made that impossible with the original design, where each GUI
opened the port directly.

The firmware (`tmaze_test.ino`) speaks newline-terminated ASCII over the Due's
native USB CDC at 115200 baud. There is no inherent reason the *serial* port
must be opened by each consumer — only that exactly one process owns it.

## Decision

Introduce `host/rig_broker.py`: a single process that exclusively owns the
serial port and re-broadcasts its byte stream to many clients over a local TCP
server.

- Topology: `Arduino Due  <--USB-->  rig_broker.py  <--TCP 127.0.0.1:5555-->  N clients`.
- Fan-out: every line read from the Due is sent to every connected client
  (`broadcast()` in `host/rig_broker.py`); every line received from any client
  is written to the Due (`_forward_to_due()`), serialized by `serial_lock`.
- Defaults live at the top of `host/rig_broker.py`: `DEFAULT_TCP_PORT = 5555`,
  `DEFAULT_HOST = "127.0.0.1"` (localhost-only bind, so the rig is not exposed
  on the network), `DEFAULT_PORT = "COM4"`, `DEFAULT_BAUD = 115200`.
- GUIs become thin TCP clients via the shared `RigClient` class in
  `host/rig_io.py`.

## Alternatives considered

These were brainstormed explicitly (options A–F) before choosing D:

- **A — Tabbed single-process GUI (one window, `ttk.Notebook`).** Rejected:
  forces every tool into one file/window, does not scale cleanly to additional
  tools, and the user wanted separate, independently placeable windows.
- **B — Two `Toplevel` windows in one process.** Rejected: still one process
  owning the port; any future tool would have to live inside that same process.
- **C — Shared `rig_io` library only (no broker).** Useful refactor on its own,
  but by itself it does not let two *separate* applications run at once. (Note:
  a shared client module was still created, `host/rig_io.py`, but as the broker
  client, not as a same-process serial owner.)
- **E — Virtual COM-port splitter (`com0com` + `hub4com`).** Rejected:
  kernel-mode signed-driver installation hassle on modern Windows, and
  bidirectional write multiplexing through such splitters is historically
  flaky.
- **F — Mirror telemetry on both Due USB ports (programming + native).**
  Rejected: requires firmware changes, doubles the MCU's serial output
  bandwidth, needs two cables/COM ports, and still caps at two consumers.
- **D — Serial broker process (chosen).** Keeps the existing GUIs as separate
  apps, allows N simultaneous clients, needs no firmware change, and makes
  adding new tools trivial (just another TCP client).

## Consequences

- Multiple tools can observe and command the rig at once; this is the whole
  point and it is now satisfied.
- The broker is transparent to firmware: any sketch that speaks the existing
  ASCII protocol works through it unchanged (see ADR 0002).
- New trade-off: the broker is now a required process (see ADR 0003 for the
  decision to make clients broker-only, and ADR 0004 for how it is launched).
- Localhost-only bind keeps the surface minimal; if remote access is ever
  wanted, that is a deliberate future change (out of scope here).
- Robustness was built in: the broker reopens the serial port if the Due is
  unplugged (`SERIAL_REOPEN_DELAY_S = 2.0`) and drops clients whose sockets
  fail, without taking down other clients.
- Would revisit if: we needed hard real-time command latency (the extra TCP
  hop adds a small, normally-negligible delay), or if a non-localhost / multi-
  machine topology became a requirement.

## Evidence / links

- Commit `38731a7` "Restructure repo into firmware/ and host/ layout"
  (2026-06-01, authored by Ravi) is where `host/rig_broker.py`,
  `host/rig_io.py`, and `host/start_rig.bat` first appear in git history, along
  with the broker refactor of `host/maze_gui.py` and `host/lick_tuner.py`.
  (The broker was designed/written in the 2026-05-13 working session; it landed
  in git as part of this restructure commit.)
- Code: `host/rig_broker.py` — `broadcast()` (fan-out), `serial_reader_thread()`,
  `_forward_to_due()`, and `main()` TCP server setup; constants
  `DEFAULT_TCP_PORT`, `DEFAULT_HOST`.
- Motivating symptom: `PermissionError(13, 'Access is denied')` on COM4,
  observed on the bench (2026-05-13) and traced to an orphaned `python.exe`
  (PID 18120) holding the port. The error screenshot was pasted into the
  working session; whether a copy was archived in the `os_code_t_maze_resources`
  repo is **uncertain** (that repo holds rig photos and the wiring manual, not
  software/networking artifacts).
- No scope traces or bench measurements apply to this decision; it is a
  software-transport choice, not a timing/threshold constant.
