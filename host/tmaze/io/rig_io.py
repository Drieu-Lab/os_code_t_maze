"""
rig_io.py - Thin TCP client for talking to rig_broker.py.

The broker (rig_broker.py) is the single owner of the Arduino Due's COM port.
GUIs and other tools open a RigClient instead of opening serial directly,
which lets several tools share the rig at once.

The wire protocol is identical to what the Due itself speaks: newline-
terminated ASCII lines in both directions. This module is therefore a
near-drop-in replacement for the prior pyserial reader/writer pattern.

Usage:
    client = RigClient(root, host="127.0.0.1", port=5555)
    client.on_line(handle_line)        # callback runs on the Tk main thread
    client.on_status(set_status_text)  # connection-state changes (optional)
    client.start()                     # raises on first-connect failure
    ...
    client.send("R10")                 # newline appended automatically
    ...
    client.stop()                      # call from on_close before destroy()
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional


class RigClient:
    """TCP client that exchanges newline-terminated ASCII with rig_broker.py.

    All registered callbacks fire on the Tk main thread via ``root.after``,
    so callers never need their own locking around Tk widgets.
    """

    RECONNECT_DELAY_S = 2.0
    RECV_BYTES = 4096

    def __init__(self, root, host: str = "127.0.0.1", port: int = 5555):
        self._root = root
        self._host = host
        self._port = port

        self._sock: Optional[socket.socket] = None
        self._sock_lock = threading.Lock()  # serializes writes
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._line_cb: Optional[Callable[[str], None]] = None
        # Optional receipt-stamped line callback (docs/decisions/0175): fired with
        # (recv_wall, recv_mono, line), where the stamps are captured on THIS reader
        # thread the moment the bytes arrive - before the Tk hop - so a consumer can
        # record when a line actually reached the host, not when Tk got round to it.
        # Takes precedence over ``_line_cb`` when set; ``_line_cb`` stays the plain
        # fallback so other clients (rig_logger, lick_tuner, ...) are unaffected.
        self._line_stamped_cb: Optional[Callable[[float, float, str], None]] = None
        self._status_cb: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------ API

    def on_line(self, cb: Callable[[str], None]) -> None:
        """Register a callback for each newline-terminated line received."""
        self._line_cb = cb

    def on_line_stamped(
        self, cb: Callable[[float, float, str], None]
    ) -> None:
        """Register a receipt-stamped line callback (docs/decisions/0175).

        Fires with ``(recv_wall, recv_mono, line)`` on the Tk main thread, where
        the stamps (``time.time()`` / ``time.monotonic()``) are taken on the
        reader thread the instant the bytes arrive - before the Tk hop, which can
        be delayed under GUI load. Use this over ``on_line`` when you need to know
        WHEN a line reached the host (e.g. to timestamp the serial capture at
        receipt rather than at decision time). Takes precedence over ``on_line``.
        """
        self._line_stamped_cb = cb

    def on_status(self, cb: Callable[[str], None]) -> None:
        """Register a callback for connection-state messages."""
        self._status_cb = cb

    def start(self) -> None:
        """Connect to the broker and start the background reader thread.

        Raises ConnectionError (or the underlying OSError) on initial failure
        so the caller can show a clean error dialog instead of silently
        retrying. Subsequent disconnects are recovered automatically.

        Clients are broker-only: there is no direct-serial fallback, so a
        first-connect failure is surfaced rather than worked around.
        see docs/decisions/0003
        """
        self._connect_once()  # may raise; we want this visible at startup
        self._thread = threading.Thread(
            target=self._reader_loop, name="RigClient-reader", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the reader thread to exit and close the socket."""
        self._stop.set()
        with self._sock_lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def send(self, cmd: str) -> bool:
        """Send a single command line to the broker (newline appended).

        Returns whether the line reached the wire: True on a successful
        ``sendall``, False when there is no socket (not connected / dropped) or
        ``sendall`` raised ``OSError`` (the status line still reports the failure).
        The truthful return lets a caller avoid logging a command as sent when it
        was not - e.g. the Phase 3 controller records a cue onset only when its
        send returned True (docs/decisions/0173)."""
        if not cmd.endswith("\n"):
            cmd = cmd + "\n"
        data = cmd.encode("utf-8", errors="ignore")
        with self._sock_lock:
            sock = self._sock
            if sock is None:
                return False
            try:
                sock.sendall(data)
            except OSError as e:
                self._fire_status(f"Send failed: {e}")
                return False
        return True

    # ------------------------------------------------------------- internals

    def _connect_once(self) -> None:
        sock = socket.create_connection((self._host, self._port), timeout=5.0)
        sock.settimeout(None)  # blocking reads after connect
        with self._sock_lock:
            self._sock = sock
        self._fire_status(f"Connected to broker {self._host}:{self._port}")

    def _reader_loop(self) -> None:
        buf = b""
        while not self._stop.is_set():
            with self._sock_lock:
                sock = self._sock
            if sock is None:
                if not self._try_reconnect():
                    continue
                buf = b""
                continue

            try:
                chunk = sock.recv(self.RECV_BYTES)
                # Receipt stamp: taken the instant recv() returns, i.e. when the
                # host actually has the bytes. Every line carved out of this chunk
                # shares it (they arrived together); a line split across chunks is
                # stamped when its terminating newline arrives (docs/decisions/0175).
                recv_wall = time.time()
                recv_mono = time.monotonic()
            except OSError as e:
                if self._stop.is_set():
                    return
                self._fire_status(f"Broker disconnected: {e}")
                self._drop_socket()
                continue

            if not chunk:
                # peer closed the connection
                if self._stop.is_set():
                    return
                self._fire_status("Broker disconnected, retrying...")
                self._drop_socket()
                continue

            buf += chunk
            while b"\n" in buf:
                raw_line, buf = buf.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="ignore").rstrip("\r")
                if line and (
                    self._line_stamped_cb is not None or self._line_cb is not None
                ):
                    self._dispatch_line(recv_wall, recv_mono, line)

    def _try_reconnect(self) -> bool:
        """Attempt one reconnect; return True on success."""
        # back off first so we don't hammer the broker
        for _ in range(int(self.RECONNECT_DELAY_S * 10)):
            if self._stop.is_set():
                return False
            time.sleep(0.1)
        try:
            self._connect_once()
            return True
        except OSError as e:
            self._fire_status(f"Reconnect failed: {e}")
            return False

    def _drop_socket(self) -> None:
        with self._sock_lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _dispatch_line(
        self, recv_wall: float, recv_mono: float, line: str
    ) -> None:
        """Hop one received line to the Tk thread, preferring the stamped callback.

        The receipt stamps travel with the line so the consumer sees when the
        bytes arrived (docs/decisions/0175), not when this ``after`` finally ran.
        """
        stamped = self._line_stamped_cb
        if stamped is not None:
            try:
                self._root.after(0, stamped, recv_wall, recv_mono, line)
            except RuntimeError:
                # root may be torn down already; safe to ignore
                pass
            return
        cb = self._line_cb
        if cb is None:
            return
        try:
            self._root.after(0, cb, line)
        except RuntimeError:
            pass

    def _fire_status(self, text: str) -> None:
        cb = self._status_cb
        if cb is None:
            return
        try:
            self._root.after(0, cb, text)
        except RuntimeError:
            pass
