"""trajectories.py - load per-frame tracking + carve it into per-trial outbound
runs for offline behavioural figures (pure pandas/numpy).

The rest of :mod:`tmaze.analysis` works at the per-TRIAL grain (one row per trial
from ``bundle_reader``). This module adds the missing per-FRAME grain: it reads a
session's video sidecar (the ``video/<id>.csv`` that ``camera_view`` writes beside
the recording - the Kalman-smoothed rat centroid per frame, stamped with
``host_unix_s`` wall-clock; ADR-0014) and joins it to the trial table by time so a
notebook can draw the actual path the animal took on each trial.

Two deliberate definitions live here (a future reader will ask "why these bounds /
these zones?", so they are pinned in ADR-0128):

- **Outbound decision window** (the DEFAULT) = ``[trial_start_iso, choice_time_iso]``
  (+ a small ``end_pad_s`` so the trace visibly enters the chosen arm). That is the
  cue->choice run - the stem climb, the pause at the split, the commit to an arm -
  and it EXCLUDES the reward/return/ITI leg. :func:`outbound_windows` now lets the
  caller pick ANY per-trial ISO events as the start/end bounds (with independent
  pads), e.g. ``end_event="trial_end_iso"`` for the whole trial; the default
  reproduces this ADR-0128 window (which the VTE dwell/velocity panels rely on).
- **VTE dwell** = seconds the centroid spends in ``CENTRAL_STEM`` u ``JUNCTION``
  during that window: the stem captures a pause at cue onset, the junction the
  head-swinging deliberation at the choice point. Zone membership reuses a
  vendored copy of the live classifier (:class:`tmaze.analysis.phase3_format
  .ZoneMap`, byte-for-byte the rig's ``maze_state`` logic) so offline == on-rig.

Peri-junction VELOCITY (ADR-0130) is a finer deliberation readout layered on top:
the rats usually blast through the mid-stem (~130 cm/s peak) straight into an arm,
but occasionally decelerate / near-stop by the junction. :func:`with_kinematics`
adds per-frame ``speed_cms`` (central-difference speed from the smoothed centroid x
the bundle's ``cm_per_px``, lightly smoothed) and ``s_junction_cm`` (signed distance
along the stem axis, 0 at the junction, negative down the stem). The stem axis is
the ``HUB``-centroid -> ``JUNCTION``-centroid line (the ``zones.json`` ``stem_axis``
is optional + usually absent). From that: junction-anchored speed PROFILES and three
scalars (junction min speed, slowing index, pause time) - see :func:`junction_kinematics`.

Time base. Trial ``*_iso`` stamps are host wall-clock written by ``datetime.now()``
(tz-naive local); the sidecar's ``host_unix_s`` is ``time.time()`` (POSIX). They
line up when the ISO string is read as LOCAL time, i.e. ``datetime.fromisoformat(
s).timestamp()`` - validated against a session's ``start_wall_clock`` header (match
to one frame). :func:`iso_to_unix` is that one conversion; use it everywhere.

Coordinates. Sidecar centroids are FULL-frame pixels of the undistorted/oriented
image; zones are normalized 0..1 (ADR-0015). :func:`load_sidecar` divides by the
header ``width``/``height`` so ``cx_n``/``cy_n`` overlay the zone polygons and the
``_background.png`` directly.

Read-only, pure pandas/numpy + stdlib. Tokens + the zone classifier come from the
analysis-side data contract (:mod:`tmaze.analysis.phase3_format`), so this module
does not import the live rig core. NO Tk/OpenCV/matplotlib: plotting stays in the
notebook. Headless tests: ``python -m unittest test_analysis_trajectories``.
"""
from __future__ import annotations

import glob
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from tmaze.analysis import phase3_format
from tmaze.analysis.phase3_format import CORRECT, HIGH, LOW

# Cue A / cue B are the proposal's plain labels for the two tones. Cue A = the HIGH
# tone, cue B = the LOW tone (the notebook maps A->blue, B->green). Pinned here so
# the label<->tone mapping has one home. See ADR-0128.
CUE_A = "A"
CUE_B = "B"
_TONE_TO_CUE = {HIGH: CUE_A, LOW: CUE_B}

# Zones whose union defines the VTE dwell region (stem pause + junction
# deliberation). Names come from the live vocabulary so a rename can't silently
# drift. See ADR-0128.
VTE_ZONES: Tuple[str, ...] = (phase3_format.CENTRAL_STEM, phase3_format.JUNCTION)

# Default validity gate on the tracker, matching the live scoring path: keep only
# frames the tracker actually locked onto (ADR-0014 track states).
_DEFAULT_MIN_CONFIDENCE = 0.5
_TRACKING = "TRACKING"

# Per-trial ISO event stamps (phase3_log) usable as window bounds in
# :func:`outbound_windows`, in task time order. A caller can bound each drawn
# window by any two of these (see ADR-0128 for the default cue->choice pair).
_ISO_EVENTS: Tuple[str, ...] = (
    "trial_start_iso", "choice_cue_on_iso", "choice_cue_off_iso",
    "choice_time_iso", "reward_cmd_iso", "return_cue_on_iso",
    "return_cue_off_iso", "trial_end_iso", "lick_time_iso",
)

# --- peri-junction velocity (VTE) constants; pinned in ADR-0130 ---------------
# Speed smoothing window (frames) over the central-difference velocity. 5 frames
# is ~0.12-0.2 s at 25-42 fps - enough to tame centroid jitter without blurring a
# real slow-down at the junction.
DEFAULT_SMOOTH_N = 5
# A "pause" is speed below this (cm/s). ~8% of the observed ~130 cm/s peak run
# speed on the stem - well below normal locomotion, so it flags a near-stop /
# deliberation, not just an ordinary turn (measured on 602 8/25-26; ADR-0130).
DEFAULT_PAUSE_SPEED_CMS = 10.0
# Junction min-speed / pause region: a spatial band along the stem axis measured in
# cm from the junction POINT (s_junction_cm == 0), NOT JUNCTION-zone membership. The
# peri-junction speed profile shows the deceleration begins up-stem and the minimum
# sits at/just before s=0, before the centroid is point-in-polygon inside JUNCTION -
# so a band anchored on s=0 (10 cm before / 5 cm after) captures the dip that
# zone-membership misses. Supersedes the ADR-0130 zone-membership region; see the
# superseding ADR.
DEFAULT_JUNCTION_BEFORE_CM = 10.0
DEFAULT_JUNCTION_AFTER_CM = 5.0
# Fallback metric scale if a bundle lacks config/rig_scale.json (the mean landmark
# cm/px at 1280x1024; see the resources repo rig_scale.json). Real bundles carry
# their own value, read by load_scale.
DEFAULT_CM_PER_PX = 0.233
# Distance-from-junction window (cm) for the across-trial mean speed profile: the
# outbound run spans roughly -70 cm (stem foot) to a few cm into the arm (0 =
# junction). See ADR-0130.
PROFILE_S_MIN_CM = -70.0
PROFILE_S_MAX_CM = 10.0
PROFILE_BIN_CM = 2.5


# ------------------------------------------------------------------ discovery
def find_bundle(session_id: str, root: Optional[Sequence[str]] = None) -> Optional[str]:
    """Absolute path to a session bundle dir, searching the recordings roots.

    ``root`` defaults to :func:`tmaze.analysis.phase3_format.phase3_recordings_roots`
    (primary + archives), so a history split across drives is found. Returns ``None`` if no
    bundle with a ``session_<id>.json`` manifest is present. In a notebook you can
    also read the ``bundle`` column of ``bundle_reader``'s sessions frame instead.
    """
    if root is None:
        roots = [str(p) for p in phase3_format.phase3_recordings_roots()]
    else:
        roots = [root] if isinstance(root, str) else list(root)
    for one in roots:
        cand = os.path.join(one, session_id)
        if os.path.isfile(os.path.join(cand, f"session_{session_id}.json")):
            return os.path.abspath(cand)
    return None


def _sidecar_path(bundle: str) -> Optional[str]:
    """The ``video/<id>.csv`` sidecar in a bundle (by manifest-style naming)."""
    session_id = os.path.basename(os.path.normpath(bundle))
    direct = os.path.join(bundle, "video", f"{session_id}.csv")
    if os.path.isfile(direct):
        return direct
    # Fallback: the single non-background CSV under video/.
    found = [
        p
        for p in sorted(glob.glob(os.path.join(bundle, "video", "*.csv")))
        if not p.endswith("_background.csv")
    ]
    return found[0] if found else None


# ------------------------------------------------------------------ time base
def iso_to_unix(value) -> float:
    """Host ISO wall-clock string -> POSIX seconds, reading the naive stamp as LOCAL.

    Matches the sidecar's ``host_unix_s`` (``time.time()``). Blank/unparsable ->
    ``nan``. Accepts a scalar; use :func:`iso_series_to_unix` for a column.
    """
    if value is None:
        return math.nan
    s = str(value).strip()
    if not s:
        return math.nan
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return math.nan


def iso_series_to_unix(series: pd.Series) -> pd.Series:
    """Vectorised :func:`iso_to_unix` over a Series (element-wise, NA-safe)."""
    return series.map(iso_to_unix).astype(float)


# ------------------------------------------------------------------ sidecar
def read_sidecar_header(path: str) -> Dict[str, str]:
    """Fold the leading ``# key: value`` comment lines of a sidecar into a dict."""
    hdr: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            if not raw.startswith("#"):
                break
            body = raw.lstrip("#").strip()
            if ":" in body:
                key, val = body.split(":", 1)
                hdr[key.strip()] = val.strip()
    return hdr


@dataclass(frozen=True)
class Sidecar:
    """A loaded per-frame track: the frame plus the geometry it was sized against."""

    frames: pd.DataFrame  # host_unix_s + track_smooth_cx/cy + cx_n/cy_n + raw cols
    width: float
    height: float
    header: Dict[str, str]


def load_sidecar(
    bundle_or_csv: str,
    *,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
    require_tracking: bool = True,
) -> Sidecar:
    """Load a session's video sidecar into a tidy per-frame frame.

    ``bundle_or_csv`` is a bundle directory or a direct sidecar CSV path. Frames are
    filtered to valid detections (``track_state == TRACKING`` and
    ``track_confidence >= min_confidence``) and given normalized ``cx_n``/``cy_n``
    (pixel centroid / frame size) so they overlay zones + the background image. The
    Kalman-smoothed centroid (``track_smooth_cx/cy``) is preferred - the plot-ready
    columns are built from it. Rows are time-ordered by ``host_unix_s``.
    """
    if os.path.isdir(bundle_or_csv):
        path = _sidecar_path(bundle_or_csv)
        if path is None:
            raise FileNotFoundError(f"no video sidecar CSV under {bundle_or_csv}")
    else:
        path = bundle_or_csv
    header = read_sidecar_header(path)
    width = float(header.get("width") or 0) or math.nan
    height = float(header.get("height") or 0) or math.nan

    df = pd.read_csv(path, comment="#")
    for col in ("host_unix_s", "track_smooth_cx", "track_smooth_cy",
                "track_confidence"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "track_state" not in df.columns:
        df["track_state"] = ""

    keep = df["track_smooth_cx"].notna() & df["track_smooth_cy"].notna()
    if require_tracking:
        keep &= df["track_state"].astype(str).eq(_TRACKING)
    if "track_confidence" in df.columns:
        keep &= df["track_confidence"].fillna(0.0) >= float(min_confidence)
    df = df[keep].sort_values("host_unix_s", kind="stable").reset_index(drop=True)

    df["cx_n"] = df["track_smooth_cx"] / width
    df["cy_n"] = df["track_smooth_cy"] / height
    return Sidecar(frames=df, width=width, height=height, header=header)


def load_zone_map(bundle: str) -> phase3_format.ZoneMap:
    """The per-session :class:`ZoneMap` from ``config/zones.json`` in the bundle.

    Each bundle snapshots the zones in force when it recorded (geometry is re-tuned
    across days), so always read the bundle's own copy, not the live ``host/``
    file. Reuses the live loader so the polygons/classification match the rig.
    """
    return phase3_format.load_zone_map(os.path.join(bundle, "config", "zones.json"))


# ------------------------------------------------------------------ windows
def outbound_windows(
    trials: pd.DataFrame, *,
    start_event: str = "trial_start_iso", end_event: str = "choice_time_iso",
    start_pad_s: float = 0.0, end_pad_s: float = 0.6, completed_only: bool = True,
) -> pd.DataFrame:
    """One row per trial with a per-trial time window in POSIX seconds.

    The window is ``[t_start_event + start_pad_s, t_end_event + end_pad_s]``, where
    ``start_event`` / ``end_event`` are any per-trial ISO stamps from ``_ISO_EVENTS``
    (``trial_start_iso``, ``choice_cue_on_iso``, ``choice_cue_off_iso``,
    ``choice_time_iso``, ``reward_cmd_iso``, ``return_cue_on_iso``,
    ``return_cue_off_iso``, ``trial_end_iso``, ``lick_time_iso``). The DEFAULT pair
    (``trial_start_iso`` -> ``choice_time_iso`` + 0.6 s) reproduces the ADR-0128
    cue->choice outbound decision window that the VTE panels use; pass e.g.
    ``end_event="trial_end_iso"`` for the whole trial (incl. the reward/return/ITI
    leg), or nudge either edge with the pads.

    From a per-session (or pooled) trials frame - the ``bundle_reader`` shape, ideally
    after ``choice_signals.add_signals`` so ``correct``/``completed`` exist - build:
    ``t_start`` (window start), ``t_end`` (window end), ``t_choice`` (the choice stamp
    when present, for reference), plus ``cue`` (A/B), ``choice_cue`` (HIGH/LOW),
    ``chosen_arm``, ``correct``. Rows missing either chosen bound are dropped;
    ``completed_only`` keeps only scored trials (the default). Raises ``ValueError``
    if ``start_event``/``end_event`` name a column the frame doesn't have.
    """
    if trials.empty:
        return trials.copy()
    df = trials.copy()
    if completed_only and "completed" in df.columns:
        df = df[df["completed"].astype("boolean").fillna(False)]
    df = df.copy()
    for label, col in (("start_event", start_event), ("end_event", end_event)):
        if col not in df.columns:
            avail = [c for c in _ISO_EVENTS if c in df.columns]
            raise ValueError(
                f"{label}={col!r} is not a column of the trials frame; "
                f"available ISO events: {avail}")
    t0 = iso_series_to_unix(df[start_event])
    t1 = iso_series_to_unix(df[end_event])
    keep = t0.notna() & t1.notna()
    df = df[keep].copy()
    df["t_start"] = t0[keep] + float(start_pad_s)
    df["t_end"] = t1[keep] + float(end_pad_s)
    if "choice_time_iso" in df.columns:
        df["t_choice"] = iso_series_to_unix(df["choice_time_iso"])
    df["cue"] = df["choice_cue"].map(_TONE_TO_CUE)
    if "correct" not in df.columns:
        df["correct"] = df["outcome"].eq(CORRECT)
    cols = [c for c in ("session_id", "run_idx", "trial", "cue", "choice_cue",
                        "chosen_arm", "outcome", "correct", "t_start", "t_choice",
                        "t_end") if c in df.columns]
    return df[cols].reset_index(drop=True)


def slice_xy(sidecar: Sidecar, t0: float, t1: float) -> pd.DataFrame:
    """The sidecar frames whose ``host_unix_s`` lies in ``[t0, t1]`` (time-ordered).

    Returns a view-copy with at least ``host_unix_s``/``cx_n``/``cy_n``; empty when
    the window has no valid samples (e.g. tracking was lost through the run).
    """
    f = sidecar.frames
    if f.empty or not (math.isfinite(t0) and math.isfinite(t1)) or t1 < t0:
        return f.iloc[0:0].copy()
    m = (f["host_unix_s"] >= t0) & (f["host_unix_s"] <= t1)
    return f[m].copy()


# ------------------------------------------------------------------ dwell / VTE
def dwell_seconds(
    seg: pd.DataFrame, zone_map: phase3_format.ZoneMap,
    names: Sequence[str] = VTE_ZONES,
) -> float:
    """Seconds the centroid spends inside the union of ``names`` zones over ``seg``.

    ``seg`` is a :func:`slice_xy` result (normalized ``cx_n``/``cy_n`` +
    ``host_unix_s``). Each inter-sample gap is credited to the zone of its EARLIER
    sample (a left Riemann sum over the frame timeline); the final sample carries no
    gap. Returns ``0.0`` for fewer than two samples. Zone membership uses the live
    :meth:`ZoneMap.classify_point`, so it matches the rig's on-line zoning.
    """
    if seg is None or len(seg) < 2:
        return 0.0
    want = set(names)
    t = seg["host_unix_s"].to_numpy(dtype=float)
    xs = seg["cx_n"].to_numpy(dtype=float)
    ys = seg["cy_n"].to_numpy(dtype=float)
    dt = np.diff(t)
    total = 0.0
    for i in range(len(dt)):
        if dt[i] <= 0:
            continue
        zone = zone_map.classify_point(xs[i], ys[i])
        if zone in want:
            total += float(dt[i])
    return total


def dwell_per_trial(
    sidecar: Sidecar, zone_map: phase3_format.ZoneMap, windows: pd.DataFrame,
    *, names: Sequence[str] = VTE_ZONES,
) -> pd.DataFrame:
    """Add a ``dwell_s`` column to ``windows`` (VTE dwell per outbound run).

    ``windows`` is an :func:`outbound_windows` frame for the SAME session as
    ``sidecar``. Trials with no valid samples in-window get ``dwell_s = 0.0`` and
    ``n_samples = 0`` (surface them; they usually mean tracking dropped out).
    """
    out = windows.copy()
    dwell: List[float] = []
    nsamp: List[int] = []
    for _, r in out.iterrows():
        seg = slice_xy(sidecar, r["t_start"], r["t_end"])
        nsamp.append(len(seg))
        dwell.append(dwell_seconds(seg, zone_map, names))
    out["dwell_s"] = dwell
    out["n_samples"] = nsamp
    return out


# ------------------------------------------------------------- peri-junction velocity
def load_scale(bundle: str) -> float:
    """``cm_per_px`` from the bundle's ``config/rig_scale.json`` (px -> real cm).

    Falls back to :data:`DEFAULT_CM_PER_PX` if the file is absent/unreadable or the
    value is non-positive. See ADR-0130 (the rig scale itself is ADR-0015-adjacent
    landmark calibration, `homography` currently null so this is a linear scale).
    """
    path = os.path.join(bundle, "config", "rig_scale.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            val = float(json.load(fh).get("cm_per_px"))
        if val > 0:
            return val
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return DEFAULT_CM_PER_PX


def _zone_centroid(zone_map: phase3_format.ZoneMap, name: str):
    for z in zone_map.zones:
        if z.name == name and len(z.polygon) >= 3:
            a = np.asarray(z.polygon, dtype=float)
            return (float(a[:, 0].mean()), float(a[:, 1].mean()))
    return None


def stem_axis(zone_map: phase3_format.ZoneMap):
    """The stem axis ``(base_xy, tip_xy)`` in normalized coords, junction at the tip.

    Prefers an explicit ``stem_axis`` if the zones file carries one (base = HUB/well
    end, tip = junction end); otherwise derives it as the ``HUB``-centroid ->
    ``JUNCTION``-centroid line. The stem is near-vertical and the arms diverge in x,
    so this line is the stem direction and the junction sits at its tip. Returns
    ``None`` if HUB or JUNCTION is missing. See ADR-0130.
    """
    if zone_map.stem_base is not None and zone_map.stem_tip is not None:
        return zone_map.stem_base, zone_map.stem_tip
    base = _zone_centroid(zone_map, phase3_format.HUB)
    tip = _zone_centroid(zone_map, phase3_format.JUNCTION)
    if base is None or tip is None:
        return None
    return base, tip


def add_kinematics(
    frames: pd.DataFrame, cm_per_px: float, axis, width: float, height: float,
    *, smooth_n: int = DEFAULT_SMOOTH_N,
) -> pd.DataFrame:
    """Return ``frames`` with ``speed_cms`` and ``s_junction_cm`` columns added.

    ``speed_cms`` is the central-difference speed of the smoothed centroid
    (``np.gradient`` over ``host_unix_s``, so uneven frame spacing is handled) times
    ``cm_per_px``, then a centred rolling mean over ``smooth_n`` frames to tame
    jitter. ``s_junction_cm`` projects each centroid onto the stem ``axis`` IN PIXEL
    space (exact, unlike normalized space where x/y scale differently) and returns
    the signed distance from the junction in cm: 0 at the junction, negative down the
    stem toward the hub, positive into the arm. ``axis`` is a
    (base_norm, tip_norm) pair (see :func:`stem_axis`); a ``None`` axis leaves
    ``s_junction_cm`` as NaN. Rows are assumed time-ordered (the loader sorts them).
    """
    df = frames.copy()
    n = len(df)
    t = df["host_unix_s"].to_numpy(dtype=float)
    cx = df["track_smooth_cx"].to_numpy(dtype=float)
    cy = df["track_smooth_cy"].to_numpy(dtype=float)

    if n >= 2:
        with np.errstate(divide="ignore", invalid="ignore"):
            vx = np.gradient(cx, t)
            vy = np.gradient(cy, t)
        speed = np.hypot(vx, vy) * float(cm_per_px)
        speed = np.where(np.isfinite(speed), speed, np.nan)
    else:
        speed = np.full(n, np.nan)
    df["speed_cms"] = (
        pd.Series(speed, index=df.index)
        .rolling(window=max(1, int(smooth_n)), center=True, min_periods=1)
        .mean()
    )

    if axis is not None:
        (bx, by), (tx, ty) = axis
        bpx = np.array([bx * width, by * height], dtype=float)
        tpx = np.array([tx * width, ty * height], dtype=float)
        u = tpx - bpx
        length_px = float(np.hypot(u[0], u[1]))
        if length_px > 0:
            along_px = ((cx - bpx[0]) * u[0] + (cy - bpx[1]) * u[1]) / length_px
            df["s_junction_cm"] = (along_px - length_px) * float(cm_per_px)
        else:
            df["s_junction_cm"] = np.nan
    else:
        df["s_junction_cm"] = np.nan
    return df


def with_kinematics(
    sidecar: Sidecar, cm_per_px: float, axis, *, smooth_n: int = DEFAULT_SMOOTH_N,
) -> Sidecar:
    """A copy of ``sidecar`` whose frames carry ``speed_cms``/``s_junction_cm``.

    Convenience wrapper around :func:`add_kinematics` so :func:`slice_xy` (and thus
    the per-trial helpers) yield segments already carrying the kinematics columns.
    """
    aug = add_kinematics(sidecar.frames, cm_per_px, axis, sidecar.width,
                         sidecar.height, smooth_n=smooth_n)
    return Sidecar(frames=aug, width=sidecar.width, height=sidecar.height,
                   header=sidecar.header)


def peri_junction_profile(seg: pd.DataFrame) -> pd.DataFrame:
    """One trial's ``(s_junction_cm, speed_cms)`` samples, NaNs dropped, s-ordered.

    ``seg`` is a :func:`slice_xy` result from a :func:`with_kinematics` sidecar. The
    caller pools these across a block and bins them with :func:`mean_profile`.
    """
    if seg is None or seg.empty or "s_junction_cm" not in seg.columns:
        return pd.DataFrame(columns=["s_junction_cm", "speed_cms"])
    out = seg[["s_junction_cm", "speed_cms"]].dropna()
    return out.sort_values("s_junction_cm", kind="stable").reset_index(drop=True)


def junction_kinematics(
    seg: pd.DataFrame, zone_map: phase3_format.ZoneMap,
    *, pause_speed_cms: float = DEFAULT_PAUSE_SPEED_CMS,
    band_before_cm: float = DEFAULT_JUNCTION_BEFORE_CM,
    band_after_cm: float = DEFAULT_JUNCTION_AFTER_CM,
) -> Dict[str, float]:
    """Deliberation scalars for one outbound run's speed around the junction.

    From a :func:`with_kinematics` segment (has ``speed_cms``/``s_junction_cm``/
    ``cx_n``/``cy_n``/``host_unix_s``). The junction min-speed / pause REGION is a
    spatial band along the stem axis - frames whose signed distance ``s_junction_cm``
    lies in ``[-band_before_cm, +band_after_cm]`` cm of the junction point (``s=0``),
    NOT JUNCTION-zone membership: the speed dip begins up-stem and bottoms out at/just
    before ``s=0``, before the centroid is inside the JUNCTION polygon (superseding the
    ADR-0130 zone region). ``peak_stem_speed_cms`` still uses the live ``CENTRAL_STEM``
    zone. Returns:

    - ``min_speed_cms`` - slowest smoothed speed inside the band (NaN if no tracked
      frame lands in the band, e.g. missing ``s_junction_cm`` / no near-junction
      tracking).
    - ``peak_stem_speed_cms`` - fastest speed while in ``CENTRAL_STEM`` (the run
      speed the junction dip is relative to; NaN if no stem frame).
    - ``slowing_index`` - ``1 - min_band / peak_stem`` clipped to ``[0, 1]``: 0 =
      barrels through, ->1 = near-stops at the junction (NaN if either speed is NaN
      or the peak is non-positive).
    - ``pause_time_s`` - seconds spent inside the band below ``pause_speed_cms`` (a
      left-Riemann sum over frame gaps, like :func:`dwell_seconds`).
    - ``n_samples`` - segment length.
    """
    nan = math.nan
    out: Dict[str, float] = {
        "min_speed_cms": nan, "peak_stem_speed_cms": nan,
        "slowing_index": nan, "pause_time_s": 0.0,
        "n_samples": float(0 if seg is None else len(seg)),
    }
    if seg is None or len(seg) < 2:
        return out
    t = seg["host_unix_s"].to_numpy(dtype=float)
    xs = seg["cx_n"].to_numpy(dtype=float)
    ys = seg["cy_n"].to_numpy(dtype=float)
    sp = seg["speed_cms"].to_numpy(dtype=float)
    if "s_junction_cm" in seg.columns:
        s = seg["s_junction_cm"].to_numpy(dtype=float)
        in_band = np.isfinite(s) & (s >= -abs(band_before_cm)) & (s <= abs(band_after_cm))
    else:
        in_band = np.zeros(len(seg), dtype=bool)
    in_stem = np.array(
        [zone_map.classify_point(xs[i], ys[i]) == phase3_format.CENTRAL_STEM
         for i in range(len(seg))]
    )

    band_sp = sp[in_band]
    stem_sp = sp[in_stem]
    if band_sp.size and np.isfinite(band_sp).any():
        out["min_speed_cms"] = float(np.nanmin(band_sp))
    if stem_sp.size and np.isfinite(stem_sp).any():
        out["peak_stem_speed_cms"] = float(np.nanmax(stem_sp))
    if (math.isfinite(out["min_speed_cms"]) and math.isfinite(out["peak_stem_speed_cms"])
            and out["peak_stem_speed_cms"] > 0):
        out["slowing_index"] = float(
            min(1.0, max(0.0, 1.0 - out["min_speed_cms"] / out["peak_stem_speed_cms"]))
        )

    dt = np.diff(t)
    pause = 0.0
    for i in range(len(dt)):
        if dt[i] > 0 and in_band[i] and math.isfinite(sp[i]) and sp[i] < pause_speed_cms:
            pause += float(dt[i])
    out["pause_time_s"] = pause
    return out


def kinematics_per_trial(
    sidecar: Sidecar, zone_map: phase3_format.ZoneMap, windows: pd.DataFrame,
    *, pause_speed_cms: float = DEFAULT_PAUSE_SPEED_CMS,
    band_before_cm: float = DEFAULT_JUNCTION_BEFORE_CM,
    band_after_cm: float = DEFAULT_JUNCTION_AFTER_CM,
) -> pd.DataFrame:
    """Add the :func:`junction_kinematics` scalars to ``windows`` (one row/trial).

    ``sidecar`` must already carry kinematics (:func:`with_kinematics`) and match the
    session of ``windows``. ``band_before_cm``/``band_after_cm`` set the junction
    min-speed / pause band (see :func:`junction_kinematics`). Mirrors
    :func:`dwell_per_trial`.
    """
    out = windows.copy()
    rows = []
    for _, r in out.iterrows():
        seg = slice_xy(sidecar, r["t_start"], r["t_end"])
        rows.append(junction_kinematics(
            seg, zone_map, pause_speed_cms=pause_speed_cms,
            band_before_cm=band_before_cm, band_after_cm=band_after_cm,
        ))
    metrics = pd.DataFrame(rows, index=out.index)
    for col in metrics.columns:
        out[col] = metrics[col]
    return out


def mean_profile(profiles: Sequence[pd.DataFrame], bin_edges=None) -> pd.DataFrame:
    """Bin pooled ``(s_junction_cm, speed_cms)`` samples into a mean +/- SEM curve.

    ``profiles`` is a list of :func:`peri_junction_profile` frames (one per trial).
    Returns one row per non-empty bin: ``s_cm`` (bin centre), ``mean``, ``sem``, ``n``.
    ``bin_edges`` defaults to :data:`PROFILE_S_MIN_CM`..:data:`PROFILE_S_MAX_CM` in
    :data:`PROFILE_BIN_CM` steps.
    """
    if bin_edges is None:
        bin_edges = np.arange(
            PROFILE_S_MIN_CM, PROFILE_S_MAX_CM + PROFILE_BIN_CM, PROFILE_BIN_CM
        )
    frames = [p for p in profiles if p is not None and not p.empty]
    if not frames:
        return pd.DataFrame(columns=["s_cm", "mean", "sem", "n"])
    cat = pd.concat(frames, ignore_index=True)
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    idx = pd.cut(cat["s_junction_cm"], bins=bin_edges, labels=False,
                 include_lowest=True)
    cat = cat.assign(_bin=idx).dropna(subset=["_bin"])
    cat["_bin"] = cat["_bin"].astype(int)
    grp = cat.groupby("_bin")["speed_cms"]
    stats = grp.agg(["mean", "std", "count"]).reset_index()
    stats["s_cm"] = centers[stats["_bin"].to_numpy()]
    stats["n"] = stats["count"].astype(int)
    stats["sem"] = stats["std"] / np.sqrt(stats["n"].clip(lower=1))
    return stats[["s_cm", "mean", "sem", "n"]].sort_values("s_cm").reset_index(drop=True)


def speed_occupancy_grid(
    cx_n: Sequence[float], cy_n: Sequence[float], speed_cms: Sequence[float],
    *, bins: int = 60, min_count: int = 3,
):
    """2D occupancy + mean-speed grids over the unit square, for the per-rat heatmaps.

    Pools per-frame normalized positions (``cx_n``/``cy_n``, both 0..1) and per-frame
    ``speed_cms`` into ``bins`` x ``bins`` grids indexed ``[ix, iy]`` over
    ``[0, 1] x [0, 1]`` (matching :func:`load_sidecar`'s coordinates, so zone polygons
    overlay directly). Returns ``(occupancy, mean_speed, x_edges, y_edges)``:

    - ``occupancy`` - frame COUNT per bin (where the animal spent time; a preference
      reference, dominated by wells/hub if the caller doesn't restrict to the
      outbound leg).
    - ``mean_speed`` - MEAN ``speed_cms`` per bin, NaN where fewer than ``min_count``
      finite-speed frames landed (so a couple of stray frames can't paint a fake slow
      spot). This is the "where does it slow down" map: cool bins = deceleration.

    Occupancy uses every finite position; mean speed additionally needs a finite speed
    (``add_kinematics`` leaves the endpoints defined, but a caller may pass NaNs).
    """
    x = np.asarray(cx_n, dtype=float)
    y = np.asarray(cy_n, dtype=float)
    s = np.asarray(speed_cms, dtype=float)
    rng = [[0.0, 1.0], [0.0, 1.0]]
    pos = np.isfinite(x) & np.isfinite(y)
    occupancy, xe, ye = np.histogram2d(x[pos], y[pos], bins=bins, range=rng)
    sp = pos & np.isfinite(s)
    ssum, _, _ = np.histogram2d(x[sp], y[sp], bins=bins, range=rng, weights=s[sp])
    scount, _, _ = np.histogram2d(x[sp], y[sp], bins=bins, range=rng)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_speed = np.where(scount >= max(1, int(min_count)), ssum / scount, np.nan)
    return occupancy, mean_speed, xe, ye


# ------------------------------------------------------------------ block picks
@dataclass(frozen=True)
class Block:
    """A run of ``n`` consecutive completed trials within one session/run.

    ``trials`` is that slice of the signals frame (cue/correct/iso columns intact),
    ``accuracy`` its fraction correct, ``n_a``/``n_b`` the cue-A/cue-B counts.
    ``label`` is a short caption for a figure title.
    """

    session_id: str
    run_idx: int
    start_trial: int
    end_trial: int
    accuracy: float
    n_a: int
    n_b: int
    trials: pd.DataFrame

    @property
    def label(self) -> str:
        return (f"{self.session_id}  trials {self.start_trial}-{self.end_trial}  "
                f"acc {self.accuracy * 100:.0f}%  (A={self.n_a}, B={self.n_b})")


def make_blocks(
    signals: pd.DataFrame, *, block: int = 20, step: Optional[int] = None,
    exclude_intervention: bool = True,
) -> List[Block]:
    """Cut each session/run's completed trials into fixed windows of ``block`` trials.

    ``step`` is the stride between successive window START trials (defaults to
    ``block`` = the classic NON-OVERLAPPING tiling: trials ``[1-20], [21-40], ...``).
    Pass ``step=1`` for a 1-trial MOVING window (``[1-20], [2-21], [3-22], ...``),
    which overlaps by ``block - step`` trials; any ``1 <= step <= block`` works.
    Windows never cross a session or restart boundary (so each maps to ONE background
    image + zone map), and a trailing partial window (< ``block`` trials) is dropped
    so accuracies compare like-for-like.

    ``exclude_intervention`` (default True) drops any window that overlaps a de-bias /
    de-alternation INTERVENTION block - i.e. any window containing a trial whose
    ``choice_signals`` ``in_block`` flag is set - so a block reflects the animal's own
    free-choice behaviour, not a period when the task was pushing a side. Windows
    fully before/after an intervention are kept (a straddling window is simply
    skipped, leaving a gap). No-op if the frame lacks an ``in_block`` column. Requires
    the ``choice_signals`` columns ``completed``/``correct``/``cue`` (derived here from
    ``choice_cue``).
    """
    if signals.empty:
        return []
    stride = block if step is None else int(step)
    if stride < 1:
        stride = 1
    df = signals.copy()
    comp = df["completed"].astype("boolean").fillna(False) if "completed" in df \
        else df["outcome"].isin((CORRECT, "INCORRECT"))
    df = df[comp].copy()
    if "cue" not in df.columns:
        df["cue"] = df["choice_cue"].map(_TONE_TO_CUE)
    if "correct" not in df.columns:
        df["correct"] = df["outcome"].eq(CORRECT)
    skip_intervention = exclude_intervention and "in_block" in df.columns

    blocks: List[Block] = []
    for (sid, run), grp in df.groupby(["session_id", "run_idx"], sort=False):
        grp = grp.sort_values("trial", kind="stable")
        n = len(grp)
        ib = (grp["in_block"].astype("boolean").fillna(False).to_numpy()
              if skip_intervention else None)
        for start in range(0, n - block + 1, stride):
            if ib is not None and ib[start:start + block].any():
                continue  # window straddles a de-bias / de-alternation block
            sl = grp.iloc[start:start + block]
            corr = sl["correct"].astype("boolean").fillna(False)
            blocks.append(Block(
                session_id=str(sid),
                run_idx=int(run),
                start_trial=int(sl["trial"].iloc[0]),
                end_trial=int(sl["trial"].iloc[-1]),
                accuracy=float(corr.mean()),
                n_a=int((sl["cue"] == CUE_A).sum()),
                n_b=int((sl["cue"] == CUE_B).sum()),
                trials=sl.reset_index(drop=True),
            ))
    return blocks


def _blocks_overlap(a: Block, b: Block) -> bool:
    """True if two blocks share any trial (same session/run + intersecting range).

    Two windows can only share trials when they're the same session AND run; then a
    contiguous trial-number range intersection is exact (windows are contiguous
    slices of the trial-sorted frame).
    """
    if a is b:
        return True
    if a.session_id != b.session_id or a.run_idx != b.run_idx:
        return False
    return not (a.end_trial < b.start_trial or b.end_trial < a.start_trial)


def select_blocks(
    signals: pd.DataFrame, *, block: int = 20, step: Optional[int] = None,
    exclude_intervention: bool = True,
) -> Tuple[Optional[Block], Optional[Block]]:
    """Pick the most-separated (low-accuracy, high-accuracy) DISJOINT block pair.

    Returns the pair of :func:`make_blocks` windows that maximizes the accuracy gap
    ``high.accuracy - low.accuracy`` subject to sharing NO trials (:func:`_blocks_overlap`
    is False), ties broken by earliest low then earliest high. With the default
    non-overlapping tiling (``step=None``) every pair is already disjoint, so this
    reduces to the global lowest/highest-accuracy blocks - identical to the old
    behaviour. With an overlapping ``step`` (e.g. ``step=1``) the disjoint constraint
    keeps the two overlay panels - and the Panel 4/5 VTE stats - from sharing trials.
    ``exclude_intervention`` is forwarded to :func:`make_blocks` (default True: skip
    windows overlapping a de-bias / de-alternation block).

    ``(None, None)`` if no window exists. If NO disjoint pair exists (e.g. a single
    run shorter than ``2 * block`` under a sliding ``step``), it falls back to the
    global lowest/highest window (which may then overlap). The caller can always
    override by constructing a :class:`Block` from an explicit slice. NOTE: on a
    stretch of late-training sessions the "low" block may sit above chance - read its
    ``accuracy``/``label`` and widen the date span for a truer chance block.
    """
    blocks = make_blocks(signals, block=block, step=step,
                         exclude_intervention=exclude_intervention)
    if not blocks:
        return None, None
    best: Optional[Tuple[float, Block, Block]] = None
    for lo in blocks:
        for hi in blocks:
            if hi.accuracy < lo.accuracy or _blocks_overlap(lo, hi):
                continue
            gap = hi.accuracy - lo.accuracy
            if best is None or gap > best[0]:
                best = (gap, lo, hi)
    if best is not None:
        return best[1], best[2]
    # No disjoint pair (too few trials in one run under a sliding step): fall back to
    # the global lowest/highest even though they overlap.
    return (min(blocks, key=lambda b: b.accuracy),
            max(blocks, key=lambda b: b.accuracy))


# ------------------------------------------------------------------ stats
def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float]:
    """Two-sided Mann-Whitney U with the normal approximation (+ tie correction).

    A tiny stdlib/numpy MWU so the notebook can annotate the VTE contrast without a
    SciPy dependency. Returns ``(U, p)``; ``p = nan`` when either group is empty or
    both are degenerate. The normal approximation is fine for the ~20-per-group
    block sizes here (not a small-n exact test).
    """
    x = np.asarray([v for v in a if math.isfinite(v)], dtype=float)
    y = np.asarray([v for v in b if math.isfinite(v)], dtype=float)
    n1, n2 = len(x), len(y)
    if n1 == 0 or n2 == 0:
        return math.nan, math.nan
    pooled = np.concatenate([x, y])
    order = pooled.argsort(kind="stable")
    ranks = np.empty(len(pooled), dtype=float)
    ranks[order] = np.arange(1, len(pooled) + 1, dtype=float)
    # Average ranks within ties.
    sp = np.sort(pooled)
    i = 0
    tie_term = 0.0
    while i < len(sp):
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        if j > i:
            avg = (i + 1 + j + 1) / 2.0
            mask = pooled == sp[i]
            ranks[mask] = avg
            t = j - i + 1
            tie_term += t ** 3 - t
        i = j + 1
    r1 = ranks[:n1].sum()
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    n = n1 + n2
    mu = n1 * n2 / 2.0
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie_term / (n * (n - 1)))
    if sigma2 <= 0:
        return u, math.nan
    z = (u - mu) / math.sqrt(sigma2)
    p = math.erfc(abs(z) / math.sqrt(2.0))  # two-sided normal tail
    return u, p


def wilson_interval(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score CI ``(lo, hi)`` for a binomial proportion ``k/n``.

    The score interval behaves well for the small per-quartile counts and near-0/1
    accuracies of the VTE-vs-accuracy panel (unlike the normal/Wald interval, which can
    run past ``[0, 1]``). ``(nan, nan)`` for ``n == 0``. ``z`` is the standard-normal
    quantile (1.96 = 95%).
    """
    if n <= 0:
        return math.nan, math.nan
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = (z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> Tuple[float, float]:
    """Two-sided two-proportion z-test of ``k1/n1`` vs ``k2/n2``: ``(z, p)``.

    Pooled-variance normal-approximation test for equal proportions (here: accuracy of
    the lowest vs highest VTE quartile). SciPy-free, same two-sided normal tail as
    :func:`mann_whitney_u`. ``(nan, nan)`` if either group is empty or the pooled
    proportion is degenerate (0 or 1, so the two groups are identical all-correct /
    all-incorrect and there is nothing to test).
    """
    if n1 <= 0 or n2 <= 0:
        return math.nan, math.nan
    p1, p2 = k1 / n1, k2 / n2
    pool = (k1 + k2) / (n1 + n2)
    if pool <= 0.0 or pool >= 1.0:
        return math.nan, math.nan
    se = math.sqrt(pool * (1.0 - pool) * (1.0 / n1 + 1.0 / n2))
    if se <= 0.0:
        return math.nan, math.nan
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2.0))


def zscore_by_group(df: pd.DataFrame, value_col: str, group_col: str) -> pd.Series:
    """Within-group z-score of ``value_col`` (subtract group mean / group std).

    Used to pool a per-trial VTE measure ACROSS sessions without letting between-day
    differences (e.g. a slow-learning early session) dominate the quartile split: each
    session is centred/scaled to its own distribution first. A group with zero variance
    (or a single trial) maps to 0. NaNs pass through as NaN.
    """
    g = df.groupby(group_col)[value_col]
    mean = g.transform("mean")
    std = g.transform("std")
    out = (df[value_col] - mean) / std
    return out.where(std.notna() & (std != 0), 0.0).where(df[value_col].notna())


def accuracy_by_quartile(
    df: pd.DataFrame, value_col: str, *, correct_col: str = "correct", q: int = 4
) -> pd.DataFrame:
    """Accuracy per ``q``-tile of ``value_col`` with Wilson 95% CIs.

    Ranks the trials by ``value_col`` (dropping NaNs in that column only - the
    per-measure drop policy) and splits them into ``q`` EQUAL-SIZED groups via a
    rank-based cut (``pd.qcut`` on the first-tie-broken rank), so a measure with heavy
    ties - e.g. the many ``pause_time_s == 0`` trials - still divides into equal groups
    instead of collapsing. Returns one row per quartile (1 = lowest values):
    ``quartile, n, n_correct, accuracy, ci_lo, ci_hi``. Empty frame if fewer than ``q``
    non-NaN trials. The caller compares quartile 1 vs ``q`` with
    :func:`two_proportion_z`.
    """
    cols = ["quartile", "n", "n_correct", "accuracy", "ci_lo", "ci_hi"]
    sub = df[[value_col, correct_col]].dropna(subset=[value_col])
    if len(sub) < q:
        return pd.DataFrame(columns=cols)
    ranks = sub[value_col].rank(method="first")
    labels = list(range(1, q + 1))
    sub = sub.assign(_q=pd.qcut(ranks, q, labels=labels).astype(int))
    corr = sub[correct_col].astype("boolean").fillna(False)
    rows = []
    for qi in labels:
        mask = sub["_q"] == qi
        n = int(mask.sum())
        k = int(corr[mask].sum())
        lo, hi = wilson_interval(k, n)
        rows.append({"quartile": qi, "n": n, "n_correct": k,
                     "accuracy": (k / n if n else math.nan), "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(rows, columns=cols)
