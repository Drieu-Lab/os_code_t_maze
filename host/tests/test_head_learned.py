"""Headless tests for the learned head-point helpers (tmaze.vision.head_learned).

Covers the network input crop (window position, zero padding at the image edge,
channel order and the difference gain), the ridge features, the ridge fit
(recovers a known linear map despite unknown targets; save/load round trip),
and HeadNet refusing a model trained with other crop settings. No trained model
is needed.

Run from host/:  python -m unittest tests.test_head_learned
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from tmaze.vision import head_learned as HL

_HAVE = HL.np is not None and HL.cv2 is not None


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestCrop(unittest.TestCase):
    def test_window_is_centred_and_zero_padded(self):
        np = HL.np
        grey = np.zeros((200, 300), np.uint8)
        grey[100, 150] = 255                       # one bright pixel at the CoM
        diff = np.full((200, 300), 10, np.uint8)
        blob = np.zeros((200, 300), np.uint8)
        x = HL.crop_input(grey, diff, blob, (150.2, 99.6))
        self.assertEqual(x.shape, (3, HL.INPUT_PX, HL.INPUT_PX))
        self.assertEqual(x.dtype, np.uint8)
        c = HL.INPUT_PX // 2
        self.assertGreater(int(x[0, c, c]), 0)             # the bright pixel lands mid-crop
        self.assertEqual(int(x[1, c, c]), int(10 * HL.DIFF_GAIN))
        corner = HL.crop_input(grey, diff, blob, (0.0, 0.0))
        self.assertEqual(int(corner[1, 0, 0]), 0)          # outside the image -> zero
        self.assertEqual(int(corner[1, -1, -1]), int(10 * HL.DIFF_GAIN))

    def test_crop_centre_rounds(self):
        self.assertEqual(HL.crop_centre((10.4, 20.6)), (10, 21))


@unittest.skipUnless(HL.np is not None, "numpy required")
class TestRidge(unittest.TestCase):
    def test_features(self):
        est = SimpleNamespace(head_far=(15.0, 10.0), head=(13.0, 10.0), tail=None, valid=True)
        f = HL.shape_features(est, (10.0, 10.0), (0.5, -1.0))
        self.assertEqual(list(f), [5.0, 0.0, 3.0, 0.0, 0.0, 0.0, 0.5, -1.0, 1.0])
        self.assertEqual(list(HL.shape_features(None, (0, 0), (0, 0))), [0.0] * len(HL.FEATURES))

    def test_fit_recovers_a_linear_map_with_unknown_targets(self):
        np = HL.np
        rng = np.random.default_rng(1)
        F = rng.normal(size=(400, len(HL.FEATURES)))
        W = rng.normal(size=(len(HL.FEATURES) + 1, 2 * len(HL.TARGETS)))
        Y = (np.hstack([F, np.ones((400, 1))]) @ W).reshape(400, len(HL.TARGETS), 2)
        Y[::3, 2] = np.nan                                   # tail base unknown on a third
        model = HL.RidgeHead.fit(F, Y, lam=1e-6)
        self.assertTrue(np.allclose(model.weights, W, atol=1e-4))
        self.assertEqual(model.predict(F[0]).shape, (len(HL.TARGETS), 2))
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        model.save(path)
        again = HL.RidgeHead.load(path)
        self.assertTrue(np.allclose(again.predict(F[:5]), model.predict(F[:5])))

    def test_load_refuses_other_features(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump({"features": ["x"], "targets": list(HL.TARGETS), "weights": [[0]]}, fh)
        self.addCleanup(os.remove, path)
        with self.assertRaises(ValueError):
            HL.RidgeHead.load(path)


@unittest.skipUnless(_HAVE, "numpy + OpenCV required")
class TestHeadNet(unittest.TestCase):
    def test_refuses_a_model_built_for_another_crop(self):
        d = tempfile.mkdtemp()
        onnx = os.path.join(d, "m.onnx")
        open(onnx, "wb").close()
        with open(os.path.join(d, "m.json"), "w") as fh:
            json.dump(dict(HL.net_meta(), input_version=HL.INPUT_VERSION + 1), fh)
        with self.assertRaises(ValueError):
            HL.HeadNet(onnx)

    def test_offsets_to_points(self):
        np = HL.np
        pts = HL.offsets_to_points(np.array([[1.0, 2.0], [0.0, 0.0], [-3.0, 4.0]]), (10, 20))
        self.assertEqual(pts["head"], (11.0, 22.0))
        self.assertEqual(pts["tail_base"], (7.0, 24.0))


if __name__ == "__main__":
    unittest.main()
