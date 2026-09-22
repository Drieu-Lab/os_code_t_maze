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

Run as a module (needs the tmaze package installed - pip install -e . once per
machine; works from any directory):
    python -m tmaze.io.rig_broker
    python -m tmaze.io.rig_broker --port COM5

Dependencies: pyserial (already required by the original GUIs).
"""

from __future__ import annotations

import argparse
import queue
import signal
import socket
import sys
import threading
import time
from typing import List, Optional

try:
    import serial
except ImportError:
    # pyserial is required to actually OWN the COM port (the broker's day job), but
    # not for the TCP fan-out logic (Client/broadcast), which headless tests
    # exercise without a serial port. Import stays soft so those tests run in an
    # env without pyserial; open_serial() raises a clear error if it's really used.
    serial = None  # type: ignore[assignment]

from tmaze.core.protocol import leading_ms
from tmaze.core.serial_liveness import DEAD, PROBE, LivenessMonitor, LivenessParams

# ================= DEFAULTS =================
DEFAULT_PORT = "COM3"          # Due Programming port; see docs/decisions/0065
DEFAULT_BAUD = 115200          # a real UART rate on this port, not a formality
DEFAULT_HOST = "127.0.0.1"     # localhost only; safe default
DEFAULT_TCP_PORT = 5555
SERIAL_REOPEN_DELAY_S = 2.0
STATS_INTERVAL_S = 10.0
# Cap on a single serial write. pyserial's default (None) blocks forever, so a
# wedged Due froze every client's commands indefinitely - and silently, because
# the blocked write held serial_lock. see docs/decisions/0062
WRITE_TIMEOUT_S = 1.0
# Per-client outbound backlog cap (review finding R1, docs/decisions/0183). Each
# client drains its OWN bounded queue on its OWN writer thread, so broadcast() only
# ever appends - a slow or stuck client can never block the serial reader (and thus
# never back-pressure the Due into firing valves/TTLs late). A client whose backlog
# reaches this many lines is too slow to keep up and is dropped. At the rig's line
# rate (~tens/s of short lines) this is many seconds of buffer.
MAX_QUEUED_LINES = 2000
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
clients: List["Client"] = []

serial_lock = threading.Lock()  # serializes writes to the Due
ser: Optional[serial.Serial] = None

# Set by any thread that finds the port unusable; only serial_reader_thread acts
# on it. Keeping the close/reopen in that one thread avoids re-entering
# serial_lock from a writer that already holds it. see docs/decisions/0062
reopen_request = threading.Event()

# Decides when a quiet port gets probed and when it counts as dead. Touched only
# by serial_reader_thread, so it needs no lock. main() replaces it with the
# CLI-configured schedule before that thread starts. see docs/decisions/0062
liveness = LivenessMonitor()

# The Due's own clock, read off the "<ms>," prefix of the last line it sent.
# NOTE it is the firmware's SESSION clock (millis() - session.startMs), not
# uptime: it starts when setup() stops waiting for a host and restarts on a MODE
# switch. A lockup otherwise leaves no board-side timestamp at all, and the
# host's wall clock cannot supply one. Written only by serial_reader_thread; a failure
# logged from a client thread may read a value a moment stale, which costs
# nothing but a slightly old number in a log line. see docs/decisions/0064
last_ms: Optional[int] = None
last_ms_at = 0.0

# Lightweight throughput counters for the periodic stats line.
stats_lock = threading.Lock()
stats = {"from_due": 0, "to_due": 0, "probes": 0}


def log(msg: str) -> None:
    """Write a timestamped line to stdout."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ================= SERIAL =================
def open_serial(port: str, baud: int, reset: bool = True) -> "serial.Serial":
    """Open the Due's serial port.

    ``reset=True`` - the broker's FIRST open since it started: open the normal way,
    which asserts DTR. On the Programming port the ATmega16U2 turns that into a
    reset of the SAM3X, so we also wait out the reboot. That reboot re-emits the
    ``0,PROTO`` handshake, which is what sets the bundle's ``sensor_polarity``
    (docs/decisions/0164), and it starts the board from a known state.

    ``reset=False`` - every REOPEN while the broker stays up (a cable hiccup, a
    write-timeout recovery; the ADR-0065 reopen path): open with DTR held LOW so
    the Due is NOT reset. A reconnect then no longer reboots a session that is
    already running. The board keeping its clock across a DTR-low open was
    verified on the rig 2026-09-21 (no handshake, ``millis()`` kept counting).
    see docs/decisions/0194 (supersedes the accept-the-reset note in 0065)
    """
    if serial is None:
        raise RuntimeError("pyserial is not installed; the broker needs it to open "
                          "the Due's COM port (pip install pyserial)")
    if reset:
        s = serial.Serial(port, baud, timeout=0.1, write_timeout=WRITE_TIMEOUT_S)
        time.sleep(2.0)  # let the Due settle after the reset (matches existing GUIs)
    else:
        # Set DTR=False BEFORE opening: the reset is triggered by the DTR
        # transition at open time, so deasserting it afterwards would be too late.
        s = serial.Serial()
        s.port = port
        s.baudrate = baud
        s.timeout = 0.1
        s.write_timeout = WRITE_TIMEOUT_S
        s.dtr = False
        s.open()
    try:
        s.reset_input_buffer()
    except Exception:
        pass
    return s


def serial_reader_thread(port: str, baud: int) -> None:
    """Read lines from the Due and broadcast them to every connected client."""
    global ser, last_ms, last_ms_at
    # State for throttling a repeating open failure (see OPEN_FAIL_SUMMARY_S). The
    # retry interval itself is deliberately NOT backed off: after the operator
    # presses RESET the board must come back within a couple of seconds.
    fail_count = 0
    fail_msg = ""
    fail_logged_at = 0.0
    # Only the FIRST open since the broker started resets the Due (to get a clean
    # handshake + known state); every reopen after that leaves the running board
    # alone. see open_serial / docs/decisions/0194
    first_open = True
    while not stop_event.is_set():
        if ser is None:
            try:
                if not fail_count:
                    log(f"opening serial {port} @ {baud} ...")
                reset_this_open = first_open
                ser = open_serial(port, baud, reset=reset_this_open)
                first_open = False
                if fail_count:
                    log(f"serial recovered after {fail_count} failed attempts")
                    fail_count, fail_msg = 0, ""
                reopen_request.clear()
                liveness.reset(time.monotonic())
                last_ms = None  # the next line reports this board's uptime
                log("serial open (reset the Due)" if reset_this_open
                    else "serial open (no reset; left the running board alone)")
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

        ms = leading_ms(line)
        if ms is not None:
            if last_ms is None:
                # A few seconds on the Programming port, whose open resets the
                # board. A large value means we joined a session that was already
                # running - which is how the two clocks get separated, and what
                # the Native USB port used to do. see docs/decisions/0065
                log(f"firmware session clock at connect: {ms} ms")
            last_ms = ms
            last_ms_at = time.monotonic()

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
            f"{liveness.quiet_for(now):.0f}s despite probes{_last_line_note()}; "
            f"will reopen. If reopening fails, press RESET on the Due.")
        reopen_request.set()


def _last_line_note() -> str:
    """The Due's clock at its last word, for a failure log line (or "")."""
    ms = last_ms
    if ms is None:
        return ""
    return (f"; last firmware line: ms={ms} (session clock {ms / 1000.0:.1f} s)"
            f", {time.monotonic() - last_ms_at:.1f} s ago")


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
class Client:
    """One connected TCP client: a socket plus a BOUNDED outbound queue drained by
    its OWN writer thread (review finding R1, docs/decisions/0183).

    The point: ``broadcast`` (which runs on the serial-reader thread) only ever
    APPENDS to this queue - it never calls ``sendall``. So a slow or stuck client
    stalls only its own queue/thread, never the serial reader. A client that falls
    ``MAX_QUEUED_LINES`` behind is dropped rather than allowed to back-pressure the
    whole rig.
    """

    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.peer = f"{addr[0]}:{addr[1]}"
        self.out: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=MAX_QUEUED_LINES)
        self._close_lock = threading.Lock()
        self._closing = False
        self.writer = threading.Thread(
            target=self._writer_loop, name=f"client-writer-{self.peer}",
            daemon=True)

    def start(self) -> None:
        self.writer.start()

    def enqueue(self, payload: bytes) -> bool:
        """Queue one line to send. Returns False if the backlog is full (the client
        is too slow) - the caller then drops it. Never blocks."""
        try:
            self.out.put_nowait(payload)
            return True
        except queue.Full:
            return False

    def backlog(self) -> int:
        return self.out.qsize()

    def _writer_loop(self) -> None:
        """Drain the queue with blocking ``sendall`` - on THIS thread, so a stuck
        socket blocks only here. A send failure drops the client."""
        while not stop_event.is_set():
            try:
                payload = self.out.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:          # shutdown sentinel from close()
                break
            try:
                self.sock.sendall(payload)
            except Exception:
                drop_client(self, "send failed")
                return

    def close(self) -> None:
        """Idempotently wake the writer and close the socket."""
        with self._close_lock:
            if self._closing:
                return
            self._closing = True
        try:
            self.out.put_nowait(None)    # wake a writer waiting in get()
        except queue.Full:
            pass                          # sock.close() below unblocks a stuck send
        try:
            self.sock.close()
        except Exception:
            pass


def drop_client(client: "Client", reason: str) -> None:
    """Remove ``client`` from the fan-out and close it. Idempotent and safe to call
    from any thread (broadcast overflow, writer send-failure, reader disconnect);
    only the caller that actually removes it logs."""
    removed = False
    with clients_lock:
        try:
            clients.remove(client)
            removed = True
        except ValueError:
            pass
    client.close()
    if removed:
        log(f"client dropped ({reason}): {client.peer} (backlog {client.backlog()})")


def broadcast(line: str) -> None:
    """ENQUEUE ``line + "\\n"`` to every client's outbound queue - never sends here,
    so the serial reader is never blocked by a slow client. A client whose queue is
    full is dropped (R1, docs/decisions/0183)."""
    payload = (line + "\n").encode("utf-8", errors="ignore")
    overflowed: List[Client] = []
    with clients_lock:
        for c in clients:
            if not c.enqueue(payload):
                overflowed.append(c)
    for c in overflowed:                  # drop OUTSIDE the lock (it re-acquires)
        drop_client(c, "too slow: outbound queue full")


def client_reader_thread(client: "Client") -> None:
    """Forward each newline-terminated command from the client to the Due."""
    peer = client.peer
    buf = b""
    try:
        while not stop_event.is_set():
            try:
                chunk = client.sock.recv(4096)
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
            already_gone = client not in clients
            try:
                clients.remove(client)
            except ValueError:
                pass
        client.close()
        # If it was already removed it was dropped for cause (logged there);
        # avoid a duplicate line for the same client.
        if not already_gone:
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
            log(f"serial write failed forwarding {cmd!r} from {source}: "
                f"{e}{_last_line_note()}")
            reopen_request.set()
            return False
    return True


def _forward_to_due(cmd: str, peer: str) -> None:
    if _write_to_due(cmd, peer):
        with stats_lock:
            stats["to_due"] += 1


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
            max_backlog = max((c.backlog() for c in clients), default=0)
        # Probes only appear once the port has gone quiet, so a non-zero count in
        # the log is the timestamp of when trouble started.
        probe_note = f"  probes={probes}" if probes else ""
        # A non-zero max_backlog means a client is falling behind the fan-out (R1);
        # it's dropped at MAX_QUEUED_LINES, so this is the early warning.
        backlog_note = (f"  max_backlog={max_backlog}/{MAX_QUEUED_LINES}"
                        if max_backlog else "")
        log(f"stats: clients={n}  from_due={from_due}/{STATS_INTERVAL_S:.0f}s  "
            f"to_due={to_due}/{STATS_INTERVAL_S:.0f}s{probe_note}{backlog_note}")


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
    # Liveness-probe schedule (see docs/decisions/0062). Defaults suit a normal
    # run; a faster probe is a diagnostic tool - it pins the moment of a lockup
    # more tightly, and separates a time-triggered failure from a
    # message-count-triggered one.
    d = LivenessParams()
    p.add_argument("--probe-quiet", type=float, default=d.quiet_probe_s,
                   metavar="S",
                   help="seconds of silence before the first STATUS probe "
                        "(default %(default)s)")
    p.add_argument("--probe-interval", type=float, default=d.probe_interval_s,
                   metavar="S",
                   help="seconds between repeat probes (default %(default)s)")
    p.add_argument("--probe-dead", type=float, default=d.dead_after_s,
                   metavar="S",
                   help="seconds of unbroken silence before the port is called "
                        "dead (default %(default)s)")
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
    global liveness
    args = parse_args()
    install_signal_handlers()

    try:
        params = LivenessParams(quiet_probe_s=args.probe_quiet,
                                probe_interval_s=args.probe_interval,
                                dead_after_s=args.probe_dead)
    except ValueError as e:
        log(f"bad probe schedule: {e}")
        return 2
    liveness = LivenessMonitor(params)

    log(f"rig_broker starting (serial={args.port}@{args.baud}, "
        f"tcp={args.host}:{args.tcp_port})")
    # Logged every run, not just when overridden: a log pasted into a bug report
    # has to say which schedule produced it.
    log(f"liveness probe: after {params.quiet_probe_s:g}s quiet, "
        f"every {params.probe_interval_s:g}s, dead at {params.dead_after_s:g}s")

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
            client = Client(client_sock, addr)
            with clients_lock:
                clients.append(client)
            client.start()                 # its own outbound writer thread (R1)
            t = threading.Thread(
                target=client_reader_thread,
                args=(client,),
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
            current = list(clients)
            clients.clear()
        for c in current:
            try:
                c.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            c.close()                      # wakes + closes its writer thread
        _close_serial()
        log("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
