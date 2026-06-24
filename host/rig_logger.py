"""
rig_logger.py - persistent session logger for the T-maze rig.

The broker (rig_broker.py) fans the Arduino Due's serial stream out over TCP, but
nothing writes it to disk. This tool is a headless broker client (broker-only,
per docs/decisions/0003) that appends every firmware line verbatim to a
per-session file:

    data/<animal>_<YYYYMMDD_HHMMSS>.csv

Each row is the firmware line prefixed with a host wall-clock timestamp column:

    <wall_clock_iso>,<ms>,<TYPE>,<payload...>

The firmware `ms` is session-relative and resets whenever the mode is switched
(MODE,FREE / MODE,FULL each start a fresh session clock; see PROTOCOL.md), so
the wall-clock column is what anchors events on an absolute timeline across mode
switches and session restarts.

The animal id used in the filename is read from the `0,ANIMAL,<id>` handshake
line. If the logger is started after the handshake has already gone by, it falls
back to "unknown" (no lines are lost - early lines are buffered until the file is
opened). Override the id with --animal.

Run from os_code_t_maze/host/ (broker must be running; start_rig.bat launches
both):
    python rig_logger.py
    python rig_logger.py --host 127.0.0.1 --port 5555 --dir data --animal RAT001
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Handshake line that carries the animal id, e.g. "0,ANIMAL,RAT001".
ANIMAL_RE = re.compile(r"^\d+,ANIMAL,(.+)$")

RECV_BYTES = 4096
RECONNECT_DELAY_S = 2.0


def _sanitize(name: str) -> str:
    """Keep a filename-safe slug of the animal id."""
    slug = "".join(ch for ch in name.strip() if ch.isalnum() or ch in "-_")
    return slug or "unknown"


class SessionLog:
    """Lazily opens one file per process run and streams lines to it.

    Lines received before the animal id is known are buffered, then flushed once
    the file is opened, so the handshake (PROTO/ANIMAL/...) is never lost.
    """

    MAX_BUFFER = 50   # if no ANIMAL line arrives, open as "unknown" after this many lines

    def __init__(self, directory: str, animal_override: str | None):
        self._directory = directory
        self._animal_override = animal_override
        self._file = None
        self.path: str | None = None
        self._buffer: list[str] = []

    def _open(self, animal: str) -> None:
        os.makedirs(self._directory, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(self._directory, f"{_sanitize(animal)}_{stamp}.csv")
        self._file = open(self.path, "a", encoding="utf-8", newline="")
        self._file.write("# wall_clock_iso,firmware_line (ms,TYPE,payload...)\n")
        for line in self._buffer:
            self._write(line)
        self._buffer.clear()
        print(f"[rig_logger] logging to {self.path}", flush=True)

    def _write(self, line: str) -> None:
        ts = datetime.now().isoformat(timespec="milliseconds")
        self._file.write(f"{ts},{line}\n")
        self._file.flush()

    def handle(self, line: str) -> None:
        if self._file is None:
            if self._animal_override:
                self._open(self._animal_override)
            else:
                m = ANIMAL_RE.match(line)
                if m:
                    self._open(m.group(1))
                else:
                    self._buffer.append(line)
                    if len(self._buffer) >= self.MAX_BUFFER:
                        self._open("unknown")
                    return
        self._write(line)

    def close(self) -> None:
        if self._file is None and self._buffer:
            self._open("unknown")
        if self._file is not None:
            self._file.close()
            self._file = None


def _stream(sock: socket.socket, log: SessionLog) -> None:
    """Read newline-terminated lines until the peer closes or errors."""
    buf = b""
    while True:
        chunk = sock.recv(RECV_BYTES)
        if not chunk:
            return  # broker closed the connection
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("utf-8", errors="ignore").rstrip("\r")
            if line:
                log.handle(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="Persistent T-maze session logger (broker client).")
    ap.add_argument("--host", default="127.0.0.1", help="broker host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=5555, help="broker TCP port (default 5555)")
    ap.add_argument("--dir", default=os.path.join(SCRIPT_DIR, "data"),
                    help="output directory (default: host/data)")
    ap.add_argument("--animal", default=None,
                    help="override the animal id used in the filename")
    args = ap.parse_args()

    log = SessionLog(args.dir, args.animal)
    print(f"[rig_logger] connecting to broker {args.host}:{args.port} ...", flush=True)

    try:
        while True:
            try:
                sock = socket.create_connection((args.host, args.port), timeout=5.0)
            except OSError as e:
                print(f"[rig_logger] connect failed: {e}; retrying in {RECONNECT_DELAY_S}s",
                      file=sys.stderr, flush=True)
                time.sleep(RECONNECT_DELAY_S)
                continue
            sock.settimeout(None)
            print("[rig_logger] connected", flush=True)
            try:
                _stream(sock, log)
            except OSError as e:
                print(f"[rig_logger] read error: {e}", file=sys.stderr, flush=True)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
            print(f"[rig_logger] broker disconnected; reconnecting in {RECONNECT_DELAY_S}s",
                  flush=True)
            time.sleep(RECONNECT_DELAY_S)
    except KeyboardInterrupt:
        print("\n[rig_logger] stopping", flush=True)
    finally:
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
