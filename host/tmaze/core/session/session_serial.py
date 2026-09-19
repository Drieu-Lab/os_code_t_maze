"""
session_serial.py - the per-session serial capture: naming + a filtered-view helper.

Since docs/decisions/0178 the Session controller writes the COMPLETE verbatim
serial stream it receives between Start and Stop into a session's ``serial/``
folder as ``serial_<id>.csv`` (host-receipt-stamped, docs/decisions/0175). Before
that (docs/decisions/0049) it wrote only a SENSOR + CENTRAL-PORT subset to
``sensors_<id>.csv`` and left the full stream to ``rig_logger.py``.

Two things live here, both pure (protocol-only; no Tk/IO):

- ``capture_filename`` / ``resolve_capture_path`` - the on-disk name of that
  capture, and a reader-side resolver that prefers the full ``serial_<id>.csv``
  and falls back to the legacy ``sensors_<id>.csv`` so tools read either.
- ``is_sensor_or_central`` - the old capture predicate, kept as a READER helper:
  a consumer that wants only the SENSOR + CENTRAL-PORT view of a full capture
  (the pre-0178 subset) can filter with it. It selects:

  - every body-beam sensor edge ``S,<i>,<state>`` - the rat's position signal;
  - the CENTRAL reward-well activity: its lick edges ``L,<center>,<state>`` and
    the reward it delivers - ``REWARD,...`` (all reward is at the central port,
    see docs/decisions/0007) plus the central solenoid ack ``SOL,<center>,<sol>``;
  - the ``PONG`` session-start fiducial - the reply to the single ``PING`` the
    host sends at Start Session (see docs/decisions/0048/0062). It is NOT sensor
    data, but it is a clean firmware-clock<->host-wall tie point (docs/decisions/0124).

Everything else (other-port licks, cue echoes, MODE/handshake, ...) is what the
filtered view drops - and exactly what the full capture now keeps.

Headless tests: python -m unittest test_session_serial   (from host/)
"""

from __future__ import annotations

import os
from typing import Optional

from tmaze.core import protocol


def capture_filename(session_id: str) -> str:
    """Name of a session's serial capture file (the full stream, docs/decisions/0178)."""
    return f"serial_{session_id}.csv"


def resolve_capture_path(serial_dir: str, session_id: str) -> str:
    """Path to a bundle's serial capture within its ``serial/`` folder.

    Prefers the full-stream ``serial_<id>.csv`` (docs/decisions/0178) when it
    exists, else falls back to the pre-0178 filtered ``sensors_<id>.csv`` so a
    reader works on old and new bundles alike. Returns the full-stream path when
    neither exists (the caller's open then raises with the current-format name).
    """
    full = os.path.join(serial_dir, capture_filename(session_id))
    if os.path.exists(full):
        return full
    legacy = os.path.join(serial_dir, f"sensors_{session_id}.csv")
    if os.path.exists(legacy):
        return legacy
    return full


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
