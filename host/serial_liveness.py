"""
serial_liveness.py - the pure core deciding when a quiet serial port should be
probed and when it should be declared dead (see docs/decisions/0062).

The problem it solves: when the Arduino Due's loop() wedges mid-session, the port
stays open and readline() simply returns empty instead of raising, so the broker
kept relaying nothing for 17 minutes without a single log line. Plain silence is
not enough to call it dead either - the broker log shows over five minutes of
legitimate quiet on a healthy rig, because a resting rat breaks no beams. So the
host asks: after a stretch of quiet it sends STATUS (a liveness probe - see
docs/glossary.md) and expects an answer. Silence that survives several probes is
a dead port.

This module owns only the TIMING decision. It is a stateful PURE core (stdlib
only, no serial/Tk/threads) like maze_state.py / cue_gate.py: time is INJECTED as
a ``now`` argument rather than read from the clock, so the tests are exact and
instant. The thin shell (rig_broker.serial_reader_thread) turns a "probe" verdict
into a STATUS write and a "dead" verdict into a port reopen.

Headless tests: python -m unittest test_serial_liveness

Thread-safety: driven single-threaded (the broker's serial reader thread), like
the other cores; it takes no locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# step() verdicts.
OK = "ok"        # nothing to do
PROBE = "probe"  # send a STATUS now
DEAD = "dead"    # the port has stopped answering; cycle it


@dataclass(frozen=True)
class LivenessParams:
    """Tunables for the probe schedule.

    With the defaults, quiet shorter than ``quiet_probe_s`` is ignored, then
    probes go out at 10 s, 15 s and 20 s of unbroken silence, and the port is
    declared dead at 25 s. Three unanswered probes is deliberate: one lost byte
    or a momentarily busy host must not condemn a working port, while 25 s is
    still far shorter than the minutes-long freezes this replaces.

    Sane values keep ``quiet_probe_s`` < ``dead_after_s`` and
    ``probe_interval_s`` > 0.
    """
    quiet_probe_s: float = 10.0
    probe_interval_s: float = 5.0
    dead_after_s: float = 25.0


class LivenessMonitor:
    """Tracks how long the port has been silent and schedules probes.

    Feed it every received line via :meth:`note_rx` and call :meth:`step` when a
    read comes back empty. Both take the current time in seconds (any monotonic
    source; the caller's choice of clock is not the core's business).
    """

    def __init__(self, params: Optional[LivenessParams] = None) -> None:
        self._p = params or LivenessParams()
        self._last_rx = 0.0
        self._last_probe: Optional[float] = None

    @property
    def params(self) -> LivenessParams:
        return self._p

    def reset(self, now: float) -> None:
        """Start the clock fresh - call after every successful port open.

        Without this, a monitor carrying pre-reopen silence would condemn the new
        port on its first quiet read.
        """
        self._last_rx = now
        self._last_probe = None

    def note_rx(self, now: float) -> None:
        """Record that the Due said something. Any line counts as proof of life.

        A probe reply is not distinguished from ordinary sensor traffic: both mean
        the firmware's loop() is still running, which is the only question here.
        """
        self._last_rx = now
        self._last_probe = None

    def quiet_for(self, now: float) -> float:
        """Seconds since the last received line (for logging)."""
        return now - self._last_rx

    def step(self, now: float) -> str:
        """Decide what the quiet port needs right now.

        DEAD is checked first and is sticky: once the silence passes
        ``dead_after_s`` every call keeps saying DEAD until :meth:`reset` or
        :meth:`note_rx`. The caller is expected to cycle the port and reset.
        """
        quiet = now - self._last_rx
        if quiet >= self._p.dead_after_s:
            return DEAD
        if quiet >= self._p.quiet_probe_s and (
                self._last_probe is None
                or now - self._last_probe >= self._p.probe_interval_s):
            self._last_probe = now
            return PROBE
        return OK
