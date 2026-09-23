"""test_led_detector.py - the LED detector and constellation registration on
synthetic frames (tmaze.vision.led_detector), plus the led_map.json schema.

Frames are rendered here: a noisy grey board with small Gaussian LED dots that
follow a scripted on/off schedule - some lit at rest and dark during an event
(beam channel LEDs), some dark at rest and lit during a pulse (the reward valve).
No video files, no OpenCV needed beyond what the module imports optionally.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import unittest

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

from tmaze.core.sync import led_map as LM
from tmaze.core.sync import spans as S

if np is not None:
    from tmaze.vision import led_detector as D

W, H = 120, 100
BG = 60.0


# ----------------------------------------------------------------- synthetic rig

LEDS = {
    # name: (x, y, rest_lit, colour BGR gain)
    "beam_a": (30.3, 40.6, True, (0.2, 0.3, 1.0)),
    "beam_b": (42.7, 43.1, True, (0.2, 0.3, 1.0)),     # 12 px right, like the module pairs
    "beam_c": (30.9, 48.8, True, (0.2, 0.3, 1.0)),     # 8 px below beam_a
    "sol": (80.5, 20.2, False, (0.2, 1.0, 0.3)),       # green, lit only during the pulse
    "power": (100.0, 80.0, True, (0.2, 1.0, 0.3)),     # always on, never blinks
}
# event schedule: frame ranges during which the LED is in its ACTIVE (non-rest) state
SCHEDULE = {
    "beam_a": [(40, 48), (120, 131), (200, 206)],
    "beam_b": [(40, 48), (120, 131), (200, 206)],
    "beam_c": [(70, 79), (160, 172)],
    "sol": [(90, 97), (180, 187), (230, 237)],
    "power": [],
}
N_FRAMES = 260


def _dot(frame: np.ndarray, x: float, y: float, amp: float, gain) -> None:
    yy, xx = np.mgrid[0:H, 0:W]
    g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.1 ** 2)) * amp
    for c in range(3):
        frame[:, :, c] += g * gain[c]


def render(k: int, rng: np.random.Generator, shift=(0.0, 0.0)) -> np.ndarray:
    f = np.full((H, W, 3), BG, np.float32)
    f += rng.normal(0, 2.0, f.shape).astype(np.float32)
    for name, (x, y, rest_lit, gain) in LEDS.items():
        active = any(a <= k < b for a, b in SCHEDULE[name])
        lit = rest_lit != active
        if lit:
            _dot(f, x + shift[0], y + shift[1], 140.0, gain)
    return np.clip(f, 0, 255).astype(np.uint8)


def frames(n=N_FRAMES, seed=0, shift=(0.0, 0.0)):
    rng = np.random.default_rng(seed)
    return [render(k, rng, shift) for k in range(n)]


@unittest.skipIf(np is None, "numpy not installed")
class TestBlobs(unittest.TestCase):
    def setUp(self):
        self.p = LM.DetectorParams(blob_thresh=30.0)
        self.ext = D.Extremes(self.p.sample_every)
        for fr in frames():
            self.ext.add(fr)

    def test_blinking_leds_found_at_their_positions_and_directions(self):
        blobs, floor = D.detect_blobs(self.ext, self.p)
        self.assertEqual(len(blobs), 4, [(b.x, b.y) for b in blobs])   # power LED never blinks
        self.assertLess(floor, 15.0)                                  # noise ~2 sigma; LEDs 100+
        by_name = {}
        for name, (x, y, _, _) in LEDS.items():
            near = [b for b in blobs if abs(b.x - x) < 0.6 and abs(b.y - y) < 0.6]
            if name != "power":
                self.assertEqual(len(near), 1, name)
                by_name[name] = near[0]
            else:
                self.assertEqual(near, [])
        self.assertEqual(by_name["beam_a"].direction, D.OFF)     # lit at rest, goes dark
        self.assertEqual(by_name["sol"].direction, D.ON)
        # ids are stable: numbered top-to-bottom, left-to-right
        self.assertEqual([b.id for b in blobs], [0, 1, 2, 3])
        self.assertEqual(blobs[0].y, min(b.y for b in blobs))

    def test_neighbours_8px_apart_are_separate_blobs(self):
        blobs, _ = D.detect_blobs(self.ext, self.p)
        xs = sorted((round(b.x), round(b.y)) for b in blobs if b.x < 50)
        self.assertEqual(xs, [(30, 41), (31, 49), (43, 43)])

    def test_threshold_above_contrast_finds_nothing(self):
        blobs, _ = D.detect_blobs(self.ext, LM.DetectorParams(blob_thresh=250.0))
        self.assertEqual(blobs, [])

    def test_merge_blobs_marks_both_directions(self):
        on = [D.Blob(0, 10.0, 10.0, 3, 90.0, D.ON)]
        off = [D.Blob(0, 10.4, 9.7, 4, 70.0, D.OFF), D.Blob(1, 40.0, 10.0, 3, 80.0, D.OFF)]
        m = D.merge_blobs(on, off, 2.0)
        self.assertEqual([(b.id, b.direction, b.peak, b.area) for b in m],
                         [(0, D.BOTH, 90.0, 4), (1, D.OFF, 80.0, 3)])


@unittest.skipIf(np is None, "numpy not installed")
class TestBoardSearchMask(unittest.TestCase):
    """The operator-drawn board search polygon (ADR-0195): everything outside it
    is blanked so it neither spawns blobs nor lifts the noise floor."""

    def test_polygon_mask_true_inside_only(self):
        m = D.polygon_mask(10, 10, [(2, 2), (8, 2), (8, 8), (2, 8)])
        self.assertTrue(m[5, 5])
        self.assertFalse(m[0, 0])
        self.assertFalse(m[9, 9])
        # fewer than 3 vertices -> all False (a no-op mask)
        self.assertFalse(D.polygon_mask(10, 10, [(1, 1), (2, 2)]).any())

    def test_mask_extremes_excludes_outside_blobs(self):
        p = LM.DetectorParams(blob_thresh=30.0)
        ext = D.Extremes(p.sample_every)
        for fr in frames():
            ext.add(fr)
        # a box around the left cluster only (beam_a/b/c); sol (80,20) is outside
        D.mask_extremes(ext, [(20, 30), (55, 30), (55, 55), (20, 55)])
        blobs, _ = D.detect_blobs(ext, p)
        self.assertEqual(len(blobs), 3, [(b.x, b.y) for b in blobs])
        for b in blobs:
            self.assertTrue(20 <= b.x <= 55 and 30 <= b.y <= 55, (b.x, b.y))

    def test_mask_removes_outside_noise_and_keeps_inside_led(self):
        # a rest-lit LED on the LEFT, and a bright smear sweeping the RIGHT (a rat):
        # the smear inflates the floor and spawns blobs until the mask blanks it.
        p = LM.DetectorParams(blob_thresh=30.0)
        rng = np.random.default_rng(5)
        yy, xx = np.mgrid[0:H, 0:W]
        ext = D.Extremes(p.sample_every)
        for k in range(120):
            f = np.full((H, W, 3), BG, np.float32)
            f += rng.normal(0, 2.0, f.shape).astype(np.float32)
            if not (40 <= k < 46):                     # left LED blinks off briefly
                _dot(f, 30.0, 40.0, 140.0, (0.2, 0.3, 1.0))
            cx = 70 + (k % 40)                          # smear sweeps the right half
            smear = np.exp(-((xx - cx) ** 2 + (yy - 50) ** 2) / (2 * 5.0 ** 2)) * 180.0
            for c in range(3):
                f[:, :, c] += smear
            ext.add(np.clip(f, 0, 255).astype(np.uint8))
        _blobs_all, floor_all = D.detect_blobs(ext, p)
        D.mask_extremes(ext, [(15, 25), (50, 25), (50, 60), (15, 60)])
        blobs, floor = D.detect_blobs(ext, p)
        self.assertLessEqual(floor, floor_all)                 # right-side noise gone
        self.assertTrue(any(abs(b.x - 30) < 1.5 and abs(b.y - 40) < 1.5 for b in blobs))
        for b in blobs:
            self.assertTrue(15 <= b.x <= 50 and 25 <= b.y <= 60, (b.x, b.y))


@unittest.skipIf(np is None, "numpy not installed")
class TestTraces(unittest.TestCase):
    def setUp(self):
        self.p = LM.DetectorParams(blob_thresh=30.0)
        self.frames = frames()
        ext = D.Extremes(self.p.sample_every)
        for fr in self.frames:
            ext.add(fr)
        self.blobs, _ = D.detect_blobs(ext, self.p)
        self.by_name = {name: next(b for b in self.blobs if abs(b.x - x) < 0.6 and abs(b.y - y) < 0.6)
                        for name, (x, y, _, _) in LEDS.items() if name != "power"}
        sampler = D.TraceSampler((H, W), self.blobs, self.p)
        self.grey = np.zeros((len(self.frames), len(self.blobs)), np.float32)
        self.rmg = np.zeros_like(self.grey)
        for k, fr in enumerate(self.frames):
            self.grey[k], self.rmg[k] = sampler.sample(fr)

    def test_trace_swings_between_two_levels(self):
        j = self.by_name["beam_a"].id
        tr = self.grey[:, j]
        dark = [tr[k] for a, b in SCHEDULE["beam_a"] for k in range(a, b)]
        lit = [tr[k] for k in range(N_FRAMES) if not any(a <= k < b for a, b in SCHEDULE["beam_a"])]
        self.assertLess(max(dark), 10.0)
        self.assertGreater(min(lit), 25.0)      # red dot: grey gain 0.5 of the 140 amplitude

    def test_red_led_is_positive_in_red_minus_green(self):
        j_red = self.by_name["beam_a"].id
        j_green = self.by_name["sol"].id
        self.assertGreater(self.rmg[0, j_red], 20.0)               # lit at rest, red
        self.assertLess(self.rmg[92, j_green], -20.0)              # lit during pulse, green

    def test_blob_spans_reproduce_the_schedule(self):
        fr = [S.Frame(1000 + k, 1.7e9 + k * 0.04, 10 ** 9 * k) for k in range(N_FRAMES)]
        j = self.by_name["beam_a"].id
        bs = D.blob_spans(j, self.grey[:, j], fr, 1e-9)
        self.assertEqual([(s.ref0 - 1000, s.ref1 - 1000) for s in bs.dark], SCHEDULE["beam_a"])
        self.assertEqual(len(bs.lit), len(SCHEDULE["beam_a"]) - 1)   # lit between the crossings
        j = self.by_name["sol"].id
        bs = D.blob_spans(j, self.grey[:, j], fr, 1e-9)
        self.assertEqual([(s.ref0 - 1000, s.ref1 - 1000) for s in bs.lit], SCHEDULE["sol"])
        self.assertEqual(bs.blob, j)

    def test_neighbour_flash_does_not_leak_into_the_ring(self):
        # beam_a and beam_c are 8 px apart: c's disk is inside a's ring radius, and
        # is excluded from it, so c going dark must not move a's trace.
        j = self.by_name["beam_a"].id
        during_c = self.grey[72:78, j].mean()
        quiet = self.grey[5:35, j].mean()
        self.assertLess(abs(during_c - quiet), 6.0)


# ------------------------------------------------------------------ registration

REF = np.array([[80.2, 120.0], [47.0, 213.0], [59.0, 216.0], [170.0, 232.0], [182.0, 235.0],
                [108.0, 236.0], [120.0, 239.0], [168.0, 241.0], [181.0, 243.0],
                [106.0, 245.0], [118.0, 248.0]]) if np is not None else None   # the spike's 11 LEDs


@unittest.skipIf(np is None, "numpy not installed")
class TestRegistration(unittest.TestCase):
    def test_identity(self):
        r = D.register(REF, REF)
        self.assertEqual(r.verdict, D.REG_OK)
        self.assertEqual(r.pairs, [(i, i) for i in range(len(REF))])
        self.assertLess(r.rms_px, 1e-6)
        self.assertAlmostEqual(r.transform.scale, 1.0, places=6)

    def test_shift_rotation_scale_recovered_with_noise_dropout_and_a_spurious_blob(self):
        true = D.Similarity(1.03, math.radians(4.0), 7.5, -3.2)
        rng = np.random.default_rng(1)
        obs = true.apply(REF) + rng.normal(0, 0.3, REF.shape)
        obs = np.delete(obs, 5, axis=0)                          # one LED silent this session
        obs = np.vstack([obs, [[20.0, 20.0]]])                   # one blob that is not an LED
        rng.shuffle(obs)
        r = D.register(obs, REF)
        self.assertEqual(r.verdict, D.REG_OK)
        self.assertEqual(r.n_inliers, len(REF) - 1)
        self.assertEqual(r.unmatched_reference, [5])
        self.assertEqual(len(r.unmatched_observed), 1)
        self.assertLess(r.rms_px, 0.7)
        self.assertAlmostEqual(r.transform.scale, 1.03, delta=0.01)
        self.assertAlmostEqual(math.degrees(r.transform.theta), 4.0, delta=0.5)
        # every inlier pairs the right reference LED with its moved copy
        for ref_i, obs_i in r.pairs:
            self.assertLess(np.linalg.norm(true.apply(REF[ref_i]) - obs[obs_i]), 1.0)

    def test_row_of_identical_leds_with_one_silent_is_ambiguous(self):
        row = np.array([[0.0, 0.0], [8.0, 0.0], [16.0, 0.0], [24.0, 0.0]])
        obs = row[:3]                        # the last LED never blinked
        r = D.register(obs, row, min_inliers=3)
        self.assertEqual(r.verdict, D.REG_AMBIGUOUS)
        self.assertEqual(r.n_inliers, 3)
        self.assertEqual(len(r.alternatives), 1)         # shifted by one pitch
        self.assertLess(r.transform.displacement(row), 1e-6)   # identity preferred among ties
        self.assertAlmostEqual(abs(r.alternatives[0].tx), 8.0, delta=1e-6)

    def test_too_few_points_or_large_residual_fails(self):
        self.assertEqual(D.register(REF[:1], REF).verdict, D.REG_FAILED)
        rng = np.random.default_rng(2)
        r = D.register(rng.uniform(0, 300, (11, 2)), REF)     # unrelated point cloud
        self.assertIn(r.verdict, (D.REG_FAILED, D.REG_AMBIGUOUS))
        self.assertLess(r.n_inliers, 4)

    def test_similarity_fit_and_two_pairs_agree(self):
        t = D.Similarity(0.97, math.radians(-6.0), -4.0, 2.5)
        dst = t.apply(REF)
        f = D.Similarity.fit(REF, dst)
        self.assertAlmostEqual(f.scale, t.scale, places=6)
        self.assertAlmostEqual(f.theta, t.theta, places=6)
        self.assertAlmostEqual(f.tx, t.tx, places=5)
        g = D.Similarity.from_two_pairs(REF[0], REF[3], dst[0], dst[3])
        self.assertAlmostEqual(g.theta, t.theta, places=6)
        self.assertLess(np.abs(g.apply(REF) - dst).max(), 1e-6)

    def test_real_shift_of_the_whole_frame_is_found_from_frames(self):
        # the same synthetic board rendered 3 px right, 2 px down
        p = LM.DetectorParams(blob_thresh=30.0)
        ext0, ext1 = D.Extremes(), D.Extremes()
        for fr in frames(shift=(0.0, 0.0)):
            ext0.add(fr)
        for fr in frames(seed=3, shift=(3.0, 2.0)):
            ext1.add(fr)
        b0, _ = D.detect_blobs(ext0, p)
        b1, _ = D.detect_blobs(ext1, p)
        r = D.register([(b.x, b.y) for b in b1], [(b.x, b.y) for b in b0], min_inliers=4)
        self.assertEqual(r.verdict, D.REG_OK)
        self.assertAlmostEqual(r.transform.tx, 3.0, delta=0.4)
        self.assertAlmostEqual(r.transform.ty, 2.0, delta=0.4)
        self.assertAlmostEqual(r.transform.scale, 1.0, delta=0.02)


# ----------------------------------------------------------------- fiducials

# Persistent (always-on) colour LEDs spread in 2D, plus a blinking channel LED in
# the middle and a brief bright "hand" that must NOT be mistaken for a fiducial.
FIDS = {
    "r_tl": (18.0, 18.0, (0.2, 0.3, 1.0)),
    "g_tr": (98.0, 20.0, (0.2, 1.0, 0.3)),
    "r_bl": (20.0, 82.0, (0.2, 0.3, 1.0)),
    "g_br": (95.0, 80.0, (0.2, 1.0, 0.3)),
    "g_top": (58.0, 12.0, (0.2, 1.0, 0.3)),
}
BLINK_XY = (56.0, 50.0)                 # a channel LED inside the fiducial hull
BLINK_ON = [(a, a + 6) for a in range(20, 150, 18)]     # ~33% duty
HAND_SPAN = (30, 44)                    # ~9% of frames: a transient bright smear


def fid_render(k: int, rng: "np.random.Generator") -> np.ndarray:
    f = np.full((H, W, 3), BG, np.float32)
    f += rng.normal(0, 2.0, f.shape).astype(np.float32)
    for _name, (x, y, gain) in FIDS.items():
        _dot(f, x, y, 150.0, gain)                      # always on
    if any(a <= k < b for a, b in BLINK_ON):
        _dot(f, BLINK_XY[0], BLINK_XY[1], 150.0, (0.2, 0.3, 1.0))
    if HAND_SPAN[0] <= k < HAND_SPAN[1]:                # a broad, bright, neutral smear
        yy, xx = np.mgrid[0:H, 0:W]
        smear = np.exp(-((xx - 40) ** 2 + (yy - 62) ** 2) / (2 * 6.0 ** 2)) * 170.0
        for c in range(3):
            f[:, :, c] += smear
    return np.clip(f, 0, 255).astype(np.uint8)


def fid_frames(n=150, seed=1):
    rng = np.random.default_rng(seed)
    return [fid_render(k, rng) for k in range(n)]


@unittest.skipIf(np is None, "numpy not installed")
class TestFiducials(unittest.TestCase):
    def setUp(self):
        self.p = LM.DetectorParams()
        self.ext = D.Extremes(self.p.sample_every)
        for fr in fid_frames():
            self.ext.add(fr)

    def test_finds_persistent_colour_leds_only(self):
        fids = D.detect_fiducials(self.ext, self.p)
        # exactly the five persistent LEDs - not the blinking one, not the hand
        self.assertEqual(len(fids), 5, [(f.x, f.y, f.color) for f in fids])
        by_pos = {(round(f.x), round(f.y)): f.color for f in fids}
        for _n, (x, y, gain) in FIDS.items():
            near = min(by_pos, key=lambda q: (q[0] - x) ** 2 + (q[1] - y) ** 2)
            self.assertLess((near[0] - x) ** 2 + (near[1] - y) ** 2, 4.0)
            self.assertEqual(by_pos[near], LM.RED if gain[2] > gain[1] else LM.GREEN)
        # nothing found at the blinking LED or the hand smear
        for f in fids:
            self.assertGreater((f.x - BLINK_XY[0]) ** 2 + (f.y - BLINK_XY[1]) ** 2, 25.0)

    def test_detect_fiducials_excludes_rest_lit_channel_leds(self):
        # a red LED lit AT REST but dark during two brief events (a beam LED) looks
        # persistent (on >90%), yet it must be excluded from the fiducials because
        # it also blinks - only the truly always-on LED is a fiducial.
        rng = np.random.default_rng(2)
        dark_ev = [(43, 47), (93, 97)]        # off the 10-frame sample grid: seen lit in samples
        fr = []
        for k in range(150):
            f = np.full((H, W, 3), BG, np.float32)
            f += rng.normal(0, 2.0, f.shape).astype(np.float32)
            _dot(f, 90.0, 80.0, 150.0, (0.2, 1.0, 0.3))        # green, always on (a fiducial)
            if not any(a <= k < b for a, b in dark_ev):
                _dot(f, 60.0, 50.0, 150.0, (0.2, 0.3, 1.0))    # red, rest-lit beam LED
            fr.append(np.clip(f, 0, 255).astype(np.uint8))
        ext = D.Extremes(self.p.sample_every)
        for f in fr:
            ext.add(f)
        without = D.detect_fiducials(ext, self.p)
        self.assertTrue(any(f.color == LM.RED for f in without))    # rest-lit red looks persistent
        blinking, _ = D.detect_blobs(ext, self.p)
        kept = D.detect_fiducials(ext, self.p, exclude=blinking)
        self.assertEqual([f.color for f in kept], [LM.GREEN])       # the beam LED is excluded
        self.assertLess((kept[0].x - 90.0) ** 2 + (kept[0].y - 80.0) ** 2, 4.0)

    def test_blinking_led_is_still_found_by_the_blink_detector(self):
        blobs, _ = D.detect_blobs(self.ext, self.p)
        self.assertTrue(any((b.x - BLINK_XY[0]) ** 2 + (b.y - BLINK_XY[1]) ** 2 < 4.0
                            for b in blobs))

    def test_fiducial_hull_roi_contains_the_channel_led(self):
        fids = D.detect_fiducials(self.ext, self.p)
        poly = LM.board_poly_around([(f.x, f.y) for f in fids], self.p.hull_margin_px, (W, H))
        self.assertTrue(LM.point_in_poly(BLINK_XY, poly))

    def test_colour_labels_break_registration_ambiguity(self):
        ref = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
        obs = ref + [(40.0, 0.0)]                 # a shift-by-one-pitch also fits 4
        plain = D.register(obs, ref, tol_px=1.5, min_inliers=4)
        self.assertEqual(plain.verdict, D.REG_AMBIGUOUS)
        colours = [LM.RED, LM.GREEN, LM.RED, LM.GREEN]
        labelled = D.register(obs, ref, tol_px=1.5, min_inliers=4,
                              ref_labels=colours, obs_labels=colours + [LM.RED])
        self.assertEqual(labelled.verdict, D.REG_OK)
        self.assertEqual(labelled.n_inliers, 4)
        self.assertLess(labelled.transform.displacement(np.array(ref)), 1e-6)

    def test_hybrid_blobs_fills_missing_leds_and_keeps_extras(self):
        expected = [(10.0, 10.0), (20.0, 20.0), (30.0, 30.0)]
        detected = [D.Blob(0, 10.2, 10.1, 3, 90.0, D.OFF), D.Blob(1, 99.0, 5.0, 2, 80.0, D.ON)]
        out = D.hybrid_blobs(expected, detected, tol_px=2.0)
        by_exp = {ei: (b, det) for b, ei, det in out}
        self.assertTrue(by_exp[0][1])                       # LED0 matched a detected blob
        self.assertFalse(by_exp[1][1])                      # LED1 synthetic (fallback)
        self.assertFalse(by_exp[2][1])                      # LED2 synthetic (fallback)
        self.assertAlmostEqual(by_exp[1][0].x, 20.0)
        extras = [(b, ei) for b, ei, det in out if ei is None]
        self.assertEqual(len(extras), 1)                    # the (99, 5) blob is unmatched
        self.assertEqual(len(out), 4)
        self.assertEqual([b.id for b, _e, _d in out], [0, 1, 2, 3])   # renumbered

    @unittest.skipUnless(np is not None and D.cv2 is not None, "cv2 not available")
    def test_overlay_draws_without_error(self):
        canvas = np.full((H, W, 3), 40, np.uint8)
        fids = D.detect_fiducials(self.ext, self.p)
        blobs, _ = D.detect_blobs(self.ext, self.p)
        poly = LM.board_poly_around([(f.x, f.y) for f in fids], self.p.hull_margin_px, (W, H))
        img = D.draw_overlay(canvas, poly, fids, blobs, labels={blobs[0].id: "S2_a"})
        self.assertEqual(img.shape, (H, W, 3))
        self.assertEqual(img.dtype, np.uint8)
        self.assertTrue((img != 40).any())                  # something was drawn


# ------------------------------------------------------------------- led_map

class TestLedMap(unittest.TestCase):
    def _map(self) -> LM.LedMap:
        return LM.LedMap("600_20260903_134552", (826, 570, 1004, 740), [
            LM.LedEntry("SOL2.1_a", (880.2, 590.0), "SOL2.1", "lit", 0.031),
            LM.LedEntry("S3_a", (968.0, 711.0), "S3=0", "dark", 0.073, "input side"),
            LM.LedEntry("blob_11", (900.0, 700.0)),
        ], LM.DetectorParams(blob_thresh=32.0), (1280, 1024), "2026-09-09")

    def test_round_trip(self):
        m = self._map()
        with tempfile.TemporaryDirectory() as d:
            p = LM.save(m, os.path.join(d, "led_map.json"))
            back = LM.load(p)
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            self.assertEqual(LM.load(p).to_dict(), m.to_dict())
        self.assertEqual(raw["schema_version"], 3)
        self.assertEqual(back.session_id, m.session_id)
        self.assertEqual(back.board_rect, m.board_rect)
        self.assertEqual(back.frame_size, (1280, 1024))
        self.assertEqual(back.detector.blob_thresh, 32.0)
        self.assertEqual([e.led_id for e in back.leds], ["SOL2.1_a", "S3_a", "blob_11"])
        self.assertEqual(back.leds[1].channel, "S3=0")
        self.assertEqual(back.leds[1].note, "input side")
        self.assertFalse(back.leds[2].bound)
        self.assertEqual(back.constellation(), [(880.2, 590.0), (968.0, 711.0), (900.0, 700.0)])

    def test_validation_catches_typos(self):
        good = self._map().to_dict()
        bad_pol = json.loads(json.dumps(good)); bad_pol["leds"][0]["polarity"] = "on"
        dup = json.loads(json.dumps(good)); dup["leds"][1]["led_id"] = "SOL2.1_a"
        rect = json.loads(json.dumps(good)); rect["reference"]["board_rect"] = [10, 10, 5, 20]
        half = json.loads(json.dumps(good)); half["leds"][0]["polarity"] = None
        ver = json.loads(json.dumps(good)); ver["schema_version"] = 7
        for d in (bad_pol, dup, rect, half, ver):
            with self.assertRaises(ValueError):
                LM.from_dict(d)

    def test_board_rect_around_and_ids(self):
        self.assertEqual(LM.board_rect_around([(10.5, 20.0), (30.0, 25.9)], 5, (100, 100)),
                         (5, 15, 36, 31))
        self.assertEqual(LM.board_rect_around([(2.0, 3.0)], 10, (100, 100)), (0, 0, 13, 14))
        taken = ["S3_a"]
        self.assertEqual(LM.led_id_for("S3=0", "dark", taken, 8), "S3_b")
        self.assertEqual(LM.led_id_for("SOL2.1", "lit", [], 0), "SOL2.1_a")
        self.assertEqual(LM.led_id_for(None, None, [], 11), "blob_11")

    # --------------------------------------------------- fiducials + ROI (v2)

    def _fid_map(self) -> LM.LedMap:
        return LM.LedMap("600_20260903_134552", (826, 570, 1004, 740), [
            LM.LedEntry("SOL2.1_a", (900.0, 640.0), "SOL2.1", "lit", 0.031),
        ], LM.DetectorParams(), (1280, 1024), "2026-09-10", fiducials=[
            LM.Fiducial("red_0", (840.0, 590.0), LM.RED),
            LM.Fiducial("green_0", (980.0, 600.0), LM.GREEN),
            LM.Fiducial("red_1", (860.0, 720.0), LM.RED),
            LM.Fiducial("green_1", (975.0, 715.0), LM.GREEN),
        ], board_poly=[(830.0, 585.0), (985.0, 595.0), (980.0, 725.0), (855.0, 730.0)])

    def test_fiducials_round_trip(self):
        m = self._fid_map()
        with tempfile.TemporaryDirectory() as d:
            p = LM.save(m, os.path.join(d, "led_map.json"))
            back = LM.load(p)
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            self.assertEqual(LM.load(p).to_dict(), m.to_dict())
        self.assertEqual(raw["schema_version"], 3)
        self.assertEqual([f.fid_id for f in back.fiducials], ["red_0", "green_0", "red_1", "green_1"])
        self.assertEqual(back.fiducial_colors(), [LM.RED, LM.GREEN, LM.RED, LM.GREEN])
        self.assertEqual(back.fiducial_constellation()[0], (840.0, 590.0))
        self.assertEqual(back.board_poly[0], (830.0, 585.0))

    def test_board_search_poly_round_trips(self):
        m = self._fid_map()
        m.board_search_poly = [(820.0, 560.0), (1010.0, 575.0), (1005.0, 745.0), (835.0, 750.0)]
        with tempfile.TemporaryDirectory() as d:
            p = LM.save(m, os.path.join(d, "led_map.json"))
            back = LM.load(p)
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        self.assertEqual(raw["schema_version"], 3)
        self.assertEqual(raw["reference"]["board_search_poly"][0], [820.0, 560.0])
        self.assertEqual(back.board_search_poly[0], (820.0, 560.0))
        self.assertEqual(back.to_dict(), m.to_dict())

    def test_v2_map_without_search_poly_still_loads(self):
        v2 = self._fid_map().to_dict()
        v2["schema_version"] = 2
        v2["reference"].pop("board_search_poly", None)
        back = LM.from_dict(v2)
        self.assertIsNone(back.board_search_poly)
        self.assertEqual(back.board_poly[0], (830.0, 585.0))    # v2 hull still loads

    def test_v1_map_still_loads_without_fiducials(self):
        v1 = self._map().to_dict()
        v1["schema_version"] = 1
        v1.pop("fiducials", None)
        v1["reference"].pop("board_poly", None)
        back = LM.from_dict(v1)
        self.assertEqual(back.fiducials, [])
        self.assertIsNone(back.board_poly)
        self.assertEqual(len(back.leds), 3)          # blinking-LED registration still available

    def test_fiducial_validation_catches_typos(self):
        good = self._fid_map().to_dict()
        bad_color = json.loads(json.dumps(good)); bad_color["fiducials"][0]["color"] = "blue"
        dup = json.loads(json.dumps(good)); dup["fiducials"][1]["fid_id"] = "red_0"
        short_poly = json.loads(json.dumps(good)); short_poly["reference"]["board_poly"] = [[1, 2], [3, 4]]
        for d in (bad_color, dup, short_poly):
            with self.assertRaises(ValueError):
                LM.from_dict(d)

    def test_convex_hull_ignores_interior_points(self):
        square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (5.0, 5.0)]
        hull = LM.convex_hull(square)
        self.assertEqual(set(hull), {(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)})
        self.assertEqual(LM.poly_bbox(hull), (0, 0, 11, 11))

    def test_point_in_poly(self):
        poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        self.assertTrue(LM.point_in_poly((5.0, 5.0), poly))
        self.assertFalse(LM.point_in_poly((15.0, 5.0), poly))
        self.assertFalse(LM.point_in_poly((-1.0, 5.0), poly))

    def test_board_poly_around_grows_outward_and_contains_the_points(self):
        pts = [(20.0, 20.0), (40.0, 22.0), (38.0, 42.0), (18.0, 40.0), (30.0, 30.0)]
        poly = LM.board_poly_around(pts, margin_px=6, frame_size=(100, 100))
        for p in pts:                                # every fiducial stays inside the ROI
            self.assertTrue(LM.point_in_poly(p, poly), p)
        # the ROI is strictly larger than the bare hull's bounding box
        hb = LM.poly_bbox(LM.convex_hull(pts))
        pb = LM.poly_bbox(poly)
        self.assertLess(pb[0], hb[0])
        self.assertGreater(pb[2], hb[2])

    def test_board_poly_around_falls_back_to_rect_when_degenerate(self):
        poly = LM.board_poly_around([(5.0, 5.0), (9.0, 9.0)], margin_px=3, frame_size=(50, 50))
        self.assertEqual(len(poly), 4)               # a rectangle when the hull is a line
        self.assertTrue(LM.point_in_poly((7.0, 7.0), poly))


if __name__ == "__main__":
    unittest.main()
