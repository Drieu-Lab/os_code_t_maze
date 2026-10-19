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
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.core import head_eval as HE
from tmaze.core import offline_track as OT
from tmaze.paths import host_dir

LABEL_CSV_GLOB = "CollectedData_*.csv"
SPINE_GROW_PX = 6   # how far (proc px) the "light" spine mask may reach past the blob
WARMUP_FRAMES = 50  # ~2 s at 25 fps: enough for the Kalman lock and the motion cue

# Overlay colours (BGR).
_COL_LABEL = (0, 255, 0)
_COL_COM = (0, 0, 255)
_COL_CAND = (255, 200, 0)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tmaze.tools.head_point_eval",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--dlc-project", required=True, help="DeepLabCut project folder")
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


def _light_mask(frame, background, params, roi_norm):
    """The tracker's foreground mask WITHOUT the morphological open step.

    Mirrors RatTracker.process() up to the threshold (downscale, grey, blur,
    absolute difference), then only a CLOSE, then the ROI."""
    import cv2
    import numpy as np

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

    diff = cv2.absdiff(prep(frame), prep(background))
    _, m = cv2.threshold(diff, params.thresh, 255, cv2.THRESH_BINARY)
    if params.morph >= 3:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (params.morph, params.morph))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    if roi_norm:
        ph, pw = m.shape[:2]
        roi = np.zeros_like(m)
        pts = np.array([[int(round(x * pw)), int(round(y * ph))] for x, y in roi_norm],
                       np.int32)
        cv2.fillPoly(roi, [pts], 255)
        m = cv2.bitwise_and(m, roi)
    return m


SPINE_MASKS = ("mask", "light")


def _spine_masks(result, frame, background, tracker):
    """``(masks by name, CoM in proc px, proc scale)`` for one tracked frame, or
    None when the tracker chose no blob."""
    from tmaze.vision import head_point as HP

    if not result.detected or result.centroid is None or not result.chosen_members:
        return None
    params = tracker.get_params()
    scale = min(1.0, params.proc_max_w / float(frame.shape[1]))
    members = result.chosen_members
    masks = {"mask": HP.blob_mask_from_contours(members, result.mask.shape[:2]),
             "light": HP.spine_mask(_light_mask(frame, background, params,
                                                tracker.get_roi_polygon()),
                                    members, grow_px=SPINE_GROW_PX)}
    com = result.centroid
    return masks, (com[0] * scale, com[1] * scale), scale


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
                masks, com_p, scale = prepared
                add(f"{rule}/{mname}", HP.estimate_head(masks[mname], com_p, rule=rule), scale)
            if tracked_heads is not None:
                est = tracked_heads[(rule, mname)] if prepared is not None else None
                add(f"track:{rule}/{mname}", est, prepared[2] if prepared else 1.0)
    return out


def _update_head_trackers(head_trackers, prepared) -> Dict[Tuple[str, str], object]:
    """Feed one frame to every HeadTracker; returns their estimates by key."""
    out = {}
    for (rule, mname), ht in head_trackers.items():
        if prepared is None:
            out[(rule, mname)] = ht.update(None, None)
        else:
            masks, com_p, _ = prepared
            out[(rule, mname)] = ht.update(masks[mname], com_p)
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
    tracked_heads), or None if the video ends early. Warns when the decoded
    target frame does not match DeepLabCut's extracted PNG."""
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
    return frame, result, prepared, tracked_heads


def _run_config(label: str, sets: Sequence[str], sessions: List[Tuple[str, str, str]],
                overlay_dir: Optional[str], n_overlays: int, warmup: int) -> List[HE.Score]:
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
                frame, result, prepared, tracked_heads = tracked
            else:
                if png is None:
                    continue
                frame = png
                OTR.install_background(tracker, background)
                result = tracker.process(frame)
                prepared = _spine_masks(result, frame, background, tracker)
                tracked_heads = None
            cands = _candidates(result, prepared, tracked_heads)
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
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
    scores: List[HE.Score] = []
    for i, (label, sets) in enumerate(configs):
        scores += _run_config(label, sets, sessions, overlay_dir if i == 0 else None,
                              args.overlays, args.warmup_frames)

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
