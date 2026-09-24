"""test_led_detector_clip.py - the LED detector on a REAL 60-second board clip.

Fixture: ``tests/fixtures/sync_excerpt_600_20260903_134552_board.mp4`` (+ its
``_board.csv`` sidecar rows) - session frames 12305..13804, cropped to the
board (full-frame x 815-1025, y 560-744 - WIDER than the LED cluster so it also
contains the persistent red/green fiducial LEDs) and re-encoded with libx264 at
CRF 22, a little HARSHER than the rig's CRF 18, so the detector is tested under
slightly worse compression than it will see. The window sits inside the
five-minute ``sync_excerpt_600_20260903_134552.json`` excerpt whose LED spans
were produced from the full-resolution video, so this test checks that the
vision layer reproduces the pixels-to-spans step the core fixture was built on:

- the 11 blinking LEDs are found where the JSON fixture has them (after the
  crop offset), and registration against those positions is an identity;
- the spans the traces produce match the JSON fixture's spans frame for frame;
- feeding them to the core binds every LED to the expected channel;
- the persistent red/green fiducials are found and their hull ROI contains all
  11 blinking LEDs (the steady LEDs stay invisible to the blink detector).

Skipped when OpenCV cannot decode the clip.
"""
from __future__ import annotations

import json
import os
import unittest

try:
    import numpy as np
    import cv2
except Exception:  # pragma: no cover
    np = cv2 = None

from tmaze.core.phase3.bundle_format import POLARITY_LEGACY
from tmaze.core.sync import led_map as LM
from tmaze.core.sync import matching, pipeline
from tmaze.core.sync import spans as S

if np is not None:
    from tmaze.vision import led_detector as D

HERE = os.path.dirname(os.path.abspath(__file__))
SID = "600_20260903_134552"
FX_JSON = os.path.join(HERE, "fixtures", f"sync_excerpt_{SID}.json")
CLIP = os.path.join(HERE, "fixtures", f"sync_excerpt_{SID}_board.mp4")
CLIP_CSV = os.path.join(HERE, "fixtures", f"sync_excerpt_{SID}_board.csv")
SPIKE_CROP = (800, 470)          # the JSON fixture's crop_xy are relative to this origin


def _clip_readable() -> bool:
    if cv2 is None or not os.path.isfile(CLIP):
        return False
    cap = cv2.VideoCapture(CLIP)
    ok = cap.isOpened() and cap.read()[0]
    cap.release()
    return bool(ok)


@unittest.skipUnless(_clip_readable(), "OpenCV cannot read the board clip fixture")
class TestBoardClip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FX_JSON, encoding="utf-8") as f:
            cls.fx = json.load(f)
        with open(CLIP_CSV, encoding="utf-8") as f:
            cls.header, cls.frames = S.parse_sidecar_csv(f)
        cx0, cy0 = (int(v) for v in cls.header["crop"].split(",")[:2])
        cls.expected = np.array([[b["crop_xy"][0] + SPIKE_CROP[0] - cx0,
                                  b["crop_xy"][1] + SPIKE_CROP[1] - cy0] for b in cls.fx["blobs"]])
        cls.params = LM.DetectorParams()
        cls.blobs, cls.floor, cls.grey, cls.rmg, cls.n = D.scan_video(CLIP, None, 0, None, cls.params)
        cls.tick = S.cam_tick_s(cls.frames)
        cls.reg = D.register([(b.x, b.y) for b in cls.blobs], cls.expected)

    def test_sidecar_rows_match_the_clip(self):
        self.assertEqual(self.n, len(self.frames))
        self.assertEqual(self.frames[0].idx, 12305)
        self.assertAlmostEqual(self.tick, 1e-9, delta=2e-12)

    def test_eleven_leds_found_where_the_fixture_has_them(self):
        self.assertEqual(len(self.blobs), 11)
        for b in self.blobs:
            d = np.linalg.norm(self.expected - [b.x, b.y], axis=1).min()
            # ~1 px: this clip is a fresh CRF-22 re-encode of a WIDER crop than the
            # full-res video the JSON positions came from, so luma centroids drift
            # sub-pixel; still far tighter than the ~12 px LED spacing.
            self.assertLess(d, 1.3, b)
        # the reward LED lights up, the beam/lick LEDs go dark
        dirs = {b.id: b.direction for b in self.blobs}
        self.assertEqual(dirs[0], D.ON)
        self.assertTrue(all(dirs[k] == D.OFF for k in range(3, 11)))
        # ids are top-to-bottom: the reward LED (highest on the board) is blob 0
        self.assertEqual(self.blobs[0].y, min(b.y for b in self.blobs))

    def test_noise_floor_is_reported_and_below_the_leds(self):
        # CRF 22 lifts the floor to ~30 (the rig's CRF 18 measured ~25); the LEDs
        # peak at 78-190, so the default threshold still finds no false blob.
        self.assertLess(self.floor, 40.0)
        self.assertGreater(min(b.peak for b in self.blobs), 60.0)

    def test_registration_against_the_fixture_positions_is_identity(self):
        r = self.reg
        self.assertEqual(r.verdict, D.REG_OK)
        self.assertEqual(r.n_inliers, 11)
        # ~1 px sub-pixel drift from the fresh re-encode (see the LED-position test)
        self.assertLess(r.rms_px, 0.8)
        self.assertAlmostEqual(r.transform.scale, 1.0, delta=0.01)
        self.assertLess(abs(r.transform.tx) + abs(r.transform.ty), 1.6)
        self.assertEqual(r.pairs, [(i, i) for i in range(11)])     # same numbering both sides

    def test_spans_reproduce_the_fixture_frame_for_frame(self):
        f0, f1 = self.frames[0].idx, self.frames[-1].idx
        want_n = hit = 0
        for ref_i, obs_i in self.reg.pairs:
            fb = self.fx["blobs"][ref_i]
            bs = D.blob_spans(obs_i, self.grey[:, obs_i], self.frames, self.tick)
            for kind, got in (("lit", bs.lit), ("dark", bs.dark)):
                want = [s for s in fb[kind] if s[4] >= f0 + 2 and s[5] <= f1 - 2]
                got_fr = [(s.ref0, s.ref1) for s in got]
                hit += sum(1 for w in want
                           if any(abs(g[0] - w[4]) <= 1 and abs(g[1] - w[5]) <= 1 for g in got_fr))
                want_n += len(want)
        self.assertGreater(want_n, 200)                  # the lick pair alone gives ~200
        self.assertGreaterEqual(hit / want_n, 0.98)     # measured 242/243

    def test_persistent_fiducials_found_and_their_hull_contains_the_leds(self):
        ext = D.accumulate_extremes(CLIP, None, 0, None, self.params)
        blinking, _ = D.detect_blobs(ext, self.params)
        fids = D.detect_fiducials(ext, self.params, exclude=blinking)
        # several persistent LEDs, of both colours, and none coinciding with a
        # blinking LED (a rest-lit channel LED must not be mistaken for a fiducial)
        self.assertGreaterEqual(len(fids), 8)
        colours = {f.color for f in fids}
        self.assertEqual(colours, {LM.RED, LM.GREEN})
        for f in fids:
            self.assertGreater(min(np.hypot(f.x - b.x, f.y - b.y) for b in blinking), 2.0)
        # the fiducials' hull (grown by the margin) is a board ROI that contains
        # every blinking LED - so the auto-ROI never drops a channel LED
        poly = LM.board_poly_around([(f.x, f.y) for f in fids], self.params.hull_margin_px)
        for b in blinking:
            self.assertTrue(LM.point_in_poly((b.x, b.y), poly), (b.x, b.y))

    def test_every_led_binds_to_its_channel_from_one_minute(self):
        events = S.serial_spans(S.parse_serial_csv(self.fx["serial_rows"]))
        blob_spans = [D.blob_spans(b.id, self.grey[:, b.id], self.frames, self.tick) for b in self.blobs]
        res = pipeline.analyze(blob_spans, events, min_matched=2, min_pairs=10,
                               sensor_polarity=POLARITY_LEGACY)   # pre-v8 recording
        want = {0: ("SOL2.1", matching.LIT), 1: ("L2=0", matching.LIT), 2: ("L2=0", matching.LIT),
                3: ("S2=0", matching.DARK), 4: ("S2=0", matching.DARK),
                5: ("S5=0", matching.DARK), 6: ("S5=0", matching.DARK),
                7: ("S3=0", matching.DARK), 8: ("S3=0", matching.DARK),
                9: ("S4=0", matching.DARK), 10: ("S4=0", matching.DARK)}
        self.assertEqual({k: (b.channel, b.polarity) for k, b in res.bindings.items()}, want)
        self.assertEqual(res.verdict, "ok")


if __name__ == "__main__":
    unittest.main()
