"""
offline_track.py - re-track recorded session videos with the live rat tracker.

Runs the same ``RatTracker`` the experiment GUI uses over a session bundle's
recorded video, EVERY frame, and writes two new files beside the video (the
original sidecar is never touched):

    video/<id>_offline_track[_<tag>].csv    one row per frame (sidecar-style)
    video/<id>_offline_track[_<tag>].json   what was used + per-state counts

By default it replays the session's own setup from its ``config/`` snapshot
(model, ROI, landmarks, tracker settings) and its ``video/<id>_background.png``.
Missing pieces fall back by rule: the current shared rat model, a per-pixel
median background computed from the video. The current blocker model is loaded
unless ``--no-blocker``. Every slider/tolerance from the live Tracking tab can be
changed with ``--set key=value`` or ``--params-file``; ``--list-params`` lists
them. See docs/decisions/0211.

Run (env ``drieu_t_maze``, any directory):

    python -m tmaze.tools.offline_track <bundle_dir>
    python -m tmaze.tools.offline_track <bundle_dir> --tag thresh25 --set thresh=25
    python -m tmaze.tools.offline_track --root <phase3_recordings> --tag v2
    python -m tmaze.tools.offline_track <bundle_dir> --start-frame 6000 \
        --end-frame 9000 --warmup-frames 300 --out-dir <scratch>
    python -m tmaze.tools.offline_track --list-params

Thin shell: decisions live in ``tmaze.core.offline_track``; pixels in
``tmaze.vision.offline_tracker``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import asdict
from typing import List, Optional, Sequence

from tmaze.core import offline_track as OT
from tmaze.paths import host_dir


def ratmodels_dir() -> str:
    return str(host_dir() / "ratmodels")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tmaze.tools.offline_track",
        description="Re-track recorded session videos with the live rat tracker.")
    p.add_argument("bundles", nargs="*", help="session bundle folder(s)")
    p.add_argument("--root", action="append", default=[],
                   help="also run every bundle directly inside this folder (repeatable)")
    p.add_argument("--tag", default=None,
                   help="name for this run, added to the output file names "
                        "(letters, digits, _ and -)")
    p.add_argument("--out-dir", default=None,
                   help="write outputs here instead of each bundle's video/ folder")
    p.add_argument("--overwrite", action="store_true",
                   help="replace an existing run with the same name")

    f = p.add_argument_group("input files (default: the session's own snapshot)")
    f.add_argument("--model", help="rat appearance model .npz")
    b = f.add_mutually_exclusive_group()
    b.add_argument("--blocker-model", help="blocker model .npz")
    b.add_argument("--no-blocker", action="store_true", help="track without a blocker model")
    f.add_argument("--roi", help="ROI polygon .json")
    f.add_argument("--landmarks", help="landmarks .json")
    g = f.add_mutually_exclusive_group()
    g.add_argument("--background", help="empty-rig background .png")
    g.add_argument("--background-from-video", action="store_true",
                   help="compute a median background from the video even if a PNG exists")
    f.add_argument("--save-background", metavar="PATH",
                   help="also write the background used to this PNG")

    s = p.add_argument_group("tracker settings (applied over the session's snapshot)")
    s.add_argument("--params-file", help="JSON of settings (flat, or a runtime_params.json)")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="change one setting (repeatable), e.g. --set thresh=25")
    s.add_argument("--list-params", action="store_true",
                   help="list every tracker setting with its default, then exit")

    r = p.add_argument_group("frame range")
    r.add_argument("--start-frame", type=int, default=0, help="first frame to write")
    r.add_argument("--end-frame", type=int, default=None,
                   help="last frame to write (inclusive; default: end of video)")
    r.add_argument("--warmup-frames", type=int, default=0,
                   help="frames before --start-frame to track but not write, so the "
                        "tracker is already locked on")
    return p


def list_params() -> int:
    from tmaze.vision.tracker import TrackerParams
    for k, v in asdict(TrackerParams()).items():
        print(f"{k:28s} {type(v).__name__:6s} {v}")
    return 0


def _progress(sid: str):
    # Rate between callbacks, so skipping to --start-frame doesn't inflate it.
    prev = {"idx": 0, "t": time.monotonic()}

    def cb(idx: int, last: Optional[int]) -> None:
        now = time.monotonic()
        rate = (idx - prev["idx"]) / max(1e-6, now - prev["t"])
        prev.update(idx=idx, t=now)
        end = f"/{last + 1}" if last is not None else ""
        print(f"  {sid}: frame {idx}{end} ({rate:.0f} frames/s)", flush=True)
    return cb


def run_one(bundle: str, args: argparse.Namespace) -> bool:
    from tmaze.vision import offline_tracker as OTR
    sid = OT.session_id_of(bundle)
    try:
        overrides = OT.Overrides(
            model=args.model, blocker_model=args.blocker_model,
            no_blocker=args.no_blocker, roi=args.roi, landmarks=args.landmarks,
            background=args.background,
            background_from_video=args.background_from_video,
            params_file=args.params_file, sets=tuple(args.set))
        settings = OT.resolve_settings(bundle, overrides, ratmodels_dir=ratmodels_dir())
        out_dir = args.out_dir or os.path.join(bundle, "video")
        csv_path, json_path = OT.output_paths(out_dir, sid, args.tag)
        OT.check_overwrite((csv_path, json_path), args.overwrite)
        print(f"{sid}: tracking -> {csv_path}", flush=True)
        for note in settings.notes:
            print(f"  note: {note}")
        summary = OTR.run(settings, csv_path=csv_path, json_path=json_path,
                          tag=args.tag, start_frame=args.start_frame,
                          end_frame=args.end_frame, warmup_frames=args.warmup_frames,
                          save_background=args.save_background,
                          progress=_progress(sid))
    except (OSError, ValueError, RuntimeError) as e:
        print(f"{sid}: SKIPPED - {e}", file=sys.stderr)
        return False
    fr = summary["state_fractions"]
    states = ", ".join(f"{k} {v:.0%}" for k, v in fr.items())
    print(f"{sid}: done, {summary['frames_written']} frames in "
          f"{summary['elapsed_s']:.0f} s ({states})")
    return True


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.list_params:
        return list_params()
    try:
        OT.validate_tag(args.tag)
        OT.frame_range(args.start_frame, args.end_frame, args.warmup_frames)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    bundles: List[str] = []
    for b in args.bundles:
        if not OT.is_bundle(b):
            print(f"error: not a session bundle (no video/<id>.mp4|.mkv): {b}",
                  file=sys.stderr)
            return 2
        bundles.append(b)
    for root in args.root:
        found = OT.find_bundles(root)
        if not found:
            print(f"warning: no bundles under {root}", file=sys.stderr)
        bundles.extend(found)
    if not bundles:
        print("error: give at least one bundle or --root (see --help)", file=sys.stderr)
        return 2
    if args.save_background and len(bundles) > 1:
        print("error: --save-background needs a single bundle", file=sys.stderr)
        return 2
    failed = [b for b in bundles if not run_one(b, args)]
    if len(bundles) > 1:
        print(f"{len(bundles) - len(failed)}/{len(bundles)} bundles tracked")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
