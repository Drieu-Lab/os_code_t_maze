"""
head_learned.py - learned head-point estimators, trained on DeepLabCut output.

Two models predict where the rat's head centre, neck, tail base, nose and
ears are (TARGETS), relative to the tracker's blob, from what the tracker
already has each frame; ``head_direction_deg`` turns neck + head centre into
the direction the head points:

  * RidgeHead - a ridge regression (a straight-line fit with a penalty that
    keeps the weights small) on a handful of numbers describing the blob's
    shape and motion (``shape_features``: the HeadTracker's head and tail
    points and the CoM velocity). It can rescale and shift the shape rule's
    answer but not overrule it.
  * HeadNet   - a tiny convolutional network (an image model that learns its
    own filters) run on a 64 x 64 crop around the blob (``crop_input``: grey
    image, difference from the background, blob mask). Unlike the mask-based
    rules it can use the dim head and tail that the threshold misses.

Both are trained by ``tmaze.tools.train_head_net`` (DEEPLABCUT env) on samples
from ``tmaze.tools.head_point_dataset``; the network is exported to ONNX and
run here with OpenCV's ``cv2.dnn``, so the rig needs no deep-learning package.
Everything is in the tracker's proc-resolution pixel grid; points are offsets
from ``crop_centre(com)`` (the network) or from the CoM itself (the ridge).

The crop layout is versioned (INPUT_VERSION): a model records the version and
constants it was trained with, and HeadNet refuses a model that disagrees, so
a change to ``crop_input`` cannot silently feed an old model the wrong image.
numpy is enough for the constants, ``shape_features`` and ``RidgeHead`` (so the
training env needs no OpenCV); ``crop_input`` and HeadNet need OpenCV too.
"""

from __future__ import annotations

import json
from typing import Optional, Sequence, Tuple

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - depends on host environment
    np = None  # type: ignore
try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover - depends on host environment
    cv2 = None  # type: ignore

INPUT_VERSION = 1
CROP_HALF_PX = 64     # proc px either side of the crop centre (128 px window)
INPUT_PX = 64         # network input side: 2 proc px per input px
DIFF_GAIN = 4.0       # background difference is amplified: dark-on-dark is faint
TARGETS = ("head", "neck", "tail_base", "nose", "right_ear", "left_ear")
# Targets that trade places in a mirrored image (the rat's right ear lands on
# its left); training augmentation with flips must swap them.
MIRROR_PAIRS = (("right_ear", "left_ear"),)
FEATURES = ("head_far_dx", "head_far_dy", "head_dx", "head_dy",
            "tail_dx", "tail_dy", "vx", "vy", "valid")


def crop_centre(com: Tuple[float, float]) -> Tuple[int, int]:
    """Integer pixel the crop is centred on (network offsets are relative to it)."""
    return int(round(com[0])), int(round(com[1]))


def _window(img: "np.ndarray", x0: int, y0: int, size: int) -> "np.ndarray":
    """``size`` x ``size`` slice of ``img`` from (x0, y0), zero beyond the image."""
    h, w = img.shape[:2]
    out = np.zeros((size, size), np.uint8)
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(w, x0 + size), min(h, y0 + size)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = img[sy0:sy1, sx0:sx1]
    return out


def crop_input(grey: "np.ndarray", diff: "np.ndarray", blob: "np.ndarray",
               com: Tuple[float, float]) -> "np.ndarray":
    """(3, INPUT_PX, INPUT_PX) uint8 network input for one frame.

    ``grey`` is the tracker's blurred proc-resolution grey frame, ``diff`` its
    absolute difference from the background, ``blob`` the rat's mask (0/255);
    all the same shape. Channel order: grey, diff x DIFF_GAIN (clipped), mask.
    The window is 2 * CROP_HALF_PX wide around ``crop_centre(com)``, shrunk to
    INPUT_PX by area averaging."""
    cx, cy = crop_centre(com)
    size = 2 * CROP_HALF_PX
    chans = []
    for img, gain in ((grey, 1.0), (diff, DIFF_GAIN), (blob, 1.0)):
        win = _window(img, cx - CROP_HALF_PX, cy - CROP_HALF_PX, size)
        if gain != 1.0:
            win = np.clip(win.astype(np.float32) * gain, 0, 255).astype(np.uint8)
        chans.append(cv2.resize(win, (INPUT_PX, INPUT_PX), interpolation=cv2.INTER_AREA))
    return np.stack(chans)


def shape_features(est, com: Tuple[float, float],
                   velocity: Tuple[float, float]) -> "np.ndarray":
    """FEATURES for one frame: a HeadTracker estimate's head_far / head / tail
    points relative to the CoM, the CoM velocity (px/frame), and whether the
    estimate is valid. Missing points are zeros (so the fit falls back toward
    the CoM)."""
    f = np.zeros(len(FEATURES), np.float64)
    if est is not None:
        for i, pt in enumerate((est.head_far, est.head, est.tail)):
            if pt is not None:
                f[2 * i] = pt[0] - com[0]
                f[2 * i + 1] = pt[1] - com[1]
        f[8] = 1.0 if est.valid else 0.0
    f[6], f[7] = velocity
    return f


class RidgeHead:
    """Linear map from FEATURES (+ a constant) to the TARGETS' offsets from the CoM."""

    def __init__(self, weights: "np.ndarray", meta: Optional[dict] = None):
        self.weights = np.asarray(weights, np.float64)    # (len(FEATURES) + 1, 2 * len(TARGETS))
        self.meta = meta or {}

    @staticmethod
    def fit(features: "np.ndarray", targets: "np.ndarray", lam: float = 10.0) -> "RidgeHead":
        """Fit on ``features`` (N, F) and ``targets`` (N, T, 2), NaN = unknown.

        Each output column uses only the rows where it is known; the penalty
        ``lam`` applies to every weight except the constant."""
        X = np.hstack([features, np.ones((len(features), 1))])
        Y = targets.reshape(len(targets), -1)
        pen = lam * np.eye(X.shape[1])
        pen[-1, -1] = 0.0
        W = np.zeros((X.shape[1], Y.shape[1]))
        for j in range(Y.shape[1]):
            ok = ~np.isnan(Y[:, j])
            if ok.sum() >= X.shape[1]:
                Xo = X[ok]
                W[:, j] = np.linalg.solve(Xo.T @ Xo + pen, Xo.T @ Y[ok, j])
        return RidgeHead(W, {"lambda": lam, "n_train": int(len(X))})

    def predict(self, features: "np.ndarray") -> "np.ndarray":
        """(T, 2) offsets from the CoM for one feature vector, or (N, T, 2) for N."""
        f = np.atleast_2d(features)
        out = np.hstack([f, np.ones((len(f), 1))]) @ self.weights
        out = out.reshape(len(f), len(TARGETS), 2)
        return out[0] if np.ndim(features) == 1 else out

    def save(self, path: str) -> None:
        doc = dict(self.meta, kind="ridge", features=list(FEATURES), targets=list(TARGETS),
                   weights=self.weights.tolist())
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)

    @staticmethod
    def load(path: str) -> "RidgeHead":
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("features") != list(FEATURES) or doc.get("targets") != list(TARGETS):
            raise ValueError(f"{path}: features/targets differ from this code's "
                             f"{FEATURES} / {TARGETS}")
        meta = {k: v for k, v in doc.items() if k not in ("weights",)}
        return RidgeHead(np.array(doc["weights"]), meta)


def net_meta() -> dict:
    """The crop constants a network is trained with (written beside the ONNX file)."""
    return {"input_version": INPUT_VERSION, "crop_half_px": CROP_HALF_PX,
            "input_px": INPUT_PX, "diff_gain": DIFF_GAIN, "targets": list(TARGETS)}


class HeadNet:
    """A trained network (ONNX, run with cv2.dnn) predicting TARGETS' offsets.

    ``meta_path`` defaults to the ONNX path with ``.json``; it must match
    net_meta(). The network outputs offsets divided by CROP_HALF_PX."""

    def __init__(self, onnx_path: str, meta_path: Optional[str] = None):
        meta_path = meta_path or onnx_path.rsplit(".", 1)[0] + ".json"
        with open(meta_path, encoding="utf-8") as fh:
            self.meta = json.load(fh)
        want = net_meta()
        got = {k: self.meta.get(k) for k in want}
        if got != want:
            raise ValueError(f"{onnx_path}: trained with crop settings {got}, "
                             f"this code builds {want}")
        self.net = cv2.dnn.readNetFromONNX(onnx_path)

    def predict_batch(self, inputs: "np.ndarray") -> "np.ndarray":
        """(N, T, 2) offsets (proc px, from crop_centre) for (N, 3, H, W) uint8 inputs."""
        self.net.setInput(inputs.astype(np.float32) / 255.0)
        out = self.net.forward()
        return out.reshape(len(inputs), len(TARGETS), 2) * CROP_HALF_PX

    def predict(self, inp: "np.ndarray") -> "np.ndarray":
        return self.predict_batch(inp[None])[0]


def offsets_to_points(offsets: "np.ndarray", origin: Sequence[float]) -> dict:
    """``{target: (x, y)}`` from (T, 2) offsets and the point they are relative to."""
    return {t: (float(origin[0] + offsets[i, 0]), float(origin[1] + offsets[i, 1]))
            for i, t in enumerate(TARGETS)}


def mirror_order() -> Tuple[int, ...]:
    """TARGETS indices after a mirror: each MIRROR_PAIRS pair swapped, the rest kept."""
    order = list(range(len(TARGETS)))
    for a, b in MIRROR_PAIRS:
        i, j = TARGETS.index(a), TARGETS.index(b)
        order[i], order[j] = j, i
    return tuple(order)


def head_direction_deg(points, frm: str = "neck", to: str = "head") -> Optional[float]:
    """Direction from ``frm`` to ``to`` in degrees, image axes: 0 = +x (image right),
    90 = +y (image DOWN), range (-180, 180]. ``points``: ``{target: (x, y)}`` or a
    (T, 2) array in TARGETS order; None when either point is missing / NaN.

    The default, neck -> head centre, is the rig's head direction: on the
    hand-labelled test frames it misses by over 45 degrees less often than
    neck -> nose (the nose is the hardest part to place on a black head)."""
    import math

    if isinstance(points, dict):
        a, b = points.get(frm), points.get(to)
    else:
        a, b = points[TARGETS.index(frm)], points[TARGETS.index(to)]
    if a is None or b is None:
        return None
    dx, dy = float(b[0]) - float(a[0]), float(b[1]) - float(a[1])
    if math.isnan(dx) or math.isnan(dy) or (dx == 0.0 and dy == 0.0):
        return None
    return math.degrees(math.atan2(dy, dx))


def angle_diff_deg(a: float, b: float) -> float:
    """Smallest absolute difference between two directions, in degrees (0..180)."""
    d = (a - b) % 360.0
    return min(d, 360.0 - d)
