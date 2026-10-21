"""
export_dlc_live.py - package a trained DeepLabCut model for the DLC-Live helper.

DLC-Live loads one file holding the model's settings (``pytorch_config.yaml``)
and its weights (the ``snapshot-*.pt`` from training). This writes that file
without touching the shared DeepLabCut project. Run it in the ``dlclivegui``
env (it needs torch + yaml), from host/:

  $env:PYTHONPATH = (Get-Location).Path
  C:\\Users\\Celine\\anaconda3\\envs\\dlclivegui\\python.exe -m tmaze.tools.export_dlc_live

The default is the iteration-1 full-size model (shuffle 1, snapshot-best-140),
written to host/head_models/dlc/ (git-ignored: ~115 MB). It refuses a config
whose body-part links were pruned: DeepLabCut's evaluate step keeps only some
of them, and a pruned model loses the head in live use. See docs/decisions/0224.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

from tmaze.paths import host_dir

DEFAULT_TRAIN = (r"Z:\Ravi\dlc\drieu_rat_model-Ravi-2026-10-07\dlc-models-pytorch"
                 r"\iteration-1\drieu_rat_modelOct7-trainset95shuffle1\train")
DEFAULT_SNAPSHOT = "snapshot-best-140"


def default_out(train_dir: str, snapshot: str) -> str:
    parts = os.path.normpath(train_dir).split(os.sep)
    iteration = next((p for p in parts if p.startswith("iteration-")), "iteration-x")
    shuffle = next((p.split("shuffle")[-1] for p in parts if "shuffle" in p), "x")
    name = f"{iteration.replace('-', '')}_shuffle{shuffle}_{snapshot}_dlclive.pt"
    return str(host_dir() / "head_models" / "dlc" / name)


def check_edges(cfg: dict) -> None:
    pred = cfg["model"]["heads"]["bodypart"]["predictor"]
    graph, keep = pred.get("graph", []), pred.get("edges_to_keep")
    if keep is not None and len(keep) != len(graph):
        raise ValueError(
            f"the config keeps only {len(keep)} of {len(graph)} body-part links "
            "(DeepLabCut's evaluate step prunes them). Restore edges_to_keep to all "
            "links in pytorch_config.yaml before exporting.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    import torch
    import yaml

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--train-dir", default=DEFAULT_TRAIN)
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help="name without .pt")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    with open(os.path.join(a.train_dir, "pytorch_config.yaml")) as fh:
        cfg = yaml.safe_load(fh)
    check_edges(cfg)
    snap = torch.load(os.path.join(a.train_dir, a.snapshot + ".pt"), map_location="cpu",
                      weights_only=True)
    out = a.out or default_out(a.train_dir, a.snapshot)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    torch.save({"config": cfg, "pose": snap["model"]}, out)
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.0f} MB; "
          f"{len(cfg['metadata']['bodyparts'])} body parts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
