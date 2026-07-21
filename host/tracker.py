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

  - Persistence: RatModel save/load (.npz, sufficient statistics + the
    acquisition-time TrackerParams, restored on load - see docs/decisions/0027)
    and ROI save/load (.json), so a model, its tuning, and the maze outline can
    be reused across sessions.

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
from dataclasses import asdict, dataclass, field, fields, replace
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

    # --- clustering (fragmented rat = a cluster of blobs; see docs/decisions/0037) ---
    # The rat's mask is often several disconnected blobs (pose + lighting). When
    # cluster_gap > 0, blobs whose bounding boxes are within cluster_gap (proc px)
    # are merged into ONE candidate before association/registration. cluster_hist_min
    # is a lenient colour-MEMBERSHIP gate (only active once a model exists): a
    # fragment may join only if this fraction of its pixels are colours the model has
    # observed (its histogram support) - NOT a per-fragment distribution match.
    cluster_gap: float = 0.0       # 0 = off (single-blob behaviour, backward compat)
    cluster_hist_min: float = 0.0  # 0 = spatial-only merge even with a model present

    # --- registration window ---
    reg_min_frames: int = 30          # min accepted samples for a "covered" model
    reg_dominance: float = 0.5        # 2nd blob must be < this * largest to sample
    # Registration-only: the COLOUR support is learned from the dominant gap-cluster
    # plus every other in-ROI cluster within reg_proximity_mult * cluster_gap of it
    # (same bbox-gap metric), so detached-but-nearby parts (tail/head) enter the
    # support and are not rejected at tracking time. The size/shape Gaussian is still
    # learned from the dominant gap-cluster only (see docs/decisions/0037).
    reg_proximity_mult: float = 3.0
    reg_logarea_spread_min: float = 0.10  # std(log area) needed for coverage_ok
    area_gate_k: float = 3.0          # hard size band = exp(muA +/- k*sigmaA)

    # --- online enrichment (accumulate, NOT drift) ---
    adapt_enrich: bool = False    # keep accumulating model stats while tracking
    adapt_min_conf: float = 0.85  # only enrich from confident frames
    adapt_max_samples: int = 0    # cap on total form samples (0 = unlimited)

    # --- blocker (parallel-tracked physical obstacle; see docs/decisions/0034) ---
    # The blocker is identified by its ORANGE tape (a position-invariant cue), not
    # its silhouette, which changes with placement. The HSV orange range below is
    # only a SEED / fallback + the S,V floor that isolates tape pixels from the
    # blocker's black surfaces during registration; the operating range is LEARNED
    # from the tape and stored on the blocker RatModel (orange_lo/hi).
    blocker_h_lo: int = 5          # OpenCV hue is 0-180 (orange ~ 5-25)
    blocker_h_hi: int = 25
    blocker_s_min: int = 90        # saturation floor (also isolates tape vs black)
    blocker_v_min: int = 90        # value/brightness floor
    blocker_orange_min_px: int = 40  # min in-range px to call a blob the blocker
    blocker_footprint_dilate: int = 5  # px the stored footprint grows before subtraction
    # A candidate whose filled area is at least this fraction inside the blocker
    # footprint is dropped (it IS the blocker) before clustering, rather than
    # pixel-subtracting + re-detecting the whole frame. Overlap (not centroid)
    # so a separate rat blob grazing the dilated halo is kept. See ADR-0039.
    blocker_overlap_drop: float = 0.6


# --- parameter groups persisted with a saved model (see docs/decisions/0027) ---
# The acquisition-time TrackerParams are stored in the .npz and, on load, the
# full tuned operating point is restored (no opt-out) EXCEPT the provenance-only
# group. Two orthogonal reasons a knob matters, both served by restoring:
#   * correctness - the SHAPING knobs change the *measured* blob (proc geometry +
#     mask), so a model's stored statistics are only valid under the same values;
#   * robust detection of *this* model - the appearance-scoring + tracking-
#     dynamics knobs are the tuning that makes the model lock on and reject
#     intruders (these are live per ADR-0017, but "live" governs WHEN a knob
#     applies, not whether its tuned value should be persisted/restored).
MODEL_SHAPING_FIELDS = (
    "proc_max_w", "thresh", "blur", "morph", "min_area", "max_area",
    # cluster_gap changes the measured blob (fragments get merged), so a model's
    # stored form/colour stats are only valid under the same gap (see ADR-0037).
    "cluster_gap",
)
# Saved for provenance but NOT re-applied on load: reg_* only matter while
# building a *new* model, and adapt_* (keep-learning) is a live session choice.
MODEL_PROVENANCE_ONLY_FIELDS = (
    "reg_min_frames", "reg_dominance", "reg_proximity_mult",
    "reg_logarea_spread_min",
    "adapt_enrich", "adapt_min_conf", "adapt_max_samples",
)
# Everything else is restored on load (shaping + appearance-scoring +
# tracking-dynamics), so a reloaded model comes back with the tuning it was
# built under. A restored proc_max_w additionally invalidates the reference.
MODEL_REAPPLY_FIELDS = tuple(
    f.name for f in fields(TrackerParams)
    if f.name not in MODEL_PROVENANCE_ONLY_FIELDS
)


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
            # Blocker centroid marker in orange (full-frame px -> preview via scale).
            if result.blocker_centroid is not None:
                bx = int(result.blocker_centroid[0] * scale)
                by = int(result.blocker_centroid[1] * scale)
                cv2.drawMarker(img_bgr, (bx, by), (0, 140, 255),
                               markerType=cv2.MARKER_SQUARE, markerSize=14, thickness=2)
                cv2.putText(img_bgr, "BLOCKER", (bx + 8, by - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 140, 255), 1, cv2.LINE_AA)
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
    """One foreground blob (in proc-frame coordinates).

    A *clustered* detection (see _cluster_detections / ADR-0037) aggregates several
    fragments: `contour` is then the union convex hull, and `members` holds each
    fragment's own contour so the colour histogram is taken over the true rat pixels
    (the union of member interiors) rather than the hull's interior gaps. For a plain
    single blob `members` is None (treated as `[contour]`).
    """
    centroid: Tuple[float, float]
    area: float
    bbox: Tuple[int, int, int, int]
    elongation: float = 1.0
    solidity: float = 1.0
    contour: Optional["np.ndarray"] = None
    hist: Optional["np.ndarray"] = None   # lazily filled normalized H-S hist
    members: Optional[List["np.ndarray"]] = None  # fragment contours (clustered)


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
        # Acquisition-time tracker settings this model was built under, restored
        # on load so the tuned operating point comes back with it (see
        # docs/decisions/0027). None for models saved before this was added.
        self.acq_params: Optional["TrackerParams"] = None
        # Learned ORANGE range for a blocker model (see docs/decisions/0034):
        # 1-D histograms of the tape pixels' H/S/V accumulated during
        # registration, from which robust bounds (orange_lo/hi as (H,S,V)) are
        # derived in recompute(). Empty/None for a rat model.
        self.tape_h = np.zeros(180, np.float64)
        self.tape_s = np.zeros(256, np.float64)
        self.tape_v = np.zeros(256, np.float64)
        self.tape_n = 0
        self.orange_lo: Optional[Tuple[int, int, int]] = None
        self.orange_hi: Optional[Tuple[int, int, int]] = None

    # ----------------------------------------------------- accumulation
    def add_sample(self, hist_raw: "np.ndarray", form_x: "np.ndarray") -> None:
        self.hist_counts += hist_raw
        self.sum_x += form_x
        self.sum_xx += np.outer(form_x, form_x)
        self.n += 1

    def add_orange_pixels(self, hsv_pixels: "np.ndarray") -> None:
        """Accumulate an (N, 3) array of HSV tape pixels into the 1-D H/S/V
        histograms (used to LEARN the orange range in recompute())."""
        if hsv_pixels is None or len(hsv_pixels) == 0:
            return
        p = hsv_pixels.reshape(-1, 3)
        self.tape_h += np.bincount(p[:, 0].astype(np.intp), minlength=180)[:180]
        self.tape_s += np.bincount(p[:, 1].astype(np.intp), minlength=256)[:256]
        self.tape_v += np.bincount(p[:, 2].astype(np.intp), minlength=256)[:256]
        self.tape_n += int(len(p))

    @staticmethod
    def _hist_percentile(hist: "np.ndarray", q: float) -> int:
        """Value at percentile q (0..1) of a 1-D count histogram."""
        total = float(hist.sum())
        if total <= 0:
            return 0
        cdf = np.cumsum(hist) / total
        return int(np.searchsorted(cdf, q))

    def _recompute_orange(self) -> None:
        """Derive robust orange bounds (H,S,V) from the accumulated tape pixels.

        Hue uses a tight central band [p2, p98]; saturation/value use low-
        percentile floors (the tape is the colourful, bright subset). Leaves
        orange_lo/hi unchanged when no tape pixels were collected (rat model)."""
        if self.tape_n <= 0:
            return
        h_lo = self._hist_percentile(self.tape_h, 0.02)
        h_hi = self._hist_percentile(self.tape_h, 0.98)
        s_lo = self._hist_percentile(self.tape_s, 0.05)
        v_lo = self._hist_percentile(self.tape_v, 0.05)
        # Small hue margin for robustness; clamp to valid ranges.
        h_lo = max(0, h_lo - 3)
        h_hi = min(179, max(h_hi + 3, h_lo + 1))
        self.orange_lo = (int(h_lo), int(max(0, s_lo)), int(max(0, v_lo)))
        self.orange_hi = (int(h_hi), 255, 255)

    def recompute(self, params: "TrackerParams") -> None:
        self._recompute_orange()
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

    def in_support_fraction(self, hist_counts: "np.ndarray") -> float:
        """Fraction of a fragment's pixels whose H-S bin the model has *ever*
        observed (pooled `hist_counts > 0`). This is a lenient colour-MEMBERSHIP
        test used to decide whether a fragment may join the rat's cluster (see
        docs/decisions/0037): a differently-coloured intruder scores ~0, while any
        body part (tail/head/torso) whose colours are a subset of the rat's palette
        scores high - unlike hist_score, it does not require a distribution match, so
        it never rejects a legitimate part. Returns 0.0 with no model/support."""
        if self.hist_counts is None or hist_counts is None:
            return 0.0
        total = float(hist_counts.sum())
        if total <= 0:
            return 0.0
        support = self.hist_counts > 0
        return float(hist_counts[support].sum()) / total

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
        # Persist the acquisition-time params as JSON (allow_pickle=False-safe,
        # like animal/date); proc_max_w is also stored plainly for quick checks.
        extra = {}
        if self.acq_params is not None:
            extra["acq_params_json"] = np.array(json.dumps(asdict(self.acq_params)))
            extra["proc_max_w"] = np.int64(self.acq_params.proc_max_w)
        # Blocker models carry a learned orange range + its tape histograms
        # (additive keys; rat models simply have tape_n == 0). See ADR-0034.
        if self.tape_n > 0:
            extra["tape_h"] = self.tape_h.astype(np.float64)
            extra["tape_s"] = self.tape_s.astype(np.float64)
            extra["tape_v"] = self.tape_v.astype(np.float64)
            extra["tape_n"] = np.int64(self.tape_n)
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
            **extra,
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
        # Restore the learned orange tape histograms (blocker models); recompute()
        # below re-derives orange_lo/hi from them. Absent for rat/old models.
        if "tape_n" in d:
            m.tape_h = np.asarray(d["tape_h"], np.float64)
            m.tape_s = np.asarray(d["tape_s"], np.float64)
            m.tape_v = np.asarray(d["tape_v"], np.float64)
            m.tape_n = int(d["tape_n"])
        # Restore the acquisition-time params (additive/optional key; older models
        # simply lack it and load with acq_params=None). Only known dataclass
        # fields are used, so extra/missing keys don't break forward/back compat.
        if "acq_params_json" in d:
            try:
                raw = json.loads(str(d["acq_params_json"]))
                valid = {f.name for f in fields(TrackerParams)}
                m.acq_params = TrackerParams(
                    **{k: v for k, v in raw.items() if k in valid})
            except Exception:
                m.acq_params = None
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
    # Parallel-tracked blocker (see docs/decisions/0034).
    blocker_detected: bool = False
    blocker_centroid: Optional[Tuple[float, float]] = None  # full px


# Shared column set for per-frame track rows (video sidecar + standalone log).
TRACK_COLUMNS = (
    "track_state", "track_detected", "track_cx", "track_cy",
    "track_smooth_cx", "track_smooth_cy", "track_area",
    "track_elongation", "track_solidity", "track_confidence",
    "blocker_detected", "blocker_cx", "blocker_cy",
)
_TRACK_BLANKS = ("",) * len(TRACK_COLUMNS)


def track_row(result: Optional[TrackResult]) -> tuple:
    """Flatten a TrackResult into TRACK_COLUMNS values (blanks if None)."""
    if result is None:
        return _TRACK_BLANKS

    def fmt(pt):
        return (f"{pt[0]:.1f}", f"{pt[1]:.1f}") if pt is not None else ("", "")

    cx, cy = fmt(result.centroid)
    sx, sy = fmt(result.smoothed)
    bcx, bcy = fmt(result.blocker_centroid)
    return (result.state, int(result.detected), cx, cy, sx, sy,
            int(result.area), f"{result.elongation:.3f}",
            f"{result.solidity:.3f}", f"{result.confidence:.3f}",
            int(result.blocker_detected), bcx, bcy)


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

        # Blocker: parallel appearance-tracked obstacle (see docs/decisions/0034).
        # Registration is on-demand still captures (blocker as the dominant blob);
        # tracking claims the blocker by its LEARNED orange range and subtracts its
        # pixel footprint from the mask so a rat touching it keeps its own track.
        self._blocker_model: Optional[RatModel] = None
        self._blocker_reg: Optional[RatModel] = None
        self._blocker_sample_pending = False
        self._blocker_finalize_pending = False
        self._blocker_cancel_pending = False
        self._blocker_footprint: Optional["np.ndarray"] = None   # proc-res mask
        self._blocker_last: Optional[Tuple[float, float]] = None  # proc-res centroid

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
                self._blocker_footprint = None  # footprint is proc-resolution too
                self._blocker_last = None
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
            self._blocker_footprint = None
            self._blocker_last = None

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

    def load_model(self, path: str, restore: bool = True) -> bool:
        """Load a model and (by default) restore its acquisition-time settings.

        The full tuned operating point (MODEL_REAPPLY_FIELDS) is re-applied so a
        reloaded model is scored under the settings it was built with; the
        provenance-only knobs (reg_*/adapt_*) are left as the current session's.
        A restored proc_max_w invalidates the reference (re-grab background).
        Older models without stored settings load unchanged (restore is a no-op).
        See docs/decisions/0027.
        """
        params = self.get_params()
        model = RatModel.load(path, params)
        with self._lock:
            self._model = model
        if restore and model.acq_params is not None:
            self.update_params(**{f: getattr(model.acq_params, f)
                                  for f in MODEL_REAPPLY_FIELDS})
        return True

    # --------------------------------------------------------- blocker API
    # A blocker is registered from on-demand still captures (the operator places
    # the obstacle, withdraws their hand, then captures a sample); the dominant
    # foreground blob is sampled and its orange tape pixels learned. See ADR-0034.
    def capture_blocker_sample(self) -> None:
        """Request that the next processed frame contribute one blocker sample."""
        with self._lock:
            self._blocker_sample_pending = True

    def finalize_blocker_model(self) -> None:
        with self._lock:
            self._blocker_finalize_pending = True

    def cancel_blocker_registration(self) -> None:
        with self._lock:
            self._blocker_cancel_pending = True

    def is_registering_blocker(self) -> bool:
        with self._lock:
            return self._blocker_reg is not None

    def blocker_registration_status(self) -> Tuple[bool, int]:
        """(registering, accepted_samples)."""
        with self._lock:
            if self._blocker_reg is not None:
                return True, self._blocker_reg.n
            return False, 0

    def has_blocker_model(self) -> bool:
        with self._lock:
            return self._blocker_model is not None

    def get_blocker_model(self) -> Optional[RatModel]:
        with self._lock:
            return self._blocker_model

    def clear_blocker_model(self) -> None:
        with self._lock:
            self._blocker_model = None
            self._blocker_footprint = None
            self._blocker_last = None

    def get_blocker_orange_range(self):
        """Learned (lo, hi) HSV orange bounds of the blocker model, or None."""
        with self._lock:
            m = self._blocker_model
        if m is None or m.orange_lo is None:
            return None
        return (m.orange_lo, m.orange_hi)

    def save_blocker_model(self, path: str) -> bool:
        with self._lock:
            model = self._blocker_model
        if model is None:
            return False
        model.save(path)
        return True

    def load_blocker_model(self, path: str) -> bool:
        """Load a blocker model (its learned orange range + appearance).

        Unlike the rat model, the blocker's acquisition params are provenance-only
        and NOT re-applied, so loading a blocker never disturbs the rat model's
        restored operating point. See ADR-0034."""
        model = RatModel.load(path, self.get_params())
        with self._lock:
            self._blocker_model = model
            self._blocker_footprint = None
            self._blocker_last = None
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
            blocker_model = self._blocker_model
            blk_sample = self._blocker_sample_pending
            blk_finalize = self._blocker_finalize_pending
            blk_cancel = self._blocker_cancel_pending
            self._blocker_sample_pending = False
            self._blocker_finalize_pending = False
            self._blocker_cancel_pending = False
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

        # HSV is needed when scoring/registering the rat OR the blocker.
        need_hsv = ((self._reg is not None) or (model is not None) or blk_sample
                    or (self._blocker_reg is not None) or (blocker_model is not None))
        hsv = cv2.cvtColor(proc, cv2.COLOR_BGR2HSV) if need_hsv else None

        registering = (self._reg is not None or blk_sample
                       or self._blocker_reg is not None)

        # Blocker (see docs/decisions/0034, 0035, 0039): locate its orange-tape
        # footprint every frame (for the overlay readout and to move with it), and
        # - during tracking - DROP its own blob(s) from the candidate list before
        # clustering, so the blocker is neither tracked as the rat nor merged into
        # a rat cluster. This whole-detection removal (a containment test) replaces
        # the former per-frame pixel-subtract + re-detect + re-cluster: it keeps
        # process() to ONE detect + ONE cluster pass whenever the tape is merely
        # visible, which is the dominant per-frame load in #260720. The drop is
        # skipped while any registration is active (so a re-registered blocker /
        # rat sample still sees every blob). Trade-off (ADR-0039): a rat FUSED with
        # the blocker into a single contour is not split - that blob is dropped
        # rather than stealing the track - so the rat briefly coasts during direct
        # contact.
        if blocker_model is not None:
            orange_range = ((blocker_model.orange_lo, blocker_model.orange_hi)
                            if blocker_model.orange_lo is not None else None)
            fp, bc = self._blocker_footprint_from_orange(
                hsv, mask, orange_range, params, pw, ph)
            self._blocker_footprint = fp   # None when no tape this frame
            self._blocker_last = bc        # None when no tape this frame
            if fp is not None and fp.shape == mask.shape and not registering:
                detections = [d for d in detections
                              if self._footprint_overlap(d, fp)
                              < params.blocker_overlap_drop]

        # Reassemble a fragmented rat: merge nearby (colour-consistent) blobs into a
        # single candidate before registration + association (see docs/decisions/0037).
        # While ANY registration is active the colour gate is off (no committed
        # support to test against yet), so a stale model can't bias a fresh build.
        gate_model = None if registering else model
        detections = self._cluster_detections(detections, gate_model, hsv, params, pw, ph)

        # ------------------------------------ blocker registration (still captures)
        # Independent of rat tracking: each capture samples the dominant blob's
        # appearance + learns its tape colour (see docs/decisions/0034).
        if blk_cancel:
            self._blocker_reg = None
        if blk_sample:
            if self._blocker_reg is None:
                self._blocker_reg = RatModel()
                self._blocker_reg.animal = animal
            self._accumulate_blocker_sample(
                self._blocker_reg, detections, hsv, pw, ph, params)
        if blk_finalize and self._blocker_reg is not None:
            self._blocker_reg.recompute(params)
            built = self._blocker_reg if self._blocker_reg.n > 0 else None
            self._blocker_reg = None
            if built is not None:
                built.acq_params = replace(params)
                with self._lock:
                    self._blocker_model = built
                blocker_model = built

        # ------------------------------------------------ registration branch
        if self._reg is not None:
            # Decoupled sampling (ADR-0037): FORM from the dominant gap-cluster;
            # COLOUR support from the broader proximity union, so detached-but-nearby
            # parts (tail/head) enter the palette and aren't rejected while tracking.
            picked = self._pick_registration_sample(detections, pw, ph, params)
            sample = picked[0] if picked is not None else None
            if picked is not None and hsv is not None:
                dominant, colour_contours = picked
                hist_raw = self._blob_histogram(hsv, colour_contours, normalize=False)
                if hist_raw is not None:
                    self._reg.add_sample(
                        hist_raw,
                        compute_form_vector(dominant.area, dominant.elongation,
                                            dominant.solidity))
            self._state = STATE_REGISTERING

            if finalize_now:
                self._reg.recompute(params)
                built = self._reg if self._reg.n > 0 else None
                self._reg = None
                if built is not None:
                    # Capture the settings this model was built under, so a later
                    # save/load restores the tuned operating point (ADR-0027).
                    built.acq_params = replace(params)
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

        # Attach blocker readout (last known tape position, in full-frame px).
        if blocker_model is not None and self._blocker_last is not None:
            result.blocker_detected = True
            result.blocker_centroid = to_full(self._blocker_last)

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
    def _bbox_gap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
        """Nearest-edge distance between two axis-aligned bounding boxes (0 if they
        overlap/touch), in the same px units as the bboxes."""
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        dx = max(0, max(ax, bx) - min(ax + aw, bx + bw))
        dy = max(0, max(ay, by) - min(ay + ah, by + bh))
        return math.hypot(dx, dy)

    def _cluster_detections(self, dets: List[Detection], model: Optional[RatModel],
                            hsv, params: TrackerParams, pw: int, ph: int
                            ) -> List[Detection]:
        """Merge a fragmented rat's nearby blobs into one candidate (ADR-0037).

        With `cluster_gap <= 0` or a single detection, returns `dets` unchanged
        (single-blob behaviour, backward compatible). Otherwise blobs whose bounding
        boxes are within `cluster_gap` (proc px) merge into one aggregated
        `Detection`. When a model + `cluster_hist_min` + hsv are all present, a
        fragment may join only if its colour is one the model has observed
        (`in_support_fraction >= cluster_hist_min`) - a lenient membership test that
        keeps a differently-coloured intruder out without rejecting real body parts.
        """
        n = len(dets)
        if params.cluster_gap <= 0 or n <= 1:
            return dets
        gap = float(params.cluster_gap)

        gate_on = (model is not None and params.cluster_hist_min > 0.0
                   and hsv is not None)
        gate_pass = [True] * n
        if gate_on:
            for i, d in enumerate(dets):
                hc = self._blob_histogram(hsv, self._fill_contours(d), normalize=False)
                frac = model.in_support_fraction(hc) if hc is not None else 0.0
                gate_pass[i] = frac >= params.cluster_hist_min

        parent = list(range(n))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i in range(n):
            if not gate_pass[i]:
                continue
            for j in range(i + 1, n):
                if not gate_pass[j]:
                    continue
                if self._bbox_gap(dets[i].bbox, dets[j].bbox) <= gap:
                    parent[find(j)] = find(i)

        groups: dict = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(dets[i])

        out: List[Detection] = []
        for members in groups.values():
            if len(members) == 1:
                out.append(members[0])
                continue
            merged = self._merge_cluster(members, pw, ph)
            if merged is None:
                out.extend(members)
            elif params.max_area > 0 and merged.area > params.max_area:
                out.extend(members)  # merged blob too big -> keep fragments unmerged
            else:
                out.append(merged)
        return out

    def _merge_cluster(self, members: List[Detection], pw: int, ph: int
                       ) -> Optional[Detection]:
        """Build one aggregated Detection from >1 fragments: area = sum of member
        areas; centroid + elongation from the filled union; solidity = union area /
        convex-hull area; contour = union hull; members kept for the colour hist."""
        contours = [d.contour for d in members if d.contour is not None]
        if not contours:
            return None
        m = np.zeros((ph, pw), np.uint8)
        cv2.drawContours(m, contours, -1, 255, thickness=cv2.FILLED)
        area = float(sum(d.area for d in members))
        mom = cv2.moments(m, binaryImage=True)
        if mom["m00"] > 0:
            cx = mom["m10"] / mom["m00"]
            cy = mom["m01"] / mom["m00"]
            elong = self._elongation_from_moments(mom)
        else:
            tot = area if area > 0 else 1.0
            cx = sum(d.centroid[0] * d.area for d in members) / tot
            cy = sum(d.centroid[1] * d.area for d in members) / tot
            elong = 1.0
        pts = np.vstack([c.reshape(-1, 2) for c in contours]).astype(np.int32)
        hull = cv2.convexHull(pts)
        union_px = float(cv2.countNonZero(m))
        hull_area = float(cv2.contourArea(hull))
        solidity = float(min(1.0, union_px / hull_area)) if hull_area > 0 else 1.0
        bbox = cv2.boundingRect(pts)
        return Detection((cx, cy), area, bbox, elongation=elong, solidity=solidity,
                         contour=hull, members=contours)

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

    def _pick_registration_sample(self, dets: List[Detection], pw: int, ph: int,
                                  params: TrackerParams):
        """Decoupled rat-registration sampler (ADR-0037): returns
        `(dominant_cluster, colour_contours)` or None.

        The dominant gap-cluster provides the FORM sample (its area/shape match how
        `cluster_gap` assembles the rat at tracking time). `colour_contours` unions
        the dominant cluster's members with those of every other in-ROI cluster
        within `reg_proximity_mult * cluster_gap` of it, so the learned colour
        support includes detached-but-nearby parts (tail/head) and is not
        body-biased. Skips the frame on border-touch, or when a *far* comparably-
        large cluster suggests a second object / intrusion rather than a body part.
        """
        if not dets:
            return None
        ordered = sorted(dets, key=lambda d: d.area, reverse=True)
        dominant = ordered[0]
        x, y, w, h = dominant.bbox
        if x <= 1 or y <= 1 or (x + w) >= (pw - 1) or (y + h) >= (ph - 1):
            return None  # partial animal at the frame edge
        radius = max(0.0, params.reg_proximity_mult) * max(0.0, params.cluster_gap)
        colour_contours = list(self._fill_contours(dominant))
        for d in ordered[1:]:
            if radius > 0 and self._bbox_gap(dominant.bbox, d.bbox) <= radius:
                colour_contours.extend(self._fill_contours(d))  # nearby body part
            elif d.area > params.reg_dominance * dominant.area:
                return None  # far + comparably large -> ambiguous, skip the frame
        return dominant, colour_contours

    def _blob_histogram(self, hsv, contours, normalize: bool = True):
        """H-S histogram of the pixels inside a contour (raw counts or normalized).

        `contours` may be a single contour or a list of them (a clustered
        detection's `members`); all are filled into one mask so the histogram covers
        the union of the fragments' interiors (see ADR-0037)."""
        if hsv is None or contours is None:
            return None
        cs = list(contours) if isinstance(contours, (list, tuple)) else [contours]
        cs = [c for c in cs if c is not None]
        if not cs:
            return None
        m = np.zeros(hsv.shape[:2], np.uint8)
        cv2.drawContours(m, cs, -1, 255, thickness=cv2.FILLED)
        hist = cv2.calcHist([hsv], [0, 1], m, [HIST_H_BINS, HIST_S_BINS], HIST_RANGES)
        if normalize:
            s = float(hist.sum())
            if s > 0:
                hist = hist / s
        return hist

    @staticmethod
    def _fill_contours(det: Detection) -> List["np.ndarray"]:
        """The contours to fill for a detection's colour histogram: its member
        fragments when clustered, else just its own contour."""
        if det.members:
            return det.members
        return [det.contour] if det.contour is not None else []

    def _footprint_overlap(self, det: Detection, fp) -> float:
        """Fraction (0..1) of a detection's filled area that lies inside the
        blocker footprint mask `fp`. Used to drop the blocker's own blob(s)
        before clustering (see ADR-0039). Overlap - not a centroid test - so a
        separate rat blob merely grazing the dilated footprint halo is kept."""
        contours = self._fill_contours(det)
        if not contours:
            return 0.0
        m = np.zeros(fp.shape, np.uint8)
        cv2.drawContours(m, contours, -1, 255, thickness=cv2.FILLED)
        total = int(cv2.countNonZero(m))
        if total <= 0:
            return 0.0
        inter = int(cv2.countNonZero(cv2.bitwise_and(m, fp)))
        return inter / float(total)

    def _enrich_model(self, model: RatModel, hsv, chosen: Detection,
                      params: TrackerParams) -> None:
        """Accumulate a confident observation into the model's statistics."""
        hist_raw = self._blob_histogram(hsv, self._fill_contours(chosen), normalize=False)
        if hist_raw is None:
            return
        form_x = compute_form_vector(chosen.area, chosen.elongation, chosen.solidity)
        with self._lock:
            if self._model is not model:   # model swapped meanwhile
                return
            model.add_sample(hist_raw, form_x)
            model.recompute(params)

    # ------------------------------------------------------- blocker helpers
    def _orange_mask(self, hsv, orange_range, params: TrackerParams):
        """Binary mask of orange (tape) pixels. Uses the model's LEARNED range
        when given, else the TrackerParams seed. See docs/decisions/0034."""
        if hsv is None:
            return None
        if orange_range is not None and orange_range[0] is not None:
            lo, hi = orange_range
        else:
            lo = (params.blocker_h_lo, params.blocker_s_min, params.blocker_v_min)
            hi = (params.blocker_h_hi, 255, 255)
        return cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))

    def _accumulate_blocker_sample(self, reg: RatModel, detections: List[Detection],
                                   hsv, pw: int, ph: int, params: TrackerParams) -> bool:
        """Add one blocker sample from a still capture: the dominant foreground
        blob's appearance + its colourful (tape) pixels. See docs/decisions/0034."""
        blob = self._pick_registration_blob(detections, pw, ph, params)
        if blob is None or hsv is None:
            return False
        fill = self._fill_contours(blob)   # member fragments when clustered
        hist_raw = self._blob_histogram(hsv, fill, normalize=False)
        if hist_raw is None:
            return False
        reg.add_sample(hist_raw,
                       compute_form_vector(blob.area, blob.elongation, blob.solidity))
        # Learn the tape colour: the colourful subset inside the blob (S,V above
        # the seed floors isolates the orange tape from the blocker's black body).
        m = np.zeros(hsv.shape[:2], np.uint8)
        cv2.drawContours(m, fill, -1, 255, thickness=cv2.FILLED)
        inside = hsv[m > 0]
        if inside.size:
            floor = ((inside[:, 1] >= params.blocker_s_min)
                     & (inside[:, 2] >= params.blocker_v_min))
            reg.add_orange_pixels(inside[floor])
        return True

    def _blocker_footprint_from_orange(self, hsv, mask, orange_range,
                                       params: TrackerParams, pw: int, ph: int):
        """Locate the blocker from its ORANGE tape and return (footprint, centroid)
        in proc-res, or (None, None) if too little tape is visible.

        The tape is only ever on the blocker (never on the black rat), so filling
        the convex hull of the in-range orange pixels covers the whole blocker
        (interior included) even when a rat is touching it - which is what lets the
        footprint split a merged rat+blocker blob without erasing the rat, and
        follow the blocker as it is moved. See docs/decisions/0034."""
        om = self._orange_mask(hsv, orange_range, params)
        if om is None:
            return None, None
        om = cv2.bitwise_and(om, mask)  # tape is foreground; ignore stray orange
        if int(cv2.countNonZero(om)) < params.blocker_orange_min_px:
            return None, None
        ys, xs = np.where(om > 0)
        pts = np.column_stack([xs, ys]).astype(np.int32)
        hull = cv2.convexHull(pts)
        fp = np.zeros((ph, pw), np.uint8)
        cv2.fillConvexPoly(fp, hull, 255)
        dsz = int(params.blocker_footprint_dilate)
        if dsz >= 1:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dsz + 1, 2 * dsz + 1))
            fp = cv2.dilate(fp, k)
        return fp, (float(xs.mean()), float(ys.mean()))

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
                d.hist = self._blob_histogram(hsv, self._fill_contours(d), normalize=True)
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
