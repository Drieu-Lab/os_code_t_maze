"""
rig_process.py - Windows helpers to close every process start_rig.bat opened.

start_rig.bat tags each launched program with "-X rigsid=<id>" (see ADR-0016).
experiment_control.py's "Quit rig" button uses these helpers to enumerate and
terminate the broker, logger, lick tuner, and sibling GUIs for that session.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Command-line fragments start_rig.bat launches; fallback when no session id is
# present. The broker/logger are module entry points ("-m tmaze.io.<name>"), so
# match those module paths; the still-flat GUIs match by script filename.
RIG_SCRIPTS = ("tmaze.io.rig_broker", "tmaze.io.rig_logger", "lick_tuner.py",
               "experiment_control.py")

# Hide transient PowerShell/taskkill console windows (Windows only).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def rig_session_id() -> str | None:
    """The session id this process was tagged with, or None if untagged."""
    sid = sys._xoptions.get("rigsid")
    if sid:
        return str(sid)
    return os.environ.get("RIG_SESSION_ID") or None


def find_rig_pids(session_id: str | None) -> list[int]:
    """PIDs of this rig session's processes (broker, logger, tuner, GUIs).

    Uses WMI via PowerShell to match each process's command line. When a session
    id is given we match "rigsid=<id>"; the literal is split in the query so the
    query's *own* PowerShell process can't match itself. Otherwise we match the
    known rig script filenames. PowerShell itself is always excluded.
    """
    if session_id:
        cond = "$_.CommandLine -like ('*rig' + 'sid=" + session_id + "*')"
    else:
        likes = " -or ".join(
            f"$_.CommandLine -like '*{name}*'" for name in RIG_SCRIPTS)
        cond = f"({likes})"
    script = ("Get-CimInstance Win32_Process | Where-Object { "
              f"({cond}) -and $_.Name -ne 'powershell.exe' "
              "} | Select-Object -ExpandProperty ProcessId")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW)
    except Exception as e:  # pragma: no cover
        print(f"[rig_process] could not list rig processes: {e}",
              file=sys.stderr, flush=True)
        return []
    pids = []
    for tok in proc.stdout.split():
        tok = tok.strip()
        if tok.isdigit():
            pids.append(int(tok))
    return pids


def taskkill(pid: int) -> None:
    """Force-kill a PID and its child tree (e.g. a cmd window + its python)."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, timeout=15,
                       creationflags=_NO_WINDOW)
    except Exception:
        pass
