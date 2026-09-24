"""test_sync_event_frames.py - the pure core of the LED-map builder's event pass.

``tmaze.core.sync.event_frames`` (which frames are "during" / "before" each
event, with the quiet-baseline rule) and ``tmaze.core.sync.board_layout`` (how
many LEDs a channel drives; the bright/dim row-offset check). See
docs/decisions/0196. Stdlib only.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from tmaze.core.sync import board_layout as BL, event_frames as EF
from tmaze.core.sync.clockfit import ClockFit, ResidualStats
from tmaze.core.sync.spans import FIRMWARE_MS, Span

FRAME_MS = 40.0
N = 200
# rate 1, offset 0: a firmware ms maps to the same number of camera units, and
# frame k is stamped k * 40.
FIT = ClockFit(1.0, {0: 0.0}, 0, ResidualStats(0, 0.0, 0.0, 0.0, 0.0))
CAM = [k * FRAME_MS for k in range(N)]


def span(ch: str, f0: float, f1: float, segment: int = 0) -> Span:
    """A span from frame ``f0`` to frame ``f1`` (fractional frames allowed)."""
    return Span(ch, 0.0, 0.0, f0 * FRAME_MS, f1 * FRAME_MS, 0, 0, segment, FIRMWARE_MS)


P = EF.EventMapParams(guard_frames=1, during_frames=3, before_frames=4, before_guard=2,
                      quiet_margin=1)


class TestSpanFrames(unittest.TestCase):
    def test_spans_land_on_the_frames_inside_them(self):
        self.assertEqual(EF.span_frames([span("S2=1", 10, 14)], FIT, CAM), [(10, 14)])
        self.assertEqual(EF.span_frames([span("S2=1", 10.2, 13.9)], FIT, CAM), [(11, 13)])

    def test_a_span_between_two_frames_has_last_before_first(self):
        self.assertEqual(EF.span_frames([span("S2=1", 10.2, 10.6)], FIT, CAM), [(11, 10)])

    def test_unplaceable_spans_are_skipped(self):
        no_clock = Span("S2=1", 0.0, 1.0)
        other_segment = span("S2=1", 10, 14, segment=3)
        past_the_end = span("S2=1", N + 5, N + 9)
        self.assertEqual(EF.span_frames([no_clock, other_segment, past_the_end], FIT, CAM), [])

    def test_frames_without_a_stamp_are_ignored(self):
        cam = list(CAM)
        cam[10] = None
        self.assertEqual(EF.span_frames([span("S2=1", 10, 14)], FIT, cam), [(11, 14)])


class TestEventWeights(unittest.TestCase):
    def test_during_and_before_windows(self):
        w = EF.event_weights({"S2=1": [span("S2=1", 50, 60)]}, FIT, CAM, P)["S2=1"]
        self.assertEqual((w.n_spans, w.n_used), (1, 1))
        during = sorted(k for k, v in w.weights.items() if v > 0)
        before = sorted(k for k, v in w.weights.items() if v < 0)
        self.assertEqual(during, [51, 52, 53])            # guard 1, then 3 frames
        self.assertEqual(before, [44, 45, 46, 47])        # 4 frames, 2 back from onset
        self.assertAlmostEqual(sum(w.weights.values()), 0.0)   # a steady pixel cancels

    def test_events_are_averaged(self):
        w = EF.event_weights({"S2=1": [span("S2=1", 50, 60), span("S2=1", 120, 130)]},
                             FIT, CAM, P)["S2=1"]
        self.assertEqual(w.n_used, 2)
        self.assertAlmostEqual(w.weights[51], 1 / 3 / 2)
        self.assertAlmostEqual(w.weights[44], -1 / 4 / 2)

    def test_quiet_baseline_skips_other_beams_and_rewards(self):
        active = {"S2=1": [span("S2=1", 50, 60)],
                  "S3=1": [span("S3=1", 44, 44.5)],        # another beam, frame 44 (+-1)
                  "SOL0.1": [span("SOL0.1", 47, 47)]}      # a reward on frame 47 (+-1)
        w = EF.event_weights(active, FIT, CAM, P)["S2=1"]
        before = sorted(k for k, v in w.weights.items() if v < 0)
        self.assertEqual(before, [])                       # 43-45 and 46-48 are busy
        self.assertEqual(w.n_used, 0)

    def test_a_lick_does_not_spoil_a_beam_baseline(self):
        active = {"S2=1": [span("S2=1", 50, 60)], "L1=1": [span("L1=1", 44, 47)]}
        w = EF.event_weights(active, FIT, CAM, P)["S2=1"]
        self.assertEqual(sorted(k for k, v in w.weights.items() if v < 0), [44, 45, 46, 47])

    def test_during_drops_frames_where_a_same_function_channel_is_active(self):
        active = {"L0=1": [span("L0=1", 50, 60)], "L1=1": [span("L1=1", 52, 52.5)]}
        w = EF.event_weights(active, FIT, CAM, P)["L0=1"]
        self.assertEqual(sorted(k for k, v in w.weights.items() if v > 0), [51, 53])

    def test_an_event_shorter_than_the_guard_is_not_used(self):
        w = EF.event_weights({"L0=1": [span("L0=1", 50, 50.5)]}, FIT, CAM, P)["L0=1"]
        self.assertEqual((w.n_spans, w.n_used, w.weights), (1, 0, {}))

    def test_frame_roles_inverts_the_weights(self):
        ws = EF.event_weights({"S2=1": [span("S2=1", 50, 60)], "S3=1": [span("S3=1", 100, 110)]},
                              FIT, CAM, P)
        roles = EF.frame_roles(ws)
        self.assertEqual([ch for ch, _ in roles[51]], ["S2=1"])
        self.assertEqual([ch for ch, _ in roles[101]], ["S3=1"])
        self.assertNotIn(70, roles)

    def test_function_of(self):
        self.assertEqual([EF.function_of(c) for c in ("S2=1", "L0=0", "SOL2.4")],
                         ["S", "L", "SOL"])


class TestBoardLayout(unittest.TestCase):
    def test_default_is_one_led_per_channel(self):
        lay = BL.BoardLayout()
        self.assertEqual((lay.expected("S0=1"), lay.expected("SOL2.1")), (1, 1))

    def test_per_channel_overrides_per_kind(self):
        lay = BL.from_dict({"schema_version": 1, "leds_per_kind": {"S": 2, "L": 2, "SOL": 1},
                            "leds_per_channel": {"S0": 1}})
        self.assertEqual([lay.expected(c) for c in ("S0=1", "S3=0", "L2=1", "SOL0.4")],
                         [1, 2, 2, 1])

    def test_the_committed_layout_says_two_per_sensor(self):
        lay = BL.load()
        self.assertEqual([lay.expected(c) for c in ("S0=1", "L2=1", "SOL2.4")], [2, 2, 1])

    def test_missing_file_is_the_default_and_bad_files_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(BL.load(os.path.join(d, "absent.json")).source, BL.SOURCE_DEFAULT)
            for bad in ({"schema_version": 2}, {"schema_version": 1, "leds_per_kind": {"S": -1}},
                        {"schema_version": 1, "leds_per_kind": {"S": 1.5}},
                        {"schema_version": 1, "leds_per_channel": [1]}):
                p = os.path.join(d, "l.json")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(bad, f)
                with self.assertRaises(ValueError):
                    BL.load(p)

    def test_row_offset_flags_the_pair_that_does_not_match(self):
        pairs = {"S0": ((950.0, 735.0), (961.0, 737.0)),
                 "S1": ((1012.0, 731.0), (1025.0, 734.0)),
                 "S4": ((950.0, 739.0), (962.0, 742.0)),
                 "L2": ((890.0, 705.0), (894.0, 698.0))}     # a neighbour picked by mistake
        off = BL.row_offset(pairs)
        self.assertEqual(BL.row_offset_outliers(pairs, off), ["L2"])
        self.assertIsNone(BL.row_offset({}))

    def test_near_and_channel_base(self):
        self.assertTrue(BL.near((0.0, 0.0), [(3.0, 4.0)], 5.0))
        self.assertFalse(BL.near((0.0, 0.0), [(3.0, 4.1)], 5.0))
        self.assertEqual((BL.channel_base("S3=1"), BL.channel_base("SOL2.1")), ("S3", "SOL2.1"))


if __name__ == "__main__":
    unittest.main()
