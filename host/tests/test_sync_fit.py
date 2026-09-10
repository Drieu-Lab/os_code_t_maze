"""test_sync_fit.py - the offline sync_fit runnable (tmaze.tools.sync_fit).

Two layers, no rig and (mostly) no OpenCV:

- the pure pieces - the led_events row builder, the clock_fit dict, and the
  led_map builder - on a hand-built SyncRun (synthetic spans, no video);
- an end-to-end run of ``main`` over a throwaway bundle assembled from the
  committed board-clip fixture (the same 60 s clip test_led_detector_clip uses),
  which exercises detection + bootstrap binding + artifact writing on real
  pixels. That half skips when OpenCV cannot decode the clip.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tmaze.core.sync import clockfit, led_map as LM, matching, pipeline
from tmaze.core.sync import spans as S
from tmaze.tools import sync_fit

try:
    import cv2
    import numpy  # noqa: F401
except Exception:  # pragma: no cover
    cv2 = None

HERE = os.path.dirname(os.path.abspath(__file__))
SID = "600_20260903_134552"
FX_JSON = os.path.join(HERE, "fixtures", f"sync_excerpt_{SID}.json")
CLIP = os.path.join(HERE, "fixtures", f"sync_excerpt_{SID}_board.mp4")
CLIP_CSV = os.path.join(HERE, "fixtures", f"sync_excerpt_{SID}_board.csv")


def _clip_readable() -> bool:
    if cv2 is None or not os.path.isfile(CLIP):
        return False
    cap = cv2.VideoCapture(CLIP)
    ok = cap.isOpened() and cap.read()[0]
    cap.release()
    return bool(ok)


# --------------------------------------------------------- synthetic SyncRun

def _synthetic_run() -> sync_fit.SyncRun:
    """One beam LED (dark during S2=0) with a clean +40 ms lag at a 1 GHz tick."""
    rate, offset = 1_000_000.0, 5_000_000_000.0
    events = {"S2=0": [], "S2=1": []}
    dark = []
    host, fw = 1_700_000_000.0, 1_000
    for i in range(20):
        c0 = fw + 800 + i * 1000
        c1 = c0 + 200
        events["S2=1"].append(S.Span("S2=1", host, host + 0.8, c0 - 800, c0, 2 * i, 2 * i + 1, 0, S.FIRMWARE_MS))
        events["S2=0"].append(S.Span("S2=0", host + 0.8, host + 1.0, c0, c1, 2 * i, 2 * i + 1, 0, S.FIRMWARE_MS))
        dark.append(S.Span("blob0", host + 0.84, host + 1.04,
                           offset + rate * (c0 + 40), offset + rate * (c1 + 40),
                           1000 + 2 * i, 1000 + 2 * i + 1, 0, 1e-9))
        host += 1.0
    bs = pipeline.BlobSpans(0, [], dark)
    result = pipeline.analyze([bs], events, min_matched=3, min_pairs=10)

    class _Blob:
        def __init__(self, id, x, y):
            self.id, self.x, self.y, self.area, self.peak, self.direction = id, x, y, 5, 120.0, "off"

    header = {"codec": "h264", "fps": "25", "width": "1280", "height": "1024"}
    return sync_fit.SyncRun(SID, header, [_Blob(0, 970.0, 702.0)], 22.0,
                            {0: "S2_a"}, result, [bs], events, None, None,
                            (0, 500), 0.20, 0.06, None)


class TestPureBuilders(unittest.TestCase):
    def setUp(self):
        self.run = _synthetic_run()

    def test_binding_recovered(self):
        self.assertEqual(self.run.result.bindings[0].channel, "S2=0")
        self.assertEqual(self.run.result.verdict, clockfit.VERDICT_OK)

    def test_led_event_rows(self):
        rows = sync_fit.led_event_rows(self.run)
        self.assertTrue(rows)
        self.assertEqual(set(sync_fit.LED_EVENT_COLUMNS), set(rows[0]))
        # dark polarity: the span starts with the LED going OFF, ends ON
        edges = {r["edge"] for r in rows}
        self.assertEqual(edges, {clockfit.ON, clockfit.OFF})
        self.assertTrue(all(r["led_id"] == "S2_a" for r in rows))
        self.assertTrue(all(r["matched_event"] == "S2=0" for r in rows))
        # residuals are tiny (synthetic data sits exactly on the line)
        resid = [abs(r["residual_ms"]) for r in rows if r["residual_ms"] is not None]
        self.assertTrue(resid and max(resid) < 1.0)
        # rows carry the LED's frame index and camera tick, not the serial clock
        self.assertTrue(all(r["frame_idx"] is not None and r["cam_timestamp"] is not None for r in rows))

    def test_clock_fit_dict_shape(self):
        d = sync_fit.clock_fit_dict(self.run)
        self.assertEqual(d["method"], "led-fit")
        self.assertEqual(d["verdict"], clockfit.VERDICT_OK)
        self.assertFalse(d["led_map"]["used"])
        self.assertIn("S2_a", d["bindings"])
        self.assertAlmostEqual(d["fit"]["rate"], 1_000_000.0, delta=1.0)
        self.assertEqual(d["bindings"]["S2_a"]["channel"], "S2=0")
        self.assertIn("recall", d["bindings"]["S2_a"])

    def test_artifacts_round_trip(self):
        with tempfile.TemporaryDirectory() as dd:
            ep = os.path.join(dd, "led_events.csv")
            fp = os.path.join(dd, "clock_fit.json")
            sync_fit.write_led_events(ep, sync_fit.led_event_rows(self.run), SID)
            sync_fit.write_clock_fit(fp, self.run)
            with open(fp, encoding="utf-8") as f:
                back = json.load(f)
            with open(ep, encoding="utf-8") as f:
                lines = [ln for ln in f if not ln.startswith("#")]
        self.assertEqual(back["session_id"], SID)
        self.assertEqual(lines[0].strip().split(","), sync_fit.LED_EVENT_COLUMNS)

    def test_build_led_map_from_bootstrap(self):
        m = sync_fit.build_led_map(self.run)
        self.assertEqual(m.session_id, SID)
        self.assertEqual([e.led_id for e in m.leds], ["S2_a"])
        self.assertEqual(m.leds[0].channel, "S2=0")
        self.assertEqual(m.leds[0].polarity, matching.DARK)
        self.assertEqual(m.leds[0].ref_px, (970.0, 702.0))
        # a whole-frame bootstrap fits the rect around the LEDs (+ margin), clipped
        x0, y0, x1, y1 = m.board_rect
        self.assertTrue(x0 < 970 < x1 and y0 < 702 < y1)


class TestSelftest(unittest.TestCase):
    def test_selftest_passes(self):
        self.assertEqual(sync_fit.main(["--selftest"]), 0)

    def test_bundle_required_without_selftest(self):
        with self.assertRaises(SystemExit):
            sync_fit.main([])


def _make_bundle(root: str) -> str:
    """A throwaway bundle from the clip fixture: video/<id>.mp4 + sidecar +
    serial capture, laid out the way analyze_bundle expects."""
    bundle = os.path.join(root, SID)
    os.makedirs(os.path.join(bundle, "video"))
    os.makedirs(os.path.join(bundle, "serial"))
    shutil.copyfile(CLIP, os.path.join(bundle, "video", f"{SID}.mp4"))
    shutil.copyfile(CLIP_CSV, os.path.join(bundle, "video", f"{SID}.csv"))
    with open(FX_JSON, encoding="utf-8") as f:
        rows = json.load(f)["serial_rows"]
    with open(os.path.join(bundle, "serial", f"sensors_{SID}.csv"), "w", encoding="utf-8") as f:
        f.write("# wall_clock_iso,firmware_line\n")
        for r in rows:
            f.write(r + "\n")
    return bundle


@unittest.skipUnless(_clip_readable(), "OpenCV cannot read the board clip fixture")
class TestEndToEndOnClip(unittest.TestCase):
    def test_dry_run_bootstrap_binds_and_writes(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            out = os.path.join(root, "dryrun")
            # no host/led_map.json in play: point --board-rect at the clip's own
            # frame so the whole (already-cropped) board is searched, and bootstrap.
            rc = sync_fit.main([bundle, "--dry-run", "--out", out, "--board-rect", "0", "0", "158", "152",
                                "--min-matched", "2", "--min-pairs", "10"])
            self.assertEqual(rc, 0)
            ep = os.path.join(out, f"led_events_{SID}.csv")
            fp = os.path.join(out, f"clock_fit_{SID}.json")
            self.assertTrue(os.path.isfile(ep) and os.path.isfile(fp))
            # the bundle itself must be untouched by a dry run
            self.assertFalse(os.path.isdir(os.path.join(bundle, "sync")))
            with open(fp, encoding="utf-8") as f:
                fit = json.load(f)
        self.assertFalse(fit["led_map"]["used"])
        self.assertEqual(fit["verdict"], clockfit.VERDICT_OK)
        self.assertAlmostEqual(fit["fit"]["rate"], 1e9 * 1e-3, delta=2000.0)   # ~1e6 ticks/ms (1 GHz)
        channels = {b["channel"] for b in fit["bindings"].values()}
        self.assertIn("SOL2.1", channels)
        self.assertTrue({"S2=0", "S3=0", "S4=0", "S5=0"} <= channels)
        self.assertIn("L2=0", channels)

    def test_write_mode_creates_sync_folder(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            rc = sync_fit.main([bundle, "--board-rect", "0", "0", "158", "152"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(os.path.join(bundle, "sync", f"clock_fit_{SID}.json")))
            self.assertTrue(os.path.isfile(os.path.join(bundle, "sync", f"led_events_{SID}.csv")))

    def test_map_mode_writes_led_map(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            mp = os.path.join(root, "led_map.json")
            rc = sync_fit.main([bundle, "--map", "--map-out", mp, "--board-rect", "0", "0", "158", "152",
                                "--min-matched", "2", "--min-pairs", "10"])
            self.assertEqual(rc, 0)
            m = LM.load(mp)
        self.assertEqual(m.session_id, SID)
        self.assertEqual(len(m.leds), 11)
        self.assertEqual(sum(1 for e in m.leds if e.bound), 11)
        self.assertEqual(m.board_rect, (0, 0, 158, 152))


if __name__ == "__main__":
    unittest.main()
