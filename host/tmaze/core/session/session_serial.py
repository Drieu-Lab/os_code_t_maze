"""
session_serial.py - which raw serial lines go in the per-session serial log.

A pure predicate (protocol-only; no Tk/IO) deciding whether a firmware line
belongs in the per-session raw-signal capture the Session controller writes into
a session's ``serial/`` folder (see docs/decisions/0049). By the experimenter's
scope, that capture is the SENSOR + CENTRAL-PORT subset of the stream, not the
whole thing (the complete verbatim stream stays with ``rig_logger.py``):

  - every body-beam sensor edge ``S,<i>,<state>`` - the rat's position signal;
  - the CENTRAL reward-well activity: its lick edges ``L,<center>,<state>`` and
    the reward it delivers - ``REWARD,...`` (all reward is at the central port,
    see docs/decisions/0007) plus the central solenoid ack ``SOL,<center>,<sol>``;
  - the ``PONG`` session-start fiducial - the reply to the single ``PING`` the
    host sends at Start Session (see docs/decisions/0048/0062). It is NOT sensor
    data, but keeping it makes the bundle self-anchoring: the ``PONG`` line
    carries the firmware session ms (its own prefix) AND, via the per-line
    host-wall prefix the writer adds, the host wall time - one clean tie point
    that lets an offline tool map firmware-clock events onto the host timeline
    from inside the bundle rather than only the full logger (see docs/decisions/0124).

Everything else (other-port licks, cue echoes, MODE/handshake, ...) is left
to the full logger. Keeping this as a pure function makes the capture rule easy
to read and to test without a rig.

Headless tests: python -m unittest test_session_serial   (from host/)
"""

from __future__ import annotations

from typing import Optional

from tmaze.core import protocol


def is_sensor_or_central(info: protocol.LineInfo, *,
                         center_lick_index: Optional[int],
                         center_sol_port: int) -> bool:
    """True if the pre-parsed line is a body-beam edge or central-port
    lick/reward/solenoid. Reads the fields the shared parser already computed
    (docs/decisions/0056) instead of re-parsing.

    ``center_lick_index`` is the lick channel of the central well (from the rig
    pin map; ``None`` if unknown -> central licks are simply not captured), and
    ``center_sol_port`` is the solenoid port of the central well.
    """
    # Body-beam sensor edges: the rat's position signal (all beams).
    if info.sensor is not None:
        return True
    # Central-well lick edges (ignore the left/right choice-well licks).
    if (info.lick is not None and center_lick_index is not None
            and info.lick[0] == center_lick_index):
        return True
    # Reward delivery - all reward is at the central port (docs/decisions/0007).
    if info.reward is not None:
        return True
    # The central well's solenoid acks (drop other ports' valve-test acks).
    if info.sol is not None and info.sol[0] == center_sol_port:
        return True
    # The session-start fiducial: PONG ties the firmware clock to host wall time
    # inside the bundle (docs/decisions/0124). Not sensor data, but self-anchoring.
    if info.parsed is not None and info.parsed.type == "PONG":
        return True
    return False


def is_sensor_or_central_line(line: str, *, center_lick_index: Optional[int],
                              center_sol_port: int) -> bool:
    """Raw-string convenience wrapper over is_sensor_or_central() for callers
    that hold a line rather than a pre-parsed protocol.LineInfo."""
    return is_sensor_or_central(
        protocol.classify(line), center_lick_index=center_lick_index,
        center_sol_port=center_sol_port)
