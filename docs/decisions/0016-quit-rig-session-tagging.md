# ADR 0016: Tag rig processes with a session id so one button can quit them all

- **Status:** Accepted
- **Date:** 2026-06-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/start_rig.bat`, `host/experiment_control.py`
- **Supersedes / Superseded by:** —

## Context

`start_rig.bat` opens the rig as **four independent processes**, each in its own
window: the serial broker (`rig_broker.py`), the session logger
(`rig_logger.py`), the lick tuner (`lick_tuner.py`), and the experiment-control
GUI (`experiment_control.py`). The broker and logger run inside `cmd /k`
windows; the two GUIs run as bare `python` windows. There is no parent process
left alive that owns the group — `start` detaches each child and the launcher
exits (see ADR-0004).

Shutting the rig down therefore meant closing four windows by hand, which is
easy to get wrong (e.g. leaving the broker holding the COM port, or the logger
mid-file). The request was a single **"Quit rig"** button in the GUI that closes
*exactly* the windows this launcher opened — and nothing else (a developer may
have an unrelated `python` running).

The hard part on Windows is identifying "the processes from this rig session"
from inside one of them. Options for tagging a process so siblings can find it:
a per-process environment variable (not queryable without reading another
process's PEB), the window title (unreliable to match for console windows), or
the **command line** (reliably queryable via WMI / `Get-CimInstance
Win32_Process`).

## Decision

`start_rig.bat` generates a unique session id (`RIG_SID=rig_%RANDOM%_%RANDOM%_%RANDOM%`)
and launches every program with Python's **`-X rigsid=<id>`** interpreter
option:

```
start "Rig Broker" cmd /k python -X rigsid=%RIG_SID% rig_broker.py --port %RIG_PORT%
...
start "Experiment Control" python -X rigsid=%RIG_SID% experiment_control.py
```

`-X <key>=<value>` is ideal here: Python ignores unknown `-X` options, the value
never reaches the script's `argv` (so no parser changes in the four scripts),
the running process exposes it to itself via `sys._xoptions["rigsid"]`, **and it
appears verbatim in the process command line**, so siblings can find it.

The Rig view tab gains a **"Quit rig (close all windows)"** button. On click it:

1. Reads its own id from `sys._xoptions` (`rig_session_id()`).
2. Enumerates every process whose command line contains `rigsid=<id>` via
   PowerShell `Get-CimInstance Win32_Process` (`find_rig_pids()`). The search
   token is built as `'*rig' + 'sid=<id>*'` so the query's *own* PowerShell
   command line doesn't contain the contiguous token and can't match itself;
   `powershell.exe` is excluded as a belt-and-suspenders measure.
3. `taskkill /F /T /PID` each PID **except its own** (the `/T` tree kill also
   closes the `cmd /k` windows that host the broker and logger), then closes
   itself cleanly via the normal `on_close()` path so the camera recording is
   finalized and the broker connection is released last.

If the GUI was started without a session id (run standalone, not via
`start_rig.bat`), the button falls back to matching the known rig script
filenames (`rig_broker.py`, `rig_logger.py`, `lick_tuner.py`,
`experiment_control.py`).

## Alternatives considered

- **Environment variable tag (`RIG_SESSION_ID`)** — children inherit it, but
  Windows has no supported, simple way to read *another* process's environment
  block (needs `NtQueryInformationProcess`/PEB reads). Kept only as a secondary
  source for this process's *own* id.
- **Match by window title** (`taskkill /FI "WINDOWTITLE eq ..."`) — console
  window titles are mangled/prefixed and match unreliably; bare `python`
  windows have no stable title.
- **Match by script name only** (no id) — simple, but would also kill an
  unrelated/second rig session or a manually launched copy of a script. Kept as
  the no-id fallback, not the primary path.
- **Windows Job Object with kill-on-close** — the cleanest OS-level grouping,
  but batch can't create job objects and the group owner (the launcher) exits
  immediately, so nothing would hold the job handle open.
- **A broker "shutdown" broadcast over TCP** — only reaches broker clients, not
  the broker itself or its `cmd` window, and needs a protocol change.

## Consequences

- One click cleanly tears down a whole rig session; the broker releases the COM
  port and the logger/camera files are finalized.
- Precise scoping: only this launcher's session is touched, so a second rig
  session or an unrelated developer `python` survives.
- No changes to `rig_broker.py` / `rig_logger.py` / `lick_tuner.py` — the `-X`
  tag is invisible to their argument parsing.
- Windows-specific (relies on `Get-CimInstance` + `taskkill`). On a non-Windows
  host the button would find nothing and just close the GUI; acceptable since
  the rig launcher is Windows-only.
- If someone launches the stack **without** `start_rig.bat`, the button uses the
  name-based fallback, which is broader. Launch via `start_rig.bat` for the
  precise, id-scoped behavior.

## Evidence / links

- `host/start_rig.bat` — session id generation + `-X rigsid=` on each launch.
- `host/experiment_control.py` — `rig_session_id()`, `find_rig_pids()`,
  `taskkill()`, and the `quit_rig()` button handler on the Rig view tab.
- Verified on this Windows host: `python -X rigsid=<id>` exposes
  `sys._xoptions == {'rigsid': '<id>'}` with a clean `argv`; a `cmd /c start`
  launch puts `rigsid=<id>` in the command line; the PowerShell query found the
  tagged PID and `taskkill /F /T` removed it (re-query returned empty).
