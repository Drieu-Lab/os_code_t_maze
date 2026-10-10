"""
import_legacy_recordings.py - make pre-bundle recordings trackable offline.

Two steps (see docs/decisions/0217):

1. ``--draft`` scans the recordings root's loose ``<stem>.csv`` / video /
   background files and writes a table, ``legacy_bundles/legacy_recordings.csv``,
   proposing for each recording: include or not (and why), a session id
   ``<rat>_<date>_<time>`` (``unk_...`` when the rat is not in the file name),
   the rat model, and how sure that model choice is. Review and edit it.
2. Without ``--draft`` it builds one bundle per included row under
   ``legacy_bundles/<session_id>/``: hard links to the original video, sidecar
   and background in ``video/`` (no extra disk; the originals are untouched),
   copies of the chosen model and the rig's ROI / landmarks / zones in
   ``config/``, and ``config/legacy_import.json`` recording where each came
   from. A bundle folder that already exists is left alone.

Then track them with ``python -m tmaze.tools.offline_track --root <legacy_bundles>``.

Run (env ``drieu_t_maze``, any directory):

    python -m tmaze.tools.import_legacy_recordings --draft
    python -m tmaze.tools.import_legacy_recordings --dry-run
    python -m tmaze.tools.import_legacy_recordings
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from tmaze.core import legacy_import as LI
from tmaze.core.offline_track import read_sidecar_header
from tmaze.core.session.session_config import sha256_file
from tmaze.paths import camera_output_dir, host_dir

OUT_SUBDIR = "legacy_bundles"


def _sidecar_info(path: str) -> LI.SidecarInfo:
    header = read_sidecar_header(path)
    end = None
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.startswith("#")]
    if len(lines) > 1:
        cols = lines[0].strip().split(",")
        try:
            end = float(lines[-1].split(",")[cols.index("host_unix_s")])
        except (ValueError, IndexError):
            end = None
    return LI.SidecarInfo(undistorted=header.get("undistorted", "").lower() == "true",
                          end_unix=end)


def draft(root: str, table: str, models_dir: str, min_bytes: int) -> int:
    if os.path.exists(table):
        print(f"error: {table} exists - edit it, or delete it to draft again", file=sys.stderr)
        return 2
    files = {n: os.path.getsize(os.path.join(root, n)) for n in os.listdir(root)
             if os.path.isfile(os.path.join(root, n))}
    models = {n: os.path.getmtime(os.path.join(models_dir, n))
              for n in os.listdir(models_dir) if n.endswith(".npz")} \
        if os.path.isdir(models_dir) else {}
    rows = [LI.draft_row(rec, _sidecar_info(os.path.join(root, rec.sidecar)), models,
                         min_bytes=min_bytes)
            for rec in LI.find_recordings(files)]
    os.makedirs(os.path.dirname(table), exist_ok=True)
    with open(table, "w", encoding="utf-8", newline="") as fh:
        fh.write(LI.format_table(rows))
    n_in = sum(r["include"] == LI.YES for r in rows)
    print(f"wrote {table}: {len(rows)} recordings, {n_in} proposed for import")
    for r in rows:
        if r["include"] == LI.YES:
            print(f"  {r['session_id']:22s} {r['model'] or '-':40s} {r['model_quality']}")
    return 0


def _background_name(sidecar: str) -> Optional[str]:
    return read_sidecar_header(sidecar).get("background") or None


def build(root: str, out: str, table: str, models_dir: str, *, dry_run: bool) -> int:
    with open(table, encoding="utf-8") as fh:
        rows = LI.parse_table(fh.read())
    errors = LI.validate_table(rows)
    if errors:
        print("error: the table has problems:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 2
    built: List[str] = []
    for row in (r for r in rows if r["include"] == LI.YES):
        bundle = os.path.join(out, row["session_id"])
        if os.path.exists(bundle):
            print(f"{row['session_id']}: exists, left alone")
            continue
        sidecar = os.path.join(root, row["stem"] + ".csv")
        steps = LI.plan_bundle(row, root=root, models_dir=models_dir, host_dir=str(host_dir()),
                               background_name=_background_name(sidecar))
        missing = [s.src for s in steps if not os.path.isfile(s.src)]
        if missing:
            print(f"{row['session_id']}: SKIPPED - missing {', '.join(missing)}", file=sys.stderr)
            continue
        print(f"{row['session_id']}: {'would build' if dry_run else 'building'} from {row['stem']}"
              f" (model {row['model'] or 'fallback'}, {row['model_quality']})")
        if dry_run:
            continue
        hashes: Dict[str, Optional[str]] = {}
        for s in steps:
            dst = os.path.join(bundle, s.dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if s.action == "link":
                os.link(s.src, dst)
            else:
                shutil.copy2(s.src, dst)
                hashes[s.src] = sha256_file(s.src)
        prov = LI.provenance(row, steps, hashes,
                             created=datetime.now().astimezone().isoformat(timespec="seconds"))
        with open(os.path.join(bundle, "config", LI.PROVENANCE_NAME), "w", encoding="utf-8") as fh:
            json.dump(prov, fh, indent=2)
        built.append(row["session_id"])
    if not dry_run:
        print(f"built {len(built)} bundle(s) under {out}")
        if built:
            print(f"next: python -m tmaze.tools.offline_track --root {out}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tmaze.tools.import_legacy_recordings",
                                description="Make pre-bundle recordings trackable offline.")
    p.add_argument("--root", default=None,
                   help="folder holding the loose recordings (default: the rig's recordings folder)")
    p.add_argument("--out", default=None, help=f"bundles folder (default: <root>/{OUT_SUBDIR})")
    p.add_argument("--table", default=None,
                   help=f"the import table (default: <out>/{LI.TABLE_NAME})")
    p.add_argument("--models-dir", default=str(host_dir() / "ratmodels"),
                   help="rat models folder (default: host/ratmodels)")
    p.add_argument("--draft", action="store_true", help="write the table for review, then stop")
    p.add_argument("--min-gb", type=float, default=LI.MIN_VIDEO_BYTES / 1e9,
                   help="draft: propose only videos at least this large (default 1)")
    p.add_argument("--dry-run", action="store_true", help="show what would be built")
    args = p.parse_args(argv)
    root = args.root or str(camera_output_dir())
    out = args.out or os.path.join(root, OUT_SUBDIR)
    table = args.table or os.path.join(out, LI.TABLE_NAME)
    if args.draft:
        return draft(root, table, args.models_dir, int(args.min_gb * 1e9))
    if not os.path.isfile(table):
        print(f"error: no table at {table} - run with --draft first", file=sys.stderr)
        return 2
    return build(root, out, table, args.models_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
