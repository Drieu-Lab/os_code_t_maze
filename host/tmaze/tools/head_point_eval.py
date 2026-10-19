"""
head_point_eval.py - score candidate "head point" estimators against DeepLabCut labels.

For every hand-labelled image in a DeepLabCut project's ``labeled-data/<session>/``
folder, re-runs the live rat tracker as the matching rig session had it (its
snapshotted model, tracker settings, ROI and background - the same resolution
``tmaze.tools.offline_track`` uses), then places each candidate point:

  * ``com``                               - the tracker's centre of mass (today's output)
  * ``<rule>/<spine mask>/<variant>``     - tmaze.vision.head_point, where
      rule       = half_mean | thin_end      (which end is the head)
      spine mask = mask  (the tracker's own cleaned mask)
                 | light (+ nearby pixels the cleanup OPEN step removed)
      variant    = edge  (spine end pushed to the blob edge)
                 | far   (farthest blob pixel along the body axis)

and scores it against the labelled head centre (mean of nose + ears), the neck,
and whether it landed on the wrong (tail) end. Optionally repeats the whole run
at other thresholds (``--thresh``). See tmaze.core.head_eval for the scoring.

Outputs go to ``--out`` (default: <recordings parent>/_head_point_eval):
    head_point_scores.csv     one row per (config, candidate, image)
    head_point_summary.txt    the comparison table(s)
    overlays/<session>_<img>.png   a crop of the first few frames per session
                                   with the label head centre and each point drawn

Run (env ``drieu_t_maze``, any directory):
    python -m tmaze.tools.head_point_eval --dlc-project Z:\\Ravi\\dlc\\drieu_rat_model-Ravi-2026-10-07 \\
        --recordings-root Z:\\Ravi\\phase3_recordings --thresh 8 19

WHOLE-VIDEO mode (``--predictions <folder>``): instead of the labelled images,
tracks every frame of each session that has a DeepLabCut prediction CSV in the
folder (file name starting with the session id; DeepLabCut's standard CSV,
parts below ``--min-likelihood`` ignored) and scores a short list of candidates
against the predicted head centre frame by frame, adding how often the point
jumps, how much it jitters while the head is still, and how much wrong-end time
falls in stretches of 1 s or more (see tmaze.core.head_eval.VideoScorer). A
``dlc`` row scores the predictions themselves for steadiness. Writes
``head_point_video_summary.txt`` and ``<session>_video_points.csv``:
    python -m tmaze.tools.head_point_eval --predictions <folder> \\
        --recordings-root Z:\\Ravi\\phase3_recordings
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from tmaze.core import head_eval as HE
from tmaze.core import offline_track as OT
from tmaze.paths import host_dir

LABEL_CSV_GLOB = "CollectedData_*.csv"
WARMUP_FRAMES = 50  # ~2 s at 25 fps: enough for the Kalman lock and the motion cue
MIN_LIKELIHOOD = 0.6  # DeepLabCut's usual plotting cut-off (pcutoff)
JUMP_PX = 40.0        # a frame-to-frame move this large (~9 cm) is a flip or glitch
VIDEO_CANDIDATES = ("com", "thin_end/light/far", "half_mean/light/far",
                    "track:thin_end/light/far", "track:thin_end/light/edge",
                    "track:half_mean/light/far", "track:thin_end/mask/far",
                    "track:thin_end/light/far+com")

# Overlay colours (BGR).
_COL_LABEL = (0, 255, 0)
_COL_COM = (0, 0, 255)
_COL_CAND = (255, 200, 0)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tmaze.tools.head_point_eval",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--dlc-project", help="DeepLabCut project folder (labelled-image mode)")
    p.add_argument("--predictions",
                   help="folder of DeepLabCut prediction CSVs (whole-video mode)")
    p.add_argument("--min-likelihood", type=float, default=MIN_LIKELIHOOD,
                   help="whole-video mode: ignore predicted parts below this likelihood")
    p.add_argument("--max-frames", type=int, default=0,
                   help="whole-video mode: stop after this many frames (0 = all)")
    p.add_argument("--models", help="folder with trained head_ridge.json / head_net.onnx "
                                    "(adds the ridge / net candidates)")
    p.add_argument("--recordings-root", required=True,
                   help="folder holding the matching rig session bundles")
    p.add_argument("--out", default=None, help="output folder")
    p.add_argument("--thresh", type=int, nargs="*", default=[],
                   help="extra thresholds to evaluate besides each session's recorded one")
    p.add_argument("--sessions", nargs="*", default=None, help="only these session ids")
    p.add_argument("--overlays", type=int, default=3,
                   help="annotated crops per session (recorded settings only; 0 = none)")
    p.add_argument("--warmup-frames", type=int, default=WARMUP_FRAMES,
                   help="frames tracked from the video before each labelled frame "
                        "(0 = single still image, no motion/continuity cues)")
    p.add_argument("--only", nargs="*", default=None,
                   help="report only candidates containing one of these substrings")
    return p


class Prepared(NamedTuple):
    """One tracked frame's inputs for the head-point candidates (proc px)."""
    masks: Dict[str, object]       # spine masks by name (SPINE_MASKS)
    com: Tuple[float, float]       # tracker CoM in proc px
    scale: float                   # proc px per full-frame px
    grey: object                   # blurred proc grey frame
    diff: object                   # |grey - background grey|


def proc_images(frame, background, params, roi_norm):
    """``(grey, diff, light mask)`` at the tracker's proc resolution.

    Mirrors RatTracker.process() up to the threshold (downscale, grey, blur,
    absolute difference); the light mask then gets only a CLOSE (no OPEN) and
    the ROI."""
    import cv2
    import numpy as np
    from tmaze.vision import head_learned as HL

    def prep(img):
        h, w = img.shape[:2]
        s = min(1.0, params.proc_max_w / float(w))
        if s < 1.0:
            img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                             interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if params.blur >= 3 and params.blur % 2 == 1:
            g = cv2.GaussianBlur(g, (params.blur, params.blur), 0)
        return g

    grey = prep(frame)
    diff = cv2.absdiff(grey, prep(background))
    roi = None
    if roi_norm:
        ph, pw = diff.shape[:2]
        roi = np.zeros_like(diff)
        pts = np.array([[int(round(x * pw)), int(round(y * ph))] for x, y in roi_norm],
                       np.int32)
        cv2.fillPoly(roi, [pts], 255)
    return grey, diff, HL.light_mask(diff, params.thresh, params.morph, roi)


SPINE_MASKS = ("mask", "light")


def _spine_masks(result, frame, background, tracker) -> Optional[Prepared]:
    """The Prepared inputs for one tracked frame, or None when the tracker
    chose no blob."""
    from tmaze.vision import head_learned as HL
    from tmaze.vision import head_point as HP

    if not result.detected or result.centroid is None or not result.chosen_members:
        return None
    params = tracker.get_params()
    scale = min(1.0, params.proc_max_w / float(frame.shape[1]))
    members = result.chosen_members
    grey, diff, light = proc_images(frame, background, params, tracker.get_roi_polygon())
    masks = {"mask": HP.blob_mask_from_contours(members, result.mask.shape[:2]),
             "light": HP.spine_mask(light, members, grow_px=HL.LIGHT_GROW_PX)}
    com = result.centroid
    return Prepared(masks, (com[0] * scale, com[1] * scale), scale, grey, diff)


def _make_head_trackers():
    from tmaze.vision import head_point as HP
    return {(r, m): HP.HeadTracker(rule=r) for r in HP.RULES for m in SPINE_MASKS}


def _candidates(result, prepared, tracked_heads=None
                ) -> Dict[str, Tuple[Optional[HE.Point], bool]]:
    """``{candidate name: (full-frame point or None, valid)}`` for one frame.

    ``prepared`` is _spine_masks()'s output. ``tracked_heads`` (this frame's
    HeadTracker estimates, from _update_head_trackers) adds the ``track:``
    candidates. Every head candidate also gets a ``+com`` twin that falls back
    to the CoM when it is not valid."""
    from tmaze.vision import head_point as HP

    out: Dict[str, Tuple[Optional[HE.Point], bool]] = {}
    com = result.centroid if prepared is not None else None
    out["com"] = (com, com is not None)

    def add(name, est, scale):
        for variant, pt in (("edge", est.head if est else None),
                            ("far", est.head_far if est else None)):
            full = None if pt is None else (pt[0] / scale, pt[1] / scale)
            valid = bool(est is not None and est.valid and full is not None)
            out[f"{name}/{variant}"] = (full, valid)
            out[f"{name}/{variant}+com"] = (full if valid else com, com is not None)

    for rule in HP.RULES:
        for mname in SPINE_MASKS:
            if prepared is None:
                add(f"{rule}/{mname}", None, 1.0)
            else:
                add(f"{rule}/{mname}",
                    HP.estimate_head(prepared.masks[mname], prepared.com, rule=rule),
                    prepared.scale)
            if tracked_heads is not None:
                est = tracked_heads[(rule, mname)] if prepared is not None else None
                add(f"track:{rule}/{mname}", est, prepared.scale if prepared else 1.0)
    return out


LEARNED_KEY = ("thin_end", "light")   # the HeadTracker whose estimate feeds the ridge
BODY_AXIS = "body axis (com -> track)"  # direction baseline: CoM -> that tracker's head


class Learned:
    """The trained models in ``--models`` (``head_ridge.json`` / ``head_net.onnx``,
    either may be missing) as extra ``ridge`` / ``net`` candidates."""

    def __init__(self, models_dir: str):
        from tmaze.vision import head_learned as HL

        ridge = os.path.join(models_dir, "head_ridge.json")
        net = os.path.join(models_dir, "head_net.onnx")
        self.ridge = HL.RidgeHead.load(ridge) if os.path.exists(ridge) else None
        self.net = HL.HeadNet(net) if os.path.exists(net) else None
        if self.ridge is None and self.net is None:
            raise SystemExit(f"no head_ridge.json or head_net.onnx in {models_dir}")

    def names(self) -> List[str]:
        return [n for n, m in (("ridge", self.ridge), ("net", self.net)) if m is not None]

    def candidates(self, prepared: Optional[Prepared], est, velocity
                   ) -> Tuple[Dict[str, Tuple[Optional[HE.Point], bool]],
                              Dict[str, Optional[float]]]:
        """(head-centre points in full-frame px, head directions in degrees) per model."""
        from tmaze.vision import head_learned as HL

        if prepared is None:
            return ({n: (None, False) for n in self.names()},
                    {n: None for n in self.names()})
        s = prepared.scale
        points, dirs = {}, {}
        if self.ridge is not None:
            off = self.ridge.predict(HL.shape_features(est, prepared.com, velocity))
            head = off[HL.TARGETS.index("head")]
            points["ridge"] = (((prepared.com[0] + head[0]) / s,
                                (prepared.com[1] + head[1]) / s), True)
            dirs["ridge"] = HL.head_direction_deg(off)
        if self.net is not None:
            cx, cy = HL.crop_centre(prepared.com)
            off = self.net.predict(HL.crop_input(prepared.grey, prepared.diff,
                                                 prepared.masks["light"], prepared.com))
            head = off[HL.TARGETS.index("head")]
            points["net"] = (((cx + head[0]) / s, (cy + head[1]) / s), True)
            dirs["net"] = HL.head_direction_deg(off)
        return points, dirs


def _update_head_trackers(head_trackers, prepared) -> Dict[Tuple[str, str], object]:
    """Feed one frame to every HeadTracker; returns their estimates by key."""
    out = {}
    for (rule, mname), ht in head_trackers.items():
        if prepared is None:
            out[(rule, mname)] = ht.update(None, None)
        else:
            out[(rule, mname)] = ht.update(prepared.masks[mname], prepared.com)
    return out


def _draw_overlay(path: str, frame, parts, cands) -> None:
    import cv2
    pts = [p for p in parts.values()] + [c[0] for c in cands.values() if c[0] is not None]
    if not pts:
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    pad = 40
    x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
    x1 = min(frame.shape[1], int(max(xs)) + pad)
    y1 = min(frame.shape[0], int(max(ys)) + pad)
    img = frame.copy()
    for name, p in parts.items():
        cv2.circle(img, (int(p[0]), int(p[1])), 2, (180, 180, 180), -1)
    hc = HE.head_centre(parts)
    if hc is not None:
        cv2.drawMarker(img, (int(hc[0]), int(hc[1])), _COL_LABEL, cv2.MARKER_CROSS, 12, 2)
    for name, tag in (("com", "com"), ("thin_end/light/edge", "shape"),
                      ("track:thin_end/light/edge", "track")):
        p = cands.get(name, (None, False))[0]
        if p is None:
            continue
        col = _COL_COM if name == "com" else _COL_CAND
        cv2.circle(img, (int(p[0]), int(p[1])), 4, col, 2)
        cv2.putText(img, tag, (int(p[0]) + 6, int(p[1]) - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.35, col, 1, cv2.LINE_AA)
    crop = cv2.resize(img[y0:y1, x0:x1], None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(path, crop)


def _cm_per_px(bundle: str) -> Optional[float]:
    path = os.path.join(bundle, "config", "rig_scale.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return float(json.load(fh)["cm_per_px"])
    except (OSError, KeyError, ValueError, TypeError):
        return None


def _track_to(cap, tracker, background, target: int, warmup: int, label_png):
    """Seek to ``target - warmup``, track forward to ``target`` (resetting the
    tracker + fresh HeadTrackers first). Returns (frame, result, prepared,
    tracked_heads, velocity of the LEARNED_KEY tracker), or None if the video
    ends early. Warns when the decoded target frame does not match
    DeepLabCut's extracted PNG."""
    import cv2
    import numpy as np
    from tmaze.vision import offline_tracker as OTR

    OTR.install_background(tracker, background)
    head_trackers = _make_head_trackers()
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target - warmup))
    frame = result = prepared = tracked_heads = None
    for _ in range(max(0, target - warmup), target + 1):
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        result = tracker.process(frame)
        prepared = _spine_masks(result, frame, background, tracker)
        tracked_heads = _update_head_trackers(head_trackers, prepared)
    if label_png is not None and label_png.shape == frame.shape:
        diff = float(np.abs(frame.astype(np.int16) - label_png.astype(np.int16)).mean())
        if diff > 1.0:
            print(f"    warning: decoded frame {target} differs from the label PNG "
                  f"(mean |diff| {diff:.1f})", file=sys.stderr)
    return frame, result, prepared, tracked_heads, head_trackers[LEARNED_KEY].velocity()


def _run_config(label: str, sets: Sequence[str], sessions: List[Tuple[str, str, str]],
                overlay_dir: Optional[str], n_overlays: int, warmup: int,
                learned: Optional[Learned] = None) -> List[HE.Score]:
    import cv2
    from tmaze.vision import offline_tracker as OTR

    scores: List[HE.Score] = []
    for sid, label_dir, bundle in sessions:
        settings = OT.resolve_settings(bundle, OT.Overrides(sets=tuple(sets)),
                                       ratmodels_dir=str(host_dir() / "ratmodels"))
        tracker, params, _ = OTR.build_tracker(settings)
        background = cv2.imread(settings.background.path, cv2.IMREAD_COLOR) \
            if settings.background.path else None
        if background is None:
            print(f"  {sid}: no background PNG, skipped", file=sys.stderr)
            continue
        label_csv = glob.glob(os.path.join(label_dir, LABEL_CSV_GLOB))[0]
        labels = HE.read_collected_data(label_csv)
        cap = cv2.VideoCapture(settings.video) if warmup > 0 else None
        drawn = 0
        for img_name in sorted(labels):
            png = cv2.imread(os.path.join(label_dir, img_name), cv2.IMREAD_COLOR)
            fidx = HE.frame_index_of(img_name)
            if cap is not None and fidx is not None:
                tracked = _track_to(cap, tracker, background, fidx, warmup, png)
                if tracked is None:
                    continue
                frame, result, prepared, tracked_heads, velocity = tracked
            else:
                if png is None:
                    continue
                frame = png
                OTR.install_background(tracker, background)
                result = tracker.process(frame)
                prepared = _spine_masks(result, frame, background, tracker)
                tracked_heads = None
                velocity = (0.0, 0.0)
            cands = _candidates(result, prepared, tracked_heads)
            if learned is not None:
                est = tracked_heads.get(LEARNED_KEY) if tracked_heads else None
                cands.update(learned.candidates(prepared, est, velocity)[0])
            parts = labels[img_name]
            for name, (pt, valid) in cands.items():
                scores.append(HE.score_point(f"{label}:{name}", sid,
                                             -1 if fidx is None else fidx, pt, valid, parts))
            if overlay_dir and drawn < n_overlays:
                _draw_overlay(os.path.join(overlay_dir, f"{sid}_{img_name}"), frame, parts, cands)
                drawn += 1
        if cap is not None:
            cap.release()
        print(f"  {label} {sid}: {len(labels)} images (thresh={params['thresh']}, "
              f"morph={params['morph']})")
    return scores


def _run_video(sid: str, pred_csv: str, bundle: str, out: str, min_likelihood: float,
               max_frames: int, wanted: Sequence[str],
               learned: Optional[Learned] = None
               ) -> Tuple[List[HE.VideoSummary], List[HE.DirectionSummary]]:
    """Track one whole session video and score ``wanted`` candidates (plus the
    ``dlc`` predictions themselves) frame by frame. With ``learned``, also
    scores head directions (the models' neck -> head centre, and the shape
    tracker's body axis CoM -> head as a baseline) against DeepLabCut's."""
    import time

    import cv2
    from tmaze.vision import offline_tracker as OTR

    preds = HE.read_dlc_predictions(pred_csv, min_likelihood=min_likelihood)
    settings = OT.resolve_settings(bundle, OT.Overrides(),
                                   ratmodels_dir=str(host_dir() / "ratmodels"))
    tracker, _, _ = OTR.build_tracker(settings)
    background = cv2.imread(settings.background.path, cv2.IMREAD_COLOR) \
        if settings.background.path else None
    if background is None:
        print(f"  {sid}: no background PNG, skipped", file=sys.stderr)
        return []
    OTR.install_background(tracker, background)
    cap = cv2.VideoCapture(settings.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if max_frames > 0:
        total = min(total, max_frames)
    names = ["dlc"] + list(wanted) + (learned.names() if learned else [])
    scorers = {n: HE.VideoScorer(n, fps, jump_px=JUMP_PX) for n in names}
    dir_names = (["dlc", BODY_AXIS] + learned.names()) if learned else []
    dir_scorers = {n: HE.DirectionScorer(n, fps) for n in dir_names}
    head_trackers = _make_head_trackers()
    t0 = time.time()
    with open(os.path.join(out, f"{sid}_video_points.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame"] + [f"{n}_{c}" for n in names for c in ("x", "y", "valid")]
                   + [f"{n}_dir_deg" for n in dir_names])
        for frame_i in range(total):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            result = tracker.process(frame)
            prepared = _spine_masks(result, frame, background, tracker)
            tracked_heads = _update_head_trackers(head_trackers, prepared)
            cands = _candidates(result, prepared, tracked_heads)
            parts = preds.get(frame_i, {})
            dirs: Dict[str, Optional[float]] = {}
            if learned is not None:
                est = tracked_heads[LEARNED_KEY]
                points, dirs = learned.candidates(prepared, est,
                                                  head_trackers[LEARNED_KEY].velocity())
                cands.update(points)
                dirs[BODY_AXIS] = HE.direction_deg(
                    prepared.com if prepared is not None else None,
                    est.head_far if est is not None else None)
                dirs["dlc"] = HE.head_direction(parts)
            dlc_head = HE.head_centre(parts)
            cands["dlc"] = (dlc_head, dlc_head is not None)
            row = [frame_i]
            for n in names:
                pt, valid = cands.get(n, (None, False))
                scorers[n].add(frame_i, pt, valid, parts)
                row += (["", ""] if pt is None else [f"{pt[0]:.1f}", f"{pt[1]:.1f}"]) \
                    + [int(valid)]
            for n in dir_names:
                dir_scorers[n].add(frame_i, dirs.get(n), dirs.get("dlc"))
                row.append("" if dirs.get(n) is None else f"{dirs[n]:.1f}")
            w.writerow(row)
            if frame_i and frame_i % 5000 == 0:
                rate = frame_i / (time.time() - t0)
                print(f"    {sid}: frame {frame_i}/{total} ({rate:.0f} fps)", flush=True)
    cap.release()
    print(f"  {sid}: {total} frames in {time.time() - t0:.0f} s")
    return [scorers[n].summary() for n in names], [dir_scorers[n].summary() for n in dir_names]


def _main_video(args) -> int:
    out = args.out or os.path.join(os.path.dirname(os.path.normpath(args.recordings_root)),
                                   "_head_point_eval", "video")
    os.makedirs(out, exist_ok=True)
    wanted = [c for c in VIDEO_CANDIDATES
              if not args.only or c == "com" or any(o in c for o in args.only)]
    learned = Learned(args.models) if args.models else None
    text: List[str] = []
    cm = None
    for pred_csv in sorted(glob.glob(os.path.join(args.predictions, "*.csv"))):
        name = os.path.basename(pred_csv)
        bundles = [d for d in glob.glob(os.path.join(args.recordings_root, "*"))
                   if name.startswith(os.path.basename(d)) and OT.is_bundle(d)]
        if not bundles:
            print(f"no rig bundle matches {name}; skipped", file=sys.stderr)
            continue
        bundle = max(bundles, key=len)
        sid = os.path.basename(bundle)
        if args.sessions and sid not in args.sessions:
            continue
        summaries, dir_summaries = _run_video(sid, pred_csv, bundle, out, args.min_likelihood,
                                              args.max_frames, wanted, learned)
        cm = cm or _cm_per_px(bundle)
        text += [f"{sid}  (likelihood >= {args.min_likelihood}, jump > {JUMP_PX:.0f} px)",
                 HE.format_video_table(summaries, px_to_cm=cm), ""]
        if dir_summaries:
            text += ["head direction, neck -> head centre (degrees; vs DeepLabCut's; "
                     "turn = > 90 deg between frames)",
                     HE.format_direction_table(dir_summaries), ""]
    if not text:
        print("no prediction CSV with a matching rig bundle", file=sys.stderr)
        return 1
    report = "\n".join(text)
    with open(os.path.join(out, "head_point_video_summary.txt"), "w", encoding="utf-8") as fh:
        fh.write(report)
    print(report)
    print(f"wrote {out}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.predictions:
        return _main_video(args)
    if not args.dlc_project:
        _parser().error("give --dlc-project (labelled images) or --predictions (whole videos)")
    label_root = os.path.join(args.dlc_project, "labeled-data")
    sessions: List[Tuple[str, str, str]] = []
    for d in sorted(glob.glob(os.path.join(label_root, "*"))):
        sid = os.path.basename(d)
        if sid.endswith("_labeled") or not glob.glob(os.path.join(d, LABEL_CSV_GLOB)):
            continue
        if args.sessions and sid not in args.sessions:
            continue
        bundle = os.path.join(args.recordings_root, sid)
        if not OT.is_bundle(bundle):
            print(f"no rig bundle for {sid} under {args.recordings_root}; skipped",
                  file=sys.stderr)
            continue
        sessions.append((sid, d, bundle))
    if not sessions:
        print("no labelled sessions with a matching rig bundle", file=sys.stderr)
        return 1

    out = args.out or os.path.join(os.path.dirname(os.path.normpath(args.recordings_root)),
                                   "_head_point_eval")
    overlay_dir = os.path.join(out, "overlays") if args.overlays > 0 else None
    os.makedirs(overlay_dir or out, exist_ok=True)

    configs = [("recorded", [])] + [(f"thresh{t}", [f"thresh={t}"]) for t in args.thresh]
    learned = Learned(args.models) if args.models else None
    scores: List[HE.Score] = []
    for i, (label, sets) in enumerate(configs):
        scores += _run_config(label, sets, sessions, overlay_dir if i == 0 else None,
                              args.overlays, args.warmup_frames, learned)

    with open(os.path.join(out, "head_point_scores.csv"), "w", newline="",
              encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["candidate", "session", "frame", "x", "y", "valid",
                    "d_head_px", "d_neck_px", "wrong_end"])
        for s in scores:
            w.writerow([s.candidate, s.session, s.frame,
                        "" if s.point is None else f"{s.point[0]:.1f}",
                        "" if s.point is None else f"{s.point[1]:.1f}", int(s.valid),
                        "" if s.d_head is None else f"{s.d_head:.1f}",
                        "" if s.d_neck is None else f"{s.d_neck:.1f}",
                        "" if s.wrong_end is None else int(s.wrong_end)])

    cm = _cm_per_px(sessions[0][2])
    summaries = HE.summarise(scores)
    if args.only:
        summaries = [s for s in summaries
                     if s.candidate.endswith(":com") or any(o in s.candidate for o in args.only)]
    text = [HE.format_table(summaries, px_to_cm=cm), "",
            "Median distance to the head centre per session (px):"]
    sids = [s[0] for s in sessions]
    text.append(f"{'candidate':34s} " + " ".join(f"{s[:3] + s[-6:]:>10s}" for s in sids))
    for s in summaries:
        text.append(f"{s.candidate:34s} " + " ".join(
            f"{'-' if s.per_group.get(x) is None else f'{s.per_group[x]:.0f}':>10s}"
            for x in sids))
    report = "\n".join(text)
    with open(os.path.join(out, "head_point_summary.txt"), "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
