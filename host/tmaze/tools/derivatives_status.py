"""
derivatives_status - what the derived-data folder holds, for every pipeline
(docs/decisions/0226).

For each pipeline folder under the derivatives root it counts the sessions
that are ``done``, ``stale`` (the bundle changed since), ``partial`` (a run was
interrupted) and have ``no result`` yet. It needs no pipeline-specific code:
each manifest says which inputs it used, and those are re-checked against the
bundles as they are now. Read-only.

    python -m tmaze.tools.derivatives_status
    python -m tmaze.tools.derivatives_status --pipeline dlc_full_iter1 --list stale

"No result" counts every session bundle; a pipeline that only applies to some
sessions (e.g. DLC needs a video) reports its own pending list
(``python -m tmaze.tools.dlc_offline --dry-run``).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Sequence

from tmaze.core import derivatives as D
from tmaze.io import derivatives_io as DIO
from tmaze.paths import derivatives_root

NO_RESULT = "no result"


def pipeline_states(root: str, pipeline: str, bundles: Dict[str, str]) -> Dict[str, str]:
    """{session_id: state} over every session bundle plus any result folder
    whose bundle is no longer found (shown as stale)."""
    pdir = D.pipeline_dir(root, pipeline)
    try:
        folders = {n for n in os.listdir(pdir)
                   if not n.startswith("_") and os.path.isdir(os.path.join(pdir, n))}
    except OSError:
        folders = set()
    out: Dict[str, str] = {}
    for sid in sorted(set(bundles) | folders):
        if sid not in folders:
            out[sid] = NO_RESULT
            continue
        manifest = DIO.read_manifest(os.path.join(pdir, sid))
        if manifest is None or D.manifest_problems(manifest, pipeline, sid):
            out[sid] = D.STATE_PARTIAL
            continue
        now = DIO.current_inputs_like(manifest["inputs"], bundles.get(sid), root)
        out[sid] = D.classify(True, manifest, pipeline, sid, now)
    return out


def summary_lines(root: str, states_by_pipeline: Dict[str, Dict[str, str]]) -> List[str]:
    lines = [f"derived data: {root}"]
    if not states_by_pipeline:
        lines.append("  (no pipeline folders yet)")
    for pipe, states in states_by_pipeline.items():
        c = {k: 0 for k in (D.STATE_DONE, D.STATE_STALE, D.STATE_PARTIAL, NO_RESULT)}
        for st in states.values():
            c[st] = c.get(st, 0) + 1
        lines.append(f"  {pipe}: {c[D.STATE_DONE]} done, {c[D.STATE_STALE]} stale, "
                     f"{c[D.STATE_PARTIAL]} partial, {c[NO_RESULT]} no result")
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", help="derived-data root (default: host/derivatives_root.json)")
    ap.add_argument("--pipeline", action="append", help="only this pipeline (repeatable)")
    ap.add_argument("--list", choices=(D.STATE_DONE, D.STATE_STALE, D.STATE_PARTIAL, "none"),
                    help="also list the sessions in this state ('none' = no result)")
    a = ap.parse_args(argv)
    root = a.root or str(derivatives_root())
    bundles = DIO.session_bundles()
    pipes = a.pipeline or DIO.list_pipelines(root)
    by_pipe = {p: pipeline_states(root, p, bundles) for p in pipes}
    print("\n".join(summary_lines(root, by_pipe)))
    if a.list:
        want = NO_RESULT if a.list == "none" else a.list
        for p, states in by_pipe.items():
            ids = sorted((s for s, st in states.items() if st == want),
                         key=D.session_sort_key, reverse=True)
            print(f"\n{p} - {want} ({len(ids)}):")
            for sid in ids:
                print(f"  {sid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
