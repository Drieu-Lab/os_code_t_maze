"""
calibration.py - shared lens-distortion calibration loading + undistortion.

One implementation used by both camera_view.py (the live GUI camera) and
blob_tracker_prototype.py (the tuning tool), so the undistortion applied while
tuning matches the real pipeline. See docs/decisions/0015.

The calibration JSON is OpenCV-convention (produced from MATLAB by
rig-hardware/camera/export_calibration_to_json.m):

    {
      "camera_matrix": [[fx,0,cx],[0,fy,cy],[0,0,1]],
      "distortion":    [k1,k2,p1,p2,k3],
      "image_size":    [width, height],
      "rms_reprojection_error": <float>   # optional
    }
"""

from __future__ import annotations

import json
import threading
from typing import Optional, Tuple

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore


def load_calibration(path: str) -> dict:
    """Parse an OpenCV-convention calibration JSON. Raises on malformed input.

    Returns a dict: camera_matrix (3x3 ndarray), distortion (ndarray),
    image_size ((w, h)), rms (float|None), path (str).
    """
    if np is None:
        raise RuntimeError("numpy is required to load a calibration")
    with open(path, "r") as f:
        data = json.load(f)
    K = np.array(data["camera_matrix"], dtype=np.float64)
    dist = np.array(data["distortion"], dtype=np.float64).ravel()
    size = data["image_size"]
    cal_w, cal_h = int(size[0]), int(size[1])
    if K.shape != (3, 3):
        raise ValueError("camera_matrix must be 3x3")
    if dist.size < 4:
        raise ValueError("distortion must have >=4 coefficients")
    return {
        "camera_matrix": K,
        "distortion": dist,
        "image_size": (cal_w, cal_h),
        "rms": data.get("rms_reprojection_error"),
        "path": path,
    }


class Undistorter:
    """Builds + applies an undistortion remap for a fixed calibration.

    Thread-safe: maps are built lazily on the thread that first calls apply()
    (e.g. the camera grab thread); set_calibration() may be called from another
    thread. apply() returns (frame, status) where status is one of
    "applied", "no_calib", or "mismatch" (calibration size != frame size).
    """

    def __init__(self, calib: Optional[dict] = None, alpha: float = 0.0):
        self._lock = threading.Lock()
        self._calib = calib
        self._alpha = float(alpha)
        self._maps = None
        self._size: Optional[Tuple[int, int]] = None

    def set_calibration(self, calib: Optional[dict]) -> None:
        with self._lock:
            self._calib = calib
            self._maps = None
            self._size = None

    @property
    def calibration(self) -> Optional[dict]:
        with self._lock:
            return self._calib

    def is_active(self) -> bool:
        """True if maps are built and being applied (size matched)."""
        with self._lock:
            return self._maps is not None

    def apply(self, frame):
        with self._lock:
            calib = self._calib
            alpha = self._alpha
        if cv2 is None or np is None or calib is None:
            return frame, "no_calib"
        h, w = frame.shape[:2]
        cal_w, cal_h = calib["image_size"]
        if (w, h) != (cal_w, cal_h):
            with self._lock:
                self._maps = None
                self._size = None
            return frame, "mismatch"
        with self._lock:
            if self._maps is None or self._size != (w, h):
                K = calib["camera_matrix"]
                dist = calib["distortion"]
                new_k, _roi = cv2.getOptimalNewCameraMatrix(K, dist, (w, h), alpha, (w, h))
                self._maps = cv2.initUndistortRectifyMap(
                    K, dist, None, new_k, (w, h), cv2.CV_16SC2)
                self._size = (w, h)
            maps = self._maps
        return cv2.remap(frame, maps[0], maps[1], interpolation=cv2.INTER_LINEAR), "applied"
