"""
tracker.py - UI-free markerless position-tracking core for the T-maze rig.

Single source of truth for the classic-CV rat tracker, shared by the standalone
tuning tool (blob_tracker_prototype.py) and the experiment GUI (camera_view.py /
tracker_view.py). This module has NO Tk and NO OpenCV-window code - it takes BGR
frames in and returns a TrackResult out, so it can run in a camera grab thread
or a prototype loop unchanged.

Phase 2 implemented the minimal core: reference-frame background subtraction, a
polygon ROI mask, distance + area gating, and a constant-velocity Kalman filter.

Phase 3 (this file) adds robustness so an experimenter stepping into frame does
not steal the track:

  - RatModel: a learned appearance prior for the tracked animal, kept as cheap
    *sufficient statistics* so it can be extended online without re-storing raw
    frames:
      * a pooled Hue-Saturation colour histogram (pooled raw counts), and
      * a multivariate Gaussian (mean + covariance) over a rotation-invariant
        FORM vector [log(area), log(elongation), solidity]. Modelling size and
        shape *jointly* captures their natural correlation (a rearing/grooming
        rat is smaller AND rounder; a running rat is larger AND elongated) and
        gives one principled Mahalanobis score for the range of poses.
    The model is built during a registration window (let the animal run, turn,
    rear, groom) and can optionally keep *enriching* (accumulating) during
    tracking - see below. With no model the tracker behaves like Phase 2.

  - A scoring association: distance-to-prediction + colour-histogram + FORM
    (Mahalanobis) terms, weighted. Colour and size are the strong cues against an
    intruder; SHAPE is a deliberately SOFT, low-weight term (never a hard gate)
    because shape is the most variable signal and depends on mask quality and
    posture. A model-derived size band (from the log-area marginal) is the only
    hard form gate, for fast rejection of grossly wrong-sized blobs.

  - A fuller state machine: NO_REFERENCE -> ACQUIRING -> TRACKING -> COASTING ->
    LOST (-> ACQUIRING). COASTING is bounded (coast_max_frames) so a stale lock is
    released; on a fresh (re)acquisition the distance term is dropped so APPEARANCE
    governs which blob is chosen. ACQUIRING needs confirm_frames good frames.

  - A per-frame confidence in [0, 1], plus OPTIONAL online ENRICHMENT
    (adapt_enrich): while confidently TRACKING an unambiguous blob, the blob's
    colour + form are *added to the model's running statistics* (not blended/
    forgotten), so the model's captured RANGE grows to cover the poses/lighting it
    actually sees. This is accumulation, not EMA drift: old registration data is
    never discarded, and because it is sufficient-statistics-based the mean/cov
    converge rather than chase the latest frame. Guarded by confidence + an
    unambiguous-frame check (+ optional sample cap) to avoid learning an intruder.

  - Persistence: RatModel save/load (.npz, sufficient statistics) and ROI
    save/load (.json), so a model and maze outline can be reused across sessions.

Head/body orientation (heading) is intentionally NOT produced yet: a silhouette's
axis is only known modulo 180 deg, and robust head-vs-tail disambiguation wants
real rat video to validate. Deferred to a follow-up (see docs/future-work.md).

Coordinates: detection runs on a downscaled copy (<= proc_max_w wide) for speed;
all coordinates in TrackResult are mapped back to FULL-frame pixels so they align
with the recorded video. Note the FORM features (area/elongation) are measured at
proc resolution, so a model is tied to its proc_max_w (changing it invalidates the
reference and should prompt re-registration). Thread-safety: process() runs on one
thread (the camera grab thread); control methods (grab_background, set_roi_polygon,
update_params, reset, start/finalize_registration, save/load_model,
snapshot_result) may be called from another thread (Tk) and are guarded by a lock
+ flags consumed inside process().
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

try:
    import cv2  # type: ignore
    _CV2_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover - depends on host environment
    cv2 = None  # type: ignore
    _CV2_ERR = f"{type(e).__name__}: {e}"

try:
    import numpy as np  # type: ignore
    _NUMPY_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    np = None  # type: ignore
    _NUMPY_ERR = f"{type(e).__name__}: {e}"


# ================================== config ===================================
PATH_LEN = 120        # how many recent points to retain for the path overlay

# Hue-Saturation histogram resolution for the appearance model.
HIST_H_BINS = 30
HIST_S_BINS = 32
HIST_RANGES = [0, 180, 0, 256]

# FORM Gaussian over [log(area), log(elongation), solidity].
FORM_DIM = 3
FORM_DOF = 3.0                       # divides Mahalanobis^2 so a typical in-
#                                     distribution sample (chi2~dof) scores ~0.6
# Per-feature standard-deviation floors (regularize a thin registration so the
# model does not become over-confident / reject legitimate pose variation).
FORM_STD_FLOOR = (0.18, 0.12, 0.06)  # [log-area, log-elong, solidity]
FORM_RIDGE = 1e-4                    # diagonal ridge added for invertibility
FORM_MIN_FOR_COV = 8                 # below this, use a diagonal (no correlation)
ELONG_MAX = 20.0                     # clamp elongation (degenerate thin blobs)

MODEL_VERSION = 2

# Landmark annotations (see ADR-0018). Single image-space points the experimenter
# marks on a still frame to record where the rig's reward wells and body-sensor
# beams are, so later task rules can reason about the tracked animal's position
# relative to them. Stored normalized (0..1) and overlaid on every preview; the
# tracker only *holds* them (no use in process() yet).
LANDMARKS_VERSION = 1
# Canonical fixed set - single source of truth for landmark names/indices. The
# `index` ties each marker to its firmware event: wells -> lick port L,i
# (0=left, 1=right, 2=center/start); sensors -> body-beam S,i in SENSOR_PINS
# order (see firmware/PROTOCOL.md and host/rig_view.py). (kind, index, label).
FIXED_LANDMARKS = (
    ("well", 0, "Left reward (L0)"),
    ("well", 1, "Right reward (L1)"),
    ("well", 2, "Center/start (L2)"),
    ("sensor", 0, "ENTRY (S0)"),
    ("sensor", 1, "EXIT (S1)"),
    ("sensor", 2, "L_CHOICE (S2)"),
    ("sensor", 3, "R_CHOICE (S3)"),
    ("sensor", 4, "L_RETURN (S4)"),
    ("sensor", 5, "R_RETURN (S5)"),
)
# Overlay marker colours per landmark kind (BGR), kept distinct from the ROI
# outline (255,180,0) and the path (0,200,255).
LANDMARK_COLORS = {
    "well": (60, 220, 60),      # green-ish
    "sensor": (200, 80, 220),   # magenta-ish
    "custom": (40, 220, 255),   # amber
}
_LANDMARK_DEFAULT_COLOR = (255, 255, 255)

# State strings.
STATE_NO_REFERENCE = "NO_REFERENCE"   # no empty-rig reference grabbed yet
STATE_REF_MISMATCH = "REF_MISMATCH"   # reference size != current proc frame
STATE_REGISTERING = "REGISTERING"     # accumulating the appearance model
STATE_ACQUIRING = "ACQUIRING"         # have reference, (re)locking onto a target
STATE_TRACKING = "TRACKING"           # confirmed lock, being tracked
STATE_COASTING = "COASTING"           # no blob this frame; coasting on prediction
STATE_LOST = "LOST"                   # coasted too long; track dropped

# Backward-compatible alias (Phase 2 used SEARCHING for "no track yet").
STATE_SEARCHING = STATE_ACQUIRING

COAST_CONF_DECAY = 0.85   # confidence multiplier per coasting frame


@dataclass
class TrackerParams:
    """Mutable tuning knobs. Copied under lock for each process() call."""
    thresh: int = 30           # reference-diff binary threshold (0-255)
    min_area: float = 150.0    # smallest accepted blob area (proc px^2)
    max_area: float = 0.0      # largest accepted blob area; 0 = no upper bound
    blur: int = 5              # gaussian blur kernel (odd; <3 disables)
    morph: int = 5             # morphological open/close kernel size (<3 disables)
    proc_max_w: int = 720      # downscale width for detection
    gate_radius: float = 120.0 # max jump between frames (proc px); 0 = disabled
    use_kalman: bool = True

    # --- appearance scoring weights (renormalized internally) ---
    # w_hist:w_form set the colour:shape split of the APPEARANCE term;
    # appearance_bias (below) sets appearance-vs-distance. w_dist is kept for
    # backward compat but superseded by appearance_bias at scoring time.
    w_dist: float = 0.45       # (legacy) weight of distance-to-prediction term
    w_hist: float = 0.35       # weight of colour-histogram term (needs a model)
    w_form: float = 0.20       # weight of size+shape Gaussian term (SOFT; needs a model)
    accept_score: float = 0.5  # min score to accept a candidate (model present)
    confirm_frames: int = 3    # consecutive good frames ACQUIRING -> TRACKING
    coast_max_frames: int = 30 # COASTING frames before declaring LOST

    # --- model tolerances (all read live at scoring time; no recompute needed) ---
    appearance_bias: float = 0.55  # appearance's share of the score (rest = distance)
    form_tol: float = 1.0          # size+shape Gaussian softness (>1 = more tolerant)
    hist_min: float = 0.0          # hard colour-correlation gate (0 = off)

    # --- registration window ---
    reg_min_frames: int = 30          # min accepted samples for a "covered" model
    reg_dominance: float = 0.5        # 2nd blob must be < this * largest to sample
    reg_logarea_spread_min: float = 0.10  # std(log area) needed for coverage_ok
    area_gate_k: float = 3.0          # hard size band = exp(muA +/- k*sigmaA)

    # --- online enrichment (accumulate, NOT drift) ---
    adapt_enrich: bool = False    # keep accumulating model stats while tracking
    adapt_min_conf: float = 0.85  # only enrich from confident frames
    adapt_max_samples: int = 0    # cap on total form samples (0 = unlimited)


def draw_overlay(img_bgr, scale: float, tracker, result) -> None:
    """Draw the tracking overlay on a (preview-scaled) BGR image, in place.

    Shared by every preview (camera tab + tracking tab) so they look identical.
    `scale` maps full-frame px -> the image's px (path/centroids are full-frame);
    the ROI polygon is normalized and maps to the image size. `result` may be None
    (e.g. tracking off) - then only the ROI outline + any path are drawn.
    """
    if cv2 is None or np is None or img_bgr is None:
        return
    try:
        dh, dw = img_bgr.shape[:2]
        roi = tracker.get_roi_polygon() if tracker is not None else None
        if roi:
            pts = np.array([[int(x * dw), int(y * dh)] for x, y in roi], np.int32)
            cv2.polylines(img_bgr, [pts], True, (255, 180, 0), 1)
        landmarks = tracker.get_landmarks() if tracker is not None else []
        for lm in landmarks:
            lx, ly = int(lm["x"] * dw), int(lm["y"] * dh)
            color = LANDMARK_COLORS.get(lm.get("kind", "custom"),
                                        _LANDMARK_DEFAULT_COLOR)
            cv2.drawMarker(img_bgr, (lx, ly), color, markerType=cv2.MARKER_DIAMOND,
                           markerSize=10, thickness=2)
            label = lm.get("label", "")
            if label:
                cv2.putText(img_bgr, label, (lx + 7, ly - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
        path = tracker.get_path() if tracker is not None else []
        for i in range(1, len(path)):
            cv2.line(img_bgr,
                     (int(path[i - 1][0] * scale), int(path[i - 1][1] * scale)),
                     (int(path[i][0] * scale), int(path[i][1] * scale)),
                     (0, 200, 255), 2)
        if result is not None:
            for c in result.candidates:
                cv2.circle(img_bgr, (int(c[0] * scale), int(c[1] * scale)), 4,
                           (120, 120, 120), 1)
            if result.centroid is not None:
                cv2.circle(img_bgr, (int(result.centroid[0] * scale),
                                     int(result.centroid[1] * scale)), 5, (0, 0, 255), -1)
            if result.smoothed is not None:
                cv2.circle(img_bgr, (int(result.smoothed[0] * scale),
                                     int(result.smoothed[1] * scale)), 7, (0, 255, 0), 2)
            cv2.putText(img_bgr, f"{result.state} {result.confidence:.2f}", (6, 16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    except Exception:
        pass


def compute_form_vector(area: float, elongation: float, solidity: float):
    """Rotation-invariant FORM feature vector [log(area), log(elong), solidity]."""
    return np.array([
        math.log(max(area, 1.0)),
        math.log(max(elongation, 1.0)),
        float(min(max(solidity, 0.0), 1.0)),
    ], dtype=np.float64)


@dataclass
class Detection:
    """One foreground blob (in proc-frame coordinates)."""
    centroid: Tuple[float, float]
    area: float
    bbox: Tuple[int, int, int, int]
    elongation: float = 1.0
    solidity: float = 1.0
    contour: Optional["np.ndarray"] = None
    hist: Optional["np.ndarray"] = None   # lazily filled normalized H-S hist


class RatModel:
    """Learned appearance prior: pooled colour histogram + size/shape Gaussian.

    Stored as sufficient statistics (pooled hist counts; sample count + sum and
    sum-of-outer-products of the FORM vector) so it can be extended online and
    serialized compactly. Derived quantities (normalized hist, mean, covariance,
    inverse covariance, size band) are recomputed from the statistics.
    """

    def __init__(self):
        self.hist_counts = np.zeros((HIST_H_BINS, HIST_S_BINS), np.float64)
        self.n = 0
        self.sum_x = np.zeros(FORM_DIM, np.float64)
        self.sum_xx = np.zeros((FORM_DIM, FORM_DIM), np.float64)
        # Derived (filled by recompute()).
        self.hist: Optional["np.ndarray"] = None      # normalized float32
        self.mu: Optional["np.ndarray"] = None        # (FORM_DIM,)
        self.cov: Optional["np.ndarray"] = None
        self.inv_cov: Optional["np.ndarray"] = None
        self.area_min = 0.0
        self.area_max = 0.0
        self.area_p50 = 0.0
        self.coverage_ok = False
        self.hist_bins = (HIST_H_BINS, HIST_S_BINS)
        self.animal = ""
        self.date = ""

    # ----------------------------------------------------- accumulation
    def add_sample(self, hist_raw: "np.ndarray", form_x: "np.ndarray") -> None:
        self.hist_counts += hist_raw
        self.sum_x += form_x
        self.sum_xx += np.outer(form_x, form_x)
        self.n += 1

    def recompute(self, params: "TrackerParams") -> None:
        s = float(self.hist_counts.sum())
        self.hist = ((self.hist_counts / s) if s > 0 else self.hist_counts).astype(np.float32)
        if self.n < 1:
            return
        mu = self.sum_x / self.n
        self.mu = mu
        if self.n >= 2:
            cov = self.sum_xx / self.n - np.outer(mu, mu)
            cov = (cov + cov.T) / 2.0
        else:
            cov = np.zeros((FORM_DIM, FORM_DIM), np.float64)

        floors = np.array(FORM_STD_FLOOR, np.float64) ** 2
        if self.n < FORM_MIN_FOR_COV:
            # Too few samples to trust correlations: diagonal only.
            cov = np.diag(np.maximum(np.diag(cov), floors))
        else:
            add = np.maximum(0.0, floors - np.diag(cov))
            cov = cov + np.diag(add) + np.eye(FORM_DIM) * FORM_RIDGE
        self.cov = cov
        try:
            self.inv_cov = np.linalg.inv(cov)
        except Exception:  # pragma: no cover - cov is regularized
            self.inv_cov = np.diag(1.0 / np.maximum(np.diag(cov), 1e-6))

        mu_a = float(mu[0])
        sigma_a = math.sqrt(max(float(cov[0, 0]), 1e-9))
        k = params.area_gate_k
        self.area_p50 = math.exp(mu_a)
        self.area_min = math.exp(mu_a - k * sigma_a)
        self.area_max = math.exp(mu_a + k * sigma_a)
        self.coverage_ok = (self.n >= params.reg_min_frames
                            and sigma_a >= params.reg_logarea_spread_min)

    # ----------------------------------------------------------- scoring
    def hist_score(self, hist_norm: "np.ndarray") -> float:
        if self.hist is None or hist_norm is None:
            return 0.0
        v = float(cv2.compareHist(hist_norm.astype(np.float32),
                                  self.hist.astype(np.float32), cv2.HISTCMP_CORREL))
        return max(0.0, min(1.0, v))

    def form_score(self, form_x: "np.ndarray", tol: float = 1.0) -> float:
        if self.inv_cov is None or self.mu is None:
            return 1.0  # neutral until the Gaussian is defined
        d = form_x - self.mu
        d2 = float(d @ self.inv_cov @ d)
        tol = tol if tol > 0 else 1.0
        return math.exp(-0.5 * d2 / (FORM_DOF * tol))

    def area_band(self, k: float) -> Tuple[float, float]:
        """Live size band exp(muA +/- k*sigmaA) from stored stats.

        Computed on demand so the size-tolerance knob `k` takes effect without
        re-running recompute(); falls back to the baked area_min/max if the
        Gaussian stats are not available yet.
        """
        if self.mu is None or self.cov is None:
            return (self.area_min, self.area_max)
        mu_a = float(self.mu[0])
        sigma_a = math.sqrt(max(float(self.cov[0, 0]), 1e-9))
        return (math.exp(mu_a - k * sigma_a), math.exp(mu_a + k * sigma_a))

    # ------------------------------------------------------- persistence
    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        np.savez(
            path,
            version=np.int64(MODEL_VERSION),
            hist_counts=self.hist_counts.astype(np.float64),
            n=np.int64(self.n),
            sum_x=self.sum_x.astype(np.float64),
            sum_xx=self.sum_xx.astype(np.float64),
            coverage_ok=np.int64(1 if self.coverage_ok else 0),
            hist_bins=np.array(self.hist_bins, np.int64),
            animal=np.array(self.animal),
            date=np.array(self.date),
        )

    @staticmethod
    def load(path: str, params: Optional["TrackerParams"] = None) -> "RatModel":
        d = np.load(path, allow_pickle=False)
        ver = int(d["version"]) if "version" in d else 1
        if ver != MODEL_VERSION:
            raise ValueError(
                f"unsupported RatModel version {ver} (expected {MODEL_VERSION}); "
                f"re-register the model")
        m = RatModel()
        m.hist_counts = np.asarray(d["hist_counts"], np.float64)
        m.n = int(d["n"])
        m.sum_x = np.asarray(d["sum_x"], np.float64)
        m.sum_xx = np.asarray(d["sum_xx"], np.float64)
        m.hist_bins = tuple(int(x) for x in d["hist_bins"])
        m.animal = str(d["animal"]) if "animal" in d else ""
        m.date = str(d["date"]) if "date" in d else ""
        m.recompute(params or TrackerParams())
        m.coverage_ok = bool(int(d["coverage_ok"])) if "coverage_ok" in d else m.coverage_ok
        return m


@dataclass
class TrackResult:
    """Per-frame output. Coordinates are in FULL-frame pixels."""
    detected: bool
    state: str
    centroid: Optional[Tuple[float, float]] = None   # raw detection (full px)
    smoothed: Optional[Tuple[float, float]] = None    # kalman estimate (full px)
    area: float = 0.0
    elongation: float = 0.0
    solidity: float = 0.0
    confidence: float = 0.0
    candidates: List[Tuple[float, float]] = field(default_factory=list)  # rejected blobs (full px)
    frame_size: Tuple[int, int] = (0, 0)              # full (w, h)
    mask: Optional["np.ndarray"] = None               # proc-resolution binary mask (debug)
    chosen_contour: Optional["np.ndarray"] = None     # tracked blob outline, PROC-res (aligns with mask)
    # Registration progress (only meaningful while state == REGISTERING).
    reg_accepted: int = 0
    reg_target: int = 0
    has_model: bool = False
    model_samples: int = 0
    model_coverage: bool = False


class RatTracker:
    """Classic-CV single-rat position tracker (Phase 3)."""

    def __init__(self, params: Optional[TrackerParams] = None):
        if cv2 is None or np is None:
            missing = _CV2_ERR or _NUMPY_ERR
            raise RuntimeError(f"tracker requires opencv-python + numpy ({missing})")
        self._lock = threading.Lock()
        self._params = params or TrackerParams()

        # Shared mutable state (guarded by _lock).
        self._reference: Optional["np.ndarray"] = None     # grayscale proc-res reference
        # Full-resolution COLOUR empty-rig frame captured at the same grab, kept so
        # the background can be saved for offline tuning (it lines up pixel-for-
        # pixel with the recorded video and re-derives a reference at any
        # proc_max_w/blur, unlike the gray proc reference). See docs/decisions/0020.
        self._reference_full: Optional["np.ndarray"] = None
        self._grab_ref_pending = False
        self._roi_norm: Optional[List[Tuple[float, float]]] = None
        self._roi_cache: Optional[Tuple[tuple, Tuple[int, int], "np.ndarray"]] = None
        # Landmark annotations (normalized image-space points; see ADR-0018).
        # Each is a dict {kind, index, label, x, y}; held only (no use in
        # process() yet), exposed for the overlay + future task rules.
        self._landmarks: List[dict] = []
        self._latest: Optional[TrackResult] = None
        self._path: "deque[Tuple[float, float]]" = deque(maxlen=PATH_LEN)
        self._reset_pending = False

        # Appearance model + registration (guarded by _lock for control fields).
        self._model: Optional[RatModel] = None
        self._animal: str = ""
        self._reg_start_pending = False
        self._reg_finalize_pending = False
        self._reg_cancel_pending = False
        self._reg: Optional[RatModel] = None         # active accumulator inside process()

        # Tracking state machine (touched only inside process()).
        self._kalman = self._make_kalman()
        self._kalman_primed = False
        self._state = STATE_NO_REFERENCE
        self._frames_confirmed = 0
        self._frames_since_seen = 0
        self._confidence = 0.0

    # --------------------------------------------------------- kalman helpers
    @staticmethod
    def _make_kalman():
        kf = cv2.KalmanFilter(4, 2)  # state [x, y, vx, vy], measurement [x, y]
        kf.transitionMatrix = np.array(
            [[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)
        kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
        kf.processNoiseCov = np.eye(4, dtype=np.float32) * 1e-2
        kf.measurementNoiseCov = np.eye(2, dtype=np.float32) * 1e-1
        return kf

    # ------------------------------------------------------------ public API
    def update_params(self, **kwargs) -> None:
        """Merge tuning changes. A proc_max_w change invalidates the reference."""
        with self._lock:
            new = replace(self._params, **kwargs)
            if new.proc_max_w != self._params.proc_max_w:
                self._reference = None          # reference is proc-resolution
                self._roi_cache = None
                self._reset_pending = True
            self._params = new

    def get_params(self) -> TrackerParams:
        with self._lock:
            return replace(self._params)

    def grab_background(self) -> None:
        """Request that the next processed frame become the empty-rig reference."""
        with self._lock:
            self._grab_ref_pending = True

    def clear_background(self) -> None:
        with self._lock:
            self._reference = None
            self._reference_full = None

    def has_reference(self) -> bool:
        with self._lock:
            return self._reference is not None

    def get_background_image(self):
        """Full-resolution COLOUR empty-rig frame from the last grab (or None).

        This is the raw frame captured when the reference was grabbed (already
        undistorted + oriented, same geometry as the recorded video), suitable
        for saving and reloading for offline tuning. See docs/decisions/0020.
        """
        with self._lock:
            return None if self._reference_full is None else self._reference_full.copy()

    def set_roi_polygon(self, norm_verts: List[Tuple[float, float]]) -> None:
        """Set the ROI polygon from >=3 normalized (0..1) vertices."""
        with self._lock:
            if norm_verts and len(norm_verts) >= 3:
                self._roi_norm = [(float(x), float(y)) for x, y in norm_verts]
            else:
                self._roi_norm = None
            self._roi_cache = None

    def clear_roi(self) -> None:
        with self._lock:
            self._roi_norm = None
            self._roi_cache = None

    def get_roi_polygon(self) -> Optional[List[Tuple[float, float]]]:
        with self._lock:
            return list(self._roi_norm) if self._roi_norm else None

    def reset(self) -> None:
        """Drop the current track (kalman + path) but keep reference + ROI + model."""
        with self._lock:
            self._reset_pending = True
            self._path.clear()

    def get_path(self) -> List[Tuple[float, float]]:
        with self._lock:
            return list(self._path)

    def snapshot_result(self) -> Optional[TrackResult]:
        with self._lock:
            return self._latest

    # ----------------------------------------------------- registration API
    def set_animal(self, animal: str) -> None:
        with self._lock:
            self._animal = str(animal or "")

    def start_registration(self) -> None:
        """Begin accumulating an appearance model from the dominant in-ROI blob."""
        with self._lock:
            self._reg_start_pending = True
            self._reg_finalize_pending = False
            self._reg_cancel_pending = False

    def finalize_registration(self) -> None:
        """Stop the registration window and build the model (consumed in process)."""
        with self._lock:
            self._reg_finalize_pending = True

    def cancel_registration(self) -> None:
        with self._lock:
            self._reg_cancel_pending = True

    def is_registering(self) -> bool:
        with self._lock:
            return self._reg is not None

    def registration_status(self) -> Tuple[bool, int, int]:
        """(registering, accepted_samples, target_samples)."""
        with self._lock:
            if self._reg is not None:
                return True, self._reg.n, self._params.reg_min_frames
            return False, 0, self._params.reg_min_frames

    # ----------------------------------------------------------- model API
    def has_model(self) -> bool:
        with self._lock:
            return self._model is not None

    def get_model(self) -> Optional[RatModel]:
        with self._lock:
            return self._model

    def set_model(self, model: Optional[RatModel]) -> None:
        with self._lock:
            self._model = model

    def clear_model(self) -> None:
        with self._lock:
            self._model = None

    def save_model(self, path: str) -> bool:
        with self._lock:
            model = self._model
        if model is None:
            return False
        model.save(path)
        return True

    def load_model(self, path: str) -> bool:
        params = self.get_params()
        model = RatModel.load(path, params)
        with self._lock:
            self._model = model
        return True

    # ------------------------------------------------------------- ROI I/O
    def save_roi(self, path: str) -> bool:
        with self._lock:
            roi = list(self._roi_norm) if self._roi_norm else None
        if not roi:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"polygon_norm": [[float(x), float(y)] for x, y in roi]}, f, indent=2)
        return True

    def load_roi(self, path: str) -> bool:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        verts = data.get("polygon_norm") or []
        if len(verts) < 3:
            return False
        self.set_roi_polygon([(float(x), float(y)) for x, y in verts])
        return True

    # --------------------------------------------------------- landmark API
    # Landmarks are pure rig geometry the tracker only holds (no use in
    # process() yet): normalized point annotations for the reward wells and
    # body-sensor beams, persisted separately from the model + ROI so they can
    # be reused across sessions and consumed by a future task-rule engine. See
    # ADR-0018. Mirrors the ROI API above.
    @staticmethod
    def _clean_landmark(lm) -> Optional[dict]:
        """Validate/normalize one landmark dict; return None if unusable."""
        try:
            x = float(lm["x"])
            y = float(lm["y"])
        except (KeyError, TypeError, ValueError):
            return None
        kind = str(lm.get("kind", "custom")) or "custom"
        raw_index = lm.get("index", None)
        try:
            index = None if raw_index is None else int(raw_index)
        except (TypeError, ValueError):
            index = None
        label = str(lm.get("label", "")) or kind
        return {"kind": kind, "index": index, "label": label, "x": x, "y": y}

    def set_landmarks(self, landmarks: List[dict]) -> None:
        """Replace the landmark set with cleaned copies of `landmarks`."""
        cleaned = []
        for lm in (landmarks or []):
            c = self._clean_landmark(lm)
            if c is not None:
                cleaned.append(c)
        with self._lock:
            self._landmarks = cleaned

    def get_landmarks(self) -> List[dict]:
        with self._lock:
            return [dict(lm) for lm in self._landmarks]

    def clear_landmarks(self) -> None:
        with self._lock:
            self._landmarks = []

    def save_landmarks(self, path: str) -> bool:
        with self._lock:
            landmarks = [dict(lm) for lm in self._landmarks]
        if not landmarks:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": LANDMARKS_VERSION, "landmarks": landmarks},
                      f, indent=2)
        return True

    def load_landmarks(self, path: str) -> bool:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        landmarks = data.get("landmarks") or []
        self.set_landmarks(landmarks)
        return bool(self.get_landmarks())

    # --------------------------------------------------------------- process
    def process(self, frame_bgr) -> TrackResult:
        """Detect + track the rat in one BGR frame; return a TrackResult."""
        with self._lock:
            params = replace(self._params)
            grab_ref = self._grab_ref_pending
            self._grab_ref_pending = False
            reference = self._reference
            roi_norm = self._roi_norm
            model = self._model
            animal = self._animal
            reg_start = self._reg_start_pending
            reg_finalize = self._reg_finalize_pending
            reg_cancel = self._reg_cancel_pending
            self._reg_start_pending = False
            self._reg_finalize_pending = False
            self._reg_cancel_pending = False
            if self._reset_pending:
                self._reset_pending = False
                self._kalman = self._make_kalman()
                self._kalman_primed = False
                self._frames_confirmed = 0
                self._frames_since_seen = 0
                self._confidence = 0.0

        # Consume registration control transitions.
        if reg_cancel:
            self._reg = None
        if reg_start:
            self._reg = RatModel()
            self._reg.animal = animal
        finalize_now = reg_finalize and self._reg is not None

        full_h, full_w = frame_bgr.shape[:2]
        scale = min(1.0, params.proc_max_w / float(full_w)) if full_w else 1.0
        if scale < 1.0:
            proc = cv2.resize(frame_bgr, (max(1, int(full_w * scale)),
                                          max(1, int(full_h * scale))),
                              interpolation=cv2.INTER_AREA)
        else:
            proc = frame_bgr
        ph, pw = proc.shape[:2]

        gray = cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY)
        if params.blur >= 3 and params.blur % 2 == 1:
            gray = cv2.GaussianBlur(gray, (params.blur, params.blur), 0)

        # Grab the reference on request (uses the same processing as detection).
        if grab_ref:
            with self._lock:
                self._reference = gray.copy()
                # Keep the full-res colour frame too, for saving the background
                # (see docs/decisions/0020 + get_background_image()).
                self._reference_full = frame_bgr.copy()
            reference = gray

        def to_full(pt: Tuple[float, float]) -> Tuple[float, float]:
            return (pt[0] / scale, pt[1] / scale) if scale else pt

        def model_fields():
            return dict(has_model=model is not None,
                        model_samples=(model.n if model is not None else 0),
                        model_coverage=(model.coverage_ok if model is not None else False))

        # No / mismatched reference -> nothing to subtract.
        if reference is None:
            self._state = STATE_NO_REFERENCE
            return self._finish(TrackResult(False, STATE_NO_REFERENCE,
                                            frame_size=(full_w, full_h), **model_fields()))
        if reference.shape != gray.shape:
            self._state = STATE_REF_MISMATCH
            return self._finish(TrackResult(False, STATE_REF_MISMATCH,
                                            frame_size=(full_w, full_h), **model_fields()))

        # Foreground mask: absolute difference, thresholded + cleaned.
        diff = cv2.absdiff(gray, reference)
        _, mask = cv2.threshold(diff, params.thresh, 255, cv2.THRESH_BINARY)
        if params.morph >= 3:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (params.morph, params.morph))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

        roi_mask = self._roi_mask(roi_norm, pw, ph)
        if roi_mask is not None:
            mask = cv2.bitwise_and(mask, roi_mask)

        detections = self._detections(mask, params)

        # HSV is needed only when scoring/registering with appearance.
        need_hsv = (self._reg is not None) or (model is not None)
        hsv = cv2.cvtColor(proc, cv2.COLOR_BGR2HSV) if need_hsv else None

        # ------------------------------------------------ registration branch
        if self._reg is not None:
            sample = self._pick_registration_blob(detections, pw, ph, params)
            if sample is not None and hsv is not None:
                hist_raw = self._blob_histogram(hsv, sample.contour, normalize=False)
                if hist_raw is not None:
                    self._reg.add_sample(
                        hist_raw,
                        compute_form_vector(sample.area, sample.elongation, sample.solidity))
            self._state = STATE_REGISTERING

            if finalize_now:
                self._reg.recompute(params)
                built = self._reg if self._reg.n > 0 else None
                self._reg = None
                if built is not None:
                    with self._lock:
                        self._model = built
                    model = built
                self._state = STATE_ACQUIRING
                # fall through to normal tracking this frame
            else:
                centroid = to_full(sample.centroid) if sample is not None else None
                return self._finish(TrackResult(
                    sample is not None, STATE_REGISTERING,
                    centroid=centroid, smoothed=centroid,
                    area=sample.area if sample is not None else 0.0,
                    elongation=sample.elongation if sample is not None else 0.0,
                    solidity=sample.solidity if sample is not None else 0.0,
                    confidence=0.0,
                    candidates=[to_full(d.centroid) for d in detections if d is not sample],
                    frame_size=(full_w, full_h), mask=mask,
                    chosen_contour=(sample.contour if sample is not None else None),
                    reg_accepted=self._reg_accepted_safe(),
                    reg_target=params.reg_min_frames, **model_fields()), params)

        # ------------------------------------------------------ tracking branch
        predicted = None
        if self._kalman_primed:
            pred = self._kalman.predict()
            predicted = (float(pred[0, 0]), float(pred[1, 0]))

        chosen, rejected, score = self._associate(
            detections, predicted, model, hsv, params)

        if chosen is not None:
            meas = np.array([[np.float32(chosen.centroid[0])],
                             [np.float32(chosen.centroid[1])]])
            if not self._kalman_primed:
                self._kalman.statePost = np.array(
                    [[meas[0, 0]], [meas[1, 0]], [0.0], [0.0]], np.float32)
                self._kalman_primed = True
                est = chosen.centroid
            else:
                e = self._kalman.correct(meas)
                est = (float(e[0, 0]), float(e[1, 0]))

            self._frames_since_seen = 0
            self._confidence = float(score)
            if self._state == STATE_TRACKING:
                pass
            else:
                self._frames_confirmed += 1
                if self._frames_confirmed >= max(1, params.confirm_frames):
                    self._state = STATE_TRACKING
                else:
                    self._state = STATE_ACQUIRING

            # Online ENRICHMENT (accumulate, not drift): add this confident,
            # unambiguous observation to the model's running statistics so the
            # captured colour/size/shape RANGE grows. Guarded so an intruder is
            # not learned.
            if (model is not None and params.adapt_enrich
                    and self._state == STATE_TRACKING
                    and self._confidence >= params.adapt_min_conf
                    and not rejected
                    and (params.adapt_max_samples <= 0 or model.n < params.adapt_max_samples)
                    and hsv is not None):
                self._enrich_model(model, hsv, chosen, params)

            result = TrackResult(
                True, self._state,
                centroid=to_full(chosen.centroid),
                smoothed=to_full(est),
                area=chosen.area, elongation=chosen.elongation,
                solidity=chosen.solidity, confidence=self._confidence,
                candidates=[to_full(d.centroid) for d in rejected],
                frame_size=(full_w, full_h), mask=mask,
                chosen_contour=chosen.contour, **model_fields())
        else:
            # No accepted blob this frame.
            self._frames_confirmed = 0
            if self._kalman_primed:
                self._frames_since_seen += 1
                self._confidence *= COAST_CONF_DECAY
                if self._frames_since_seen > max(1, params.coast_max_frames):
                    self._state = STATE_LOST
                    self._kalman_primed = False
                    self._confidence = 0.0
                    smoothed = None
                else:
                    self._state = STATE_COASTING
                    smoothed = to_full(predicted) if predicted is not None else None
            else:
                self._state = STATE_ACQUIRING
                self._confidence = 0.0
                smoothed = None
            result = TrackResult(
                False, self._state, centroid=None, smoothed=smoothed, area=0.0,
                confidence=self._confidence,
                candidates=[to_full(d.centroid) for d in rejected],
                frame_size=(full_w, full_h), mask=mask, **model_fields())

        return self._finish(result, params)

    # --------------------------------------------------------- internals
    def _reg_accepted_safe(self) -> int:
        return self._reg.n if self._reg is not None else 0

    def _detections(self, mask, params: TrackerParams) -> List[Detection]:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out: List[Detection] = []
        for c in contours:
            area = cv2.contourArea(c)
            if area < params.min_area:
                continue
            if params.max_area > 0 and area > params.max_area:
                continue
            m = cv2.moments(c)
            if m["m00"] <= 0:
                continue
            cx = m["m10"] / m["m00"]
            cy = m["m01"] / m["m00"]
            elong = self._elongation_from_moments(m)
            solidity = self._solidity(c, area)
            out.append(Detection((cx, cy), area, cv2.boundingRect(c),
                                  elongation=elong, solidity=solidity, contour=c))
        return out

    @staticmethod
    def _elongation_from_moments(m) -> float:
        """Major/minor axis ratio from the contour's 2nd central moments
        (rotation invariant; 1.0 = circular, larger = more elongated)."""
        m00 = m["m00"]
        if m00 <= 0:
            return 1.0
        a = m["mu20"] / m00
        b = m["mu11"] / m00
        c = m["mu02"] / m00
        common = math.sqrt(max(0.0, (a - c) ** 2 + 4.0 * b * b))
        l1 = (a + c + common) / 2.0
        l2 = (a + c - common) / 2.0
        if l2 <= 1e-6:
            return ELONG_MAX
        return float(min(ELONG_MAX, math.sqrt(l1 / l2)))

    @staticmethod
    def _solidity(contour, area: float) -> float:
        """Contour area / convex-hull area (rotation invariant; <1 = concave)."""
        hull = cv2.convexHull(contour)
        ha = cv2.contourArea(hull)
        if ha <= 0:
            return 1.0
        return float(min(1.0, area / ha))

    def _pick_registration_blob(self, detections: List[Detection], pw: int, ph: int,
                                params: TrackerParams) -> Optional[Detection]:
        """Largest in-ROI blob, accepted only if it clearly dominates and is not
        clipped by the frame edge (so the model learns whole-animal samples)."""
        if not detections:
            return None
        ordered = sorted(detections, key=lambda d: d.area, reverse=True)
        largest = ordered[0]
        if len(ordered) > 1 and ordered[1].area > params.reg_dominance * largest.area:
            return None  # ambiguous (two similar blobs) -> skip this frame
        x, y, w, h = largest.bbox
        if x <= 1 or y <= 1 or (x + w) >= (pw - 1) or (y + h) >= (ph - 1):
            return None  # touching border -> probably partially out of view
        return largest

    def _blob_histogram(self, hsv, contour, normalize: bool = True):
        """H-S histogram of the pixels inside a contour (raw counts or normalized)."""
        if hsv is None or contour is None:
            return None
        m = np.zeros(hsv.shape[:2], np.uint8)
        cv2.drawContours(m, [contour], -1, 255, thickness=cv2.FILLED)
        hist = cv2.calcHist([hsv], [0, 1], m, [HIST_H_BINS, HIST_S_BINS], HIST_RANGES)
        if normalize:
            s = float(hist.sum())
            if s > 0:
                hist = hist / s
        return hist

    def _enrich_model(self, model: RatModel, hsv, chosen: Detection,
                      params: TrackerParams) -> None:
        """Accumulate a confident observation into the model's statistics."""
        hist_raw = self._blob_histogram(hsv, chosen.contour, normalize=False)
        if hist_raw is None:
            return
        form_x = compute_form_vector(chosen.area, chosen.elongation, chosen.solidity)
        with self._lock:
            if self._model is not model:   # model swapped meanwhile
                return
            model.add_sample(hist_raw, form_x)
            model.recompute(params)

    def _associate(self, detections: List[Detection], predicted,
                   model: Optional[RatModel], hsv, params: TrackerParams
                   ) -> Tuple[Optional[Detection], List[Detection], float]:
        """Pick the rat blob.

        Without a model: Phase 2 behaviour - largest blob within the gate.

        With a model: each gated, size-plausible blob is scored on distance,
        colour-histogram and FORM (size+shape Gaussian, soft); the best is
        accepted only if its score clears accept_score. During a fresh
        (re)acquisition (no prediction) the distance term is dropped so APPEARANCE
        decides, which is what rejects a wrong-looking intruder.
        """
        if not detections:
            return None, [], 0.0

        gated = detections
        if predicted is not None and params.gate_radius > 0:
            gated = [d for d in detections
                     if math.hypot(d.centroid[0] - predicted[0],
                                   d.centroid[1] - predicted[1]) <= params.gate_radius]
        if not gated:
            return None, detections, 0.0

        if model is None:
            chosen = max(gated, key=lambda d: d.area)
            if predicted is not None and params.gate_radius > 0:
                dist = math.hypot(chosen.centroid[0] - predicted[0],
                                  chosen.centroid[1] - predicted[1])
                conf = math.exp(-(dist * dist) / (2.0 * params.gate_radius ** 2))
            else:
                conf = 1.0
            return chosen, [d for d in detections if d is not chosen], conf

        # --- model-based scoring (all tolerance knobs read live here) ---
        have_pred = predicted is not None
        # Appearance-vs-position balance: appearance_bias is appearance's share
        # of the score; the remainder goes to distance (only when a prediction
        # exists). The colour:shape split keeps the w_hist:w_form ratio.
        bias = min(max(params.appearance_bias, 0.0), 1.0)
        w_d = (1.0 - bias) if have_pred else 0.0
        denom = params.w_hist + params.w_form
        ratio_h = (params.w_hist / denom) if denom > 0 else 0.5
        w_h = bias * ratio_h
        w_f = bias * (1.0 - ratio_h)
        wsum = w_d + w_h + w_f

        lo, hi = model.area_band(params.area_gate_k)
        size_gated = [d for d in gated if lo <= d.area <= hi]
        if not size_gated:
            return None, detections, 0.0
        if wsum <= 0:
            chosen = max(size_gated, key=lambda d: d.area)
            return chosen, [d for d in detections if d is not chosen], 1.0

        sigma_d = params.gate_radius if params.gate_radius > 0 else 150.0
        hist_min = min(max(params.hist_min, 0.0), 1.0)
        best: Optional[Detection] = None
        best_score = -1.0
        for d in size_gated:
            if have_pred:
                dist = math.hypot(d.centroid[0] - predicted[0],
                                  d.centroid[1] - predicted[1])
                s_dist = math.exp(-(dist * dist) / (2.0 * sigma_d ** 2))
            else:
                s_dist = 0.0
            if d.hist is None:
                d.hist = self._blob_histogram(hsv, d.contour, normalize=True)
            s_hist = model.hist_score(d.hist)
            if hist_min > 0.0 and s_hist < hist_min:
                continue  # hard colour gate: reject clearly wrong-coloured blobs
            s_form = model.form_score(
                compute_form_vector(d.area, d.elongation, d.solidity), params.form_tol)
            score = (w_d * s_dist + w_h * s_hist + w_f * s_form) / wsum
            if score > best_score:
                best_score = score
                best = d

        if best is None or best_score < params.accept_score:
            return None, detections, max(0.0, best_score)
        return best, [d for d in detections if d is not best], best_score

    def _roi_mask(self, roi_norm, pw: int, ph: int):
        if not roi_norm:
            return None
        key = tuple(roi_norm)
        with self._lock:
            cache = self._roi_cache
        if cache is not None and cache[0] == key and cache[1] == (pw, ph):
            return cache[2]
        pts = np.array([[int(round(x * pw)), int(round(y * ph))] for x, y in roi_norm],
                       dtype=np.int32)
        m = np.zeros((ph, pw), np.uint8)
        cv2.fillPoly(m, [pts], 255)
        with self._lock:
            self._roi_cache = (key, (pw, ph), m)
        return m

    def _finish(self, result: TrackResult, params: Optional[TrackerParams] = None) -> TrackResult:
        use_kalman = params.use_kalman if params is not None else True
        draw = result.smoothed if (use_kalman and result.smoothed is not None) else result.centroid
        with self._lock:
            self._latest = result
            if draw is not None:
                self._path.append(draw)
        return result
