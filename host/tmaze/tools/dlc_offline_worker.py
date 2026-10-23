"""
dlc_offline_worker - runs DeepLabCut on ONE video (docs/decisions/0227, 0229).

Started by ``tmaze.tools.dlc_offline`` in its own process, in the
``DEEPLABCUT`` env, so the parent can stop it cleanly when the rig is needed.
Not meant to be run by hand. It imports only the standard library,
DeepLabCut and the pure ``tmaze.core.dlc_best``, because the ``DEEPLABCUT``
env does not have the rig's other dependencies.

    python -m tmaze.tools.dlc_offline_worker JOB.json

JOB.json: ``{"video", "dest", "dlc_config", "shuffle", "snapshot_index",
"device", "extra_site_packages"}``. Runs DeepLabCut's pose step only (no
tracking / stitching), which leaves its per-frame candidates in
``dest/<stem>DLC..._assemblies.pickle``; then writes ``dest/<stem>.csv`` (the
best candidate on each frame, DeepLabCut's standard CSV layout) and
``dest/worker_info.json`` (software versions, frame counts), and exits 0.
When no frame has any body part found it writes only the info file, with
``"no_animal": true``, and still exits 0. Any other failure exits non-zero.

A job with ``"assemblies"`` (a candidates pickle from an earlier run of the same
model) instead of ``"video"`` skips the analysis and only builds the CSV - how
``dlc_offline --adopt`` takes over earlier results.
"""

from __future__ import annotations

import glob
import json
import os
import pickle
import sys

from tmaze.core import dlc_best

ASSEMBLIES_SUFFIX = "_assemblies.pickle"


def main(job_path: str) -> int:
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    extra = job.get("extra_site_packages")
    if extra and os.path.isdir(extra):
        sys.path.insert(0, extra)

    import deeplabcut
    import torch
    from deeplabcut.utils import auxiliaryfunctions

    dest = job["dest"]
    if job.get("assemblies"):
        path = job["assemblies"]
        stem = os.path.basename(path).split("DLC", 1)[0]
    else:
        video = job["video"]
        stem = os.path.splitext(os.path.basename(video))[0]
        print(f"torch {torch.__version__}, cuda {torch.cuda.is_available()}", flush=True)
        deeplabcut.analyze_videos(
            job["dlc_config"], [video], shuffle=int(job["shuffle"]),
            snapshot_index=job["snapshot_index"], device=job["device"],
            destfolder=dest, auto_track=False)
        found = sorted(glob.glob(os.path.join(dest, f"{stem}DLC*{ASSEMBLIES_SUFFIX}")))
        if not found:
            print(f"no {ASSEMBLIES_SUFFIX} was written", flush=True)
            return 2
        path = found[-1]

    bodyparts = auxiliaryfunctions.read_config(job["dlc_config"])["multianimalbodyparts"]
    with open(path, "rb") as fh:
        raw = pickle.load(fh)
    frames = {k: v.tolist() for k, v in raw.items() if isinstance(k, int)}
    n_frames = max(frames) + 1 if frames else 0
    rows = dlc_best.track(frames, n_frames, len(bodyparts))
    found_frames = dlc_best.found_frames(rows)
    info = {"assemblies": os.path.basename(path), "rows": n_frames, "found_frames": found_frames}
    if found_frames == 0:
        print("no animal found on any frame", flush=True)
        info["no_animal"] = True
    else:
        scorer = os.path.basename(path)[len(stem):-len(ASSEMBLIES_SUFFIX)]
        with open(os.path.join(dest, stem + ".csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write(dlc_best.dlc_csv(scorer, stem.split("_")[0], bodyparts, rows))
    _write_info(dest, deeplabcut, torch, info)
    return 0


def _write_info(dest: str, deeplabcut, torch, extra: dict) -> None:
    info = {
        "deeplabcut": getattr(deeplabcut, "__version__", ""),
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
    }
    info.update(extra)
    with open(os.path.join(dest, "worker_info.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
