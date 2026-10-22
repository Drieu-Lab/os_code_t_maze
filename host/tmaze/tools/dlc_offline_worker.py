"""
dlc_offline_worker - runs DeepLabCut on ONE video (docs/decisions/0227).

Started by ``tmaze.tools.dlc_offline`` in its own process, in the
``DEEPLABCUT`` env, so the parent can stop it cleanly when the rig is needed.
Not meant to be run by hand. It imports only the standard library and
DeepLabCut, because the ``DEEPLABCUT`` env does not have the rig's other
dependencies.

    python -m tmaze.tools.dlc_offline_worker JOB.json

JOB.json: ``{"video", "dest", "dlc_config", "shuffle", "snapshot_index",
"device", "n_tracks", "extra_site_packages"}``. Writes DeepLabCut's own files
into ``dest``, then ``dest/<video stem>.csv`` (the stitched track in
DeepLabCut's standard CSV layout) and ``dest/worker_info.json`` (software
versions), and exits 0. Any failure exits non-zero.

A job with ``"h5"`` (a stitched ``_sk.h5`` from an earlier run) instead of
``"video"`` skips the analysis and only writes ``dest/<stem>.csv`` + the
info file - how ``dlc_offline --adopt`` takes over earlier results.
"""

from __future__ import annotations

import glob
import json
import os
import sys


def main(job_path: str) -> int:
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    extra = job.get("extra_site_packages")
    if extra and os.path.isdir(extra):
        sys.path.insert(0, extra)

    import deeplabcut
    import pandas as pd
    import torch

    dest = job["dest"]
    if job.get("h5"):
        h5 = job["h5"]
        stem = os.path.basename(h5).split("DLC", 1)[0]
    else:
        video = job["video"]
        stem = os.path.splitext(os.path.basename(video))[0]
        print(f"torch {torch.__version__}, cuda {torch.cuda.is_available()}", flush=True)
        deeplabcut.analyze_videos(
            job["dlc_config"], [video], shuffle=int(job["shuffle"]),
            snapshot_index=job["snapshot_index"], device=job["device"],
            destfolder=dest, auto_track=True, n_tracks=int(job["n_tracks"]))
        h5s = sorted(glob.glob(os.path.join(dest, f"{stem}DLC*_sk.h5")))
        if not h5s:
            print("no stitched _sk.h5 was written", flush=True)
            return 2
        h5 = h5s[-1]
    df = pd.read_hdf(h5)
    df.index.name = None
    df.to_csv(os.path.join(dest, stem + ".csv"))
    info = {
        "deeplabcut": getattr(deeplabcut, "__version__", ""),
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        "h5": os.path.basename(h5),
        "rows": int(len(df)),
    }
    with open(os.path.join(dest, "worker_info.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
