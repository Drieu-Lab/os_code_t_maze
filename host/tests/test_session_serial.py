"""
test_session_serial.py - headless tests for the per-session serial helpers.

Since docs/decisions/0178 the capture is the full stream (``serial_<id>.csv``);
``is_sensor_or_central`` is kept as a READER helper that reproduces the pre-0178
SENSOR + CENTRAL-PORT view, and ``resolve_capture_path`` prefers the full file
and falls back to the legacy ``sensors_<id>.csv``. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_session_serial
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core import protocol
from tmaze.core.session import session_serial
from tmaze.core.session.session_serial import is_sensor_or_central, is_sensor_or_central_line

# Verified central-well mapping (docs/decisions/0022): center lick = L2; the
# central solenoid port = 2 ("Center / start").
CENTER_LICK = 2
CENTER_PORT = 2


def _keep(line: str, center_lick=CENTER_LICK) -> bool:
    return is_sensor_or_central_line(
        line, center_lick_index=center_lick, center_sol_port=CENTER_PORT)


class TestSensorLines(unittest.TestCase):
    def test_all_body_beam_edges_kept(self):
        self.assertTrue(_keep("1000,S,0,1"))
        self.assertTrue(_keep("1000,S,4,0"))


class TestCentralPort(unittest.TestCase):
    def test_central_lick_kept_other_licks_dropped(self):
        self.assertTrue(_keep("1200,L,2,1"))     # center well
        self.assertFalse(_keep("1200,L,0,1"))    # left choice well
        self.assertFalse(_keep("1200,L,1,0"))    # right choice well

    def test_reward_kept(self):
        self.assertTrue(_keep("1300,REWARD,CENTER,150"))
        self.assertTrue(_keep("1300,REWARD,2,MANUAL,150"))

    def test_central_solenoid_ack_kept_other_ports_dropped(self):
        self.assertTrue(_keep("1400,SOL,2,0,150"))   # central port
        self.assertFalse(_keep("1400,SOL,0,1"))      # left port valve test
        self.assertFalse(_keep("1400,SOL,1,2"))      # right port valve test

    def test_no_center_lick_index_skips_central_licks(self):
        # If the pin map didn't resolve a center lick, central licks aren't kept
        # (but sensors/reward still are).
        self.assertFalse(_keep("1200,L,2,1", center_lick=None))
        self.assertTrue(_keep("1000,S,0,1", center_lick=None))


class TestSessionStartFiducial(unittest.TestCase):
    """PONG (the reply to the host's one PING at Start Session) is kept as the
    in-bundle session-start fiducial - it ties firmware ms to host wall time
    (docs/decisions/0124). Only the well-formed "<ms>,PONG" is kept; a bare,
    prefix-less "PONG" is malformed and still dropped."""

    def test_ms_prefixed_pong_kept(self):
        self.assertTrue(_keep("1234,PONG"))

    def test_bare_pong_dropped_as_malformed(self):
        self.assertFalse(_keep("PONG"))


class TestDroppedLines(unittest.TestCase):
    def test_handshake_mode_cue_dropped(self):
        for line in ("0,ANIMAL,RAT600", "0,MODE,FREEROAM", "0,PROTO,6",
                     "1500,CUE,ON,CHOICE,8000", "1500,CUE,OFF"):
            self.assertFalse(_keep(line), line)

    def test_malformed_is_dropped(self):
        for line in ("", "garbage", ",,,", "abc,S,0,1"):
            self.assertFalse(_keep(line), line)


class TestInfoAndLineParity(unittest.TestCase):
    """The pre-parsed is_sensor_or_central(info) and the raw-line wrapper agree
    on every line (the wrapper just classifies once first; docs/decisions/0056)."""

    LINES = ("1000,S,0,1", "1200,L,2,1", "1200,L,0,1", "1300,REWARD,CENTER,150",
             "1400,SOL,2,0,150", "1400,SOL,0,1", "0,MODE,FREEROAM",
             "0,ANIMAL,RAT600", "1500,CUE,OFF", "1234,PONG", "PONG", "", "garbage")

    def test_parity(self):
        for line in self.LINES:
            info_based = is_sensor_or_central(
                protocol.classify(line), center_lick_index=CENTER_LICK,
                center_sol_port=CENTER_PORT)
            self.assertEqual(info_based, _keep(line), line)


class TestResolveCapturePath(unittest.TestCase):
    """resolve_capture_path prefers the full-stream serial_<id>.csv (0178) and
    falls back to the legacy filtered sensors_<id>.csv for older bundles."""

    def test_prefers_full_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            full = os.path.join(d, "serial_abc.csv")
            open(full, "w").close()
            open(os.path.join(d, "sensors_abc.csv"), "w").close()
            self.assertEqual(session_serial.resolve_capture_path(d, "abc"), full)

    def test_falls_back_to_legacy_when_only_it_exists(self):
        with tempfile.TemporaryDirectory() as d:
            legacy = os.path.join(d, "sensors_abc.csv")
            open(legacy, "w").close()
            self.assertEqual(session_serial.resolve_capture_path(d, "abc"), legacy)

    def test_returns_full_name_when_neither_exists(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(
                session_serial.resolve_capture_path(d, "abc"),
                os.path.join(d, "serial_abc.csv"))

    def test_capture_filename_is_the_full_stream_name(self):
        self.assertEqual(session_serial.capture_filename("600_x"), "serial_600_x.csv")


if __name__ == "__main__":
    unittest.main()
