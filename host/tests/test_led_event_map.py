"""test_led_event_map.py - the pixel side of the LED-map builder's event pass.

``tmaze.vision.led_event_map`` on synthetic frames: a beam with a bright and a
dim LED (two rows), a lick whose events also fire a bright reward LED, and the
reward itself. Checks that each channel's LEDs are found brightest first, that
the strong-first claim keeps the lick off the reward LED's pixel, and the bleed
fractions (docs/decisions/0196). Needs numpy + OpenCV; skips without them.
"""
from __future__ import annotations

import io
import unittest

try:
    import cv2  # noqa: F401
    import numpy as np
    from tmaze.vision import led_event_map as EM
except Exception:  # pragma: no cover
    np = EM = None

from tmaze.core.sync import event_frames as EF
from tmaze.core.sync.clockfit import ClockFit, ResidualStats
from tmaze.core.sync.led_map import DetectorParams
from tmaze.core.sync.spans import FIRMWARE_MS, Span

H, W, N = 48, 64, 400
FRAME_MS = 40.0
FIT = ClockFit(1.0, {0: 0.0}, 0, ResidualStats(0, 0.0, 0.0, 0.0, 0.0))
BEAM_A, BEAM_B = (12.0, 20.0), (23.0, 23.0)     # bright row / dim row, ~(+11, +3)
LICK = (40.0, 12.0)
REWARD = (46.0, 12.0)                           # 6 px from the lick LED, much brighter


def _span(ch, f0, f1):
    return Span(ch, 0.0, 0.0, f0 * FRAME_MS, f1 * FRAME_MS, 0, 0, 0, FIRMWARE_MS)


def _disk(img, xy, r, v):
    yy, xx = np.mgrid[0:H, 0:W]
    img[(xx - xy[0]) ** 2 + (yy - xy[1]) ** 2 <= r * r] += v


def _scene():
    """(active spans, frame generator): beam S2 broken on 8 events (its LEDs go
    DARK: lit 100 / 33 at rest), lick L0 on 8 events (its LED lights +60) each
    firing the reward (+150) too, plus 8 reward-only pulses."""
    beam = [(20 + 40 * i, 26 + 40 * i) for i in range(8)]
    lick = [(35 + 40 * i, 40 + 40 * i) for i in range(8)]
    solo = [(330 + 8 * i, 332 + 8 * i) for i in range(8)]
    active = {"S2=1": [_span("S2=1", a, b) for a, b in beam],
              "L0=1": [_span("L0=1", a, b) for a, b in lick],
              "SOL0.0": [_span("SOL0.0", a, b) for a, b in lick + solo]}
    rng = np.random.default_rng(0)

    def on(k, spans):
        return any(a <= k <= b for a, b in spans)

    def frames():
        for k in range(N):
            img = np.full((H, W), 40.0)
            if not on(k, beam):
                _disk(img, BEAM_A, 1.5, 100.0)
                _disk(img, BEAM_B, 1.5, 33.0)
            if on(k, lick):
                _disk(img, LICK, 1.5, 60.0)
            if on(k, lick + solo):
                _disk(img, REWARD, 1.5, 150.0)
            img += rng.normal(0, 1.0, img.shape)
            yield k, np.clip(img, 0, 255).astype(np.uint8)
    return active, frames


@unittest.skipIf(EM is None, "numpy/OpenCV not available")
class TestEventMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        active, frames = _scene()
        cls.p = EF.EventMapParams(before_frames=4, during_frames=3)
        cls.weights = EF.event_weights(active, FIT, [k * FRAME_MS for k in range(N)], cls.p)
        cls.comps = EM.accumulate(frames(), EF.frame_roles(cls.weights), (H, W))
        cls.inside = np.ones((H, W), bool)
        cls.peaks = EM.localise_all(cls.comps, {"S2=1": 2, "L0=1": 2, "SOL0.0": 1},
                                    cls.inside, cls.p)

    def _near(self, pk, xy, tol=1.0):
        return abs(pk.x - xy[0]) <= tol and abs(pk.y - xy[1]) <= tol

    def test_every_event_had_a_clean_window(self):
        self.assertEqual({ch: w.n_used for ch, w in self.weights.items()},
                         {"S2=1": 8, "L0=1": 8, "SOL0.0": 16})

    def test_beam_finds_both_rows_bright_first_and_dark_on_break(self):
        a, b = self.peaks["S2=1"]
        self.assertTrue(self._near(a, BEAM_A) and self._near(b, BEAM_B))
        self.assertGreater(a.magnitude, 2 * b.magnitude)
        self.assertEqual((a.sign, b.sign), (-1.0, -1.0))

    def test_strong_first_keeps_the_lick_off_the_reward_pixel(self):
        (sol,) = self.peaks["SOL0.0"]
        self.assertTrue(self._near(sol, REWARD))
        lick = self.peaks["L0=1"]
        self.assertTrue(self._near(lick[0], LICK))
        self.assertEqual(lick[0].sign, 1.0)
        # only one real lick LED in this scene: the second slot stays empty rather
        # than taking the reward's bloom
        self.assertTrue(all(not self._near(q, REWARD, 2.5) for q in lick))

    def test_without_the_claim_the_lick_would_peak_on_the_reward(self):
        none = np.zeros((H, W), bool)
        (top,), _ = EM.find_peaks(self.comps["L0=1"], 1, self.inside, none, self.p)
        self.assertTrue(self._near(top, REWARD))

    def test_bleed_is_a_fraction_of_the_leds_own_change(self):
        leds = [("S2_a", "S2=1", BEAM_A), ("L0_a", "L0=1", LICK), ("SOL0.0_a", "SOL0.0", REWARD)]
        bleed = EM.bleed_matrix(self.comps, leds, DetectorParams())
        # every lick fires the reward, so the reward LED moves on L0 events: half
        # of its own events are lick events, so about the same change as its own
        self.assertIn("SOL0.0_a", bleed["L0=1"])
        self.assertNotIn("S2=1", bleed)          # the beam LEDs are far from the rest
        for row in bleed.values():
            self.assertTrue(all(abs(v) >= 0.05 for v in row.values()))

    def test_progress_printer_reports_percent(self):
        out = io.StringIO()
        tick = EM.progress_printer("read", 10, stream=out, step_pct=50)
        for k in range(10):
            tick(k)
        text = out.getvalue()
        self.assertIn("read:  10% (1/10", text)
        self.assertIn("read: 100% (10/10", text)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
