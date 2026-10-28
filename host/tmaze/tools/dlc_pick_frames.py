"""
dlc_pick_frames - save the video frames the live DeepLabCut model was least
sure about, for labelling before a retraining (docs/decisions/0231).

    python -m tmaze.tools.dlc_pick_frames --out <dir> --session 600_20261027_152320 [...]
        [--regions well_side returns] [--per-region 5] [--min-gap-s 2]
        [--max-likelihood 0.6]

Per session it reads ``video/<sid>_dlc_head.csv`` (the live head file) and the
video sidecar, keeps frames where the blob tracker was locked on
(``TRACKING``), the head centre's likelihood was below ``--max-likelihood`` and
at least one body part (neck, spine, tail base) was at or above it - the model
sees the rat but not its head - in the asked-for regions (classified from the blob centre with the session's
``config/zones.json``), and picks the lowest ones, at least ``--min-gap-s``
apart. It writes ``<out>/<sid>/img<frame>.png`` (DeepLabCut's labelled-frame
names) and ``<out>/picks.csv``. Nothing in the bundle or the DLC project is
changed: copying the images into the project is part of the retraining.

Reads the video from start to end (~3 min for a 90k-frame session) because
seeking in H.264 is not frame-exact. Needs ``opencv-python``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.core import dlc_frame_pick as P
from tmaze.core.dlc_head import HEAD_PART
from tmaze.core.maze_state import ZoneMap
from tmaze.core.offline_track import find_video

DETAIL_PARTS = ("nose", "right_ear", "left_ear", HEAD_PART, "neck", "mid_spine_neck")


def read_table(path: str) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """(``# key: value`` header, rows) of a sidecar-style CSV."""
    header: Dict[str, str] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        lines = []
        for ln in fh:
            if ln.startswith("#"):
                key, _, val = ln[1:].partition(":")
                header[key.strip()] = val.strip()
            else:
                lines.append(ln)
    return header, list(csv.DictReader(lines))


def _float(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def candidates(sidecar: Sequence[Dict[str, str]], dlc: Sequence[Dict[str, str]],
               zones: ZoneMap, width: int, height: int, regions: Sequence[str],
               cutoff: float = 0.6
               ) -> Tuple[List[P.Candidate], Dict[int, Dict[str, str]]]:
    """Frames that may be picked, and the DLC row of each (by frame_idx)."""
    keys = P.unwrap_block_ids(int(r["cam_block_id"]) for r in dlc)
    by_key: Dict[int, Dict[str, str]] = {}
    for k, r in zip(keys, dlc):
        by_key.setdefault(k, r)
    side_keys = P.unwrap_block_ids(int(r["cam_block_id"]) for r in sidecar)
    out: List[P.Candidate] = []
    rows: Dict[int, Dict[str, str]] = {}
    for k, s in zip(side_keys, sidecar):
        d = by_key.get(k)
        if d is None or s.get("track_state") != "TRACKING" or d.get("dlc_status") not in ("ok", "low"):
            continue
        cx, cy, lk = _float(s.get("track_cx")), _float(s.get("track_cy")), \
            _float(d.get(f"{HEAD_PART}_likelihood"))
        body = [_float(d.get(f"{b}_likelihood")) for b in P.BODY_PARTS]
        if cx is None or cy is None or not P.head_unsure_body_seen(lk, body, cutoff):
            continue
        region = P.region_of(zones.classify_point(cx / width, cy / height))
        if region not in regions:
            continue
        f = int(s["frame_idx"])
        out.append(P.Candidate(f, region, lk))
        rows[f] = d
    return out, rows


def save_frames(video: str, frames: Sequence[int], folder: str, n_frames: int) -> List[int]:
    """Write the wanted frames as PNGs; return the ones found in the video."""
    import cv2
    os.makedirs(folder, exist_ok=True)
    want, done = set(frames), []
    cap = cv2.VideoCapture(video)
    try:
        i, last = 0, max(frames) if frames else -1
        while i <= last:
            ok = cap.grab()
            if not ok:
                break
            if i in want:
                ok, img = cap.retrieve()
                if ok:
                    cv2.imwrite(os.path.join(folder, P.image_name(i, n_frames)), img)
                    done.append(i)
            i += 1
    finally:
        cap.release()
    return done


def pick_session(bundle: str, sid: str, regions: Sequence[str], per_region: int,
                 min_gap_s: float, max_likelihood: float):
    """(picks, DLC rows by frame, frame count, fps) for one session."""
    v = os.path.join(bundle, "video")
    header, sidecar = read_table(os.path.join(v, sid + ".csv"))
    _, dlc = read_table(os.path.join(v, sid + "_dlc_head.csv"))
    with open(os.path.join(bundle, "config", "zones.json"), encoding="utf-8") as fh:
        zones = ZoneMap.from_dict(json.load(fh))
    fps = float(header.get("fps") or 25.0)
    cands, rows = candidates(sidecar, dlc, zones, int(header.get("width", 1280)),
                             int(header.get("height", 1024)), regions, max_likelihood)
    picks = P.pick(cands, per_region, max(1, int(round(min_gap_s * fps))))
    return picks, rows, len(sidecar), fps


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="folder for <sid>/img*.png + picks.csv")
    ap.add_argument("--session", action="append", required=True, help="session id (repeatable)")
    ap.add_argument("--regions", nargs="+", default=["well_side", "returns"],
                    choices=sorted(P.REGIONS))
    ap.add_argument("--per-region", type=int, default=5, help="frames per region per session")
    ap.add_argument("--min-gap-s", type=float, default=2.0)
    ap.add_argument("--max-likelihood", type=float, default=0.6)
    a = ap.parse_args(argv)

    from tmaze.io.derivatives_io import session_bundles
    bundles = session_bundles()
    os.makedirs(a.out, exist_ok=True)
    out_rows = []
    for sid in a.session:
        bundle = bundles.get(sid)
        video = find_video(bundle, sid) if bundle else None
        if not video or not os.path.isfile(os.path.join(bundle, "video", sid + "_dlc_head.csv")):
            print(f"{sid}: no bundle / video / _dlc_head.csv - skipped")
            continue
        picks, rows, n, fps = pick_session(bundle, sid, a.regions, a.per_region,
                                           a.min_gap_s, a.max_likelihood)
        saved = set(save_frames(video, [p.frame_idx for p in picks], os.path.join(a.out, sid), n))
        for p in picks:
            if p.frame_idx not in saved:
                continue
            d = rows[p.frame_idx]
            out_rows.append({"session": sid, "frame_idx": p.frame_idx,
                             "image": f"{sid}/{P.image_name(p.frame_idx, n)}",
                             "t_s": round(p.frame_idx / fps, 2), "region": p.region,
                             **{f"{q}_likelihood": d.get(f"{q}_likelihood", "")
                                for q in DETAIL_PARTS}})
        by = {r: sum(1 for p in picks if p.region == r) for r in a.regions}
        print(f"{sid}: {len(saved)} frames saved ({', '.join(f'{r} {k}' for r, k in by.items())})")
    path = os.path.join(a.out, "picks.csv")
    if out_rows:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)
        print(f"{len(out_rows)} frames listed in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
