"""
head_point_dataset.py - export samples for the learned head-point models.

Builds the inputs ``tmaze.vision.head_learned`` models see (a 3-channel crop
around the blob, and the ridge's shape features) together with where
DeepLabCut put the head centre, neck, tail base, nose and ears, by re-tracking rig
sessions with the same settings resolution as ``tmaze.tools.head_point_eval``:

  * TRAIN (``--predictions <folder>``): for each session with a DeepLabCut
    prediction CSV in the folder (file name starting with the session id; or
    a ``dlc_offline`` pipeline folder such as Z:\\Ravi\\derivatives\\dlc_best_iter1),
    tracks the whole video and keeps every ``--stride``-th frame on which the
    tracker has a blob and DeepLabCut predicts at least two of nose / right ear
    / left ear above ``--min-likelihood``. With ``--dlc-project`` too, frames
    within ``--exclude-s`` seconds of that session's hand-labelled frames are
    left out, so the labelled frames stay unseen test data.
    -> ``train_<session>.npz``
  * TEST (``--dlc-project``): every hand-labelled frame of every session with
    a rig bundle, tracked from ``--warmup-frames`` before it.
    -> ``test_labelled.npz``

Each .npz holds X (N, 3, 64, 64) uint8 crops, Y (N, T, 2) target offsets from
the crop centre in proc px (NaN = unknown; in ``head_learned.TARGETS`` order), F the
shape features, plus com_p / centre / scale / frame / session and the
HeadTracker's thin_end/light/far point (track_far) for comparison.

Run (env ``drieu_t_maze``, any directory):
    python -m tmaze.tools.head_point_dataset --recordings-root Z:\\Ravi\\phase3_recordings \\
        --predictions <folder> --dlc-project Z:\\Ravi\\dlc\\drieu_rat_model-Ravi-2026-10-07 \\
        --out <dir> [--what train test] [--stride 2]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from typing import Dict, List, Optional, Sequence

from tmaze.core import head_eval as HE
from tmaze.core import offline_track as OT
from tmaze.paths import host_dir
from tmaze.tools import head_point_eval as E

STRIDE = 2           # consecutive frames are near-duplicates
EXCLUDE_S = 2.0      # keep training frames this far from any labelled frame
MIN_HEAD_PARTS = 2   # a training head centre needs at least this many of nose/ears


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tmaze.tools.head_point_dataset",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--recordings-root", required=True)
    p.add_argument("--predictions", help="folder of DeepLabCut prediction CSVs, or a "
                                         "dlc_offline pipeline folder (train)")
    p.add_argument("--dlc-project", help="DeepLabCut project (test set + train exclusion)")
    p.add_argument("--out", required=True)
    p.add_argument("--what", nargs="+", choices=("train", "test"), default=["train", "test"])
    p.add_argument("--sessions", nargs="*", default=None)
    p.add_argument("--stride", type=int, default=STRIDE)
    p.add_argument("--exclude-s", type=float, default=EXCLUDE_S)
    p.add_argument("--min-likelihood", type=float, default=E.MIN_LIKELIHOOD)
    p.add_argument("--warmup-frames", type=int, default=E.WARMUP_FRAMES)
    p.add_argument("--max-frames", type=int, default=0, help="train: stop after N frames")
    return p


class _Collector:
    """Accumulates samples and writes them as one .npz."""

    def __init__(self):
        self.rows: Dict[str, list] = {k: [] for k in (
            "X", "Y", "F", "com_p", "centre", "scale", "frame", "session",
            "track_far", "track_valid")}

    def __len__(self):
        return len(self.rows["X"])

    def add(self, sid: str, frame: int, prepared: "E.Prepared", est, velocity,
            parts: Dict[str, HE.Point]) -> None:
        import numpy as np
        from tmaze.vision import head_learned as HL

        s = prepared.scale
        cx, cy = HL.crop_centre(prepared.com)
        targets = [HE.head_centre(parts) if t == "head" else parts.get(t) for t in HL.TARGETS]
        y = np.full((len(HL.TARGETS), 2), np.nan, np.float32)
        for i, pt in enumerate(targets):
            if pt is not None:
                y[i] = (pt[0] * s - cx, pt[1] * s - cy)
        r = self.rows
        r["X"].append(HL.crop_input(prepared.grey, prepared.diff,
                                    prepared.masks["light"], prepared.com))
        r["Y"].append(y)
        r["F"].append(HL.shape_features(est, prepared.com, velocity).astype(np.float32))
        r["com_p"].append(prepared.com)
        r["centre"].append((cx, cy))
        r["scale"].append(s)
        r["frame"].append(frame)
        r["session"].append(sid)
        far = est.head_far if est is not None else None
        r["track_far"].append((np.nan, np.nan) if far is None else far)
        r["track_valid"].append(bool(est is not None and est.valid))

    def save(self, path: str, meta: dict) -> None:
        import numpy as np
        from tmaze.vision import head_learned as HL

        r = self.rows
        np.savez_compressed(
            path, X=np.array(r["X"], np.uint8), Y=np.array(r["Y"], np.float32),
            F=np.array(r["F"], np.float32), com_p=np.array(r["com_p"], np.float32),
            centre=np.array(r["centre"], np.int32), scale=np.array(r["scale"], np.float32),
            frame=np.array(r["frame"], np.int32), session=np.array(r["session"]),
            track_far=np.array(r["track_far"], np.float32),
            track_valid=np.array(r["track_valid"], bool),
            meta=json.dumps(dict(HL.net_meta(), features=list(HL.FEATURES), **meta)))
        print(f"  wrote {path} ({len(self)} samples)")


def _setup(bundle: str):
    import cv2
    from tmaze.vision import offline_tracker as OTR

    settings = OT.resolve_settings(bundle, OT.Overrides(),
                                   ratmodels_dir=str(host_dir() / "ratmodels"))
    tracker, _, _ = OTR.build_tracker(settings)
    background = cv2.imread(settings.background.path, cv2.IMREAD_COLOR) \
        if settings.background.path else None
    return settings, tracker, background


def _labelled_frames(dlc_project: Optional[str], sid: str) -> List[int]:
    if not dlc_project:
        return []
    paths = glob.glob(os.path.join(dlc_project, "labeled-data", sid, E.LABEL_CSV_GLOB))
    if not paths:
        return []
    return sorted(f for f in (HE.frame_index_of(n) for n in HE.read_collected_data(paths[0]))
                  if f is not None)


def _train_session(sid: str, pred_csv: str, bundle: str, args) -> None:
    import bisect

    import cv2
    from tmaze.vision import head_point as HP
    from tmaze.vision import offline_tracker as OTR

    preds = HE.read_dlc_predictions(pred_csv, min_likelihood=args.min_likelihood)
    settings, tracker, background = _setup(bundle)
    if background is None:
        print(f"  {sid}: no background PNG, skipped", file=sys.stderr)
        return
    OTR.install_background(tracker, background)
    cap = cv2.VideoCapture(settings.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if args.max_frames > 0:
        total = min(total, args.max_frames)
    labelled = _labelled_frames(args.dlc_project, sid)
    margin = int(round(args.exclude_s * fps))
    ht = HP.HeadTracker(rule=HP.RULE_THIN_END)
    col = _Collector()
    skipped_near_label = 0
    t0 = time.time()
    for f in range(total):
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        result = tracker.process(frame)
        prepared = E._spine_masks(result, frame, background, tracker)
        est = ht.update(prepared.masks["light"], prepared.com) if prepared else ht.update(None, None)
        if prepared is None or f % args.stride:
            continue
        parts = preds.get(f, {})
        if sum(1 for p in HE.HEAD_PARTS if p in parts) < MIN_HEAD_PARTS:
            continue
        i = bisect.bisect_left(labelled, f - margin)
        if i < len(labelled) and labelled[i] <= f + margin:
            skipped_near_label += 1
            continue
        col.add(sid, f, prepared, est, ht.velocity(), parts)
        if f and f % 10000 == 0:
            print(f"    {sid}: frame {f}/{total} ({f / (time.time() - t0):.0f} fps, "
                  f"{len(col)} samples)", flush=True)
    cap.release()
    print(f"  {sid}: {len(col)} samples ({skipped_near_label} dropped near labelled frames)")
    col.save(os.path.join(args.out, f"train_{sid}.npz"),
             {"kind": "train", "stride": args.stride, "exclude_s": args.exclude_s,
              "min_likelihood": args.min_likelihood, "predictions": os.path.basename(pred_csv)})


def _test_set(args) -> None:
    import cv2

    col = _Collector()
    label_root = os.path.join(args.dlc_project, "labeled-data")
    for d in sorted(glob.glob(os.path.join(label_root, "*"))):
        sid = os.path.basename(d)
        csvs = glob.glob(os.path.join(d, E.LABEL_CSV_GLOB))
        bundle = os.path.join(args.recordings_root, sid)
        if sid.endswith("_labeled") or not csvs or not OT.is_bundle(bundle):
            continue
        if args.sessions and sid not in args.sessions:
            continue
        settings, tracker, background = _setup(bundle)
        if background is None:
            continue
        labels = HE.read_collected_data(csvs[0])
        cap = cv2.VideoCapture(settings.video)
        n0 = len(col)
        for img_name in sorted(labels):
            fidx = HE.frame_index_of(img_name)
            if fidx is None:
                continue
            tracked = _track_one(cap, tracker, background, fidx, args.warmup_frames)
            if tracked is None:
                continue
            prepared, est, velocity = tracked
            col.add(sid, fidx, prepared, est, velocity, labels[img_name])
        cap.release()
        print(f"  test {sid}: {len(col) - n0} of {len(labels)} labelled frames")
    col.save(os.path.join(args.out, "test_labelled.npz"),
             {"kind": "test", "warmup_frames": args.warmup_frames})


def _track_one(cap, tracker, background, target: int, warmup: int):
    """Track ``warmup`` frames up to ``target``; (Prepared, estimate, velocity) or None."""
    import cv2
    from tmaze.vision import head_point as HP
    from tmaze.vision import offline_tracker as OTR

    OTR.install_background(tracker, background)
    ht = HP.HeadTracker(rule=HP.RULE_THIN_END)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target - warmup))
    prepared = est = None
    for _ in range(max(0, target - warmup), target + 1):
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        result = tracker.process(frame)
        prepared = E._spine_masks(result, frame, background, tracker)
        est = ht.update(prepared.masks["light"], prepared.com) if prepared else ht.update(None, None)
    if prepared is None:
        return None
    return prepared, est, ht.velocity()


def prediction_csvs(folder: str) -> List[str]:
    """The prediction CSVs in ``folder``: loose ``*.csv`` files, plus - when it
    is a ``dlc_offline`` pipeline folder - each finished session's
    ``<session>/<session>_dlc.csv`` (docs/decisions/0226)."""
    from tmaze.io import derivatives_io as DIO

    out = glob.glob(os.path.join(folder, "*.csv"))
    for csv in glob.glob(os.path.join(folder, "*", "*_dlc.csv")):
        if DIO.read_manifest(os.path.dirname(csv)) is not None:
            out.append(csv)
    return sorted(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    if "test" in args.what:
        if not args.dlc_project:
            _parser().error("the test set needs --dlc-project")
        _test_set(args)
    if "train" in args.what:
        if not args.predictions:
            _parser().error("the training set needs --predictions")
        for pred_csv in prediction_csvs(args.predictions):
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
            _train_session(sid, pred_csv, bundle, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
