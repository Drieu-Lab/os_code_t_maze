"""
test_rig_broker.py - the broker's per-client fan-out (docs/decisions/0183, R1).

The one property that matters: ``broadcast`` must never block on a slow client.
Each client owns a BOUNDED outbound queue drained by its own writer thread, so
``broadcast`` only appends; a client that falls ``MAX_QUEUED_LINES`` behind is
dropped instead of back-pressuring the serial reader (which would otherwise stall
the Due into firing valves/TTLs late).

These use fake sockets (no real TCP), so they're headless and fast. The
never-draining socket stands in for a stuck/slow client.

Run from host/:  python -m unittest tests.test_rig_broker
"""

from __future__ import annotations

import threading
import time
import types
import unittest
from unittest import mock

from tmaze.io import rig_broker


class _StuckSocket:
    """A socket whose ``sendall`` blocks forever - a client that never drains."""

    def __init__(self) -> None:
        self._release = threading.Event()
        self.closed = False

    def sendall(self, _payload) -> None:
        self._release.wait()             # block until close() lets it go

    def close(self) -> None:
        self.closed = True
        self._release.set()              # unblock any in-flight sendall


class _CountingSocket:
    """A socket that records everything sent, draining instantly."""

    def __init__(self) -> None:
        self.sent = []
        self.closed = False

    def sendall(self, payload) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self.closed = True


def _addr(port: int):
    return ("127.0.0.1", port)


class RigBrokerFanoutTest(unittest.TestCase):
    def setUp(self):
        # Isolate global broker state per test.
        rig_broker.stop_event.clear()
        with rig_broker.clients_lock:
            rig_broker.clients.clear()

    def tearDown(self):
        rig_broker.stop_event.set()      # let any writer threads exit
        with rig_broker.clients_lock:
            for c in list(rig_broker.clients):
                c.close()
            rig_broker.clients.clear()
        time.sleep(0.05)
        rig_broker.stop_event.clear()

    def _add(self, sock, port):
        client = rig_broker.Client(sock, _addr(port))
        with rig_broker.clients_lock:
            rig_broker.clients.append(client)
        client.start()
        return client

    def test_broadcast_reaches_a_draining_client(self):
        sock = _CountingSocket()
        self._add(sock, 5001)
        rig_broker.broadcast("100,S,0,1")
        # The writer thread delivers asynchronously; give it a moment.
        deadline = time.monotonic() + 1.0
        while not sock.sent and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(sock.sent, [b"100,S,0,1\n"])

    def test_slow_client_is_dropped_and_broadcast_never_blocks(self):
        stuck = _StuckSocket()
        self._add(stuck, 5002)

        # The writer grabs the first line and blocks forever in sendall; every
        # subsequent line piles up until the queue overflows and the client is
        # dropped. broadcast must stay fast (non-blocking) the whole time.
        for i in range(rig_broker.MAX_QUEUED_LINES + 5):
            start = time.monotonic()
            rig_broker.broadcast(f"{i},S,0,1")
            self.assertLess(time.monotonic() - start, 0.5,
                            "broadcast blocked on a slow client")

        with rig_broker.clients_lock:
            surviving = list(rig_broker.clients)
        self.assertEqual(surviving, [], "the stuck client should have been dropped")
        self.assertTrue(stuck.closed)

    def test_healthy_client_still_served_after_a_drop(self):
        # A drop of one client must not affect a healthy one added afterwards.
        stuck = _StuckSocket()
        stuck_client = self._add(stuck, 5005)
        rig_broker.drop_client(stuck_client, "test")

        good = _CountingSocket()
        self._add(good, 5006)
        rig_broker.broadcast("200,L,0,1")
        deadline = time.monotonic() + 1.0
        while not good.sent and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(good.sent, [b"200,L,0,1\n"])

    def test_drop_client_is_idempotent(self):
        sock = _CountingSocket()
        client = self._add(sock, 5004)
        rig_broker.drop_client(client, "test")
        rig_broker.drop_client(client, "test again")  # no crash, no double-remove
        with rig_broker.clients_lock:
            self.assertNotIn(client, rig_broker.clients)
        self.assertTrue(sock.closed)


class _FakeSerial:
    """Records how open_serial constructs and configures the port (no hardware)."""

    def __init__(self, port=None, baud=None, timeout=None, write_timeout=None):
        self.port = port
        self.baudrate = baud
        self.timeout = timeout
        self.write_timeout = write_timeout
        self.dtr = True            # pyserial default: DTR asserted (would reset)
        self.opened = port is not None   # constructing WITH a port opens immediately
        self.input_reset = False

    def open(self):
        self.opened = True

    def reset_input_buffer(self):
        self.input_reset = True


class OpenSerialResetTest(unittest.TestCase):
    """docs/decisions/0194: first open resets the Due; reopens leave it running."""

    def _open(self, reset):
        made = {}

        def _factory(*a, **k):
            s = _FakeSerial(*a, **k)
            made["s"] = s
            return s

        fake_mod = types.SimpleNamespace(Serial=_factory)
        with mock.patch.object(rig_broker, "serial", fake_mod), \
             mock.patch.object(rig_broker.time, "sleep") as slept:
            s = rig_broker.open_serial("COM3", 115200, reset=reset)
        return s, slept

    def test_first_open_resets_and_settles(self):
        # reset=True keeps the DTR-asserting open (Due reboots) and waits it out,
        # so the fresh 0,PROTO handshake is seen.
        s, slept = self._open(reset=True)
        self.assertTrue(s.dtr, "reset open must leave DTR asserted")
        self.assertTrue(s.opened)
        self.assertTrue(s.input_reset)
        slept.assert_called_once_with(2.0)

    def test_reopen_does_not_reset_or_settle(self):
        # reset=False holds DTR low BEFORE opening, so a reconnect doesn't reboot
        # a running session; and there is no reboot to wait out.
        s, slept = self._open(reset=False)
        self.assertFalse(s.dtr, "reopen must deassert DTR to avoid a reset")
        self.assertTrue(s.opened)
        self.assertTrue(s.input_reset)
        slept.assert_not_called()


if __name__ == "__main__":
    unittest.main()
