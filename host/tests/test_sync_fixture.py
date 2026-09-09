"""test_sync_fixture.py - the sync core on a REAL excerpt of a recorded session.

Fixture: tests/fixtures/sync_excerpt_600_20260903_134552.json - five minutes
(min 8-13) of an H.264 session: the verbatim serial rows, the lit/dark spans
of the 11 blinking board LEDs (as tmaze.core.sync.spans produced them from the
per-frame traces), and one real per-frame trace (the reward LED). It guards
against real-log quirks synthetic data would never contain - the inverted
beam polarity, sensor chatter, lick bursts - and pins the numbers the
feasibility spike measured (design doc section 3.7) so a regression in the
matcher or the fit is caught with real data.

Run (from host/):  python -m unittest tests.test_sync_fixture
"""
from __future__ import annotations

import json
import pathlib
import unittest

from tmaze.core.sync import clockfit, matching, pipeline, spans as S

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sync_excerpt_600_20260903_134552.json"


def _span(ch: str, row) -> S.Span:
    h0, h1, c0, c1, f0, f1 = row
    return S.Span(ch, h0, h1, float(c0), float(c1), f0, f1, 0, 1e-9)   # camera ticks are ns


class TestRealExcerpt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
        rows = S.parse_serial_csv(cls.fx["serial_rows"])
        cls.events = S.serial_spans(rows)
        cls.blobs = [
            pipeline.BlobSpans(b["blob"],
                               lit=[_span(f"blob{b['blob']}", r) for r in b["lit"]],
                               dark=[_span(f"blob{b['blob']}", r) for r in b["dark"]])
            for b in cls.fx["blobs"]
        ]
        cls.res = pipeline.analyze(cls.blobs, cls.events)

    # ---- the serial log itself -------------------------------------------
    def test_active_states_are_the_brief_ones(self):
        """Verifies the hard-coded sensor polarity (spans.BEAM_ACTIVE_STATE and
        LICK_ACTIVE_STATE, both "0"; PROTOCOL.md `S`/`L` rows; FW-0058) against
        a real log. A sensor's active state is the brief one: a beam crossing or
        a tongue contact lasts well under two seconds, while the resting state
        runs for tens of seconds (full session: `L2=1` up to 257 s, `L2=0` never
        above 0.3 s). Sensor chatter splits the resting state into many short
        pieces too, so compare the LONGEST spans, not medians."""
        for sensor in ("S2", "S3", "S4", "S5", "L2"):
            longest_1 = max(s.duration for s in self.events[f"{sensor}=1"])
            longest_0 = max(s.duration for s in self.events[f"{sensor}=0"])
            self.assertGreater(longest_1, 10.0, sensor)
            self.assertLess(longest_0, 2.0, sensor)
            self.assertTrue(S.is_active_channel(f"{sensor}=0"), sensor)
            self.assertFalse(S.is_active_channel(f"{sensor}=1"), sensor)

    def test_single_firmware_segment(self):
        self.assertEqual({s.segment for v in self.events.values() for s in v}, {0})

    # ---- bindings ---------------------------------------------------------
    def test_every_led_binds_to_the_expected_channel(self):
        got = {k: (b.channel, b.polarity) for k, b in self.res.bindings.items()}
        want = {
            0: ("SOL2.1", matching.LIT),                       # reward valve indicator
            1: ("L2=0", matching.LIT), 2: ("L2=0", matching.LIT),     # lick detector pair: lit on contact
            3: ("S2=0", matching.DARK), 4: ("S2=0", matching.DARK),
            7: ("S3=0", matching.DARK), 8: ("S3=0", matching.DARK),
            5: ("S5=0", matching.DARK), 6: ("S5=0", matching.DARK),
            9: ("S4=0", matching.DARK), 10: ("S4=0", matching.DARK),
        }
        self.assertEqual(got, want)
        self.assertTrue(all(v == pipeline.MATCHED for v in self.res.blob_verdicts.values()))
        # Resting states never appear as candidates.
        for cands in self.res.candidates.values():
            self.assertTrue(all(S.is_active_channel(c.channel) for c in cands))
        # The centre-beam LEDs also overlap 7 of 8 reward pulses (the reward
        # fires as the rat crosses); that coincidence is present as a runner-up
        # but rejected because the LED relights before the pulse ends.
        for k in (7, 8):
            sol = [c for c in self.res.candidates[k] if c.channel == "SOL2.1"][0]
            self.assertGreaterEqual(sol.score, 0.8)
            self.assertFalse(matching.lag_plausible(sol))
            self.assertLess(sol.end_lag_s, -0.1)

    def test_binding_quality(self):
        sol = self.res.bindings[0]
        self.assertGreaterEqual(sol.n_matched, 9)
        self.assertGreater(sol.score, 0.9)
        self.assertAlmostEqual(sol.lag_s, 0.031, delta=0.015)          # ON edge: ~+32 ms
        self.assertLess(sol.lag_mad_s, 0.010)
        for k in (1, 2):
            lick = self.res.bindings[k]
            self.assertGreaterEqual(lick.n_matched, 150, k)
            # Most blinks are a lick; the rest are two contacts the camera saw
            # as one (a release under two frames is invisible to it).
            self.assertGreater(lick.score, 0.6, k)
            self.assertLess(lick.recall, 0.5, k)                         # the camera misses most licks
            self.assertAlmostEqual(lick.lag_s, 0.040, delta=0.02, msg=k)  # ON edge
            self.assertLess(lick.lag_mad_s, 0.02, k)
        for k in (3, 4, 5, 6, 7, 8, 9, 10):
            b = self.res.bindings[k]
            self.assertGreaterEqual(b.score, 0.75, k)         # 5-min excerpt: 6-8 of 7-8 LED spans
            self.assertAlmostEqual(b.score, b.n_matched / b.n_led, msg=k)   # score = precision
            # OFF edge: ~+65-90 ms; chatter merged into a crossing puts its
            # serial start before the LED visibly reacts (blob 7: 98 ms).
            self.assertAlmostEqual(b.lag_s, 0.080, delta=0.03, msg=k)
            self.assertTrue(matching.lag_plausible(b), k)

    # ---- the fit ----------------------------------------------------------
    def test_clock_fit_matches_the_spike(self):
        fit = self.res.fit
        self.assertIsNotNone(fit)
        self.assertEqual(self.res.verdict, clockfit.VERDICT_OK)
        self.assertGreaterEqual(fit.n_pairs, 60)
        # Camera ticks are ns: rate ~ 1e6 per firmware ms, within 1e-4 of unity
        # over a 5-minute excerpt (the full session gave 1.000027).
        self.assertAlmostEqual(fit.rate / 1e6, 1.0, delta=1e-4)
        # The offset is anchored on ON edges; OFF edges sit ~one frame later.
        self.assertLess(abs(fit.per_edge_offset_ms[clockfit.ON]), 5.0)
        self.assertAlmostEqual(fit.per_edge_offset_ms[clockfit.OFF] - fit.per_edge_offset_ms[clockfit.ON],
                               40.0, delta=25.0)
        self.assertLess(fit.residuals.mad_ms, 30.0)
        # ON and OFF edges mixed (one frame apart) plus frame quantisation and
        # a few chatter-shifted edges: the bulk sits within ~3 frames. Measured
        # 111 ms on this excerpt at the 200 ms tolerance.
        self.assertLess(fit.residuals.p95_ms - fit.residuals.p5_ms, 130.0)
        self.assertGreater(fit.host_lag_median_s, 0.02)
        self.assertLess(fit.host_lag_median_s, 0.10)
        self.assertEqual(fit.host_steps, [])

    def test_licks_are_reported_but_not_fitted(self):
        self.assertIn(1, self.res.bindings)
        self.assertFalse(any(S.channel_kind(p.channel) == "L" for p in self.res.pairs))
        kinds = {S.channel_kind(p.channel) for p in self.res.pairs}
        self.assertEqual(kinds, {"S", "SOL"})

    # ---- trace -> edges on a real per-frame trace -------------------------
    def test_hysteresis_on_the_real_reward_trace(self):
        trace = [float(v) for v in self.fx["trace"]]
        rising, falling, span = S.hysteresis_edges(trace)
        self.assertEqual(rising, self.fx["trace_rising"])
        self.assertEqual(falling, self.fx["trace_falling"])
        self.assertGreater(span, 100)
        # A 1 %-duty LED: percentile-based levels would have missed it.
        srt = sorted(trace)
        p95 = srt[int(0.95 * (len(srt) - 1))]
        self.assertLess(p95 - srt[0], 15)      # the 95th percentile is still "off"
        # Every rise that is followed by a fall became one lit span of blob 0.
        n_lit = len(self.fx["blobs"][0]["lit"])
        self.assertIn(len(rising), (n_lit, n_lit + 1))


if __name__ == "__main__":
    unittest.main()
