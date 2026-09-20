"""
sync_fit.py - line up a session's video and Arduino clocks from the board LEDs.

The production tool the Slice 0 spike (``led_sync_prototype``) grew into. It
reads one recorded session bundle, reads the rig's indicator LEDs out of the
video (``tmaze.vision.led_detector``), matches each LED's blinks against the
serial log and fits ``cam_timestamp <-> firmware ms`` (``tmaze.core.sync``), and
writes the two ``sync/`` artifacts the design doc specifies (section 3.6) plus a
printed report. See ``docs/design/cross-stream-sync-design.md`` (Phase 1) and
FW-0045.

Three ways to run (env ``drieu_t_maze``, any directory):

    # Fit a session and WRITE sync/led_events + sync/clock_fit INTO the bundle:
    python -m tmaze.tools.sync_fit <bundle_dir>

    # Same, but only print the report + write to a scratch folder (bundle untouched):
    python -m tmaze.tools.sync_fit <bundle_dir> --dry-run --out <folder>

    # Build host/led_map.json from a NO-RAT mapping session (fire every valve,
    # break every beam by hand). The in-bundle capture is now the FULL stream
    # (docs/decisions/0178), so its own serial log carries the left/right reward +
    # rinse/drain valve acks - no --serial override needed for new bundles; pass
    # one only for a pre-0178 (filtered) bundle. See docs/mapping-session-runbook.md:
    python -m tmaze.tools.sync_fit <bundle_dir> --map [--serial <rig_logger.csv>]

    # No bundle, no video: check the core end to end on synthetic data:
    python -m tmaze.tools.sync_fit --selftest

Two clocks, one line: ``cam_timestamp`` is the Basler's 1 GHz hardware tick,
``firmware ms`` the Arduino's ``millis()``; the LEDs are the shared events that
tie them together, so the fit is what lets neural/behaviour/video land on one
timeline. The ROI is self-calibrating: the persistently-lit red/green fiducials
are found and their convex hull is the board region the blinking LEDs are searched
inside (so a hand/rat elsewhere never floods the blob list). When a v2
``host/led_map.json`` exists the fiducials are registered against it to recover
the board pose, and each mapped LED is located by the HYBRID rule (a detected
blinking blob when present, else its pose-transformed stored position); a v1 map
falls back to registering the blinking-LED constellation; without any map the tool
**bootstraps** the bindings from the session itself and warns. On a normal (non
``--dry-run``) run it then registers the two ``sync/`` files in the bundle's
``session_<id>.json`` manifest and flips the time-anchor ``fiducial.method`` to
``led-fit`` (the streams + format are announced in ``config/format.json`` too;
ADR-0154). ``--dry-run`` leaves the bundle and its manifest untouched.

Thin shell: all pixel work is in ``tmaze.vision.led_detector`` and all matching /
fitting in ``tmaze.core.sync``; this file only does argument parsing, bundle I/O,
and report formatting.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from tmaze.core.session import session_registry, session_serial
from tmaze.core.session.session_log import SessionManifest
from tmaze.core.sync import clockfit, led_map as LM, matching, pipeline
from tmaze.core.sync import spans as S

TOOL_VERSION = 1
FIDUCIAL_METHOD = "led-fit"


# ------------------------------------------------------------------ bundle I/O

def bundle_id(bundle: str) -> str:
    return os.path.basename(os.path.normpath(bundle))


def _video_path(bundle: str, sid: str) -> str:
    for ext in (".mp4", ".mkv"):
        p = os.path.join(bundle, "video", sid + ext)
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"no video ({sid}.mp4/.mkv) under {os.path.join(bundle, 'video')}")


def _read_frames(bundle: str, sid: str) -> Tuple[Dict[str, str], List[S.Frame]]:
    path = os.path.join(bundle, "video", f"{sid}.csv")
    with open(path, encoding="utf-8") as f:
        return S.parse_sidecar_csv(f)


def _read_events(bundle: str, sid: str, serial_path: Optional[str] = None) -> Dict[str, List[S.Span]]:
    """Serial spans for the session. Defaults to the bundle's per-session capture
    - the COMPLETE verbatim stream ``serial/serial_<id>.csv`` (docs/decisions/0178),
    falling back to the pre-0178 filtered ``sensors_<id>.csv`` for older bundles.
    ``serial_path`` overrides it with any ``<iso>,<ms>,TYPE,...`` log."""
    path = serial_path or session_serial.resolve_capture_path(
        os.path.join(bundle, "serial"), sid)
    with open(path, encoding="utf-8") as f:
        return S.serial_spans(S.parse_serial_csv(f))


# ------------------------------------------------------- detection + spans

def _window(frames: Sequence[S.Frame], start_min: float, minutes: Optional[float],
            fps: float) -> Tuple[int, Optional[int]]:
    """(start_frame, n_frames) for the requested window; n=None means to the end."""
    start = int(round(start_min * 60 * fps))
    start = max(0, min(start, len(frames)))
    if minutes is None or minutes <= 0:
        return start, None
    return start, int(round(minutes * 60 * fps))


def _frame_size(header: Dict[str, str]) -> Optional[Tuple[int, int]]:
    w = header.get("width") or header.get("frame_width")
    h = header.get("height") or header.get("frame_height")
    try:
        return (int(w), int(h)) if w and h else None
    except (TypeError, ValueError):
        return None


def _apply_point(t, xy: Tuple[float, float]) -> Tuple[float, float]:
    """Apply a vision Similarity to one point without importing numpy (so the
    stdlib self-test stays dep-free); mirrors led_detector.Similarity.apply."""
    c = math.cos(t.theta) * t.scale
    s = math.sin(t.theta) * t.scale
    x, y = xy
    return (c * x - s * y + t.tx, s * x + c * y + t.ty)


# ---------------------------------------------------------------- the run

class SyncRun:
    """Everything one session produced: blobs, spans, bindings, fit, registration."""

    def __init__(self, sid: str, header: Dict[str, str], blobs, floor: float,
                 led_ids: Dict[int, str], result: pipeline.SyncResult,
                 blob_spans: List[pipeline.BlobSpans], events: Dict[str, List[S.Span]],
                 registration, used_map: Optional[LM.LedMap], window: Tuple[int, Optional[int]],
                 tol_s: float, min_visible_s: float, crop: Optional[Tuple[int, int, int, int]],
                 fiducials=(), fid_registration=None,
                 board_poly: Optional[List[Tuple[float, float]]] = None,
                 frame_size: Optional[Tuple[int, int]] = None, median=None):
        self.sid = sid
        self.header = header
        self.blobs = blobs
        self.floor = floor
        self.led_ids = led_ids                 # blob id -> led_id
        self.result = result
        self.blob_spans = {b.blob: b for b in blob_spans}
        self.events = events
        self.registration = registration       # blinking-LED registration (v1 maps)
        self.used_map = used_map
        self.window = window
        self.tol_s = tol_s
        self.min_visible_s = min_visible_s
        self.crop = crop
        self.fiducials = fiducials             # detected persistent LEDs (crop coords)
        self.fid_registration = fid_registration   # pose from the fiducials (v2 maps / --map)
        self.board_poly = board_poly           # ROI outline in FULL-frame pixels
        self.frame_size = frame_size
        self.median = median                   # the crop's median image, for the overlay

    def led_id(self, blob: int) -> str:
        return self.led_ids.get(blob, f"blob_{blob}")


def analyze_bundle(bundle: str, *, start_min: float = 0.0, minutes: Optional[float] = None,
                   tol_s: float = 0.20, min_visible_s: float = 0.06,
                   map_path: Optional[str] = None, whole_frame: bool = False,
                   board_rect: Optional[Tuple[int, int, int, int]] = None,
                   min_matched: int = 3, min_pairs: int = 30,
                   serial_path: Optional[str] = None) -> SyncRun:
    """Detect the LEDs, bind them, fit the clock.

    The ROI is self-calibrating: the persistently-lit red/green fiducials are
    detected, and their convex hull (grown by a margin) is the board region the
    blinking channel LEDs are searched inside - so a hand/rat elsewhere on the
    maze never floods the blob list (ADR-0153). With a v2 map the fiducials are
    registered against the stored constellation to recover the board pose; each
    mapped LED is then located by the HYBRID rule (a detected blinking blob when
    present, else its pose-transformed stored position). ``whole_frame`` (--map)
    searches the whole frame for fiducials; ``board_rect`` is an explicit search
    window that overrides both (a v1-style fast path / fallback).
    """
    sid = bundle_id(bundle)
    header, frames = _read_frames(bundle, sid)
    if not frames:
        raise RuntimeError(f"no frames in the sidecar for {sid}")
    events = _read_events(bundle, sid, serial_path)
    fps = float(header.get("fps", "25") or "25")
    frame_size = _frame_size(header)

    used_map: Optional[LM.LedMap] = None
    params = LM.DetectorParams()
    crop: Optional[Tuple[int, int, int, int]] = board_rect
    candidate_map = map_path or LM.led_map_path()
    if not whole_frame and os.path.isfile(candidate_map):
        used_map = LM.load(candidate_map)
        params = used_map.detector
        if crop is None:
            crop = used_map.board_rect

    start, n = _window(frames, start_min, minutes, fps)
    win = frames[start:start + n] if n is not None else frames[start:]

    from tmaze.vision import led_detector as D
    video = _video_path(bundle, sid)
    ext = D.accumulate_extremes(video, crop, start, n, params)
    got = ext.n
    if got != len(win):
        win = win[:got]
    tick = S.cam_tick_s(win)
    ox, oy = (crop[0], crop[1]) if crop else (0, 0)
    shape = ext.max.shape[:2]

    blinking, floor = D.detect_blobs(ext, params)        # crop coords
    # A channel LED that rests lit (beam/lick) is on ~95% of the time and would
    # pass the persistence test; exclude anything that also blinks so only the
    # true always-on status LEDs remain as fiducials.
    fiducials = D.detect_fiducials(ext, params, exclude=blinking)   # crop coords

    registration = None                                  # blinking-LED registration (v1)
    fid_registration = None
    board_poly: Optional[List[Tuple[float, float]]] = None
    led_ids: Dict[int, str] = {}

    if used_map is not None and used_map.fiducials:
        # v2: register the fiducials -> board pose, then locate LEDs by the hybrid rule.
        obs = [(f.x + ox, f.y + oy) for f in fiducials]
        fid_registration = D.register(obs, used_map.fiducial_constellation(),
                                      tol_px=max(2.0, params.nms_radius),
                                      obs_labels=[f.color for f in fiducials],
                                      ref_labels=used_map.fiducial_colors())
        t = fid_registration.transform
        if used_map.board_poly:
            board_poly = [_apply_point(t, p) for p in used_map.board_poly]
        expected_crop = [(lambda q: (q[0] - ox, q[1] - oy))(_apply_point(t, e.ref_px))
                         for e in used_map.leds]
        combined = D.hybrid_blobs(expected_crop, blinking, tol_px=max(3.0, params.nms_radius + 1))
        blobs = [b for b, _ei, _det in combined]
        for b, ei, _det in combined:
            if ei is not None:
                led_ids[b.id] = used_map.leds[ei].led_id
    else:
        # No map (bootstrap / --map) or a v1 map: trace the detected blinking blobs.
        # When fiducials are present, confine the blinking blobs to the board hull
        # so a rat/hand elsewhere is excluded even on a whole-frame --map.
        if fiducials:
            board_poly = LM.board_poly_around([(f.x + ox, f.y + oy) for f in fiducials],
                                              params.hull_margin_px, frame_size)
            blinking = [b for b in blinking if LM.point_in_poly((b.x + ox, b.y + oy), board_poly)]
        blobs = blinking
        if used_map is not None:                         # v1 map: register the LED constellation
            observed = [(b.x + ox, b.y + oy) for b in blobs]
            registration = D.register(observed, used_map.constellation(),
                                      tol_px=max(2.0, params.nms_radius))
            for ref_i, obs_i in registration.pairs:
                if 0 <= ref_i < len(used_map.leds):
                    led_ids[blobs[obs_i].id] = used_map.leds[ref_i].led_id

    grey, _rmg = D.sample_traces(video, crop, start, got, blobs, shape, params)
    blob_spans = [D.blob_spans(b.id, grey[:, i], win, tick) for i, b in enumerate(blobs)]

    result = pipeline.analyze(blob_spans, events, tol_s=tol_s, min_visible_s=min_visible_s,
                              min_matched=min_matched, min_pairs=min_pairs)
    return SyncRun(sid, header, blobs, floor, led_ids, result, blob_spans, events,
                   registration, used_map, (start, got), tol_s, min_visible_s, crop,
                   fiducials=fiducials, fid_registration=fid_registration,
                   board_poly=board_poly, frame_size=frame_size, median=ext.median())


# ------------------------------------------------------------ led_map builder

def build_led_map(run: SyncRun) -> LM.LedMap:
    """Turn a mapping run into a ``led_map.json`` (schema v2): the persistent
    red/green fiducials, the board ROI polygon (their hull), and every blinking
    LED's full-frame position + binding."""
    ox, oy = (run.crop[0], run.crop[1]) if run.crop else (0, 0)
    frame_size = run.frame_size
    fiducials: List[LM.Fiducial] = []
    counts: Dict[str, int] = {}
    for f in sorted(run.fiducials, key=lambda f: (round(f.y), f.x)):
        idx = counts.get(f.color, 0)
        counts[f.color] = idx + 1
        fiducials.append(LM.Fiducial(f"{f.color}_{idx}",
                                     (round(f.x + ox, 2), round(f.y + oy, 2)), f.color))
    entries: List[LM.LedEntry] = []
    taken: List[str] = []
    for b in sorted(run.blobs, key=lambda b: (round(b.y), b.x)):
        binding = run.result.bindings.get(b.id)
        channel = binding.channel if binding else None
        polarity = binding.polarity if binding else None
        lag = binding.lag_s if binding else None
        led_id = LM.led_id_for(channel, polarity, taken, b.id)
        taken.append(led_id)
        entries.append(LM.LedEntry(led_id, (round(b.x + ox, 2), round(b.y + oy, 2)),
                                   channel, polarity, lag))
    det = run.used_map.detector if run.used_map else LM.DetectorParams()
    # The board ROI comes from the fiducials' hull; its bounding box is the search
    # rectangle. Fall back to an explicit crop, then to a box around the LEDs.
    board_poly = run.board_poly
    if board_poly:
        rect = LM.poly_bbox(board_poly)
    elif run.crop:
        rect = tuple(run.crop)
    else:
        rect = LM.board_rect_around([e.ref_px for e in entries], det.margin_px, frame_size)
    return LM.LedMap(run.sid, rect, entries, det, frame_size,
                     datetime.now().replace(microsecond=0).isoformat(),
                     fiducials=fiducials, board_poly=board_poly)


# --------------------------------------------------------------- artifacts

LED_EVENT_COLUMNS = ["led_id", "edge", "frame_idx", "cam_timestamp", "host_unix_s",
                     "matched_event", "firmware_ms", "residual_ms"]


def _residual_ms(fit: Optional[clockfit.ClockFit], y_ticks: Optional[float],
                 x_fw_ms: Optional[float], segment: int) -> Optional[float]:
    if fit is None or y_ticks is None or x_fw_ms is None or fit.rate == 0:
        return None
    if segment not in fit.offsets:
        return None
    return (y_ticks - fit.predict(x_fw_ms, segment)) / fit.rate


def led_event_rows(run: SyncRun) -> List[dict]:
    """One row per matched LED edge (both ends of every matched span)."""
    fit = run.result.fit
    active = S.active_spans(run.events)
    rows: List[dict] = []
    for blob, binding in sorted(run.result.bindings.items()):
        bs = run.blob_spans[blob]
        led = bs.lit if binding.polarity == matching.LIT else bs.dark
        vis = matching.visible(active[binding.channel], run.min_visible_s)
        matches = matching.match_spans(led, vis, run.tol_s)
        start_edge, end_edge = ((clockfit.ON, clockfit.OFF) if binding.polarity == matching.LIT
                                else (clockfit.OFF, clockfit.ON))
        for m in matches:
            e, l = vis[m.event_idx], led[m.led_idx]
            for edge, ref, y, host, x in (
                    (start_edge, l.ref0, l.clk0, l.host_t0, e.clk0),
                    (end_edge, l.ref1, l.clk1, l.host_t1, e.clk1)):
                resid = _residual_ms(fit, y, x, e.segment)
                rows.append({
                    "led_id": run.led_id(blob),
                    "edge": edge,
                    "frame_idx": ref,
                    "cam_timestamp": None if y is None else int(y),
                    "host_unix_s": round(host, 3),
                    "matched_event": binding.channel,
                    "firmware_ms": None if x is None else int(x),
                    "residual_ms": None if resid is None else round(resid, 2),
                })
    rows.sort(key=lambda r: (r["frame_idx"] if r["frame_idx"] is not None else 0, r["led_id"]))
    return rows


def write_led_events(path: str, rows: Sequence[dict], sid: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(f"# sync/led_events for session {sid}: one row per matched LED edge, "
                f"written by tmaze.tools.sync_fit v{TOOL_VERSION}.\n")
        w = csv.DictWriter(f, fieldnames=LED_EVENT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in LED_EVENT_COLUMNS})


def _fiducial_color_counts(fiducials) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in fiducials:
        counts[f.color] = counts.get(f.color, 0) + 1
    return counts


def write_overlay(path: str, run: SyncRun) -> bool:
    """Draw the ROI polygon + fiducials + located LEDs on the crop's median image
    for a manual QA check against the physical marker corners (ADR-0153). Returns
    False (no file) when OpenCV is unavailable or there is no frame to draw on."""
    from tmaze.vision import led_detector as D
    if D.cv2 is None or run.median is None:
        return False
    ox, oy = (run.crop[0], run.crop[1]) if run.crop else (0, 0)
    poly = [(x - ox, y - oy) for x, y in run.board_poly] if run.board_poly else None
    img = D.draw_overlay(run.median, poly, run.fiducials, run.blobs, labels=run.led_ids)
    D.cv2.imwrite(path, img)
    return True


def clock_fit_dict(run: SyncRun) -> dict:
    res = run.result
    start, n = run.window
    bindings = {run.led_id(b): {"blob": b, **{k: v for k, v in vars(bd).items() if k != "blob"},
                                "recall": round(bd.recall, 3)}
                for b, bd in sorted(res.bindings.items())}
    verdicts = {run.led_id(b): v for b, v in sorted(res.blob_verdicts.items())}
    return {
        "session_id": run.sid,
        "method": FIDUCIAL_METHOD,
        "generated_by": "tmaze.tools.sync_fit",
        "tool_version": TOOL_VERSION,
        "led_map": {
            "used": run.used_map is not None,
            "source_session": run.used_map.session_id if run.used_map else None,
            "schema_version": LM.SCHEMA_VERSION,
        },
        "window": {"start_frame": start, "frames": n, "fps": float(run.header.get("fps", "25") or "25")},
        "detector": asdict(run.used_map.detector if run.used_map else LM.DetectorParams()),
        "noise_floor": round(run.floor, 2),
        "board_rect": list(run.crop) if run.crop else None,
        "board_poly": None if run.board_poly is None
        else [[round(x, 2), round(y, 2)] for x, y in run.board_poly],
        "fiducials": {
            "n": len(run.fiducials),
            "by_color": _fiducial_color_counts(run.fiducials),
            "registration": run.fid_registration.to_dict() if run.fid_registration is not None else None,
        },
        "registration": run.registration.to_dict() if run.registration is not None else None,
        "blob_verdicts": verdicts,
        "bindings": bindings,
        "fit": res.fit.to_dict() if res.fit else None,
        "verdict": res.verdict,
    }


def write_clock_fit(path: str, run: SyncRun) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clock_fit_dict(run), f, indent=2)
        f.write("\n")


def _atomic_write(path: str, text: str) -> None:
    """Write ``text`` to ``path`` via temp file + os.replace (never a torn file).

    Mirrors ``session_config._atomic_json`` - used to re-write the rig-authored
    ``session_<id>.json`` in place without risking a half-written manifest."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def register_sync_in_manifest(bundle: str, run: SyncRun,
                               *, good: bool = True) -> Optional[str]:
    """Announce the two ``sync/`` files in the bundle's session manifest.

    Reads ``session_<id>.json``, registers the ``sync_led_events`` /
    ``sync_clock_fit`` streams (bundle-relative paths), and - only when ``good``
    (a usable fit was produced; docs/decisions/0188) - flips the time-anchor
    ``fiducial.method`` to ``led-fit`` while KEEPING the ``pending`` note (led-fit
    is a ~9.5 ms software fit, not the sub-ms hardware sync FW-0018 still points
    at). A FAILED fit still records the two files (so they are discoverable) but
    leaves ``fiducial.method`` as ``host-t0`` - the manifest must not claim a
    clock fit the numbers don't support. The fit numbers themselves stay in
    ``sync/clock_fit_<id>.json`` - the manifest only points at them (ADR-0154).
    Atomic read-modify-write so the file the rig wrote is never clobbered
    mid-write; idempotent. Returns the manifest path on success, or None if there
    is no manifest to update."""
    path = os.path.join(bundle, f"session_{run.sid}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        m = SessionManifest.from_dict(json.load(fh))
    m.set_stream("sync_led_events", f"sync/led_events_{run.sid}.csv")
    m.set_stream("sync_clock_fit", f"sync/clock_fit_{run.sid}.json")
    if good:
        m.fiducial["method"] = FIDUCIAL_METHOD  # host-t0 -> led-fit; pending note kept
    _atomic_write(path, m.render())
    return path


def write_fit_plot(path: str, run: SyncRun) -> bool:
    """Save a QA plot of the clock fit: per-edge residuals (ms) vs firmware_ms.

    A quick visual companion to the residual scalars in clock_fit_<id>.json - the
    dashed median +/- MAD band shows the fit's spread and any drift over the
    session. ``matplotlib`` is an OPTIONAL rig dep: if it is not installed (or
    there is no fit / no matched pairs) the plot is skipped and this returns
    False, so the tool still runs on a bare machine."""
    fit = run.result.fit
    if fit is None:
        return False
    pts = [(r["firmware_ms"], r["residual_ms"]) for r in led_event_rows(run)
           if r["firmware_ms"] is not None and r["residual_ms"] is not None]
    if not pts:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not installed - skipped the clock-fit plot)")
        return False
    xs = [p[0] / 1000.0 for p in pts]          # firmware seconds, for readability
    ys = [p[1] for p in pts]
    r = fit.residuals
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.axhspan(r.median_ms - r.mad_ms, r.median_ms + r.mad_ms,
               color="0.85", label=f"median +/- MAD ({r.mad_ms:.1f} ms)")
    ax.axhline(r.median_ms, color="0.4", lw=1, ls="--")
    ax.scatter(xs, ys, s=8, alpha=0.5, color="tab:blue")
    ax.set_xlabel("firmware clock (s)")
    ax.set_ylabel("residual (ms)  [cam - fit]")
    ax.set_title(f"{run.sid}: clock fit  rate={fit.rate:.4f}  "
                 f"{len(fit.offsets)} seg  {fit.n_pairs} pairs  "
                 f"MAD={r.mad_ms:.1f} ms  verdict={run.result.verdict}")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


# ---------------------------------------------------------------- reporting

def print_report(run: SyncRun, out: str) -> None:
    res = run.result
    print(f"\nsession {run.sid}: codec={run.header.get('codec')} "
          f"fps={run.header.get('fps')} frames(window)={run.window[1]} "
          f"blobs={len(run.blobs)} noise_floor={run.floor:.1f}")
    fc = _fiducial_color_counts(run.fiducials)
    print(f"  fiducials: {len(run.fiducials)} persistent LEDs "
          f"({fc.get('red', 0)} red, {fc.get('green', 0)} green)"
          + ("" if run.board_poly else "  [no ROI - no fiducials found]"))
    if run.fid_registration is not None:
        r = run.fid_registration
        print(f"  board pose (fiducials): {r.verdict}, {r.n_inliers}/{r.n_reference} matched, "
              f"rms {r.rms_px:.2f} px, shift ({r.transform.tx:+.1f},{r.transform.ty:+.1f}) px, "
              f"rot {math.degrees(r.transform.theta):+.2f} deg, scale {r.transform.scale:.3f}")
    if run.used_map is None:
        print("  no led_map.json - bootstrapped bindings from this session "
              "(run with --map to save them; then later sessions verify against it)")
    elif run.registration is not None:
        r = run.registration
        print(f"  led_map {run.used_map.session_id} (v1): registration {r.verdict}, "
              f"{r.n_inliers}/{r.n_reference} LEDs, rms {r.rms_px:.2f} px, "
              f"shift ({r.transform.tx:+.1f},{r.transform.ty:+.1f}) px, "
              f"rot {math.degrees(r.transform.theta):+.2f} deg")
        if r.unmatched_reference:
            print(f"    silent/displaced map LEDs: "
                  f"{[run.used_map.leds[i].led_id for i in r.unmatched_reference]}")
    else:
        print(f"  led_map {run.used_map.session_id} (v2): "
              f"{len(run.led_ids)} LEDs located (detected + pose-transformed fallback)")

    print(f"\n  {'led':>10} {'channel':>8} {'pol':>4} {'match/vis':>10} {'led':>4} "
          f"{'prec':>5} {'recall':>6} {'lag_ms':>7} {'mad':>5} {'endlag':>7}")
    for blob, b in sorted(res.bindings.items()):
        print(f"  {run.led_id(blob):>10} {b.channel:>8} {b.polarity[:4]:>4} "
              f"{f'{b.n_matched}/{b.n_events}':>10} {b.n_led:>4} {b.score:>5.2f} "
              f"{b.recall:>6.2f} {b.lag_s * 1000:>7.1f} {b.lag_mad_s * 1000:>5.1f} "
              f"{b.end_lag_s * 1000:>7.1f}")
    unbound = [run.led_id(b) for b, v in res.blob_verdicts.items() if v != pipeline.MATCHED]
    if unbound:
        print(f"  unbound/silent: {unbound}")
        for blob, v in sorted(res.blob_verdicts.items()):
            if v == pipeline.MATCHED:
                continue
            cands = res.candidates.get(blob, [])[:1]
            for c in cands:
                plausible = matching.lag_plausible(c)
                print(f"    {run.led_id(blob):>10} nearest: {c.channel} {c.polarity} "
                      f"prec {c.score:.2f} n {c.n_matched}/{c.n_events} "
                      f"lag {c.lag_s * 1000:+.0f}/{c.end_lag_s * 1000:+.0f} ms"
                      f"{'' if plausible else ' (rejected: precedes its wire)'}")

    fit = res.fit
    if fit is not None:
        r = fit.residuals
        print(f"\n  clock fit: cam_ticks = offset[seg] + {fit.rate:.4f} * fw_ms  "
              f"({len(fit.offsets)} segment(s), {fit.n_pairs} pairs)")
        print(f"    residual median={r.median_ms:.1f} ms  MAD={r.mad_ms:.1f} ms  "
              f"p5..p95=[{r.p5_ms:.1f}, {r.p95_ms:.1f}] ms")
        if fit.per_edge_offset_ms:
            print("    per-edge offset: " + ", ".join(f"{k}={v:.1f} ms"
                  for k, v in fit.per_edge_offset_ms.items()))
        print(f"    host lag median={fit.host_lag_median_s * 1000:.1f} ms "
              f"MAD={fit.host_lag_mad_s * 1000:.1f} ms; verdict {fit.verdict}")
        if fit.host_steps:
            print(f"    HOST CLOCK STEP(S): {[round(s.delta_s * 1000) for s in fit.host_steps]} ms "
                  "- the host-time join is untrustworthy past the step")
    else:
        print(f"\n  no clock fit: {res.verdict}")
    print(f"\n  wrote {out}")


# ---------------------------------------------------------------- selftest

def _selftest() -> int:
    """Synthesise a handful of LED spans + their serial events at a known rate
    and offset, run the pipeline, and confirm it recovers them. No video."""
    rate = 1_000_000.0      # cam ticks (ns) per firmware ms == 1e6 (1 GHz vs kHz)
    offset = 5_000_000_000.0
    fw_ms = 1_000
    events: Dict[str, List[S.Span]] = {"S2=0": [], "S2=1": [], "SOL2.1": []}
    lit: List[S.Span] = []
    dark: List[S.Span] = []
    host = 1_700_000_000.0
    prev_end_fw = fw_ms
    for i in range(40):
        # a beam crossing: S2=0 for 200 ms, resting S2=1 for 800 ms between
        c0 = prev_end_fw + 800
        c1 = c0 + 200
        events["S2=1"].append(S.Span("S2=1", host, host + 0.8, prev_end_fw, c0, 0, 0, 0, S.FIRMWARE_MS))
        events["S2=0"].append(S.Span("S2=0", host + 0.8, host + 1.0, c0, c1, 0, 0, 0, S.FIRMWARE_MS))
        # the LED is DARK during the crossing, +40 ms after the wire at each edge
        dark.append(S.Span("blob0", host + 0.84, host + 1.04,
                           offset + rate * (c0 + 40), offset + rate * (c1 + 40), 0, 0, 0, 1e-9))
        host += 1.0
        prev_end_fw = c1
    blob = pipeline.BlobSpans(0, lit, dark)
    res = pipeline.analyze([blob], events, min_matched=3, min_pairs=10)
    ok = (res.verdict == clockfit.VERDICT_OK and res.fit is not None
          and 0 in res.bindings and res.bindings[0].channel == "S2=0"
          and abs(res.fit.rate - rate) < 1.0
          and res.fit.residuals.mad_ms < 5.0)
    binding = res.bindings[0].channel if 0 in res.bindings else None
    got_rate = f"{res.fit.rate:.1f}" if res.fit else "none"
    got_mad = f"{res.fit.residuals.mad_ms:.2f}" if res.fit else "none"
    print(f"selftest: verdict={res.verdict} binding={binding} "
          f"rate={got_rate} (want {rate:.0f}) resid_mad={got_mad} ms")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


# ------------------------------------------------------------ batch (--all)

# The three buckets a bundle falls into in --all mode (docs/decisions/0188).
FIT_OK = "fit"          # fiducials seen and a usable clock fit was produced
FIT_FAILED = "failed"   # fiducials seen but no usable fit (poor/too-few/step)
FIT_SKIPPED = "skipped"  # not attempted: no video/data, no fiducials, or already fit


class BundleFitResult(NamedTuple):
    """One bundle's outcome in a batch run: its id, which bucket, and a one-line
    human detail (the fit numbers, the failure verdict, or why it was skipped)."""
    sid: str
    bucket: str
    detail: str


def _fit_is_good(run: SyncRun) -> bool:
    """A usable clock fit: a fit exists AND the pipeline verdict is OK (a
    host-clock step or too-few-events verdict is not usable)."""
    return run.result.fit is not None and run.result.verdict == clockfit.VERDICT_OK


def _manifest_dict(bundle: str, sid: str) -> Optional[dict]:
    """Parse a bundle's ``session_<id>.json`` best-effort; None if absent/bad."""
    path = os.path.join(bundle, f"session_{sid}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def needs_fit(manifest: Optional[dict], refit: bool) -> bool:
    """Whether a bundle should be (re)fit: always when ``refit``; otherwise only
    when its manifest has no ``sync_clock_fit`` stream yet. A missing/unreadable
    manifest counts as needing a fit (nothing recorded to skip on)."""
    if refit:
        return True
    streams = (manifest or {}).get("streams") or {}
    return "sync_clock_fit" not in streams


def summarize(results: Sequence[BundleFitResult]) -> Tuple[str, int]:
    """A human summary table + the process exit code. Exit is non-zero IFF a
    bundle was ATTEMPTED but failed (fiducials seen, no usable fit) - a bundle
    that could not be attempted (no video / no fiducials / already fit) is
    SKIPPED and never fails the run (docs/decisions/0188)."""
    fit = [r for r in results if r.bucket == FIT_OK]
    failed = [r for r in results if r.bucket == FIT_FAILED]
    skipped = [r for r in results if r.bucket == FIT_SKIPPED]
    lines = [f"  {r.sid:>28}  {r.bucket:>7}  {r.detail}" for r in results]
    if results:
        lines.append("")
    lines.append(f"  {len(fit)} fit, {len(failed)} failed, {len(skipped)} skipped "
                 f"({len(results)} bundle(s))")
    if failed:
        lines.append("  FAILED: " + ", ".join(r.sid for r in failed))
    return "\n".join(lines), (1 if failed else 0)


def fit_one_bundle(bundle: str, *, refit: bool, **fit_kwargs) -> BundleFitResult:
    """Fit a single bundle for batch mode and return its bucket + detail.

    Skips a bundle that already carries a clock fit (unless ``refit``), that has
    no video/serial data, or where no fiducials were detected (nothing to fit -
    left ``host-t0`` with no orphan clock-fit written). Otherwise writes the two
    ``sync/`` artifacts and registers the manifest (a good fit flips
    ``fiducial.method`` to led-fit; a failed one records the files but stays
    host-t0). Never raises for an expected missing input - those become SKIPPED."""
    sid = bundle_id(bundle)
    if not needs_fit(_manifest_dict(bundle, sid), refit):
        return BundleFitResult(sid, FIT_SKIPPED, "already fit (use --refit)")
    try:
        run = analyze_bundle(bundle, **fit_kwargs)
    except (FileNotFoundError, RuntimeError) as e:
        return BundleFitResult(sid, FIT_SKIPPED, f"no video/data ({e})")
    if not run.fiducials:
        return BundleFitResult(sid, FIT_SKIPPED, "no fiducials in frame")
    out_dir = os.path.join(bundle, "sync")
    os.makedirs(out_dir, exist_ok=True)
    write_led_events(os.path.join(out_dir, f"led_events_{sid}.csv"),
                     led_event_rows(run), sid)
    write_clock_fit(os.path.join(out_dir, f"clock_fit_{sid}.json"), run)
    good = _fit_is_good(run)
    register_sync_in_manifest(bundle, run, good=good)
    if good:
        fit = run.result.fit
        return BundleFitResult(
            sid, FIT_OK,
            f"rate={fit.rate:.4f} MAD={fit.residuals.mad_ms:.1f}ms "
            f"{fit.n_pairs} pairs")
    return BundleFitResult(sid, FIT_FAILED, run.result.verdict)


def run_all(roots: Sequence[str], *, refit: bool = False,
            log_path: Optional[str] = None, **fit_kwargs) -> int:
    """Fit every bundle under ``roots`` (batch mode). Scans the roots the same
    way the registry does (``session_registry.scan_roots_with_root``,
    de-duplicated by session id), fits each via :func:`fit_one_bundle`, prints a
    per-bundle table + summary (also written to ``log_path`` when given), and
    returns the process exit code (non-zero iff any bundle FAILED; see
    :func:`summarize`)."""
    pairs = session_registry.scan_roots_with_root([str(r) for r in roots])
    results = [fit_one_bundle(bundle, refit=refit, **fit_kwargs)
               for _root, bundle in pairs]
    text, code = summarize(results)
    report = f"sync_fit --all: scanned {len(pairs)} bundle(s)\n{text}\n"
    print(report)
    if log_path:
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(report)
            print(f"  wrote {log_path}")
        except OSError as e:
            print(f"  (could not write log {log_path}: {e})")
    return code


# ------------------------------------------------------------------- main

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", nargs="?", help="session bundle folder "
                    "(<recordings>/phase3_recordings/<id>)")
    ap.add_argument("--map", action="store_true",
                    help="build host/led_map.json from this session (whole-frame search) instead of fitting")
    ap.add_argument("--map-out", help="where --map writes the map (default host/led_map.json)")
    ap.add_argument("--board-rect", nargs=4, type=int, metavar=("X0", "Y0", "X1", "Y1"),
                    help="confine the LED search to this full-frame rectangle (build a map from a "
                         "session that still has a rat in it, or scan fast before a map exists)")
    ap.add_argument("--serial", help="read serial events from this log instead of the bundle's "
                    "central-only capture; use the FULL rig_logger CSV for a mapping session "
                    "(it keeps the left/right-reward and rinse/drain valve acks the capture drops)")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not touch the bundle; print the report and write artifacts to --out")
    ap.add_argument("--out", help="scratch folder for --dry-run artifacts (default <bundle>/_sync_dryrun)")
    ap.add_argument("--start-min", type=float, default=0.0, help="window start, minutes into video")
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="window length in minutes (0 = whole session, the default)")
    ap.add_argument("--tol-ms", type=float, default=200.0,
                    help="both-ends LED-vs-serial match tolerance on the host clock")
    ap.add_argument("--min-visible-ms", type=float, default=60.0,
                    help="serial spans shorter than this are sub-frame and excluded from recall")
    ap.add_argument("--min-matched", type=int, default=3,
                    help="fewest matched spans for a binding (raise for a full session, lower for a short window)")
    ap.add_argument("--min-pairs", type=int, default=30,
                    help="fewest edge pairs before the clock is fitted at all")
    ap.add_argument("--overlay", action="store_true",
                    help="also write a debug PNG (ROI + fiducials + LEDs on a still) to eyeball "
                         "against the physical marker corners; --map writes one automatically")
    ap.add_argument("--all", action="store_true",
                    help="batch mode: fit EVERY bundle under the given root "
                         "(or the tracked recordings roots when none is given); "
                         "skips already-fit bundles unless --refit")
    ap.add_argument("--refit", action="store_true",
                    help="with --all, re-fit bundles that already carry a clock fit")
    ap.add_argument("--selftest", action="store_true", help="run the synthetic self-check and exit")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    if a.all:
        from tmaze import paths
        if a.bundle:
            roots: List[str] = [a.bundle]
        else:
            roots = [str(p) for p in paths.phase3_recordings_roots()]
        minutes = None if a.minutes <= 0 else a.minutes
        log_dir = roots[0] if roots and os.path.isdir(roots[0]) else None
        log_path = (os.path.join(log_dir,
                    f"sync_fit_all_{datetime.now():%Y%m%d_%H%M%S}.log")
                    if log_dir else None)
        return run_all(roots, refit=a.refit, log_path=log_path,
                       start_min=a.start_min, minutes=minutes,
                       tol_s=a.tol_ms / 1000.0,
                       min_visible_s=a.min_visible_ms / 1000.0,
                       min_matched=a.min_matched, min_pairs=a.min_pairs,
                       serial_path=a.serial)

    if not a.bundle:
        ap.error("a bundle folder is required (or use --all / --selftest)")
    if not os.path.isdir(a.bundle):
        ap.error(f"not a folder: {a.bundle}")

    minutes = None if a.minutes <= 0 else a.minutes
    rect = tuple(a.board_rect) if a.board_rect else None
    try:
        run = analyze_bundle(a.bundle, start_min=a.start_min, minutes=minutes,
                             tol_s=a.tol_ms / 1000.0, min_visible_s=a.min_visible_ms / 1000.0,
                             whole_frame=a.map and rect is None, board_rect=rect,
                             min_matched=a.min_matched, min_pairs=a.min_pairs,
                             serial_path=a.serial)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if a.map:
        m = build_led_map(run)
        path = LM.save(m, a.map_out or LM.led_map_path())
        bound = sum(1 for e in m.leds if e.bound)
        fc = _fiducial_color_counts(run.fiducials)
        print(f"\nsession {run.sid}: {len(m.fiducials)} fiducials "
              f"({fc.get('red', 0)} red, {fc.get('green', 0)} green), "
              f"{len(m.leds)} LEDs, {bound} bound, board_rect={m.board_rect}")
        for e in m.leds:
            tag = f"{e.channel} {e.polarity}" if e.bound else "(unbound)"
            print(f"  {e.led_id:>10} at ({e.ref_px[0]:.1f},{e.ref_px[1]:.1f})  {tag}")
        print(f"\n  wrote {path}")
        overlay_path = os.path.splitext(path)[0] + f"_overlay_{run.sid}.png"
        if write_overlay(overlay_path, run):
            print(f"  wrote {overlay_path}  (check the ROI lands inside the marker corners)")
        return 0

    if a.dry_run:
        out_dir = a.out or os.path.join(a.bundle, "_sync_dryrun")
    else:
        out_dir = os.path.join(a.bundle, "sync")
    os.makedirs(out_dir, exist_ok=True)
    events_path = os.path.join(out_dir, f"led_events_{run.sid}.csv")
    fit_path = os.path.join(out_dir, f"clock_fit_{run.sid}.json")
    write_led_events(events_path, led_event_rows(run), run.sid)
    write_clock_fit(fit_path, run)
    print_report(run, out_dir)
    if a.overlay:
        overlay_path = os.path.join(out_dir, f"led_overlay_{run.sid}.png")
        if write_overlay(overlay_path, run):
            print(f"  wrote {overlay_path}")
        plot_path = os.path.join(out_dir, f"clock_fit_{run.sid}.png")
        if write_fit_plot(plot_path, run):
            print(f"  wrote {plot_path}")
    if a.dry_run:
        print("  (dry run: the bundle + its manifest were not modified)")
    else:
        manifest_path = register_sync_in_manifest(a.bundle, run,
                                                  good=_fit_is_good(run))
        if manifest_path:
            print(f"  registered sync streams + fiducial.method=led-fit in {manifest_path}")
        else:
            print("  (no session manifest found - sync/ written but not registered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
