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
    from tmaze.vision import led_detector as D
except Exception:  # pragma: no cover
    cv2 = D = None

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

    def test_serial_override_keeps_noncentral_valves(self):
        # a mapping session must read the FULL logger, whose left/right-reward and
        # rinse/drain acks (non-central SOL ports) the bundle capture drops.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "full.csv")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# wall_clock_iso,firmware_line (ms,TYPE,payload...)\n")
                f.write("2026-09-03T13:53:50.100,1000,SOL,3,1,300\n")
                f.write("2026-09-03T13:53:51.100,2000,SOL,3,1,300\n")
            ev = sync_fit._read_events(d, "x", serial_path=p)
        self.assertIn("SOL3.1", ev)
        self.assertEqual(len(ev["SOL3.1"]), 2)

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
    # the rig-written session manifest sync_fit reads back to register the sync/
    # streams + flip fiducial.method (defaults: 3 streams, host-t0 fiducial)
    manifest = sync_fit.SessionManifest(
        session_id=SID, animal="600",
        t0={"wall": 1.0, "mono": 2.0, "iso": "2026-09-03T13:53:00.000"},
        streams={"phase3_task": f"task/phase3_{SID}.csv",
                 "camera_video": f"video/{SID}.mp4",
                 "camera_sidecar": f"video/{SID}.csv"})
    with open(os.path.join(bundle, f"session_{SID}.json"), "w", encoding="utf-8") as f:
        f.write(manifest.render())
    return bundle


@unittest.skipUnless(_clip_readable(), "OpenCV cannot read the board clip fixture")
class TestEndToEndOnClip(unittest.TestCase):
    def test_dry_run_bootstrap_binds_and_writes(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            out = os.path.join(root, "dryrun")
            # no host/led_map.json in play: point --board-rect at the clip's own
            # frame so the whole (already-cropped) board is searched, and bootstrap.
            rc = sync_fit.main([bundle, "--dry-run", "--out", out, "--board-rect", "0", "0", "210", "184",
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
            rc = sync_fit.main([bundle, "--board-rect", "0", "0", "210", "184"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(os.path.join(bundle, "sync", f"clock_fit_{SID}.json")))
            self.assertTrue(os.path.isfile(os.path.join(bundle, "sync", f"led_events_{SID}.csv")))

    def test_write_mode_registers_streams_and_flips_fiducial(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            rc = sync_fit.main([bundle, "--board-rect", "0", "0", "210", "184"])
            self.assertEqual(rc, 0)
            with open(os.path.join(bundle, f"session_{SID}.json"), encoding="utf-8") as f:
                man = json.load(f)
        # the two sync files are announced with bundle-relative paths
        self.assertEqual(man["streams"]["sync_led_events"], f"sync/led_events_{SID}.csv")
        self.assertEqual(man["streams"]["sync_clock_fit"], f"sync/clock_fit_{SID}.json")
        # the time anchor flips to led-fit, but the FW-0018 pending note stays
        self.assertEqual(man["fiducial"]["method"], "led-fit")
        self.assertIn("FW-0018", man["fiducial"]["pending"])
        # what the rig wrote is otherwise preserved
        self.assertEqual(man["streams"]["camera_video"], f"video/{SID}.mp4")

    def test_dry_run_leaves_manifest_byte_identical(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            mpath = os.path.join(bundle, f"session_{SID}.json")
            with open(mpath, "rb") as f:
                before = f.read()
            out = os.path.join(root, "dryrun")
            rc = sync_fit.main([bundle, "--dry-run", "--out", out, "--board-rect", "0", "0", "210", "184",
                                "--min-matched", "2", "--min-pairs", "10"])
            self.assertEqual(rc, 0)
            with open(mpath, "rb") as f:
                after = f.read()
        self.assertEqual(before, after)   # a dry run never edits the manifest

    def test_overlay_writes_clock_fit_plot(self):
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib not installed (optional plot dep)")
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            # lower thresholds so this short clip yields a fit (needed for the plot)
            rc = sync_fit.main([bundle, "--overlay", "--board-rect", "0", "0", "210", "184",
                                "--min-matched", "2", "--min-pairs", "10"])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(
                os.path.join(bundle, "sync", f"clock_fit_{SID}.png")))

    def test_map_mode_writes_led_map(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = _make_bundle(root)
            mp = os.path.join(root, "led_map.json")
            rc = sync_fit.main([bundle, "--map", "--map-out", mp, "--board-rect", "0", "0", "210", "184",
                                "--min-matched", "2", "--min-pairs", "10"])
            self.assertEqual(rc, 0)
            m = LM.load(mp)
            overlay = os.path.splitext(mp)[0] + f"_overlay_{SID}.png"
            self.assertTrue(os.path.isfile(overlay))       # --map writes the QA overlay
        self.assertEqual(m.session_id, SID)
        self.assertEqual(len(m.leds), 11)
        self.assertEqual(sum(1 for e in m.leds if e.bound), 11)
        # v2 map: persistent fiducials of both colours + a board ROI polygon whose
        # bounding box is the (fiducial-derived) search rect, inside the crop
        self.assertGreaterEqual(len(m.fiducials), 8)
        self.assertEqual({f.color for f in m.fiducials}, {LM.RED, LM.GREEN})
        self.assertIsNotNone(m.board_poly)
        x0, y0, x1, y1 = m.board_rect       # clipped to the 210x184 frame (+1 half-open)
        self.assertTrue(0 <= x0 < x1 <= 211 and 0 <= y0 < y1 <= 185)
        # every mapped LED lies inside the ROI polygon
        for e in m.leds:
            self.assertTrue(LM.point_in_poly(e.ref_px, m.board_poly), e.led_id)

    def _build_map(self, root: str) -> tuple:
        bundle = _make_bundle(root)
        mp = os.path.join(root, "led_map.json")
        sync_fit.main([bundle, "--map", "--map-out", mp, "--board-rect", "0", "0", "210", "184",
                       "--min-matched", "2", "--min-pairs", "10"])
        return bundle, mp

    def test_v2_map_run_registers_pose_and_locates_every_led(self):
        with tempfile.TemporaryDirectory() as root:
            bundle, mp = self._build_map(root)
            # run against the v2 map; the stored (fiducial-derived) rect drives the search
            run = sync_fit.analyze_bundle(bundle, map_path=mp, min_matched=2, min_pairs=10)
        self.assertIsNotNone(run.fid_registration)
        self.assertEqual(run.fid_registration.verdict, D.REG_OK)
        self.assertAlmostEqual(run.fid_registration.transform.scale, 1.0, delta=0.02)
        self.assertLess(abs(run.fid_registration.transform.tx)
                        + abs(run.fid_registration.transform.ty), 2.0)
        self.assertIsNotNone(run.board_poly)
        self.assertEqual(len(run.led_ids), 11)             # all 11 located (hybrid)
        self.assertIsNotNone(run.result.fit)
        self.assertEqual(run.result.verdict, clockfit.VERDICT_OK)
        d = sync_fit.clock_fit_dict(run)
        self.assertEqual(d["fiducials"]["registration"]["verdict"], D.REG_OK)
        self.assertTrue(d["led_map"]["used"])

    def test_v1_map_without_fiducials_still_registers_the_constellation(self):
        with tempfile.TemporaryDirectory() as root:
            bundle, mp = self._build_map(root)
            with open(mp, encoding="utf-8") as f:
                d = json.load(f)
            d["schema_version"] = 1                          # downgrade to a v1 map
            d.pop("fiducials", None)
            d["reference"].pop("board_poly", None)
            v1 = os.path.join(root, "led_map_v1.json")
            with open(v1, "w", encoding="utf-8") as f:
                json.dump(d, f)
            run = sync_fit.analyze_bundle(bundle, map_path=v1, board_rect=(0, 0, 210, 184),
                                          min_matched=2, min_pairs=10)
        self.assertIsNone(run.fid_registration)            # no fiducials in a v1 map
        self.assertIsNotNone(run.registration)             # falls back to the LED constellation
        self.assertEqual(run.registration.verdict, D.REG_OK)
        self.assertEqual(len(run.led_ids), 11)


if __name__ == "__main__":
    unittest.main()
