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

- **Outbound decision window** = ``[trial_start_iso, choice_time_iso]`` (+ a small
  ``end_pad_s`` so the trace visibly enters the chosen arm). That is the cue->choice
  run - the stem climb, the pause at the split, the commit to an arm - and it
  EXCLUDES the reward/return/ITI leg the proposal doesn't want.
- **VTE dwell** = seconds the centroid spends in ``CENTRAL_STEM`` u ``JUNCTION``
  during that window: the stem captures a pause at cue onset, the junction the
  head-swinging deliberation at the choice point. Zone membership reuses the LIVE
  classifier (:class:`tmaze.core.maze_state.ZoneMap`) so offline == on-rig.

Time base. Trial ``*_iso`` stamps are host wall-clock written by ``datetime.now()``
(tz-naive local); the sidecar's ``host_unix_s`` is ``time.time()`` (POSIX). They
line up when the ISO string is read as LOCAL time, i.e. ``datetime.fromisoformat(
s).timestamp()`` - validated against a session's ``start_wall_clock`` header (match
to one frame). :func:`iso_to_unix` is that one conversion; use it everywhere.

Coordinates. Sidecar centroids are FULL-frame pixels of the undistorted/oriented
image; zones are normalized 0..1 (ADR-0015). :func:`load_sidecar` divides by the
header ``width``/``height`` so ``cx_n``/``cy_n`` overlay the zone polygons and the
``_background.png`` directly.

Read-only, pure pandas/numpy + stdlib (may import ``tmaze.core`` - allowed for the
analysis layer). NO Tk/OpenCV/matplotlib: plotting stays in the notebook. Headless
tests: ``python -m unittest test_analysis_trajectories``.
"""
from __future__ import annotations

import glob
import math
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from tmaze.core import maze_state
from tmaze.core.phase3.phase3_task import CORRECT
from tmaze.core.phase3.trial_sequence import HIGH, LOW

# Cue A / cue B are the proposal's plain labels for the two tones. Cue A = the HIGH
# tone, cue B = the LOW tone (the notebook maps A->blue, B->green). Pinned here so
# the label<->tone mapping has one home. See ADR-0128.
CUE_A = "A"
CUE_B = "B"
_TONE_TO_CUE = {HIGH: CUE_A, LOW: CUE_B}

# Zones whose union defines the VTE dwell region (stem pause + junction
# deliberation). Names come from the live vocabulary so a rename can't silently
# drift. See ADR-0128.
VTE_ZONES: Tuple[str, ...] = (maze_state.CENTRAL_STEM, maze_state.JUNCTION)

# Default validity gate on the tracker, matching the live scoring path: keep only
# frames the tracker actually locked onto (ADR-0014 track states).
_DEFAULT_MIN_CONFIDENCE = 0.5
_TRACKING = "TRACKING"


# ------------------------------------------------------------------ discovery
def find_bundle(session_id: str, root: Optional[Sequence[str]] = None) -> Optional[str]:
    """Absolute path to a session bundle dir, searching the recordings roots.

    ``root`` defaults to :func:`tmaze.paths.phase3_recordings_roots` (primary +
    archives), so a history split across drives is found. Returns ``None`` if no
    bundle with a ``session_<id>.json`` manifest is present. In a notebook you can
    also read the ``bundle`` column of ``bundle_reader``'s sessions frame instead.
    """
    if root is None:
        from tmaze.paths import phase3_recordings_roots

        roots = [str(p) for p in phase3_recordings_roots()]
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


def load_zone_map(bundle: str) -> maze_state.ZoneMap:
    """The per-session :class:`ZoneMap` from ``config/zones.json`` in the bundle.

    Each bundle snapshots the zones in force when it recorded (geometry is re-tuned
    across days), so always read the bundle's own copy, not the live ``host/``
    file. Reuses the live loader so the polygons/classification match the rig.
    """
    return maze_state.load_zone_map(os.path.join(bundle, "config", "zones.json"))


# ------------------------------------------------------------------ windows
def outbound_windows(
    trials: pd.DataFrame, *, end_pad_s: float = 0.6, completed_only: bool = True
) -> pd.DataFrame:
    """One row per trial with its cue->choice outbound window in POSIX seconds.

    From a per-session (or pooled) trials frame - the ``bundle_reader`` shape, ideally
    after ``choice_signals.add_signals`` so ``correct``/``completed`` exist - build:
    ``t_start`` (``trial_start_iso``), ``t_choice`` (``choice_time_iso``), ``t_end``
    (``t_choice + end_pad_s`` so the trace enters the arm), plus ``cue`` (A/B),
    ``choice_cue`` (HIGH/LOW), ``chosen_arm``, ``correct``. Rows missing either bound
    are dropped; ``completed_only`` keeps only scored trials (the default).
    """
    if trials.empty:
        return trials.copy()
    df = trials.copy()
    if completed_only and "completed" in df.columns:
        df = df[df["completed"].astype("boolean").fillna(False)]
    df = df.copy()
    df["t_start"] = iso_series_to_unix(df["trial_start_iso"])
    df["t_choice"] = iso_series_to_unix(df["choice_time_iso"])
    df = df[df["t_start"].notna() & df["t_choice"].notna()].copy()
    df["t_end"] = df["t_choice"] + float(end_pad_s)
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
    seg: pd.DataFrame, zone_map: maze_state.ZoneMap,
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
    sidecar: Sidecar, zone_map: maze_state.ZoneMap, windows: pd.DataFrame,
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


def make_blocks(signals: pd.DataFrame, *, block: int = 20) -> List[Block]:
    """Cut each session/run's completed trials into consecutive blocks of ``block``.

    Blocks never cross a session or restart boundary (so each maps to ONE background
    image + zone map). A trailing partial block (< ``block`` trials) is dropped so
    accuracies compare like-for-like. Requires the ``choice_signals`` columns
    ``completed``/``correct``/``cue``(derived here from ``choice_cue``).
    """
    if signals.empty:
        return []
    df = signals.copy()
    comp = df["completed"].astype("boolean").fillna(False) if "completed" in df \
        else df["outcome"].isin((CORRECT, "INCORRECT"))
    df = df[comp].copy()
    if "cue" not in df.columns:
        df["cue"] = df["choice_cue"].map(_TONE_TO_CUE)
    if "correct" not in df.columns:
        df["correct"] = df["outcome"].eq(CORRECT)

    blocks: List[Block] = []
    for (sid, run), grp in df.groupby(["session_id", "run_idx"], sort=False):
        grp = grp.sort_values("trial", kind="stable")
        n_full = len(grp) // block
        for k in range(n_full):
            sl = grp.iloc[k * block:(k + 1) * block]
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


def select_blocks(
    signals: pd.DataFrame, *, block: int = 20
) -> Tuple[Optional[Block], Optional[Block]]:
    """Pick a (low-accuracy, high-accuracy) pair of blocks for the two overlays.

    Returns the lowest- and highest-accuracy blocks from :func:`make_blocks` (ties
    broken by earliest). ``(None, None)`` if no full block exists. The caller can
    always override by constructing a :class:`Block` from an explicit slice; this is
    just the automatic pick the notebook shows by default. NOTE: on a stretch of
    late-training sessions the "low" block may sit above chance - read its
    ``accuracy``/``label`` and swap in an earlier session if a true chance block is
    wanted.
    """
    blocks = make_blocks(signals, block=block)
    if not blocks:
        return None, None
    low = min(blocks, key=lambda b: b.accuracy)
    high = max(blocks, key=lambda b: b.accuracy)
    return low, high


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
