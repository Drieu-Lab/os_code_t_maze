"""
train_head_net.py - train the learned head-point models on head_point_dataset output.

Fits both models of ``tmaze.vision.head_learned`` on the ``train_*.npz`` files
in ``--data`` and scores them on ``test_labelled.npz`` (the hand-labelled
DeepLabCut frames, never trained on):

  * the RIDGE regression on the shape features (penalty picked on the
    validation part from RIDGE_LAMBDAS) -> ``head_ridge.json``;
  * the tiny NETWORK on the image crops -> ``head_net.onnx`` + ``head_net.json``
    (the crop settings it was trained with, checked by HeadNet at load time).

The last ``--val-frac`` of each training session (by frame number, so it is
time-separated from the rest) is held out for validation. Training uses random
flips and quarter turns of each crop with the targets transformed to match, and
a loss that skips unknown targets (NaN); a mirrored crop swaps the ear labels.
Test distances are reported in full-frame px, like ``head_point_eval``, and the
head direction (neck -> head centre) as an angle error in degrees.

Runs in the DEEPLABCUT conda env (needs ``torch`` + ``onnx``; the rig env does
not have them), from the ``host/`` folder so ``tmaze`` is importable without
installing it:
    cd host
    python -m tmaze.tools.train_head_net --data <dataset dir> --out <model dir> [--epochs 25]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import sys
import time
from typing import Dict, Optional, Sequence

import numpy as np

from tmaze.vision import head_learned as HL

RIDGE_LAMBDAS = (0.1, 1.0, 10.0, 100.0, 1000.0)
HEAD_WEIGHT = 2.0   # the head centre matters most; the other targets also teach it the body axis


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tmaze.tools.train_head_net",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--data", required=True, help="folder with train_*.npz + test_labelled.npz")
    p.add_argument("--out", required=True, help="folder for the trained models")
    p.add_argument("--epochs", type=int, default=25)
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--lr", type=float, default=2e-3)
    p.add_argument("--val-frac", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = default)")
    return p


def _load(paths: Sequence[str]) -> Dict[str, np.ndarray]:
    parts = [dict(np.load(p, allow_pickle=False)) for p in paths]
    for p, d in zip(paths, parts):
        meta = json.loads(str(d["meta"]))
        want = HL.net_meta()
        if {k: meta.get(k) for k in want} != want:
            sys.exit(f"{p}: built with crop settings {meta}, this code expects {want}")
    keys = [k for k in parts[0] if k != "meta"]
    return {k: np.concatenate([d[k] for d in parts]) for k in keys}


def _split_val(d: Dict[str, np.ndarray], frac: float):
    val = np.zeros(len(d["frame"]), bool)
    for s in np.unique(d["session"]):
        m = d["session"] == s
        cut = np.quantile(d["frame"][m], 1.0 - frac)
        val |= m & (d["frame"] > cut)
    return ~val, val


def _ridge_targets(d) -> np.ndarray:
    """Offsets from the exact CoM (the dataset's Y is from the integer crop centre)."""
    return d["Y"] + (d["centre"] - d["com_p"])[:, None, :]


def _full_px_errors(pred_from_centre: np.ndarray, d) -> Dict[str, np.ndarray]:
    """Head distance and wrong-end flag per sample, in full-frame px."""
    err = np.linalg.norm(pred_from_centre[:, 0] - d["Y"][:, 0], axis=1) / d["scale"]
    to_neck = np.linalg.norm(pred_from_centre[:, 0] - d["Y"][:, 1], axis=1)
    to_tail = np.linalg.norm(pred_from_centre[:, 0] - d["Y"][:, 2], axis=1)
    known = ~(np.isnan(to_neck) | np.isnan(to_tail))
    return {"head": err, "wrong": np.where(known, to_tail < to_neck, np.nan)}


def _report(name: str, pred_from_centre: np.ndarray, d) -> dict:
    e = _full_px_errors(pred_from_centre, d)
    h = e["head"][~np.isnan(e["head"])]
    w = e["wrong"][~np.isnan(e["wrong"])]
    row = {"candidate": name, "n": int(len(h)), "head_median_px": float(np.median(h)),
           "head_p90_px": float(np.quantile(h, 0.9)), "wrong_end": float(w.mean()) if len(w) else None}
    per = {}
    for s in np.unique(d["session"]):
        m = (d["session"] == s) & ~np.isnan(e["head"])
        per[str(s)] = float(np.median(e["head"][m])) if m.any() else None
    row["per_session_median_px"] = per
    wrong = "-" if row["wrong_end"] is None else "%.0f%%" % (100 * row["wrong_end"])
    print(f"  {name:26s} n={row['n']:4d}  head median {row['head_median_px']:6.1f} px  "
          f"p90 {row['head_p90_px']:6.1f}  wrong end {wrong}")
    return row


def _directions(points: np.ndarray, to: str = "head") -> np.ndarray:
    """Neck -> ``to`` direction (degrees, image axes) per sample of (N, T, 2); NaN if unknown."""
    out = [HL.head_direction_deg(p, to=to) for p in points]
    return np.array([np.nan if a is None else a for a in out])


def _direction_report(name: str, pred_deg: np.ndarray, d, to: str = "head") -> dict:
    """Angle between a predicted direction and the labelled neck -> ``to`` direction."""
    true = _directions(d["Y"], to)
    m = ~(np.isnan(true) | np.isnan(pred_deg))
    err = np.array([HL.angle_diff_deg(a, b) for a, b in zip(pred_deg[m], true[m])])
    row = {"candidate": name, "n": int(m.sum()), "dir_median_deg": float(np.median(err)),
           "dir_p90_deg": float(np.quantile(err, 0.9)), "dir_over_45": float((err > 45).mean())}
    print(f"  {name:26s} n={row['n']:4d}  direction error median {row['dir_median_deg']:5.1f} deg  "
          f"p90 {row['dir_p90_deg']:5.1f}  > 45 deg {100 * row['dir_over_45']:.0f}%")
    return row


def _fit_ridge(tr, va):
    best = None
    ytr, yva = _ridge_targets(tr), _ridge_targets(va)
    for lam in RIDGE_LAMBDAS:
        model = HL.RidgeHead.fit(tr["F"].astype(np.float64), ytr, lam=lam)
        err = np.linalg.norm(model.predict(va["F"].astype(np.float64))[:, 0] - yva[:, 0], axis=1)
        med = float(np.nanmedian(err))
        print(f"  ridge lambda={lam:<7g} val head median {med:.1f} proc px")
        if best is None or med < best[0]:
            best = (med, lam, model)
    return best[2]


def _build_net(torch):
    nn = torch.nn

    def block(cin, cout, stride):
        return [nn.Conv2d(cin, cout, 3, stride=stride, padding=1, bias=False),
                nn.BatchNorm2d(cout), nn.ReLU(inplace=True)]

    layers = (block(3, 16, 1) + block(16, 32, 2) + block(32, 32, 1) + block(32, 64, 2)
              + block(64, 64, 1) + block(64, 64, 2) + block(64, 128, 2))
    return nn.Sequential(*layers, nn.Flatten(), nn.Linear(128 * 4 * 4, 128),
                         nn.ReLU(inplace=True), nn.Linear(128, 2 * len(HL.TARGETS)))


def _augment(torch, x, y):
    """Random flips / transpose of a batch (x: B,3,H,W; y: B,T,2 normalised offsets).
    Each of the three is a mirror; after an odd number of them the left / right
    targets (MIRROR_PAIRS) trade places."""
    mirrors = 0
    if torch.rand(1).item() < 0.5:
        x = x.flip(3)
        y = y * torch.tensor([-1.0, 1.0])
        mirrors += 1
    if torch.rand(1).item() < 0.5:
        x = x.flip(2)
        y = y * torch.tensor([1.0, -1.0])
        mirrors += 1
    if torch.rand(1).item() < 0.5:
        x = x.transpose(2, 3)
        y = y.flip(2)
        mirrors += 1
    if mirrors % 2:
        y = y[:, list(HL.mirror_order())]
    return x, y


def _train_net(torch, tr, va, args):
    net = _build_net(torch)
    xtr = torch.from_numpy(tr["X"])
    ytr = torch.from_numpy(tr["Y"] / HL.CROP_HALF_PX)
    weights = torch.tensor([HEAD_WEIGHT] + [1.0] * (len(HL.TARGETS) - 1))[None, :, None]
    opt = torch.optim.AdamW(net.parameters(), lr=args.lr, weight_decay=1e-4)
    steps = args.epochs * ((len(xtr) + args.batch - 1) // args.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=steps)
    loss_fn = torch.nn.SmoothL1Loss(reduction="none", beta=0.05)
    for epoch in range(args.epochs):
        net.train()
        t0 = time.time()
        perm = torch.randperm(len(xtr))
        total = 0.0
        for i in range(0, len(perm), args.batch):
            idx = perm[i:i + args.batch]
            x = xtr[idx].float() / 255.0
            y = ytr[idx]
            x, y = _augment(torch, x, y)
            known = ~torch.isnan(y)
            out = net(x).view(-1, len(HL.TARGETS), 2)
            loss = loss_fn(out, torch.nan_to_num(y)) * known * weights
            loss = loss.sum() / known.sum().clamp(min=1)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            total += loss.item() * len(idx)
        val = _predict(torch, net, va["X"])
        err = np.linalg.norm(val[:, 0] - va["Y"][:, 0], axis=1)
        print(f"  epoch {epoch + 1:2d}/{args.epochs}  train loss {total / len(perm):.4f}  "
              f"val head median {np.nanmedian(err):.1f} proc px  ({time.time() - t0:.0f} s)",
              flush=True)
    return net


def _predict(torch, net, X: np.ndarray, batch: int = 1024) -> np.ndarray:
    net.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), batch):
            x = torch.from_numpy(X[i:i + batch]).float() / 255.0
            out.append(net(x).view(-1, len(HL.TARGETS), 2).numpy())
    return np.concatenate(out) * HL.CROP_HALF_PX


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    import torch

    torch.manual_seed(args.seed)
    if args.threads > 0:
        torch.set_num_threads(args.threads)
    train_paths = sorted(glob.glob(os.path.join(args.data, "train_*.npz")))
    test_path = os.path.join(args.data, "test_labelled.npz")
    if not train_paths or not os.path.exists(test_path):
        sys.exit(f"need train_*.npz and test_labelled.npz in {args.data}")
    os.makedirs(args.out, exist_ok=True)
    data = _load(train_paths)
    test = _load([test_path])
    keep, val = _split_val(data, args.val_frac)
    tr = {k: v[keep] for k, v in data.items()}
    va = {k: v[val] for k, v in data.items()}
    print(f"train {len(tr['X'])} / val {len(va['X'])} samples from "
          f"{sorted(set(map(str, data['session'])))}; test {len(test['X'])} labelled frames")

    print("ridge:")
    ridge = _fit_ridge(tr, va)
    ridge.meta.update(trained=_dt.date.today().isoformat(),
                      sessions=sorted(set(map(str, data["session"]))))
    ridge.save(os.path.join(args.out, "head_ridge.json"))

    print("network:")
    net = _train_net(torch, tr, va, args)

    print("test (hand-labelled frames, full-frame px):")
    shift = (test["com_p"] - test["centre"])[:, None, :]
    zero = np.zeros_like(test["Y"]) + shift
    far = np.where(np.isnan(test["track_far"]), test["com_p"], test["track_far"])
    track = np.repeat((far - test["centre"])[:, None, :], len(HL.TARGETS), 1)
    ridge_test = ridge.predict(test["F"].astype(np.float64)) + shift
    net_test = _predict(torch, net, test["X"])
    rows = [_report("com", zero, test), _report("track:thin_end/light/far+com", track, test),
            _report("ridge", ridge_test, test), _report("net", net_test, test)]
    print("head direction (neck -> head centre) vs the labels:")
    body = np.degrees(np.arctan2(test["track_far"][:, 1] - test["com_p"][:, 1],
                                 test["track_far"][:, 0] - test["com_p"][:, 0]))
    net_dir = _directions(net_test)
    rows += [_direction_report("body axis (com -> track)", body, test),
             _direction_report("ridge", _directions(ridge_test), test),
             _direction_report("net", net_dir, test),
             _direction_report("net vs labelled neck->nose", net_dir, test, to="nose")]

    onnx_path = os.path.join(args.out, "head_net.onnx")
    net.eval()
    torch.onnx.export(net, torch.zeros(1, 3, HL.INPUT_PX, HL.INPUT_PX), onnx_path,
                      dynamo=False, opset_version=13, input_names=["crop"],
                      output_names=["offsets"], dynamic_axes={"crop": {0: "n"}, "offsets": {0: "n"}})
    meta = dict(HL.net_meta(), kind="net", trained=_dt.date.today().isoformat(),
                sessions=sorted(set(map(str, data["session"]))), n_train=int(len(tr["X"])),
                epochs=args.epochs, lr=args.lr, batch=args.batch, val_frac=args.val_frac,
                seed=args.seed, test=rows)
    with open(os.path.join(args.out, "head_net.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    np.save(os.path.join(args.out, "test_pred_torch.npy"), _predict(torch, net, test["X"]))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
