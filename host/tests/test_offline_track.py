"""Headless tests for offline re-tracking of recorded video (docs/decisions/0211).

Pure part (``tmaze.core.offline_track``, stdlib only): which files a run uses and
where each came from, how tracker settings are layered (model/defaults ->
session snapshot -> params file -> --set), output naming + overwrite refusal,
background sample spacing, and the run tally.

End-to-end part (needs numpy + OpenCV + an mp4 writer; skipped otherwise): a
tiny synthetic "session" - a dark square moving over a plain background, with a
matching sidecar - is tracked by ``tmaze.vision.offline_tracker.run``, with and
without a background PNG, and over a sub-range with warm-up.

Run from host/:  python -m unittest tests.test_offline_track
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import unittest

from tmaze.core import offline_track as OT

SID = "600_20260101_120000"


def _touch(path: str, data: bytes = b"x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _write_json(path: str, obj) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    return path


class _TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="offline_track_")
        self.bundle = os.path.join(self.tmp, SID)
        self.models = os.path.join(self.tmp, "ratmodels")
        os.makedirs(self.models)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _video(self) -> str:
        return _touch(os.path.join(self.bundle, "video", SID + ".mp4"))


class TestResolveSettings(_TmpCase):
    def test_no_video_raises(self):
        os.makedirs(self.bundle)
        with self.assertRaises(FileNotFoundError):
            OT.resolve_settings(self.bundle, ratmodels_dir=self.models)

    def test_bundle_snapshot_is_default(self):
        self._video()
        cfg = os.path.join(self.bundle, "config")
        _touch(os.path.join(cfg, "model_animal_ratmodel_x.npz"))
        _write_json(os.path.join(cfg, "roi_polygon.json"), {"polygon_norm": []})
        _write_json(os.path.join(cfg, "landmarks.json"), {"landmarks": []})
        _write_json(os.path.join(cfg, "runtime_params.json"),
                    {"tracker": {"thresh": 19}, "camera": {}})
        _touch(os.path.join(self.bundle, "video", SID + "_background.png"))
        _touch(os.path.join(self.models, OT.FALLBACK_MODEL_NAME))
        s = OT.resolve_settings(self.bundle, ratmodels_dir=self.models)
        self.assertEqual(s.model.source, OT.SRC_BUNDLE)
        self.assertTrue(s.model.path.endswith("model_animal_ratmodel_x.npz"))
        self.assertEqual(len(s.model.sha256), 64)
        self.assertEqual(s.roi.source, OT.SRC_BUNDLE)
        self.assertEqual(s.landmarks.source, OT.SRC_BUNDLE)
        self.assertEqual(s.background.source, OT.SRC_BUNDLE)
        self.assertEqual(s.snapshot_params, {"thresh": 19})
        self.assertIsNone(s.blocker_model)   # no blocker file in ratmodels

    def test_fallbacks(self):
        self._video()
        _touch(os.path.join(self.models, OT.FALLBACK_MODEL_NAME))
        _touch(os.path.join(self.models, OT.FALLBACK_BLOCKER_MODEL_NAME))
        s = OT.resolve_settings(self.bundle, ratmodels_dir=self.models)
        self.assertEqual(s.model.source, OT.SRC_FALLBACK)
        self.assertEqual(s.blocker_model.source, OT.SRC_FALLBACK)
        self.assertEqual(s.background.source, OT.SRC_COMPUTED)
        self.assertIsNone(s.background.path)
        self.assertIsNone(s.roi)
        joined = " ".join(s.notes)
        self.assertIn("current model", joined)
        self.assertIn("median background", joined)

    def test_no_model_anywhere_is_model_free(self):
        self._video()
        s = OT.resolve_settings(self.bundle, ratmodels_dir=self.models)
        self.assertIsNone(s.model)
        self.assertIn("without an appearance model", " ".join(s.notes))

    def test_overrides_win(self):
        self._video()
        _touch(os.path.join(self.bundle, "config", "model_snap.npz"))
        _touch(os.path.join(self.bundle, "video", SID + "_background.png"))
        _touch(os.path.join(self.models, OT.FALLBACK_BLOCKER_MODEL_NAME))
        other = _touch(os.path.join(self.tmp, "other.npz"))
        bg = _touch(os.path.join(self.tmp, "bg.png"))
        s = OT.resolve_settings(self.bundle, OT.Overrides(
            model=other, background=bg, no_blocker=True), ratmodels_dir=self.models)
        self.assertEqual(s.model.source, OT.SRC_OVERRIDE)
        self.assertEqual(os.path.basename(s.model.path), "other.npz")
        self.assertEqual(s.background.source, OT.SRC_OVERRIDE)
        self.assertIsNone(s.blocker_model)

    def test_background_from_video_flag(self):
        self._video()
        _touch(os.path.join(self.bundle, "video", SID + "_background.png"))
        s = OT.resolve_settings(self.bundle, OT.Overrides(background_from_video=True),
                                ratmodels_dir=self.models)
        self.assertEqual(s.background.source, OT.SRC_COMPUTED)

    def test_missing_override_file_raises(self):
        self._video()
        with self.assertRaises(FileNotFoundError):
            OT.resolve_settings(self.bundle, OT.Overrides(roi="nope.json"),
                                ratmodels_dir=self.models)

    def test_find_bundles(self):
        self._video()
        os.makedirs(os.path.join(self.tmp, "not_a_bundle"))
        self.assertEqual(OT.find_bundles(self.tmp), [self.bundle])
        self.assertEqual(OT.find_bundles(self.bundle), [self.bundle])


class TestMergeParams(unittest.TestCase):
    BASE = {"thresh": 30, "min_area": 150.0, "use_kalman": True, "blur": 5}

    def _settings(self, snapshot=None, file=None, sets=()):
        fc = OT.FileChoice(None, OT.SRC_COMPUTED)
        return OT.OfflineSettings(
            bundle="b", session_id=SID, video="v", sidecar=None, model=None,
            blocker_model=None, roi=None, landmarks=None, background=fc,
            snapshot_params=snapshot or {}, file_params=file or {}, set_params=sets)

    def test_layers_in_order(self):
        params, src = OT.merge_params(self.BASE, self._settings(
            snapshot={"thresh": 19, "blur": 7, "from_newer_rig": 1},
            file={"thresh": 22}, sets=("thresh=25", "use_kalman=false")))
        self.assertEqual(params["thresh"], 25)
        self.assertEqual(params["blur"], 7)
        self.assertIs(params["use_kalman"], False)
        self.assertEqual(params["min_area"], 150.0)
        self.assertEqual(src, {"thresh": OT.PSRC_SET, "blur": OT.PSRC_SNAPSHOT,
                               "use_kalman": OT.PSRC_SET})
        self.assertNotIn("from_newer_rig", params)   # unknown snapshot keys ignored

    def test_types_coerced(self):
        params, _ = OT.merge_params(self.BASE, self._settings(sets=("min_area=80", "blur=3")))
        self.assertIsInstance(params["min_area"], float)
        self.assertIsInstance(params["blur"], int)

    def test_unknown_set_key_rejected_with_valid_names(self):
        with self.assertRaises(ValueError) as cm:
            OT.merge_params(self.BASE, self._settings(sets=("thersh=25",)))
        self.assertIn("thersh", str(cm.exception))
        self.assertIn("thresh", str(cm.exception))

    def test_bad_values_rejected(self):
        for item in ("thresh=2.5", "thresh=abc", "use_kalman=maybe", "thresh"):
            with self.assertRaises(ValueError, msg=item):
                OT.merge_params(self.BASE, self._settings(sets=(item,)))

    def test_params_file_accepts_runtime_params_shape(self):
        tmp = tempfile.mkdtemp()
        try:
            p = _write_json(os.path.join(tmp, "rp.json"), {"tracker": {"thresh": 12}})
            self.assertEqual(OT.load_params_file(p), {"thresh": 12})
            p2 = _write_json(os.path.join(tmp, "flat.json"), {"thresh": 13})
            self.assertEqual(OT.load_params_file(p2), {"thresh": 13})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFramesAndOutputs(unittest.TestCase):
    def test_background_indices_spread(self):
        idx = OT.background_sample_indices(1000, 50)
        self.assertEqual(len(idx), 50)
        self.assertEqual(idx[0], 10)
        self.assertEqual(idx[-1], 990)
        self.assertEqual(OT.background_sample_indices(5, 50), [0, 1, 2, 3, 4])
        self.assertEqual(OT.background_sample_indices(0), [])

    def test_output_paths_and_tag(self):
        c, j = OT.output_paths("d", SID, None)
        self.assertEqual(os.path.basename(c), f"{SID}_offline_track.csv")
        self.assertEqual(os.path.basename(j), f"{SID}_offline_track.json")
        c, _ = OT.output_paths("d", SID, "thresh-25_b")
        self.assertEqual(os.path.basename(c), f"{SID}_offline_track_thresh-25_b.csv")
        with self.assertRaises(ValueError):
            OT.output_paths("d", SID, "bad tag")

    def test_overwrite_refused(self):
        tmp = tempfile.mkdtemp()
        try:
            p = _touch(os.path.join(tmp, "a.csv"))
            with self.assertRaises(FileExistsError):
                OT.check_overwrite([p], overwrite=False)
            OT.check_overwrite([p], overwrite=True)
            OT.check_overwrite([os.path.join(tmp, "b.csv")], overwrite=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_frame_range(self):
        self.assertEqual(OT.frame_range(0, None, 0), (0, 0, None))
        self.assertEqual(OT.frame_range(100, 200, 30), (70, 100, 200))
        self.assertEqual(OT.frame_range(10, None, 30), (0, 10, None))
        with self.assertRaises(ValueError):
            OT.frame_range(10, 5, 0)

    def test_validate_registration(self):
        R = OT.RegistrationRequest
        ok = OT.validate_registration(R(rat_frames=(10, 40), blocker_frames=(9, 3, 3)))
        self.assertEqual(ok.blocker_frames, (3, 9))
        self.assertTrue(ok.any)
        self.assertFalse(R().any)
        for bad in (R(rat_frames=(40, 10)), R(rat_frames=(-1, 5)),
                    R(blocker_frames=(-2,)), R(save_model="m.npz"),
                    R(save_blocker_model="b.npz", rat_frames=(0, 5)), R(only=True)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                OT.validate_registration(bad)

    def test_registered_choice(self):
        c = OT.registered_choice(None)
        self.assertEqual((c.path, c.source), (None, OT.SRC_REGISTERED))
        tmp = tempfile.mkdtemp()
        try:
            p = _touch(os.path.join(tmp, "m.npz"))
            c = OT.registered_choice(p)
            self.assertEqual(c.source, OT.SRC_REGISTERED)
            self.assertEqual(len(c.sha256), 64)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tally(self):
        t = OT.RunTally()
        for s, d in (("TRACKING", True), ("TRACKING", True), ("LOST", False),
                     ("ACQUIRING", False)):
            t.add(s, d)
        d = t.to_dict()
        self.assertEqual(d["frames_written"], 4)
        self.assertEqual(d["frames_detected"], 2)
        self.assertEqual(d["state_counts"]["TRACKING"], 2)
        self.assertEqual(d["state_fractions"]["TRACKING"], 0.5)


# ---------------------------------------------------------------- end to end
try:
    from tmaze.vision import offline_tracker as OTR
    from tmaze.vision import tracker as _trk
    _HAVE_CV = _trk.cv2 is not None and _trk.np is not None
except Exception:  # pragma: no cover
    _HAVE_CV = False

_W, _H, _N, _BG, _FG, _SQ = 320, 240, 60, 200, 40, 30
# A static orange "blocker" in the top-left corner, larger than the rat square,
# so it is the dominant blob in a still capture (BGR).
_BLK = (10, 10, 70, 70)
_ORANGE = (0, 128, 255)


def _square_pos(i: int):
    """Top-left of the moving square: left->right across the frame."""
    return 20 + int(i * (_W - 2 * 20 - _SQ) / (_N - 1)), 100


def _make_session(bundle: str, with_png: bool, blocker: bool = False) -> bool:
    """Write video/<id>.mp4 + sidecar (+ background PNG). False if no mp4 writer."""
    cv2, np = _trk.cv2, _trk.np
    vdir = os.path.join(bundle, "video")
    os.makedirs(vdir, exist_ok=True)
    path = os.path.join(vdir, SID + ".mp4")
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (_W, _H))
    if not vw.isOpened():
        return False
    bg = np.full((_H, _W, 3), _BG, np.uint8)
    for i in range(_N):
        f = bg.copy()
        if blocker:
            x0, y0, x1, y1 = _BLK
            f[y0:y1, x0:x1] = _ORANGE
        x, y = _square_pos(i)
        f[y:y + _SQ, x:x + _SQ] = _FG
        vw.write(f)
    vw.release()
    if with_png:
        cv2.imwrite(os.path.join(vdir, SID + "_background.png"), bg)
    with open(os.path.join(vdir, SID + ".csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# session_id: {SID}\n# fps: 25.0\n# width: {_W}\n# height: {_H}\n")
        w = csv.writer(fh)
        w.writerow(["frame_idx", "host_unix_s", "rig_ms", "cam_block_id", "cam_timestamp"])
        for i in range(_N):
            w.writerow([i, f"{1000.0 + i * 0.04:.6f}", "", i, 5000 + i])
    return True


def _read_rows(path: str):
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    return list(csv.DictReader(lines))


@unittest.skipUnless(_HAVE_CV, "numpy + OpenCV required")
class TestEndToEnd(_TmpCase):
    def _run(self, with_png: bool, blocker: bool = False, **kw):
        if not _make_session(self.bundle, with_png, blocker=blocker):
            self.skipTest("no mp4 video writer in this OpenCV build")
        settings = OT.resolve_settings(self.bundle, OT.Overrides(sets=("min_area=100",)),
                                       ratmodels_dir=self.models)
        out = os.path.join(self.tmp, "out")
        c, j = OT.output_paths(out, SID, "t")
        summary = OTR.run(settings, csv_path=c, json_path=j, tag="t", **kw)
        return settings, summary, c, j

    def test_tracks_every_frame_with_png(self):
        settings, summary, c, j = self._run(with_png=True)
        self.assertEqual(settings.background.source, OT.SRC_BUNDLE)
        rows = _read_rows(c)
        self.assertEqual(len(rows), _N)
        self.assertEqual(rows[5]["host_unix_s"], f"{1000.0 + 5 * 0.04:.6f}")
        self.assertEqual(rows[5]["cam_timestamp"], "5005")
        self.assertGreater(summary["state_counts"].get("TRACKING", 0), _N // 2)
        # Tracked centroid follows the square.
        last = rows[-1]
        x, y = _square_pos(_N - 1)
        self.assertAlmostEqual(float(last["track_cx"]), x + _SQ / 2, delta=4)
        self.assertAlmostEqual(float(last["track_cy"]), y + _SQ / 2, delta=4)
        with open(j, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["frames_written"], _N)
        self.assertEqual(saved["param_sources"]["min_area"], OT.PSRC_SET)
        self.assertFalse(os.path.exists(c + ".partial"))
        with open(c, encoding="utf-8") as fh:
            head = fh.read(400)
        self.assertIn("# fps: 25.0", head)   # sidecar header carried over

    def test_median_background_without_png(self):
        save = os.path.join(self.tmp, "bg_saved.png")
        settings, summary, c, _ = self._run(with_png=False, save_background=save)
        self.assertEqual(settings.background.source, OT.SRC_COMPUTED)
        bg = _trk.cv2.imread(save)
        x, y = _square_pos(_N // 2)
        # The square passed through here, but the median is the empty background.
        self.assertGreater(int(bg[y + _SQ // 2, x + _SQ // 2].mean()), _BG - 25)
        self.assertGreater(summary["state_counts"].get("TRACKING", 0), _N // 2)

    def test_sub_range_with_warmup(self):
        _, summary, c, _ = self._run(with_png=True, start_frame=20, end_frame=29,
                                     warmup_frames=10)
        rows = _read_rows(c)
        self.assertEqual([int(r["frame_idx"]) for r in rows], list(range(20, 30)))
        # Warm-up means the tracker is already locked on at the first written frame.
        self.assertEqual(rows[0]["track_state"], "TRACKING")
        self.assertEqual(summary["range"], {"first_written": 20, "last_written": 29,
                                            "warmup_frames": 10})

    def test_register_rat_model_then_track(self):
        save = os.path.join(self.tmp, "models", "new_rat.npz")
        req = OT.validate_registration(OT.RegistrationRequest(
            rat_frames=(0, 39), save_model=save))
        _, summary, _, j = self._run(with_png=True, registration=req)
        reg = summary["registration"]["rat"]
        self.assertEqual(reg["frames"], [0, 39])
        self.assertGreater(reg["samples"], 30)
        self.assertEqual(summary["files"]["model"]["source"], OT.SRC_REGISTERED)
        self.assertEqual(summary["files"]["model"]["path"], os.path.abspath(save))
        self.assertGreater(summary["state_counts"].get("TRACKING", 0), _N // 2)
        loaded = _trk.RatModel.load(save)
        self.assertEqual(loaded.n, reg["samples"])
        self.assertIsNotNone(loaded.acq_params)   # settings stored with it (ADR-0027)

    def test_register_blocker_model(self):
        save = os.path.join(self.tmp, "blocker.npz")
        req = OT.validate_registration(OT.RegistrationRequest(
            blocker_frames=(5, 0), save_blocker_model=save))
        _, summary, _, _ = self._run(with_png=True, blocker=True, registration=req)
        b = summary["registration"]["blocker"]
        self.assertEqual(b["frames"], [0, 5])
        self.assertEqual(b["samples"], 2)
        lo, hi = b["orange_lo"], b["orange_hi"]
        self.assertTrue(lo[0] <= 15 <= hi[0], (lo, hi))   # orange hue ~15 (OpenCV 0-180)
        self.assertEqual(summary["files"]["blocker_model"]["source"], OT.SRC_REGISTERED)
        self.assertTrue(os.path.isfile(save))

    def test_register_only_cli(self):
        from tmaze.tools import offline_track as CLI
        if not _make_session(self.bundle, with_png=True):
            self.skipTest("no mp4 video writer in this OpenCV build")
        save = os.path.join(self.tmp, "only.npz")
        out = os.path.join(self.tmp, "out")
        real = CLI.ratmodels_dir
        CLI.ratmodels_dir = lambda: self.models   # don't depend on this machine's models
        try:
            rc = CLI.main([self.bundle, "--set", "min_area=100",
                           "--register-frames", "0", "39", "--save-model", save,
                           "--register-only", "--out-dir", out])
        finally:
            CLI.ratmodels_dir = real
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(save))
        self.assertFalse(os.path.exists(out))   # no tracking output written

    def test_cli_rejects_save_model_without_frames(self):
        from tmaze.tools import offline_track as CLI
        self.assertEqual(CLI.main([self.tmp, "--save-model", "x.npz"]), 2)


if __name__ == "__main__":
    unittest.main()
