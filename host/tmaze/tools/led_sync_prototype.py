"""
led_sync_prototype.py - feasibility probe for the board-LED video<->Arduino sync.

An exploratory, standalone tool (same standing as blob_tracker_prototype.py:
cv2/numpy allowed, nothing imports it). It asks one question of a recorded
session bundle: can the indicator LEDs on the rig's electronics board be read
out of the (H.264) video reliably enough to time-stamp the Arduino's events on
the camera's hardware clock? See docs/design/cross-stream-sync-design.md, Phase 1
(section 3), and FW-0045. The production tool this grows into is `sync_fit`.

What it does, in order:

1. Reads the bundle's video sidecar (`video/<id>.csv`: frame_idx, host_unix_s,
   cam_timestamp) and the per-session serial capture (`serial/serial_<id>.csv`,
   the full stream since docs/decisions/0178, or the pre-0178 `sensors_<id>.csv`:
   beam edges S, solenoid acks SOL, REWARD, PONG), each line stamped with the
   host wall clock and the firmware ms.
2. Pass 1 over a window of frames: crops the board region and accumulates a
   running max / min and a sampled median, so LEDs that BLINK ON (max - median)
   and LEDs that BLINK OFF (median - min) both show up as blobs.
3. Pass 2 over the same frames: for each blob, a per-frame brightness trace
   (mean of a small disk around the blob minus a background ring around it), in
   grey and in red-minus-green.
4. Turns each trace into on/off edges (hysteresis), then tries every (blob,
   event type, polarity) pairing and reports which serial event each blob
   mirrors, with the lag and its spread.
5. Fits cam_timestamp = offset + rate * firmware_ms on the matched pairs
   (Theil-Sen) and reports the residuals - the number the design doc wants.

Everything is written to --out (figures + summary.json + bindings.csv); the
bundle itself is never touched.

Run (env drieu_t_maze, any directory):
    python -m tmaze.tools.led_sync_prototype <bundle_dir> --out <folder>
    python -m tmaze.tools.led_sync_prototype <bundle_dir> --out <folder> \
        --crop 800 470 1120 800 --start-min 0 --minutes 6
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import cv2  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"OpenCV (cv2) is required: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)

try:
    import numpy as np  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"numpy is required: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)

from tmaze.core import protocol
from tmaze.core.session import session_serial


# ------------------------------------------------------------------ bundle IO

@dataclass
class Frame:
    idx: int
    host_unix_s: float
    cam_ts: Optional[int]


@dataclass
class Event:
    unix_s: float          # host wall clock of the serial line (local ISO -> epoch)
    fw_ms: int             # firmware session clock
    key: str               # e.g. "S3+" (beam 3 broken), "S3-" (clear), "SOL2.1", "REWARD"
    dur_ms: Optional[int] = None   # SOL pulse width when the line carries it


@dataclass
class Interval:
    """One 'thing was active' span, in host time + (for events) firmware time."""
    t0: float
    t1: float
    fw0: Optional[int] = None
    fw1: Optional[int] = None
    f0: Optional[int] = None       # LED intervals: frame offsets of the two edges
    f1: Optional[int] = None


def iso_to_unix(s: str) -> float:
    """Host ISO wall-clock (naive, local) -> POSIX seconds; matches host_unix_s."""
    return datetime.fromisoformat(s).timestamp()


def _bundle_id(bundle: str) -> str:
    return os.path.basename(os.path.normpath(bundle))


def load_sidecar(bundle: str) -> Tuple[Dict[str, str], List[Frame]]:
    sid = _bundle_id(bundle)
    path = os.path.join(bundle, "video", f"{sid}.csv")
    header: Dict[str, str] = {}
    frames: List[Frame] = []
    with open(path, newline="", encoding="utf-8") as f:
        rows = []
        for line in f:
            if line.startswith("#"):
                body = line[1:].strip()
                if ":" in body:
                    k, v = body.split(":", 1)
                    header[k.strip()] = v.strip()
                continue
            rows.append(line)
    reader = csv.DictReader(rows)
    for r in reader:
        ts = r.get("cam_timestamp", "")
        frames.append(Frame(int(r["frame_idx"]), float(r["host_unix_s"]),
                            int(ts) if ts not in ("", None) else None))
    return header, frames


def load_events(bundle: str) -> List[Event]:
    sid = _bundle_id(bundle)
    path = session_serial.resolve_capture_path(os.path.join(bundle, "serial"), sid)
    out: List[Event] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            iso, _, fw_line = line.rstrip("\n").partition(",")
            p = protocol.parse(fw_line)
            if p is None:
                continue
            try:
                t = iso_to_unix(iso)
            except ValueError:
                continue
            if p.type in ("S", "L"):
                edge = protocol.sensor_edge(fw_line) if p.type == "S" else protocol.classify(fw_line).lick
                if edge is not None:
                    i, active = edge
                    out.append(Event(t, p.ms, f"{p.type}{i}{'+' if active else '-'}"))
            elif p.type == "SOL":
                ps = protocol.sol_pulse(fw_line)
                if ps is not None:
                    dur = p.fields[2].strip() if len(p.fields) > 2 else ""
                    out.append(Event(t, p.ms, f"SOL{ps[0]}.{ps[1]}",
                                     int(dur) if dur.isdigit() else None))
            elif p.type == "REWARD":
                out.append(Event(t, p.ms, "REWARD"))
            elif p.type == "PONG":
                out.append(Event(t, p.ms, "PONG"))
    return out


def video_path(bundle: str) -> str:
    sid = _bundle_id(bundle)
    for ext in (".mp4", ".mkv"):
        p = os.path.join(bundle, "video", sid + ext)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"no video for {sid} under {bundle}/video")


# ------------------------------------------------------------- video passes

def _open(path: str, start_frame: int) -> "cv2.VideoCapture":
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {path}")
    if start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    return cap


def pass1_extremes(path: str, crop: Tuple[int, int, int, int], start: int, n: int,
                   every: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Running max / min and a sampled median of the board crop (BGR uint8)."""
    x0, y0, x1, y1 = crop
    cap = _open(path, start)
    mx = mn = None
    samples: List[np.ndarray] = []
    got = 0
    for k in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        c = frame[y0:y1, x0:x1]
        if mx is None:
            mx, mn = c.copy(), c.copy()
        else:
            np.maximum(mx, c, out=mx)
            np.minimum(mn, c, out=mn)
        if k % every == 0:
            samples.append(c.copy())
        got += 1
    cap.release()
    if not samples:
        raise RuntimeError("no frames read in pass 1")
    med = np.median(np.stack(samples), axis=0).astype(np.uint8)
    return mx, mn, med, got


@dataclass
class Blob:
    id: int
    cx: float
    cy: float
    area: int
    peak: float
    direction: str   # "on" (brighter than median at times) or "off" (darker at times)


def find_blobs(diff: np.ndarray, thresh: float, min_area: int, direction: str,
               start_id: int, nms_radius: int = 3) -> List[Blob]:
    """Blinking LEDs as LOCAL MAXIMA of the difference image.

    Connected components would merge neighbouring channel LEDs (the modules'
    indicators sit ~8 px apart and bloom), so instead: light blur, keep pixels
    that are the maximum within a (2*nms_radius+1) window and above thresh, and
    refine each peak to the intensity-weighted centroid of its small patch.
    `area` is the count of pixels above thresh in that patch (bloom size).
    """
    sm = cv2.blur(diff, (3, 3))
    k = 2 * nms_radius + 1
    local_max = cv2.dilate(sm, np.ones((k, k), np.uint8))
    peaks = np.argwhere((sm >= local_max - 1e-6) & (sm >= thresh))
    blobs: List[Blob] = []
    h, w = diff.shape
    for (py, px) in peaks:
        y0, y1 = max(0, py - nms_radius), min(h, py + nms_radius + 1)
        x0, x1 = max(0, px - nms_radius), min(w, px + nms_radius + 1)
        patch = diff[y0:y1, x0:x1]
        wts = np.clip(patch - thresh * 0.5, 0, None)
        area = int((patch >= thresh).sum())
        if area < min_area or wts.sum() <= 0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx = float((wts * xx).sum() / wts.sum())
        cy = float((wts * yy).sum() / wts.sum())
        if any((b.cx - cx) ** 2 + (b.cy - cy) ** 2 < nms_radius ** 2 for b in blobs):
            continue                                   # duplicate of a plateau neighbour
        blobs.append(Blob(start_id + len(blobs), cx, cy, area, float(patch.max()), direction))
    return blobs


def _masks(shape: Tuple[int, int], blobs: Sequence[Blob], r: int, ring_in: int,
           ring_out: int) -> List[Tuple[np.ndarray, np.ndarray]]:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    all_disks = np.zeros((h, w), bool)
    disks = []
    for b in blobs:
        d = (xx - b.cx) ** 2 + (yy - b.cy) ** 2 <= r * r
        disks.append(d)
        all_disks |= d
    out = []
    for b, d in zip(blobs, disks):
        rr = (xx - b.cx) ** 2 + (yy - b.cy) ** 2
        ring = (rr > ring_in * ring_in) & (rr <= ring_out * ring_out) & ~all_disks
        out.append((d, ring))
    return out


def pass2_traces(path: str, crop: Tuple[int, int, int, int], start: int, n: int,
                 blobs: Sequence[Blob], r: int, ring_in: int, ring_out: int
                 ) -> Tuple[np.ndarray, np.ndarray]:
    """Per-frame (grey, red-minus-green) disk-minus-ring means, shape (n, nblobs)."""
    x0, y0, x1, y1 = crop
    masks = _masks((y1 - y0, x1 - x0), blobs, r, ring_in, ring_out)
    grey = np.full((n, len(blobs)), np.nan, np.float32)
    rmg = np.full((n, len(blobs)), np.nan, np.float32)
    cap = _open(path, start)
    for k in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        c = frame[y0:y1, x0:x1].astype(np.float32)
        g = c.mean(axis=2)
        rg = c[:, :, 2] - c[:, :, 1]
        for j, (d, ring) in enumerate(masks):
            grey[k, j] = g[d].mean() - g[ring].mean()
            rmg[k, j] = rg[d].mean() - rg[ring].mean()
    cap.release()
    return grey, rmg


# ---------------------------------------------------------------- edges/match

def _medfilt3(tr: np.ndarray) -> np.ndarray:
    p = np.pad(tr, 1, mode="edge")
    return np.median(np.stack([p[:-2], p[1:-1], p[2:]]), axis=0)


def edges_from_trace(tr: np.ndarray, min_span: float = 15.0
                     ) -> Tuple[List[int], List[int], float]:
    """Hysteresis on/off -> (rising frame offsets, falling frame offsets, span).

    Levels come from the MIN and MAX of a 3-frame median-filtered copy (robust to
    single-frame glitches) rather than from percentiles, because a reward LED is
    lit for only ~1 % of the frames and any percentile would miss it. Thresholds
    sit at 40 % / 60 % of that span; a span below `min_span` grey levels is noise.
    An edge is stamped on the FIRST frame in the new state.
    """
    v = tr[~np.isnan(tr)]
    if v.size < 10:
        return [], [], 0.0
    sm = _medfilt3(np.nan_to_num(tr, nan=float(np.nanmedian(tr))))
    lo_lvl, hi_lvl = float(sm.min()), float(sm.max())
    span = hi_lvl - lo_lvl
    if span < min_span:
        return [], [], span
    hi, lo = lo_lvl + 0.6 * span, lo_lvl + 0.4 * span
    state = sm[0] > hi
    rising, falling = [], []
    for k in range(1, sm.shape[0]):
        x = sm[k]
        if not state and x > hi:
            state = True
            rising.append(k)
        elif state and x < lo:
            state = False
            falling.append(k)
    return rising, falling, span


def led_intervals(rising: List[int], falling: List[int], t_rel: np.ndarray, t0: float,
                  lit: bool) -> List[Interval]:
    """Spans during which the LED was lit (rise->fall) or dark (fall->rise)."""
    starts, ends = (rising, falling) if lit else (falling, rising)
    out: List[Interval] = []
    j = 0
    for s in starts:
        while j < len(ends) and ends[j] <= s:
            j += 1
        if j >= len(ends):
            break
        e = ends[j]
        out.append(Interval(t_rel[s] + t0, t_rel[e] + t0, f0=s, f1=e))
    return out


def event_intervals(events: Sequence[Event], default_sol_ms: int = 300
                    ) -> Dict[str, List[Interval]]:
    """Per event type, the spans it was in a given state.

    Beams get BOTH pairings - ``S<i>=1`` (S i,1 .. next S i,0) and ``S<i>=0``
    (S i,0 .. next S i,1) - because which state is the physical *break* is
    something this tool measures rather than assumes (PROTOCOL.md says 1 =
    broken; the recorded logs show state 1 is the long RESTING state, see the
    design doc's feasibility findings). Solenoid = SOL line .. + its pulse
    width. REWARD duplicates SOL and is skipped.
    """
    out: Dict[str, List[Interval]] = {}
    last: Dict[str, Event] = {}
    for e in sorted(events, key=lambda e: e.unix_s):
        if e.key[0] in "SL" and e.key[-1] in "+-":
            chan = e.key[:-1]                      # beam "S<i>" or lick detector "L<i>"
            prev = last.get(chan)
            if prev is not None and prev.key != e.key:
                state = "1" if prev.key.endswith("+") else "0"
                out.setdefault(f"{chan}={state}", []).append(
                    Interval(prev.unix_s, e.unix_s, prev.fw_ms, e.fw_ms))
            last[chan] = e
        elif e.key.startswith("SOL"):
            dur = (e.dur_ms or default_sol_ms) / 1000.0
            out.setdefault(e.key, []).append(
                Interval(e.unix_s, e.unix_s + dur, e.fw_ms, e.fw_ms + int(dur * 1000)))
    return out


@dataclass
class Binding:
    blob: int
    key: str
    polarity: str          # "led_off_during_event" or "led_on_during_event"
    n_events: int
    n_matched: int
    n_led_intervals: int
    median_lag_ms: float   # LED start - event start (host time)
    mad_lag_ms: float
    median_end_lag_ms: float
    score: float           # F1 of interval matching
    trace: str = "grey"


def match_intervals(led: Sequence[Interval], ev: Sequence[Interval], tol_s: float
                    ) -> Tuple[List[Tuple[int, int]], np.ndarray, np.ndarray]:
    """Pair event intervals with LED intervals whose start AND end both agree within tol.

    Returns (pairs of (event idx, led idx), start lags, end lags) with lag = LED - event.
    """
    if not led or not ev:
        return [], np.asarray([]), np.asarray([])
    led_starts = np.asarray([i.t0 for i in led])
    order = np.argsort(led_starts)
    led_starts = led_starts[order]
    pairs, s_l, e_l = [], [], []
    used = set()
    for ei, e in enumerate(ev):
        p = int(np.searchsorted(led_starts, e.t0))
        best, best_d = None, None
        for c in (p - 1, p, p + 1):
            if 0 <= c < led_starts.size:
                li = int(order[c])
                if li in used:
                    continue
                ds, de = led[li].t0 - e.t0, led[li].t1 - e.t1
                if abs(ds) <= tol_s and abs(de) <= tol_s:
                    d = abs(ds) + abs(de)
                    if best_d is None or d < best_d:
                        best, best_d = li, d
        if best is not None:
            used.add(best)
            pairs.append((ei, best))
            s_l.append(led[best].t0 - e.t0)
            e_l.append(led[best].t1 - e.t1)
    return pairs, np.asarray(s_l), np.asarray(e_l)


def _mad(x: np.ndarray) -> float:
    return float(np.median(np.abs(x - np.median(x)))) if x.size else float("nan")


def theil_sen(x: np.ndarray, y: np.ndarray, max_pairs: int = 20000, seed: int = 0
              ) -> Tuple[float, float]:
    """Robust line y = a + b x: median of pairwise slopes, median intercept."""
    n = x.size
    rng = np.random.default_rng(seed)
    if n * (n - 1) // 2 <= max_pairs:
        i, j = np.triu_indices(n, 1)
    else:
        i = rng.integers(0, n, max_pairs)
        j = rng.integers(0, n, max_pairs)
    keep = x[i] != x[j]                         # duplicate x (same event twice) -> skip
    i, j = i[keep], j[keep]
    b = np.median((y[j] - y[i]) / (x[j] - x[i]))
    a = np.median(y - b * x)
    return float(a), float(b)


# ------------------------------------------------------------------- figures

def _save_fig_images(out: str, med: np.ndarray, bright: np.ndarray, dark: np.ndarray,
                     blobs: Sequence[Blob], r: int) -> None:
    vis = med.copy()
    for b in blobs:
        col = (0, 255, 255) if b.direction == "on" else (255, 128, 0)
        cv2.circle(vis, (int(round(b.cx)), int(round(b.cy))), r + 2, col, 1)
        cv2.putText(vis, str(b.id), (int(b.cx) + r + 3, int(b.cy) + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, col, 1, cv2.LINE_AA)
    scale = max(1, 900 // max(vis.shape[:2]))
    if scale > 1:
        vis = cv2.resize(vis, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(os.path.join(out, "board_blobs.png"), vis)
    cv2.imwrite(os.path.join(out, "board_median.png"), med)
    cv2.imwrite(os.path.join(out, "diff_bright.png"),
                np.clip(bright * (255.0 / max(1.0, bright.max())), 0, 255).astype(np.uint8))
    cv2.imwrite(os.path.join(out, "diff_dark.png"),
                np.clip(dark * (255.0 / max(1.0, dark.max())), 0, 255).astype(np.uint8))


def _save_fig_traces(out: str, t_rel: np.ndarray, traces: np.ndarray, blobs: Sequence[Blob],
                     bindings: Dict[int, Binding], ev_iv: Dict[str, List[Interval]],
                     t0: float, seconds: float) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    sel = [b for b in blobs if b.id in bindings]
    if not sel:
        sel = list(blobs)[:8]
    fig, axes = plt.subplots(len(sel), 1, figsize=(14, 1.6 * len(sel) + 1), sharex=True)
    if len(sel) == 1:
        axes = [axes]
    m = t_rel <= seconds
    for ax, b in zip(axes, sel):
        ax.plot(t_rel[m], traces[m, b.id], lw=0.8, color="k")
        bd = bindings.get(b.id)
        if bd is not None:
            for iv in ev_iv.get(bd.key, []):
                if iv.t1 - t0 >= 0 and iv.t0 - t0 <= seconds:
                    ax.axvspan(iv.t0 - t0, iv.t1 - t0, color="r", alpha=0.3, lw=0)
            ax.set_ylabel(f"#{b.id}\n{bd.key}", fontsize=8)
        else:
            ax.set_ylabel(f"#{b.id}", fontsize=8)
    axes[-1].set_xlabel("s since window start (red = serial event active span)")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "traces.png"), dpi=110)
    plt.close(fig)


def _save_fig_residuals(out: str, fw_ms: np.ndarray, resid_ms: np.ndarray,
                        keys: Sequence[str]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    for k in sorted(set(keys)):
        mk = np.asarray([kk == k for kk in keys])
        ax.plot(fw_ms[mk] / 1000.0, resid_ms[mk], ".", label=k, ms=5)
    ax.axhline(0, color="k", lw=0.5)
    ax.set_xlabel("firmware s"); ax.set_ylabel("residual (ms)")
    ax.set_title("cam_timestamp = a + b*firmware_ms  residuals (Theil-Sen)")
    ax.legend(fontsize=7, ncol=4)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "residuals.png"), dpi=110)
    plt.close(fig)


# --------------------------------------------------------------------- main

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", help="session bundle folder (<recordings>/phase3_recordings/<id>)")
    ap.add_argument("--out", required=True, help="folder for figures + summary (created)")
    ap.add_argument("--crop", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"),
                    default=(800, 470, 1120, 800),
                    help="board region in the recorded (undistorted, oriented) frame")
    ap.add_argument("--start-min", type=float, default=0.0, help="window start, minutes into video")
    ap.add_argument("--minutes", type=float, default=6.0, help="window length in minutes")
    ap.add_argument("--sample-every", type=int, default=10, help="median sample stride (frames)")
    ap.add_argument("--blob-thresh", type=float, default=35.0,
                    help="min |max-median| (grey, 0-255) for a pixel to count as blinking")
    ap.add_argument("--min-area", type=int, default=2)
    ap.add_argument("--radius", type=int, default=2, help="LED disk radius (px)")
    ap.add_argument("--tol-ms", type=float, default=150.0,
                    help="interval start/end match tolerance (LED vs serial, host clock)")
    ap.add_argument("--min-visible-ms", type=float, default=60.0,
                    help="serial spans shorter than this are excluded from recall (sub-frame)")
    ap.add_argument("--trace-seconds", type=float, default=120.0, help="seconds shown in traces.png")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    header, frames = load_sidecar(a.bundle)
    events = load_events(a.bundle)
    vpath = video_path(a.bundle)
    fps = float(header.get("fps", "25"))
    print(f"bundle {_bundle_id(a.bundle)}: codec={header.get('codec')} fps={fps:.3f} "
          f"undistorted={header.get('undistorted')} frames(sidecar)={len(frames)} "
          f"events={len(events)}")

    # --- sanity: video length vs sidecar, cam_timestamp tick rate ------------
    cap = cv2.VideoCapture(vpath)
    n_video = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    idxs = np.asarray([f.idx for f in frames])
    gaps = int(np.sum(np.diff(idxs) != 1))
    hu = np.asarray([f.host_unix_s for f in frames])
    ct = np.asarray([f.cam_ts if f.cam_ts is not None else np.nan for f in frames], float)
    ok_ct = ~np.isnan(ct)
    tick_hz = float("nan")
    if ok_ct.sum() > 100:
        _, tick_hz = theil_sen(hu[ok_ct][::50], ct[ok_ct][::50])
    print(f"video frames={n_video} sidecar rows={len(frames)} frame_idx gaps={gaps} "
          f"cam_ts present={int(ok_ct.sum())}/{len(frames)} tick rate~{tick_hz/1e9:.4f} GHz")

    start = int(round(a.start_min * 60 * fps))
    n = int(round(a.minutes * 60 * fps))
    n = min(n, len(frames) - start)
    win = frames[start:start + n]
    t0 = win[0].host_unix_s
    t_rel = np.asarray([f.host_unix_s - t0 for f in win])

    # --- pass 1 ---------------------------------------------------------------
    print(f"pass 1: frames {start}..{start + n} crop={tuple(a.crop)} ...", flush=True)
    mx, mn, med, got = pass1_extremes(vpath, tuple(a.crop), start, n, a.sample_every)
    g = lambda im: im.astype(np.float32).mean(axis=2)  # noqa: E731
    bright = g(mx) - g(med)
    dark = g(med) - g(mn)
    blobs = find_blobs(bright, a.blob_thresh, a.min_area, "on", 0)
    blobs += find_blobs(dark, a.blob_thresh, a.min_area, "off", len(blobs))
    # merge blobs that are the same LED seen in both directions (within radius)
    merged: List[Blob] = []
    for b in blobs:
        dup = next((m for m in merged if (m.cx - b.cx) ** 2 + (m.cy - b.cy) ** 2 <= a.radius ** 2), None)
        if dup is None:
            merged.append(Blob(len(merged), b.cx, b.cy, b.area, b.peak, b.direction))
        else:
            dup.direction = "both"
            dup.peak = max(dup.peak, b.peak)
    blobs = merged
    print(f"pass 1 read {got} frames; {len(blobs)} blinking blobs:")
    for b in blobs:
        print(f"  #{b.id:2d} at ({b.cx:6.1f},{b.cy:6.1f}) area={b.area:3d} peak={b.peak:5.1f} {b.direction}")
    _save_fig_images(a.out, med, bright, dark, blobs, a.radius)
    if not blobs:
        print("no blinking blobs found - adjust --crop / --blob-thresh")
        return 1

    # --- pass 2 ---------------------------------------------------------------
    print("pass 2: traces ...", flush=True)
    grey, rmg = pass2_traces(vpath, tuple(a.crop), start, n, blobs, a.radius,
                             a.radius + 3, a.radius + 7)

    # --- intervals + matching -----------------------------------------------
    ev_win = [e for e in events if t0 - 1 <= e.unix_s <= win[-1].host_unix_s + 1
              and e.key != "PONG"]
    counts: Dict[str, int] = {}
    for e in ev_win:
        counts[e.key] = counts.get(e.key, 0) + 1
    print("events in window: " + ", ".join(f"{k}={counts[k]}" for k in sorted(counts)))
    ev_iv = event_intervals(ev_win)
    keys = sorted(ev_iv)
    tol = a.tol_ms / 1000.0
    bindings: Dict[int, Binding] = {}
    per_blob_rows: List[Binding] = []
    led_iv_cache: Dict[Tuple[int, str, bool], List[Interval]] = {}
    for b in blobs:
        best: Optional[Binding] = None
        for tr_name, tr in (("grey", grey[:, b.id]), ("rmg", rmg[:, b.id])):
            rising, falling, span = edges_from_trace(tr)
            if not rising and not falling:
                continue
            for pol, lit in (("led_on_during_event", True), ("led_off_during_event", False)):
                liv = led_intervals(rising, falling, t_rel, t0, lit)
                led_iv_cache[(b.id, tr_name, lit)] = liv
                for k in keys:
                    eiv = ev_iv[k]
                    # Recall counts only spans the camera could possibly see: a
                    # 40 ms frame cannot register a 15 ms sensor flicker.
                    visible = [iv for iv in eiv if (iv.t1 - iv.t0) * 1000 >= a.min_visible_ms]
                    if not visible:
                        continue
                    pairs, s_l, e_l = match_intervals(liv, visible, tol)
                    if not pairs:
                        continue
                    recall = len(pairs) / len(visible)
                    precision = len(pairs) / max(1, len(liv))
                    score = 2 * recall * precision / max(1e-9, recall + precision)
                    cand = Binding(b.id, k, pol, len(visible), len(pairs), len(liv),
                                   float(np.median(s_l)) * 1000, _mad(s_l) * 1000,
                                   float(np.median(e_l)) * 1000, score, tr_name)
                    per_blob_rows.append(cand)
                    if best is None or cand.score > best.score:
                        best = cand
        if best is not None and best.score >= 0.5 and best.n_matched >= 3:
            bindings[b.id] = best
    print(f"\nbindings (score = F1 of interval matching, start AND end within +/-{a.tol_ms:.0f} ms):")
    print(f"  {'blob':>4} {'event':>7} {'polarity':>21} {'trace':>5} {'n_ev':>5} {'n_match':>7} "
          f"{'n_led':>5} {'lag_ms':>7} {'mad_ms':>6} {'end_lag':>7} {'score':>5}")
    for bid in sorted(bindings):
        bd = bindings[bid]
        print(f"  {bd.blob:>4} {bd.key:>7} {bd.polarity:>21} {bd.trace:>5} {bd.n_events:>5} "
              f"{bd.n_matched:>7} {bd.n_led_intervals:>5} {bd.median_lag_ms:>7.1f} "
              f"{bd.mad_lag_ms:>6.1f} {bd.median_end_lag_ms:>7.1f} {bd.score:>5.2f}")
    unbound = [b.id for b in blobs if b.id not in bindings]
    if unbound:
        print(f"  unbound blobs: {unbound}")
        for bid in unbound:
            cands = sorted((c for c in per_blob_rows if c.blob == bid),
                           key=lambda c: -c.score)[:2]
            for c in cands:
                print(f"    #{bid} best candidate: {c.key} {c.polarity} n_match={c.n_matched}/"
                      f"{c.n_events} n_led={c.n_led_intervals} score={c.score:.2f}")
    with open(os.path.join(a.out, "bindings.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["trace", "blob", "key", "polarity", "n_events", "n_matched", "n_led_intervals",
                    "median_lag_ms", "mad_lag_ms", "median_end_lag_ms", "score"])
        for c in per_blob_rows:
            w.writerow([c.trace, c.blob, c.key, c.polarity, c.n_events, c.n_matched,
                        c.n_led_intervals, f"{c.median_lag_ms:.1f}", f"{c.mad_lag_ms:.1f}",
                        f"{c.median_end_lag_ms:.1f}", f"{c.score:.3f}"])

    # --- clock fit on matched pairs (both ends of every matched interval) ----
    xs, ys, ks, hs = [], [], [], []
    seen = set()
    for bid, bd in bindings.items():
        liv = led_iv_cache[(bid, bd.trace, bd.polarity == "led_on_during_event")]
        eiv = [iv for iv in ev_iv[bd.key] if (iv.t1 - iv.t0) * 1000 >= a.min_visible_ms]
        pairs, _, _ = match_intervals(liv, eiv, tol)
        for ei, li in pairs:
            for fw, fidx, t_ev in ((eiv[ei].fw0, liv[li].f0, eiv[ei].t0),
                                   (eiv[ei].fw1, liv[li].f1, eiv[ei].t1)):
                if fw is None or fidx is None or (bd.key, fw) in seen:
                    continue
                fr = win[fidx]
                if fr.cam_ts is None:
                    continue
                seen.add((bd.key, fw))
                xs.append(fw); ys.append(fr.cam_ts); ks.append(bd.key)
                hs.append(fr.host_unix_s - t_ev)
    summary = {
        "bundle": _bundle_id(a.bundle), "codec": header.get("codec"), "fps": fps,
        "video_frames": n_video, "sidecar_rows": len(frames), "frame_idx_gaps": gaps,
        "cam_ts_tick_hz": tick_hz, "window_start_frame": start, "window_frames": n,
        "crop": list(a.crop), "blobs": [asdict(b) for b in blobs],
        "bindings": [asdict(b) for b in bindings.values()],
    }
    if len(xs) >= 5:
        x = np.asarray(xs, float); y = np.asarray(ys, float)
        a0, b0 = theil_sen(x, y)
        resid_ms = (y - (a0 + b0 * x)) / 1e6      # cam ticks are ns (1 GHz)
        hs_a = np.asarray(hs) * 1000
        print(f"\nclock fit on {len(xs)} pairs: cam_ts = {a0:.0f} + {b0:.3f} * fw_ms "
              f"(rate {b0/1e6:.6f} ms/ms)")
        print(f"  residual median={np.median(resid_ms):.1f} ms  MAD={np.median(np.abs(resid_ms-np.median(resid_ms))):.1f} ms "
              f"p5..p95=[{np.percentile(resid_ms,5):.1f}, {np.percentile(resid_ms,95):.1f}] ms  "
              f"(frame period {1000/fps:.1f} ms)")
        print(f"  host-clock lag (frame host_unix_s - event wall): median={np.median(hs_a):.1f} ms "
              f"MAD={np.median(np.abs(hs_a-np.median(hs_a))):.1f} ms")
        _save_fig_residuals(a.out, x, resid_ms, ks)
        summary["fit"] = {"n_pairs": len(xs), "offset_ticks": a0, "rate_ticks_per_ms": b0,
                          "resid_median_ms": float(np.median(resid_ms)),
                          "resid_mad_ms": float(np.median(np.abs(resid_ms - np.median(resid_ms)))),
                          "resid_p5_ms": float(np.percentile(resid_ms, 5)),
                          "resid_p95_ms": float(np.percentile(resid_ms, 95)),
                          "host_lag_median_ms": float(np.median(hs_a)),
                          "host_lag_mad_ms": float(np.median(np.abs(hs_a - np.median(hs_a))))}
    else:
        print("\ntoo few matched pairs for a clock fit")
    _save_fig_traces(a.out, t_rel, grey, blobs, bindings, ev_iv, t0, a.trace_seconds)
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
