"""test_sync_core.py - headless tests for the pure sync core (tmaze.core.sync)
on synthetic data: serial/sidecar parsing, trace -> spans, ordered one-to-one
matching, bindings, the Theil-Sen clock fit with segments and the host-clock
step check, and analyze() end to end. No hardware, no OpenCV, no numpy.

The synthetic rig (``Rig`` below) models what the spike measured on real
footage (design doc section 3.7): a camera clock in ns running 27 ppm fast
relative to the Arduino's ms, frames every ~40 ms, LED ON edges visible in the
frame that contains them and OFF edges one frame later, a ~60 ms host-stamp
latency for frames, a few ms of serial-stamp jitter, and the pre-v8 (legacy)
sensor polarity: beams and lick detector rest at 1, a crossing / contact is 0.
``flipped`` turns its log into the v8+ (corrected) convention.

Run (from host/):  python -m unittest tests.test_sync_core
"""
from __future__ import annotations

import math
import random
import unittest
from typing import Dict, List, Tuple

from tmaze.core.phase3.bundle_format import POLARITY_CORRECTED, POLARITY_LEGACY, POLARITY_UNKNOWN
from tmaze.core.sync import clockfit, matching, pipeline, spans as S

# ------------------------------------------------------------------ helpers

LEGACY = POLARITY_LEGACY          # what the synthetic Rig writes


def flipped(rows):
    """A legacy log rewritten in the corrected (v8+) convention: every S/L
    state bit inverted, everything else untouched."""
    out = []
    for n, stamp, fw in rows:
        parts = fw.split(",")
        if len(parts) >= 4 and parts[1] in ("S", "L") and parts[3] in ("0", "1"):
            parts[3] = "1" if parts[3] == "0" else "0"
        out.append((n, stamp, ",".join(parts)))
    return out

RATE = 1e6 * (1 + 27e-6)          # camera ns per firmware ms
CAM0 = 8_647_278_000_000_000.0    # camera ticks at firmware ms 0
HOST0 = 1_788_457_552.0           # host POSIX seconds at firmware ms 0
FRAME_MS = 40.065
FRAME_LATENCY_S = 0.012           # frame host stamp lands this long after the frame's start
# Measured on real footage: LED ON edges lag the serial stamp by ~32 ms (latency +
# half a frame of quantisation), OFF edges by ~70 ms (one frame more).


def iso(t: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


class Rig:
    """Emit serial lines and LED frame-edges for scripted events."""

    def __init__(self, seed: int = 1, serial_jitter_s: float = 0.003):
        self.rng = random.Random(seed)
        self.lines: List[Tuple[int, str, str]] = []
        self.jitter = serial_jitter_s
        self.line_no = 0

    def host_of(self, fw_ms: float) -> float:
        return HOST0 + fw_ms / 1000.0 + self.rng.uniform(-self.jitter, self.jitter)

    def line(self, fw_ms: float, payload: str) -> None:
        self.line_no += 1
        self.lines.append((self.line_no, iso(self.host_of(fw_ms)), f"{int(fw_ms)},{payload}"))

    def beam(self, i: int, t_break_ms: float, dur_ms: float) -> None:
        self.line(t_break_ms, f"S,{i},0")
        self.line(t_break_ms + dur_ms, f"S,{i},1")

    def lick(self, t_ms: float, dur_ms: float) -> None:
        self.line(t_ms, "L,2,0")                 # contact
        self.line(t_ms + dur_ms, "L,2,1")        # release

    def sol(self, t_ms: float, dur_ms: int = 300) -> None:
        self.line(t_ms, f"SOL,2,1,{dur_ms}")
        self.line(t_ms, f"REWARD,2,MANUAL,{dur_ms}")

    # ---- the camera's view of an electrical edge -------------------------
    @staticmethod
    def frame_after(fw_ms: float, extra_frames: int = 0) -> int:
        return int(math.ceil(fw_ms / FRAME_MS)) + extra_frames

    @staticmethod
    def frame(k: int) -> S.Frame:
        fw = k * FRAME_MS
        return S.Frame(k, HOST0 + fw / 1000.0 + FRAME_LATENCY_S, int(CAM0 + RATE * fw))

    def led_span(self, channel: str, t0_ms: float, t1_ms: float, dark: bool = False) -> S.Span:
        """The LED span a camera sees for an electrical span t0..t1.

        A lit span starts with an ON edge and ends with an OFF edge; a dark span
        the reverse. ON edges are seen in the frame that contains them, OFF
        edges one frame later (the LED still leaves light in the frame it
        switches off in).
        """
        k0 = self.frame_after(t0_ms, 1 if dark else 0)
        k1 = self.frame_after(t1_ms, 0 if dark else 1)
        f0, f1 = self.frame(k0), self.frame(k1)
        return S.Span(channel, f0.host_unix_s, f1.host_unix_s, float(f0.cam_ts), float(f1.cam_ts), k0, k1)

    def sorted_lines(self) -> List[Tuple[int, str, str]]:
        return sorted(self.lines, key=lambda r: r[2].split(",")[0].zfill(12))


def scripted_session(seed: int = 1, minutes: float = 10.0) -> Tuple[Rig, Dict[str, List[S.Span]]]:
    """A rat-like session: beams 2-5 crossed at random with realistic
    durations plus sub-frame flickers, rewards every ~30 s, lick bursts.

    Returns the rig (serial lines) and the LED spans a camera would see, keyed
    "beam<i>" (dark spans - the LED goes dark during a crossing), "sol" (lit
    spans) and "lick" (lit spans - the LED lights during a contact).
    """
    rig = Rig(seed)
    rng = random.Random(seed + 100)
    led: Dict[str, List[S.Span]] = {f"beam{i}": [] for i in range(2, 6)}
    led["sol"], led["lick"] = [], []
    t = 2000.0
    end = minutes * 60_000
    while t < end:
        i = rng.choice([2, 3, 4, 5])
        dur = rng.uniform(90, 400)
        rig.beam(i, t, dur)
        t_end = t + dur
        if rng.random() < 0.4:                       # sensor chatter right after the crossing
            f_t, f_d = t_end + rng.uniform(5, 30), rng.uniform(5, 25)
            rig.beam(i, f_t, f_d)                    # bounce + flicker both sub-frame:
            t_end = f_t + f_d                        # the camera sees one dark span
        led[f"beam{i}"].append(rig.led_span(f"beam{i}", t, t_end, dark=True))
        t += rng.uniform(800, 6000)
    for t in range(15_000, int(end), 30_000):
        rig.sol(t)
        led["sol"].append(rig.led_span("sol", t, t + 300))
    for burst in range(20_000, int(end), 45_000):    # 8-lick bursts at ~6 Hz
        tt = float(burst)
        for _ in range(8):
            d = rng.uniform(60, 110)
            rig.lick(tt, d)
            led["lick"].append(rig.led_span("lick", tt, tt + d))
            tt += d + rng.uniform(50, 90)
    for v in led.values():
        v.sort(key=lambda s: s.host_t0)
    return rig, led


# -------------------------------------------------------------------- spans

class TestSerialSpans(unittest.TestCase):
    def test_beam_spans_both_polarities_and_sol_duration(self):
        rows = [
            (1, iso(HOST0 + 1.0), "1000,S,2,0"),
            (2, iso(HOST0 + 1.2), "1200,S,2,1"),
            (3, iso(HOST0 + 5.0), "5000,S,2,0"),
            (4, iso(HOST0 + 5.3), "5300,S,2,1"),
            (5, iso(HOST0 + 6.0), "6000,SOL,2,1,300"),
            (6, iso(HOST0 + 6.0), "6000,REWARD,2,MANUAL,300"),
            (7, iso(HOST0 + 7.0), "7000,SOL,1,1"),          # no duration field
            (8, iso(HOST0 + 8.0), "garbage"),
            (9, "not-a-date", "8000,S,2,0"),
        ]
        out = S.serial_spans(rows)
        self.assertEqual(sorted(out), ["S2=0", "S2=1", "SOL1.1", "SOL2.1"])
        self.assertEqual(sorted(S.active_spans(out, LEGACY)), ["S2=0", "SOL1.1", "SOL2.1"])
        self.assertEqual(sorted(S.active_spans(out, POLARITY_CORRECTED)),
                         ["S2=1", "SOL1.1", "SOL2.1"])
        self.assertEqual([round(s.duration, 3) for s in out["S2=0"]], [0.2, 0.3])
        self.assertEqual([s.clk0 for s in out["S2=0"]], [1000, 5000])
        self.assertEqual([round(s.duration, 3) for s in out["S2=1"]], [3.8])
        self.assertEqual(out["SOL2.1"][0].clk1, 6300)
        self.assertAlmostEqual(out["SOL1.1"][0].duration, 0.3)      # default pulse width
        self.assertEqual(out["S2=0"][0].ref0, 1)

    def test_segment_increments_when_firmware_clock_runs_backwards(self):
        rows = [
            (1, iso(HOST0 + 1.0), "1000,S,2,0"),
            (2, iso(HOST0 + 1.2), "1200,S,2,1"),
            (3, iso(HOST0 + 9.0), "50,MODE,FULL"),            # reset
            (4, iso(HOST0 + 10.0), "1000,S,2,0"),
            (5, iso(HOST0 + 10.2), "1200,S,2,1"),
        ]
        out = S.serial_spans(rows)
        self.assertEqual([s.segment for s in out["S2=0"]], [0, 1])
        self.assertNotIn("S2=1", out)      # the 1 state straddled the reset: no span

    def test_parse_serial_csv_skips_comments(self):
        rows = S.parse_serial_csv(["# header\n", "\n", "2026-09-03T13:45:53.532,821151,SOL,2,1,300\n"])
        self.assertEqual(rows, [(3, "2026-09-03T13:45:53.532", "821151,SOL,2,1,300")])

    def test_channel_kind(self):
        self.assertEqual(S.channel_kind("S2=0"), "S")
        self.assertEqual(S.channel_kind("SOL2.1"), "SOL")
        self.assertEqual(S.channel_kind("L2=1"), "L")


class TestSidecar(unittest.TestCase):
    def test_header_and_blank_tick(self):
        text = ["# fps: 24.959", "# codec: h264", "frame_idx,host_unix_s,cam_timestamp,other",
                "0,1788457552.5,8647278000000000,x", "1,1788457552.54,,x", "bad,row"]
        header, frames = S.parse_sidecar_csv(text)
        self.assertEqual(header["codec"], "h264")
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].cam_ts, 8647278000000000)
        self.assertIsNone(frames[1].cam_ts)


class TestTraceToSpans(unittest.TestCase):
    def test_one_percent_duty_led_is_found_from_min_max_levels(self):
        rng = random.Random(3)
        trace = [10 + rng.uniform(-2, 2) for _ in range(2000)]
        for start in (300, 1100):                      # two 8-frame flashes: 0.8 % duty
            for k in range(start, start + 8):
                trace[k] = 160 + rng.uniform(-3, 3)
        trace[700] = 200                               # single-frame glitch: must not count
        rising, falling, span = S.hysteresis_edges(trace)
        self.assertEqual(rising, [300, 1100])
        self.assertEqual(falling, [308, 1108])
        self.assertGreater(span, 100)

    def test_flat_trace_has_no_edges_and_none_is_tolerated(self):
        trace = [50.0 + (i % 3) for i in range(100)]
        trace[10] = None
        self.assertEqual(S.hysteresis_edges(trace)[:2], ([], []))

    def test_visible_coalesces_sub_frame_gaps_then_drops_sub_frame_spans(self):
        ss = [span(1.0, 1.4), span(1.43, 1.5), span(3.0, 3.02), span(5.0, 5.3)]
        vis = S.visible(ss, 0.06)
        self.assertEqual([(s.host_t0, s.host_t1) for s in vis], [(1.0, 1.5), (5.0, 5.3)])

    def test_visible_trusts_the_firmware_clock_over_jittery_host_stamps(self):
        # Firmware says the gap is 99 ms (two crossings); the PC read both lines
        # in one batch so the host stamps are only 40 ms apart. Real case from
        # session 600_20260903_134552 (S3 at fw 1344348 / 1344447).
        a = S.Span("S3=0", 10.000, 10.351, 1000, 1351, clk_unit_s=S.FIRMWARE_MS)
        b = S.Span("S3=0", 10.391, 10.422, 1450, 1481, clk_unit_s=S.FIRMWARE_MS)
        self.assertAlmostEqual(S.gap_s(a, b), 0.099)
        self.assertEqual(S.visible([a, b], 0.06), [a])          # b is a 31 ms flicker
        self.assertAlmostEqual(b.duration, 0.031)

    def test_cam_tick_s_from_sidecar(self):
        frames = [Rig.frame(k) for k in range(0, 5000, 7)]
        self.assertAlmostEqual(S.cam_tick_s(frames) * 1e9, 1.0, places=3)
        self.assertIsNone(S.cam_tick_s(frames[:1]))

    def test_spans_from_edges_lit_and_dark(self):
        frames = [Rig.frame(k) for k in range(20)]
        lit = S.spans_from_edges([3, 12], [7, 15], frames, "x", lit=True)
        dark = S.spans_from_edges([3, 12], [7, 15], frames, "x", lit=False)
        self.assertEqual([(s.ref0, s.ref1) for s in lit], [(3, 7), (12, 15)])
        self.assertEqual([(s.ref0, s.ref1) for s in dark], [(7, 12)])     # trailing dark is open
        self.assertEqual(lit[0].clk0, float(frames[3].cam_ts))


# ----------------------------------------------------------------- matching

def span(t0: float, t1: float, ch: str = "x") -> S.Span:
    return S.Span(ch, t0, t1)


class TestMatchSpans(unittest.TestCase):
    def test_requires_both_ends(self):
        ev = [span(10.0, 10.3)]
        self.assertEqual(matching.match_spans([span(10.05, 10.35)], ev, 0.15)[0].event_idx, 0)
        self.assertEqual(matching.match_spans([span(10.05, 10.9)], ev, 0.15), [])   # end disagrees

    def test_ordered_one_to_one_in_a_burst(self):
        # Events every 100 ms; LED spans lag 60 ms. A greedy nearest match
        # from the LED side could pair span k with event k+1 (40 ms away)
        # instead of event k (60 ms away); ordering + max count forbids it.
        ev = [span(t, t + 0.05) for t in [1.0, 1.1, 1.2, 1.3, 1.4]]
        led = [span(t + 0.06, t + 0.11) for t in [1.0, 1.1, 1.2, 1.3, 1.4]]
        m = matching.match_spans(led, ev, 0.15)
        self.assertEqual([(x.event_idx, x.led_idx) for x in m], [(i, i) for i in range(5)])
        self.assertTrue(all(abs(x.start_lag - 0.06) < 1e-9 for x in m))

    def test_missing_on_either_side(self):
        ev = [span(1.0, 1.2), span(2.0, 2.2), span(3.0, 3.2)]      # event 2 unseen by camera
        led = [span(1.05, 1.25), span(2.5, 2.6), span(3.05, 3.25)]  # span 1 is a stray flash
        m = matching.match_spans(led, ev, 0.15)
        self.assertEqual([(x.event_idx, x.led_idx) for x in m], [(0, 0), (2, 2)])

    def test_empty(self):
        self.assertEqual(matching.match_spans([], [span(1, 2)], 0.1), [])
        self.assertEqual(matching.match_spans([span(1, 2)], [], 0.1), [])

    def test_prefers_more_pairs_then_lower_cost(self):
        ev = [span(1.0, 1.2), span(1.1, 1.3)]
        led = [span(1.05, 1.25), span(1.15, 1.35)]
        m = matching.match_spans(led, ev, 0.15)
        self.assertEqual(len(m), 2)


class TestBinding(unittest.TestCase):
    def test_beam_led_binds_dark_to_zero_state(self):
        rig, led = scripted_session(minutes=6)
        events = S.serial_spans(rig.sorted_lines())
        dark = led["beam3"]
        lit = [S.Span("beam3", a.host_t1, b.host_t0, a.clk1, b.clk0, a.ref1, b.ref0)
               for a, b in zip(dark, dark[1:])]                 # lit between the dark spans
        best, cands = matching.bind_blob(3, {matching.LIT: lit, matching.DARK: dark},
                                         S.active_spans(events, LEGACY), 0.15, 0.06)
        self.assertIsNotNone(best)
        self.assertEqual((best.channel, best.polarity), ("S3=0", matching.DARK))
        self.assertGreater(best.score, 0.9)
        # Only active states are offered, so "lit while S3=1" (the same wire
        # phrased the other way round) never competes.
        self.assertFalse(any(c.channel.endswith("=1") and c.channel.startswith("S") for c in cands))
        # The score is the precision, recomputable from the counts shown.
        self.assertAlmostEqual(best.score, best.n_matched / best.n_led)
        # OFF edge: latency + one frame + half a frame of quantisation, ~70 ms.
        self.assertAlmostEqual(best.lag_s, 0.07, delta=0.03)
        self.assertEqual(cands[0], best)

    def test_active_state_follows_the_bundle_polarity(self):
        self.assertEqual(S.active_state(LEGACY), "0")
        self.assertEqual(S.active_state(POLARITY_CORRECTED), "1")
        for pol, active, rest in ((LEGACY, "0", "1"), (POLARITY_CORRECTED, "1", "0")):
            for sensor in ("S4", "L2"):
                self.assertTrue(S.is_active_channel(f"{sensor}={active}", pol))
                self.assertFalse(S.is_active_channel(f"{sensor}={rest}", pol))
            self.assertTrue(S.is_active_channel("SOL2.1", pol))
        ev = {"S4=0": [], "S4=1": [], "L2=1": [], "L2=0": [], "SOL2.1": []}
        self.assertEqual(sorted(S.active_spans(ev, LEGACY)), ["L2=0", "S4=0", "SOL2.1"])
        self.assertEqual(sorted(S.active_spans(ev, POLARITY_CORRECTED)), ["L2=1", "S4=1", "SOL2.1"])

    def test_unknown_polarity_is_refused_not_guessed(self):
        for bad in (POLARITY_UNKNOWN, None, ""):
            with self.assertRaises(ValueError):
                S.active_state(bad)
            with self.assertRaises(ValueError):
                S.is_active_channel("S4=0", bad)

    def test_coincidence_that_precedes_its_wire_is_rejected(self):
        # Real case: the centre reward fires as the rat crosses the centre beam,
        # so a beam LED's dark spans overlap the 300 ms reward pulses - but the
        # LED relights well BEFORE each pulse ends. Every span matches, yet the
        # end lag is negative, so it must not bind.
        ev = {"SOL2.1": [span(HOST0 + t, HOST0 + t + 0.3, "SOL2.1") for t in range(10, 300, 30)]}
        dark = [span(HOST0 + t + 0.02, HOST0 + t + 0.15) for t in range(10, 300, 30)]
        best, cands = matching.bind_blob(7, {matching.LIT: [], matching.DARK: dark}, ev, 0.2, 0.06)
        self.assertIsNone(best)
        self.assertEqual(cands[0].score, 1.0)                       # it did match everything...
        self.assertFalse(matching.lag_plausible(cands[0]))          # ...150 ms too early at the end
        # Shift the LED to react after both edges and it binds.
        dark_ok = [span(HOST0 + t + 0.07, HOST0 + t + 0.33) for t in range(10, 300, 30)]
        best, _ = matching.bind_blob(7, {matching.LIT: [], matching.DARK: dark_ok}, ev, 0.2, 0.06)
        self.assertIsNotNone(best)

    def test_random_blinker_is_unbound(self):
        rig, _ = scripted_session(minutes=4)
        events = S.serial_spans(rig.sorted_lines())
        rng = random.Random(9)
        noise = [span(HOST0 + t, HOST0 + t + 0.2) for t in sorted(rng.uniform(5, 230) for _ in range(40))]
        best, _ = matching.bind_blob(99, {matching.LIT: noise, matching.DARK: []},
                                     S.active_spans(events, LEGACY), 0.15, 0.06)
        self.assertIsNone(best)


class TestInferPolarity(unittest.TestCase):
    def test_reads_both_conventions_off_the_log(self):
        rig, _ = scripted_session(minutes=6)
        rows = rig.sorted_lines()
        legacy = S.infer_polarity(S.serial_spans(rows))
        self.assertEqual(legacy.polarity, LEGACY)
        self.assertGreater(legacy.n_votes, 0)
        self.assertEqual(legacy.n_agree, legacy.n_votes)
        corrected = S.infer_polarity(S.serial_spans(flipped(rows)))
        self.assertEqual(corrected.polarity, POLARITY_CORRECTED)
        self.assertEqual(corrected.n_votes, legacy.n_votes)

    def _sensor(self, name, brief_state, brief_s, long_s):
        rest = "1" if brief_state == "0" else "0"
        return {f"{name}={brief_state}": [S.Span("", 0, brief_s)],
                f"{name}={rest}": [S.Span("", 0, long_s)]}

    def test_dissent_or_no_clear_vote_gives_none(self):
        ev = {**self._sensor("S2", "0", 0.3, 60.0), **self._sensor("S3", "1", 0.3, 60.0)}
        g = S.infer_polarity(ev)
        self.assertIsNone(g.polarity)
        self.assertEqual((g.n_agree, g.n_votes), (1, 2))
        # Longest spans within 5x of each other: that sensor abstains.
        self.assertEqual(S.infer_polarity(self._sensor("S2", "0", 10.0, 20.0)).n_votes, 0)
        self.assertIsNone(S.infer_polarity({}).polarity)
        # Valve pulses carry no polarity and never vote.
        self.assertEqual(S.infer_polarity({"SOL2.1": [S.Span("", 0, 0.3)]}).n_votes, 0)


# ----------------------------------------------------------------- clock fit

class TestClockFit(unittest.TestCase):
    def test_theil_sen_ignores_outliers(self):
        xs = [float(i) for i in range(200)]
        ys = [3.0 + 2.0 * x for x in xs]
        for i in (10, 50, 90):
            ys[i] += 500.0
        a, b = clockfit.theil_sen(xs, ys)
        self.assertAlmostEqual(a, 3.0, places=6)
        self.assertAlmostEqual(b, 2.0, places=6)

    def test_theil_sen_is_deterministic_with_subsampling(self):
        xs = [float(i) for i in range(500)]
        ys = [1.0 + 0.5 * x for x in xs]
        self.assertEqual(clockfit.theil_sen(xs, ys, max_pairs=1000, seed=4),
                         clockfit.theil_sen(xs, ys, max_pairs=1000, seed=4))

    def _pairs(self, n: int, segment: int = 0, t_shift_ms: float = 0.0, host_lag: float = 0.06,
               seed: int = 0) -> List[clockfit.Pair]:
        rng = random.Random(seed)
        out = []
        for k in range(n):
            x = 1000.0 + k * 7000.0
            edge = clockfit.ON if k % 2 == 0 else clockfit.OFF
            quant = rng.uniform(0, FRAME_MS) + (FRAME_MS if edge == clockfit.OFF else 0)
            y = CAM0 + RATE * (x + t_shift_ms + quant)
            out.append(clockfit.Pair(x, y, "S2=0", edge, host_lag, HOST0 + (x + t_shift_ms) / 1000, segment))
        return out

    def test_rate_and_offsets_with_two_segments(self):
        pairs = self._pairs(120, 0) + self._pairs(120, 1, t_shift_ms=900_000.0, seed=1)
        fit = clockfit.fit_clock(pairs)
        self.assertIsNotNone(fit)
        # 40 ms of frame quantisation over a 14-minute span pins the rate to ~1e-5.
        self.assertAlmostEqual(fit.rate / 1e6, 1 + 27e-6, delta=5e-5)
        self.assertEqual(sorted(fit.offsets), [0, 1])
        self.assertAlmostEqual(fit.offsets[1] - fit.offsets[0], RATE * 900_000.0, delta=RATE * 15)
        self.assertLess(fit.residuals.mad_ms, FRAME_MS / 2)
        # OFF edges register one frame later than ON edges.
        self.assertGreater(fit.per_edge_offset_ms[clockfit.OFF] - fit.per_edge_offset_ms[clockfit.ON], 20)
        self.assertEqual(fit.verdict, clockfit.VERDICT_OK)
        self.assertEqual(fit.host_steps, [])

    def test_too_few_pairs(self):
        self.assertIsNone(clockfit.fit_clock(self._pairs(10)))

    def test_host_clock_step_is_reported(self):
        pairs = self._pairs(150, host_lag=0.06) + [
            clockfit.Pair(p.x + 2_000_000, p.y + RATE * 2_000_000, p.channel, p.edge, 0.06 + 1.5,
                          p.host_t + 2000 + 1.5, 0)
            for p in self._pairs(150, host_lag=0.06)]
        fit = clockfit.fit_clock(pairs)
        self.assertEqual(fit.verdict, clockfit.VERDICT_HOST_STEP)
        self.assertEqual(len(fit.host_steps), 1)
        self.assertAlmostEqual(fit.host_steps[0].delta_s, 1.5, delta=0.01)

    def test_to_dict_is_json_shaped(self):
        d = clockfit.fit_clock(self._pairs(60)).to_dict()
        self.assertIn("residual_ms", d)
        self.assertEqual(d["offsets"].keys(), {"0"})


class TestTypicalRate(unittest.TestCase):
    """A session too short to pin its drift uses the rig's typical rate (ADR-0205)."""

    PRIOR = clockfit.RatePrior(RATE, 0.4, "test")

    _pairs = TestClockFit._pairs

    def test_short_session_uses_the_typical_rate_and_records_its_own(self):
        pairs = self._pairs(40)                          # ~5 min: SE ~20 ppm
        plain = clockfit.fit_clock(pairs)
        fit = clockfit.fit_clock(pairs, prior=self.PRIOR)
        self.assertEqual(plain.rate_source, clockfit.RATE_FITTED)
        self.assertEqual(fit.rate_source, clockfit.RATE_TYPICAL)
        self.assertEqual(fit.rate, RATE)
        self.assertEqual(fit.own_rate, plain.rate)       # the session's estimate is kept
        self.assertGreater(fit.own_rate_se_ppm, clockfit.RATE_SE_MAX_PPM)
        self.assertEqual(fit.typical_rate, RATE)
        # offsets + residuals are recomputed under the typical rate
        self.assertAlmostEqual(fit.offsets[0], clockfit.median(
            [p.y - RATE * p.x for p in pairs if p.edge == clockfit.ON]), places=3)
        self.assertLess(fit.residuals.mad_ms, FRAME_MS)

    def test_long_session_keeps_its_own_rate(self):
        fit = clockfit.fit_clock(self._pairs(600), prior=self.PRIOR)    # ~70 min
        self.assertEqual(fit.rate_source, clockfit.RATE_FITTED)
        self.assertEqual(fit.rate, fit.own_rate)
        self.assertLess(fit.own_rate_se_ppm, 1.0)
        self.assertFalse(fit.rate_far_from_typical)

    def test_a_well_pinned_rate_far_from_typical_is_flagged_not_replaced(self):
        prior = clockfit.RatePrior(1e6 * (1 + 60e-6), 0.4, "stale")
        fit = clockfit.fit_clock(self._pairs(600), prior=prior)
        self.assertEqual(fit.rate_source, clockfit.RATE_FITTED)
        self.assertTrue(fit.rate_far_from_typical)
        self.assertAlmostEqual(clockfit.drift_ppm(fit.rate), 27.0, delta=2.0)

    def test_the_uncertainty_estimate_is_never_optimistic(self):
        # Errs high here (~2x): flat frame-quantisation noise, which Theil-Sen
        # handles better than the straight-line formula assumes. Over-stating the
        # uncertainty only moves a session to the typical rate a little sooner.
        drifts, ses = [], []
        for seed in range(40):
            fit = clockfit.fit_clock(self._pairs(60, seed=seed))
            drifts.append(clockfit.drift_ppm(fit.rate))
            ses.append(fit.own_rate_se_ppm)
        m = sum(drifts) / len(drifts)
        sd = (sum((d - m) ** 2 for d in drifts) / (len(drifts) - 1)) ** 0.5
        mean_se = sum(ses) / len(ses)
        self.assertLess(abs(m - 27.0), 3 * sd / math.sqrt(len(drifts)) + 0.5)
        self.assertGreater(mean_se, sd)
        self.assertLess(mean_se, sd * 3)

    def test_no_spread_means_unknown_uncertainty_written_as_null(self):
        import json
        same_x = [clockfit.Pair(1000.0, CAM0 + k, "S2=0", clockfit.ON, 0.06, HOST0, 0)
                  for k in range(40)]
        self.assertEqual(clockfit.rate_se_ppm({0: same_x}, [float(k) for k in range(40)]),
                         float("inf"))
        d = clockfit.ClockFit(rate=RATE, offsets={0: CAM0}, n_pairs=0,
                              residuals=clockfit.fit_clock(self._pairs(40)).residuals,
                              host_lag_median_s=0.06, host_lag_mad_s=0.0).to_dict()
        self.assertIsNone(d["own_rate_se_ppm"])
        json.dumps(d, allow_nan=False)


# ------------------------------------------------------------------ pipeline

def session_blobs(led: Dict[str, List[S.Span]]) -> List[pipeline.BlobSpans]:
    return [
        pipeline.BlobSpans(0, lit=led["sol"], dark=[]),
        pipeline.BlobSpans(1, lit=led["lick"], dark=[]),
        pipeline.BlobSpans(2, lit=[], dark=led["beam2"]),
        pipeline.BlobSpans(3, lit=[], dark=led["beam3"]),
        pipeline.BlobSpans(4, lit=[], dark=led["beam4"]),
        pipeline.BlobSpans(5, lit=[], dark=led["beam5"]),
        pipeline.BlobSpans(6, lit=[], dark=[]),                  # a power LED: never blinks
    ]


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.rig, led = scripted_session(minutes=10)
        self.events = S.serial_spans(self.rig.sorted_lines())
        self.blobs = session_blobs(led)

    def test_bindings_fit_and_verdicts(self):
        res = pipeline.analyze(self.blobs, self.events, sensor_polarity=LEGACY)
        want = {0: "SOL2.1", 1: "L2=0", 2: "S2=0", 3: "S3=0", 4: "S4=0", 5: "S5=0"}
        self.assertEqual({k: b.channel for k, b in res.bindings.items()}, want)
        self.assertEqual(res.bindings[0].polarity, matching.LIT)
        self.assertEqual(res.bindings[1].polarity, matching.LIT)
        self.assertEqual(res.bindings[2].polarity, matching.DARK)
        self.assertEqual(res.blob_verdicts[6], pipeline.SILENT)
        self.assertEqual(res.verdict, clockfit.VERDICT_OK)
        # Licks are bound and reported, but do not feed the fit by default.
        self.assertFalse(any(S.channel_kind(p.channel) == "L" for p in res.pairs))
        self.assertTrue(any(p.channel == "SOL2.1" for p in res.pairs))
        fit = res.fit
        self.assertAlmostEqual(fit.rate / 1e6, 1 + 27e-6, delta=1e-4)     # 10-minute span
        self.assertLess(fit.residuals.mad_ms, FRAME_MS / 2)
        # Host lag: latency + quantisation, ON and OFF edges mixed -> ~30-70 ms.
        self.assertGreater(fit.host_lag_median_s, 0.02)
        self.assertLess(fit.host_lag_median_s, 0.10)
        self.assertGreater(fit.per_edge_offset_ms[clockfit.OFF], fit.per_edge_offset_ms[clockfit.ON] + 20)
        # Every edge pair is unique per (channel, firmware ms).
        self.assertEqual(len({(p.channel, p.x) for p in res.pairs}), len(res.pairs))

    def test_licks_can_be_opted_in(self):
        res = pipeline.analyze(self.blobs, self.events, fit_channel_kinds=("S", "SOL", "L"),
                               sensor_polarity=LEGACY)
        self.assertTrue(any(S.channel_kind(p.channel) == "L" for p in res.pairs))

    def test_corrected_log_binds_the_same_leds_to_the_1_state(self):
        # The same session recorded by v8+ firmware: state bits inverted. With
        # the bundle's polarity passed in, every LED binds to the same sensor,
        # now named by its active wire state 1, and the fit is unchanged.
        events = S.serial_spans(flipped(self.rig.sorted_lines()))
        res = pipeline.analyze(self.blobs, events, sensor_polarity=POLARITY_CORRECTED)
        want = {0: "SOL2.1", 1: "L2=1", 2: "S2=1", 3: "S3=1", 4: "S4=1", 5: "S5=1"}
        self.assertEqual({k: b.channel for k, b in res.bindings.items()}, want)
        self.assertEqual(res.bindings[2].polarity, matching.DARK)
        legacy = pipeline.analyze(self.blobs, self.events, sensor_polarity=LEGACY)
        self.assertEqual(res.verdict, clockfit.VERDICT_OK)
        self.assertEqual(res.fit.n_pairs, legacy.fit.n_pairs)
        self.assertAlmostEqual(res.fit.rate, legacy.fit.rate, delta=1e-6)

    def test_too_few_events_verdict(self):
        # Only the reward LED: 20 rewards -> 40 edge pairs, below a 50-pair floor.
        res = pipeline.analyze(self.blobs[:1], self.events, min_pairs=50, sensor_polarity=LEGACY)
        self.assertEqual(res.verdict, clockfit.VERDICT_TOO_FEW)
        self.assertIsNone(res.fit)
        self.assertIn(0, res.bindings)

    def test_to_dict(self):
        d = pipeline.analyze(self.blobs, self.events, sensor_polarity=LEGACY).to_dict()
        self.assertEqual(d["verdict"], "ok")
        self.assertEqual(d["bindings"]["2"]["channel"], "S2=0")


class TestMapCheck(unittest.TestCase):
    """``expected`` from a map: an LED that matches another channel instead of
    its own is reported and kept out of the fit (docs/decisions/0202)."""

    def setUp(self):
        self.rig, self.led = scripted_session(minutes=10)
        self.events = S.serial_spans(self.rig.sorted_lines())
        self.blobs = session_blobs(self.led)

    def test_matching_expectations_change_nothing(self):
        want = {0: ("SOL2.1", "lit"), 1: ("L2", "lit"), 2: ("S2", "dark"),
                3: ("S3", "dark"), 4: ("S4", "dark"), 5: ("S5", "dark")}
        plain = pipeline.analyze(self.blobs, self.events, sensor_polarity=LEGACY)
        res = pipeline.analyze(self.blobs, self.events, sensor_polarity=LEGACY, expected=want)
        self.assertEqual({k: b.channel for k, b in res.bindings.items()},
                         {k: b.channel for k, b in plain.bindings.items()})
        self.assertEqual(res.contradictions, {})
        self.assertEqual(res.map_preferred, {})
        self.assertEqual(len(res.pairs), len(plain.pairs))

    def test_a_neighbour_showing_the_valve_as_dips_contradicts_its_map(self):
        """SOL2.0's LED, whose background ring catches SOL2.1's bloom: it dims on
        every SOL2.1 pulse, so read alone it binds 'SOL2.1 dark'."""
        spill = pipeline.BlobSpans(7, lit=[], dark=self.led["sol"])
        plain = pipeline.analyze([spill], self.events, min_pairs=10, sensor_polarity=LEGACY)
        self.assertEqual(plain.bindings[7].channel, "SOL2.1")
        self.assertTrue(plain.pairs)
        res = pipeline.analyze([spill], self.events, min_pairs=10, sensor_polarity=LEGACY,
                               expected={7: ("SOL2.0", "lit")})
        self.assertEqual(res.blob_verdicts[7], pipeline.CONTRADICTS_MAP)
        self.assertNotIn(7, res.bindings)
        self.assertEqual(res.contradictions[7].channel, "SOL2.1")
        self.assertEqual(res.pairs, [])
        self.assertIn("7", res.to_dict()["contradictions"])

    def test_the_right_channel_in_the_wrong_direction_contradicts(self):
        res = pipeline.analyze(self.blobs[:1], self.events, min_pairs=10, sensor_polarity=LEGACY,
                               expected={0: ("SOL2.1", "dark")})
        self.assertEqual(res.blob_verdicts[0], pipeline.CONTRADICTS_MAP)
        ok = pipeline.analyze(self.blobs[:1], self.events, min_pairs=10, sensor_polarity=LEGACY,
                              expected={0: ("SOL2.1", None)})
        self.assertEqual(ok.blob_verdicts[0], pipeline.MATCHED, "None = direction not checked")

    def test_own_channel_is_kept_over_a_higher_scoring_other(self):
        both = pipeline.BlobSpans(8, lit=self.led["sol"], dark=self.led["beam2"])
        plain = pipeline.analyze([both], self.events, min_pairs=10, sensor_polarity=LEGACY)
        best = plain.bindings[8]
        own = ("SOL2.1", "lit") if best.channel.startswith("S2") else ("S2", "dark")
        res = pipeline.analyze([both], self.events, min_pairs=10, sensor_polarity=LEGACY,
                               expected={8: own})
        self.assertEqual(res.bindings[8].channel.split("=")[0], own[0])
        self.assertEqual(res.map_preferred[8].channel, best.channel)

    def test_an_led_matching_nothing_is_still_just_unbound(self):
        res = pipeline.analyze(self.blobs[6:], self.events, sensor_polarity=LEGACY,
                               expected={6: ("S0", "dark")})
        self.assertEqual(res.blob_verdicts[6], pipeline.SILENT)


if __name__ == "__main__":
    unittest.main()
