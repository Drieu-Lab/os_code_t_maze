"""
test_rig_view.py - headless tests for the Rig-view maze illustration module.

Covers the pure/headless bits: the session-cap banner text helper (docs/
decisions/0190). Importing rig_view pulls in tkinter but never opens a display,
and the banner-text helper is a staticmethod that needs no canvas, so this runs
headless like the rest of the suite.

Run from host/:  python -m unittest test_rig_view
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3 import session_cap
from tmaze.ui.rig_view import RigView


class CapBannerTextTest(unittest.TestCase):
    def test_trials_limit(self):
        hit = {"reason": session_cap.REASON_TRIALS, "completed": 120,
               "max_trials": 120, "max_minutes": 45.0}
        self.assertEqual(
            RigView._cap_banner_text(hit),
            "CAP REACHED (120 trials) - remove rat, Stop session")

    def test_time_limit(self):
        hit = {"reason": session_cap.REASON_TIME, "completed": 88,
               "max_trials": 120, "max_minutes": 45.0}
        self.assertEqual(
            RigView._cap_banner_text(hit),
            f"CAP REACHED ({session_cap.mmss(45.0 * 60.0)} min) "
            "- remove rat, Stop session")


if __name__ == "__main__":
    unittest.main()
