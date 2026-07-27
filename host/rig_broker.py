"""
rig_broker.py - Single-owner serial bridge for the T-maze rig.

Owns the Arduino Due's COM port and exposes its byte stream over a local
TCP socket. Multiple GUIs / tools (e.g. maze_gui.py, lick_tuner.py, future
loggers) can connect at the same time:

    Arduino Due  <--USB--> rig_broker.py <--TCP localhost:5555--> N clients

The broker is fully transparent. Every line the Due prints is fanned out
verbatim to every connected client; every line a client sends is forwarded
verbatim to the Due. Lines are newline-terminated ASCII in both directions.

The one line the broker sends on its own behalf is a periodic STATUS while the
port is quiet - a liveness probe, so a wedged Due is noticed in seconds instead
of going unnoticed for the rest of a session (see docs/decisions/0062).

Run from os_code_t_maze/host/:
    python rig_broker.py

Dependencies: pyserial (already required by the original GUIs).
"""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading
import time
from typing import List, Optional

import serial

from serial_liveness import DEAD, PROBE, LivenessMonitor

# ================= DEFAULTS =================
DEFAULT_PORT = "COM4"          # Arduino Due native USB
DEFAULT_BAUD = 115200
DEFAULT_HOST = "127.0.0.1"     # localhost only; safe default
DEFAULT_TCP_PORT = 5555
SERIAL_REOPEN_DELAY_S = 2.0
STATS_INTERVAL_S = 10.0
# Cap on a single serial write. pyserial's default (None) blocks forever, so a
# wedged Due froze every client's commands indefinitely - and silently, because
# the blocked write held serial_lock. see docs/decisions/0062
WRITE_TIMEOUT_S = 1.0
# The liveness probe. STATUS (not PING) keeps PONG reserved as the session-start
# fiducial. see docs/decisions/0062
LIVENESS_PROBE_CMD = "STATUS"
# How often to repeat an unchanged "serial open failed" line. A wedged Due
# refuses every reopen until someone presses RESET, so the retry loop can run for
# hours; at one line per retry it buries the history the log is kept for.
# see docs/decisions/0062
OPEN_FAIL_SUMMARY_S = 60.0


# ================= STATE =================
stop_event = threading.Event()

clients_lock = threading.Lock()
clients: List[socket.socket] = []

serial_lock = threading.Lock()  # serializes writes to the Due
ser: Optional[serial.Serial] = None

# Set by any thread that finds the port unusable; only serial_reader_thread acts
# on it. Keeping the close/reopen in that one thread avoids re-entering
# serial_lock from a writer that already holds it. see docs/decisions/0062
reopen_request = threading.Event()

# Decides when a quiet port gets probed and when it counts as dead. Touched only
# by serial_reader_thread, so it needs no lock. see docs/decisions/0062
liveness = LivenessMonitor()

# Lightweight throughput counters for the periodic stats line.
stats_lock = threading.Lock()
stats = {"from_due": 0, "to_due": 0, "probes": 0}


def log(msg: str) -> None:
    """Write a timestamped line to stdout."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ================= SERIAL =================
def open_serial(port: str, baud: int) -> serial.Serial:
    s = serial.Serial(port, baud, timeout=0.1, write_timeout=WRITE_TIMEOUT_S)
    time.sleep(2.0)  # let the Due settle (matches existing GUIs)
    try:
        s.reset_input_buffer()
    except Exception:
        pass
    return s


def serial_reader_thread(port: str, baud: int) -> None:
    """Read lines from the Due and broadcast them to every connected client."""
    global ser
    # State for throttling a repeating open failure (see OPEN_FAIL_SUMMARY_S). The
    # retry interval itself is deliberately NOT backed off: after the operator
    # presses RESET the board must come back within a couple of seconds.
    fail_count = 0
    fail_msg = ""
    fail_logged_at = 0.0
    while not stop_event.is_set():
        if ser is None:
            try:
                if not fail_count:
                    log(f"opening serial {port} @ {baud} ...")
                ser = open_serial(port, baud)
                if fail_count:
                    log(f"serial recovered after {fail_count} failed attempts")
                    fail_count, fail_msg = 0, ""
                reopen_request.clear()
                liveness.reset(time.monotonic())
                log("serial open")
            except Exception as e:
                fail_count += 1
                changed = str(e) != fail_msg
                # A changed message is news; an unchanged one is a heartbeat.
                if changed or time.monotonic() - fail_logged_at >= OPEN_FAIL_SUMMARY_S:
                    if changed:
                        log(f"serial open failed: {e}; retrying every "
                            f"{SERIAL_REOPEN_DELAY_S:.0f}s, repeats summarized "
                            f"every {OPEN_FAIL_SUMMARY_S:.0f}s")
                    else:
                        log(f"serial open still failing after {fail_count} "
                            f"attempts: {e}")
                    fail_msg = str(e)
                    fail_logged_at = time.monotonic()
                _sleep_until_stop(SERIAL_REOPEN_DELAY_S)
                continue

        # A failed write or a dead-port verdict asks this thread to cycle the
        # port, since it is the only one allowed to open/close it.
        if reopen_request.is_set():
            reopen_request.clear()
            log(f"reopening serial (requested); retry in {SERIAL_REOPEN_DELAY_S:.0f}s")
            _close_serial()
            _sleep_until_stop(SERIAL_REOPEN_DELAY_S)
            continue

        try:
            raw = ser.readline()
        except Exception as e:
            if stop_event.is_set():
                return
            log(f"serial read error: {e}; will reopen")
            _close_serial()
            _sleep_until_stop(SERIAL_REOPEN_DELAY_S)
            continue

        if not raw:
            # A wedged Due returns empty rather than raising, so silence is the
            # only symptom. Legitimate quiet is common (a resting rat breaks no
            # beams), hence an active probe instead of a plain timeout.
            _check_liveness()
            continue
        liveness.note_rx(time.monotonic())
        try:
            # Verbatim line relay: no reframing/metadata. see docs/decisions/0002
            line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
        except Exception:
            continue
        if not line:
            continue

        with stats_lock:
            stats["from_due"] += 1
        broadcast(line)


def _check_liveness() -> None:
    """Probe a quiet port, and ask for a reopen if it has stopped answering.

    Called from serial_reader_thread on every empty read, which the 0.1 s read
    timeout makes about 10x a second - so no extra thread is needed.
    """
    now = time.monotonic()
    verdict = liveness.step(now)
    if verdict == PROBE:
        if _write_to_due(LIVENESS_PROBE_CMD, "liveness probe"):
            with stats_lock:
                stats["probes"] += 1
    elif verdict == DEAD:
        log(f"DUE NOT RESPONDING: no serial data for "
            f"{liveness.quiet_for(now):.0f}s despite probes; will reopen. "
            f"If reopening fails, press RESET on the Due.")
        reopen_request.set()


def _close_serial() -> None:
    global ser
    with serial_lock:
        s, ser = ser, None
    if s is not None:
        try:
            s.close()
        except Exception:
            pass


# ================= TCP =================
# Single-owner broker fans every Due line out to all clients. see docs/decisions/0001
def broadcast(line: str) -> None:
    """Send ``line + "\\n"`` to every connected client; drop any that fail."""
    payload = (line + "\n").encode("utf-8", errors="ignore")
    dropped: List[socket.socket] = []
    with clients_lock:
        for c in clients:
            try:
                c.sendall(payload)
            except Exception:
                dropped.append(c)
        for c in dropped:
            try:
                clients.remove(c)
            except ValueError:
                pass
    for c in dropped:
        peer = _peer_str(c)
        try:
            c.close()
        except Exception:
            pass
        log(f"client dropped (send failed): {peer}")


def client_reader_thread(client_sock: socket.socket, addr) -> None:
    """Forward each newline-terminated command from the client to the Due."""
    peer = f"{addr[0]}:{addr[1]}"
    buf = b""
    try:
        while not stop_event.is_set():
            try:
                chunk = client_sock.recv(4096)
            except OSError as e:
                log(f"client {peer} recv error: {e}")
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                cmd = raw.decode("utf-8", errors="ignore").rstrip("\r")
                if not cmd:
                    continue
                _forward_to_due(cmd, peer)
    finally:
        with clients_lock:
            try:
                clients.remove(client_sock)
            except ValueError:
                pass
        try:
            client_sock.close()
        except Exception:
            pass
        log(f"client disconnected: {peer}")


def _write_to_due(cmd: str, source: str) -> bool:
    """Write one command to the Due; return True if it went out.

    The only place the port is written, so client commands and liveness probes get
    the same bounded, serialized treatment.
    """
    payload = (cmd + "\n").encode("utf-8", errors="ignore")
    with serial_lock:
        s = ser
        if s is None:
            log(f"command from {source} dropped (serial not open): {cmd!r}")
            return False
        try:
            s.write(payload)
        except Exception as e:
            # Bounded by WRITE_TIMEOUT_S, so this now returns instead of hanging.
            # Hand the port back to the reader thread rather than closing it here:
            # we hold serial_lock, and _close_serial() wants it too.
            log(f"serial write failed forwarding {cmd!r} from {source}: {e}")
            reopen_request.set()
            return False
    return True


def _forward_to_due(cmd: str, peer: str) -> None:
    if _write_to_due(cmd, peer):
        with stats_lock:
            stats["to_due"] += 1


def _peer_str(sock: socket.socket) -> str:
    try:
        addr = sock.getpeername()
        return f"{addr[0]}:{addr[1]}"
    except OSError:
        return "<closed>"


# ================= STATS =================
def stats_thread() -> None:
    while not stop_event.is_set():
        if _sleep_until_stop(STATS_INTERVAL_S):
            return
        with stats_lock:
            from_due = stats["from_due"]
            to_due = stats["to_due"]
            probes = stats["probes"]
            stats["from_due"] = 0
            stats["to_due"] = 0
            stats["probes"] = 0
        with clients_lock:
            n = len(clients)
        # Probes only appear once the port has gone quiet, so a non-zero count in
        # the log is the timestamp of when trouble started.
        probe_note = f"  probes={probes}" if probes else ""
        log(f"stats: clients={n}  from_due={from_due}/{STATS_INTERVAL_S:.0f}s  "
            f"to_due={to_due}/{STATS_INTERVAL_S:.0f}s{probe_note}")


def _sleep_until_stop(seconds: float) -> bool:
    """Sleep up to `seconds`; return True if stop was signaled meanwhile."""
    return stop_event.wait(timeout=seconds)


# ================= MAIN =================
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="T-maze rig serial broker")
    p.add_argument("--port", default=DEFAULT_PORT, help="serial port (default %(default)s)")
    p.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="serial baud (default %(default)s)")
    p.add_argument("--host", default=DEFAULT_HOST, help="TCP bind host (default %(default)s)")
    p.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT,
                   help="TCP listen port (default %(default)s)")
    return p.parse_args()


def install_signal_handlers() -> None:
    def handler(signum, _frame):
        log(f"signal {signum} received; shutting down")
        stop_event.set()

    signal.signal(signal.SIGINT, handler)
    try:
        signal.signal(signal.SIGTERM, handler)
    except (AttributeError, ValueError):
        pass  # Windows does not support SIGTERM the same way


def main() -> int:
    args = parse_args()
    install_signal_handlers()

    log(f"rig_broker starting (serial={args.port}@{args.baud}, "
        f"tcp={args.host}:{args.tcp_port})")

    reader = threading.Thread(target=serial_reader_thread,
                              args=(args.port, args.baud),
                              name="serial-reader", daemon=True)
    reader.start()

    stats_t = threading.Thread(target=stats_thread, name="stats", daemon=True)
    stats_t.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((args.host, args.tcp_port))
    except OSError as e:
        log(f"could not bind {args.host}:{args.tcp_port}: {e}")
        stop_event.set()
        return 1
    server.listen(8)
    server.settimeout(0.5)
    log(f"listening on {args.host}:{args.tcp_port}")

    try:
        while not stop_event.is_set():
            try:
                client_sock, addr = server.accept()
            except socket.timeout:
                continue
            except OSError as e:
                if stop_event.is_set():
                    break
                log(f"accept error: {e}")
                continue
            peer = f"{addr[0]}:{addr[1]}"
            log(f"client connected: {peer}")
            with clients_lock:
                clients.append(client_sock)
            t = threading.Thread(
                target=client_reader_thread,
                args=(client_sock, addr),
                name=f"client-{peer}",
                daemon=True,
            )
            t.start()
    finally:
        log("shutting down")
        stop_event.set()
        try:
            server.close()
        except Exception:
            pass
        with clients_lock:
            for c in clients:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    c.close()
                except Exception:
                    pass
            clients.clear()
        _close_serial()
        log("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
