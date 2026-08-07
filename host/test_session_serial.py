"""
test_session_serial.py - headless tests for the per-session serial-capture filter.

Checks that the SENSOR + CENTRAL-PORT subset is captured and everything else is
left to the full logger (see session_serial / docs/decisions/0049). Pure stdlib;
no hardware/Tk.

Run from host/:  python -m unittest test_session_serial
"""

from __future__ import annotations

import unittest

from tmaze.core import protocol
from session_serial import is_sensor_or_central, is_sensor_or_central_line

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


class TestDroppedLines(unittest.TestCase):
    def test_handshake_mode_cue_pong_dropped(self):
        for line in ("0,ANIMAL,RAT600", "0,MODE,FREEROAM", "0,PROTO,6",
                     "1500,CUE,ON,CHOICE,8000", "1500,CUE,OFF", "PONG"):
            self.assertFalse(_keep(line), line)

    def test_malformed_is_dropped(self):
        for line in ("", "garbage", ",,,", "abc,S,0,1"):
            self.assertFalse(_keep(line), line)


class TestInfoAndLineParity(unittest.TestCase):
    """The pre-parsed is_sensor_or_central(info) and the raw-line wrapper agree
    on every line (the wrapper just classifies once first; docs/decisions/0056)."""

    LINES = ("1000,S,0,1", "1200,L,2,1", "1200,L,0,1", "1300,REWARD,CENTER,150",
             "1400,SOL,2,0,150", "1400,SOL,0,1", "0,MODE,FREEROAM",
             "0,ANIMAL,RAT600", "1500,CUE,OFF", "", "garbage")

    def test_parity(self):
        for line in self.LINES:
            info_based = is_sensor_or_central(
                protocol.classify(line), center_lick_index=CENTER_LICK,
                center_sol_port=CENTER_PORT)
            self.assertEqual(info_based, _keep(line), line)


if __name__ == "__main__":
    unittest.main()
