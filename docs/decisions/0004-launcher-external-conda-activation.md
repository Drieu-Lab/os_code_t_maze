# ADR 0004: Start the rig via start_rig.bat, with conda activation done externally

- **Status:** Accepted
- **Date:** 2026-06-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/start_rig.bat`
- **Supersedes / Superseded by:** —

## Context

Making clients broker-only (ADR 0003) means startup ordering matters: the
broker must own the serial port and have its TCP listener bound before any GUI
tries to connect. Starting four processes by hand in the right order is
error-prone.

Two facts shaped the launcher design:

1. The user runs the host tools from an **Anaconda Prompt** with the
   `drieu_t_maze` conda environment activated. Dependencies (`pyserial`,
   `matplotlib`) live in that env.
2. On Windows, a `start "..." python ...` line spawns a child console that
   **inherits the parent shell's environment** (`PATH`, `CONDA_PREFIX`, etc.).
   So if the env is active in the shell that runs the launcher, every spawned
   window already uses the env's `python.exe` — no in-script activation needed.

The lifecycle approach was an explicit fork put to the user (manual /
auto-spawn the broker from the first GUI / a launcher script). The user chose a
**launcher script**. Separately, the user opted to **activate the conda env
themselves** before running the script rather than have the script do it.

## Decision

Provide `host/start_rig.bat` that:

- Is intended to be run **after** the user has activated the env
  (`conda activate drieu_t_maze`), and documents this prerequisite in a header
  comment. It deliberately does **not** run `conda activate` itself.
- Runs a pre-flight check, `python -c "import serial, matplotlib"`, and aborts
  with an actionable message if it fails — catching the "forgot to activate the
  env" case once, instead of letting all spawned windows pop and fail
  individually.
- Starts the broker first in its own persistent window
  (`start "Rig Broker" cmd /k python rig_broker.py --port %RIG_PORT%`), waits a
  fixed `timeout /t 2` for the TCP port to bind, then starts the GUI clients.
- Accepts an optional COM-port argument (`start_rig.bat COM5`), defaulting to
  `COM4`.

## Alternatives considered

- **Manual startup** (user launches each process by hand). Rejected: fragile
  ordering and four windows to manage every session.
- **Auto-spawn the broker from the first GUI to start.** Rejected: more complex,
  hides the broker's log output, and muddies the "who owns the serial port"
  model that ADR 0001/0003 deliberately keep simple.
- **Embed `conda activate` inside the .bat.** Rejected: brittle across conda
  installations and shell-init differences; the user preferred to own
  activation; and env inheritance through `start` makes in-script activation
  unnecessary anyway.

## Consequences

- One command brings up the whole rig in the correct order.
- The pre-flight import check turns the most common operator error (env not
  active) into a single clear message rather than three silent failures.
- The 2 s delay before launching clients is a **heuristic, not a handshake**: if
  the broker is unusually slow to bind, a client could still race it. This is
  tolerated because `RigClient` auto-reconnects (ADR 0003), so a brief race
  self-heals.
- Spawned windows are plain `cmd.exe` and do not show the `(drieu_t_maze)`
  prompt prefix, even though they run in-env — cosmetic only.
- Would revisit if startup races become real (replace the fixed delay with a
  readiness probe of the TCP port), or if a non-conda / packaged launch path is
  needed.

## Evidence / links

- Commit `38731a7` (2026-06-01, authored by Ravi) — `host/start_rig.bat` added.
- Code: `host/start_rig.bat` — header prerequisite comment, the
  `python -c "import serial, matplotlib"` pre-flight with `errorlevel` guard,
  `start "Rig Broker" cmd /k ...`, and `timeout /t 2 /nobreak`.
- Note: the committed launcher also starts `experiment_control.py` (a later
  addition from the restructure), in addition to the broker, `maze_gui.py`, and
  `lick_tuner.py` that this decision originally covered.
- Decision provenance: lifecycle (launcher) and external-activation choices were
  both made by the user during the implementation session.
- No bench/scope artifacts apply.
