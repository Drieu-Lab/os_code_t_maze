"""
test_serial_liveness.py - headless unit tests for the pure liveness core
(serial_liveness.py). No hardware, no Tk. See docs/decisions/0062.

Time is injected, so these run instantly and cannot flake: every case walks a
hand-written clock rather than sleeping.

Run from host/:
    python -m unittest test_serial_liveness
"""

from __future__ import annotations

import unittest

from serial_liveness import (DEAD, OK, PROBE, LivenessMonitor, LivenessParams)


def _fresh(params=None, t0=100.0):
    # t0 is deliberately non-zero: the monitor must key off elapsed time, not
    # treat 0.0 as "never heard anything".
    m = LivenessMonitor(params)
    m.reset(t0)
    return m


class TestProbeSchedule(unittest.TestCase):
    def test_short_quiet_is_ok(self):
        m = _fresh()
        # A resting rat breaks no beams; brief quiet must stay silent.
        for dt in (0.0, 0.1, 5.0, 9.9):
            self.assertEqual(m.step(100.0 + dt), OK, f"at +{dt}s")

    def test_first_probe_at_quiet_threshold(self):
        m = _fresh()
        self.assertEqual(m.step(109.9), OK)
        self.assertEqual(m.step(110.0), PROBE)

    def test_probe_repeats_on_interval_only(self):
        m = _fresh()
        self.assertEqual(m.step(110.0), PROBE)
        # Reads keep arriving ~10x/s between probes; they must not each probe.
        self.assertEqual(m.step(110.1), OK)
        self.assertEqual(m.step(114.9), OK)
        self.assertEqual(m.step(115.0), PROBE)
        self.assertEqual(m.step(119.9), OK)
        self.assertEqual(m.step(120.0), PROBE)

    def test_three_probes_before_death(self):
        m = _fresh()
        probes = [t for t in _tenth_second_ticks(100.0, 125.0)
                  if m.step(t) == PROBE]
        self.assertEqual(probes, [110.0, 115.0, 120.0])


class TestDeath(unittest.TestCase):
    def test_dead_at_threshold(self):
        m = _fresh()
        for t in (110.0, 115.0, 120.0):
            self.assertEqual(m.step(t), PROBE)  # three probes go unanswered
        self.assertEqual(m.step(124.9), OK)
        self.assertEqual(m.step(125.0), DEAD)

    def test_dead_is_sticky_until_reset(self):
        # The reader thread acts on the next pass, but until it does the verdict
        # must not flip back to ok.
        m = _fresh()
        self.assertEqual(m.step(130.0), DEAD)
        self.assertEqual(m.step(200.0), DEAD)
        m.reset(200.0)
        self.assertEqual(m.step(200.1), OK)

    def test_reset_after_reopen_does_not_inherit_old_silence(self):
        m = _fresh()
        self.assertEqual(m.step(130.0), DEAD)
        m.reset(131.0)
        self.assertEqual(m.step(140.0), OK)     # only 9 s of new quiet
        self.assertEqual(m.step(141.0), PROBE)  # 10 s


class TestTrafficResets(unittest.TestCase):
    def test_any_line_clears_the_quiet_clock(self):
        m = _fresh()
        self.assertEqual(m.step(110.0), PROBE)
        m.note_rx(112.0)  # a probe reply, or just a beam break
        self.assertEqual(m.step(121.0), OK)     # 9 s since that line
        self.assertEqual(m.step(122.0), PROBE)  # 10 s

    def test_steady_traffic_never_probes(self):
        m = _fresh()
        t = 100.0
        for _ in range(500):
            t += 1.0
            m.note_rx(t)
            self.assertEqual(m.step(t + 0.5), OK)

    def test_traffic_rearms_the_probe_interval(self):
        # After a line, the next probe is a full quiet_probe_s later - it must not
        # fire early just because a probe went out shortly before.
        m = _fresh()
        self.assertEqual(m.step(110.0), PROBE)
        m.note_rx(110.5)
        self.assertEqual(m.step(115.0), OK)
        self.assertEqual(m.step(120.5), PROBE)

    def test_quiet_for_reports_elapsed_silence(self):
        m = _fresh()
        m.note_rx(150.0)
        self.assertAlmostEqual(m.quiet_for(157.5), 7.5)


class TestParams(unittest.TestCase):
    def test_custom_params_are_honoured(self):
        m = _fresh(LivenessParams(quiet_probe_s=2.0, probe_interval_s=1.0,
                                  dead_after_s=4.0))
        self.assertEqual(m.step(101.9), OK)
        self.assertEqual(m.step(102.0), PROBE)
        self.assertEqual(m.step(103.0), PROBE)
        self.assertEqual(m.step(104.0), DEAD)

    def test_defaults_match_the_documented_schedule(self):
        p = LivenessParams()
        self.assertEqual((p.quiet_probe_s, p.probe_interval_s, p.dead_after_s),
                         (10.0, 5.0, 25.0))


def _tenth_second_ticks(start, end):
    """The reader thread's ~10 Hz wakeups, without float drift."""
    return [start + n / 10.0 for n in range(int((end - start) * 10) + 1)]


if __name__ == "__main__":
    unittest.main()
