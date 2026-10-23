"""
dlc_offline - DeepLabCut (the full model) over every session bundle's video,
into the derived-data folder (docs/decisions/0226, 0227, 0229).

Each session's result lands in ``<derivatives root>/<pipeline>/<session_id>/``:
``<session_id>_dlc.csv`` (DeepLabCut's standard CSV, one row per video frame;
its first column is the frame index, the same number as the video sidecar's
``frame_idx``; each row is the best-scoring candidate skeleton on that frame),
``raw/`` (DeepLabCut's own pickles, unless ``keep_raw`` is off), and
``manifest.json`` (written last). A video in which DeepLabCut finds no animal
at all gets only the manifest, with ``params.no_animal: true``, so it counts as
done and is not retried.

    python -m tmaze.tools.dlc_offline --dry-run          what would run
    python -m tmaze.tools.dlc_offline                    run everything not done
    python -m tmaze.tools.dlc_offline --limit 1 --session 602_20260916_145627
    python -m tmaze.tools.dlc_offline --stop             ask a running job to stop
    python -m tmaze.tools.dlc_offline --adopt DIR        build from earlier pickles

Runs newest session first, is safe to interrupt (a session stopped half-way
is redone next time), and never runs while the rig GUI
(``tmaze.ui.experiment_control``) is open: it refuses to start, and stops the
current video within ``rig_check_s`` if the GUI is opened. DeepLabCut itself
runs in a separate process in the env named in ``host/dlc_offline.json``.

Exit codes: 0 all fine, 1 some sessions failed, 2 could not start, 3 stopped.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import tmaze
from tmaze.core import derivatives as D
from tmaze.core.offline_track import find_video
from tmaze.io import derivatives_io as DIO
from tmaze.paths import derivatives_root

HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(tmaze.__file__)))
DEFAULT_CONFIG = os.path.join(HOST_DIR, "dlc_offline.json")
RIG_MARKER = "experiment_control"
STOP_NAME = "_stop"
LOGS_NAME = "_logs"
WORK_NAME = "_work"
RAW_KINDS = ("full", "meta", "assemblies")
EXIT_OK, EXIT_FAILED, EXIT_NOSTART, EXIT_STOPPED = 0, 1, 2, 3


@dataclass
class OfflineConfig:
    pipeline: str
    python: str
    dlc_config: str
    extra_site_packages: str = ""
    shuffle: int = 1
    snapshot_index: str = "best"
    device: str = "cuda"
    keep_raw: bool = True
    rig_check_s: float = 30.0
    avoid_cpus: List[int] = field(default_factory=lambda: [10, 11])


def load_config(path: str = DEFAULT_CONFIG) -> OfflineConfig:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    keys = OfflineConfig.__dataclass_fields__
    return OfflineConfig(**{k: v for k, v in raw.items() if k in keys})


# ------------------------------------------------------------ model checks
def project_iteration(config_yaml_text: str) -> Optional[int]:
    m = re.search(r"^iteration:\s*(\d+)", config_yaml_text, re.MULTILINE)
    return int(m.group(1)) if m else None


def pipeline_problem(pipeline: str, iteration: Optional[int]) -> Optional[str]:
    """The pipeline name must end in ``_iter<N>`` for the DLC project's
    current iteration, so results from a retrained model never mix with old ones."""
    if not D.valid_pipeline_name(pipeline):
        return f"not a valid pipeline name: {pipeline!r}"
    m = re.search(r"_iter(\d+)$", pipeline)
    if not m:
        return f"pipeline {pipeline!r} must end in _iter<N>"
    if iteration is None:
        return "could not read 'iteration' from the DLC config.yaml"
    if int(m.group(1)) != iteration:
        return (f"pipeline {pipeline!r} is for iteration {m.group(1)} but the DLC project "
                f"is at iteration {iteration}: name a new pipeline (_iter{iteration})")
    return None


def find_snapshot(dlc_config: str, iteration: int, shuffle: int) -> Optional[str]:
    """The ``snapshot-best-<epoch>.pt`` that ``snapshot_index='best'`` loads."""
    pattern = os.path.join(os.path.dirname(dlc_config), "dlc-models-pytorch",
                           f"iteration-{iteration}", f"*shuffle{shuffle}", "train",
                           "snapshot-best-*.pt")
    found = glob.glob(pattern)

    def epoch(p: str) -> int:
        m = re.search(r"snapshot-best-(\d+)\.pt$", p)
        return int(m.group(1)) if m else -1

    return max(found, key=epoch) if found else None


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_info(cfg: OfflineConfig) -> Tuple[Optional[dict], Optional[str]]:
    """(the manifest's ``model`` block, None) or (None, why it can't run)."""
    try:
        with open(cfg.dlc_config, encoding="utf-8") as fh:
            iteration = project_iteration(fh.read())
    except OSError as e:
        return None, f"cannot read the DLC config: {e}"
    why = pipeline_problem(cfg.pipeline, iteration)
    if why:
        return None, why
    snap = find_snapshot(cfg.dlc_config, iteration, cfg.shuffle)
    if snap is None:
        return None, f"no snapshot-best-*.pt for iteration {iteration}, shuffle {cfg.shuffle}"
    return {"dlc_project": os.path.dirname(cfg.dlc_config), "iteration": iteration,
            "shuffle": cfg.shuffle, "snapshot": os.path.basename(snap),
            "snapshot_sha256": sha256(snap)}, None


# ------------------------------------------------------------- sessions
def dlc_sessions(bundles: Dict[str, str]) -> Dict[str, Tuple[str, str]]:
    """{session_id: (bundle, video)} for the bundles that have a video."""
    out = {}
    for sid, bundle in bundles.items():
        video = find_video(bundle, sid)
        if video:
            out[sid] = (bundle, video)
    return out


def current_inputs(bundle: str, video: str) -> List[dict]:
    return [DIO.stat_file_input("video", video, bundle)]


def sidecar_rows(bundle: str, sid: str) -> Optional[int]:
    """Frames the camera wrote (data rows of ``video/<sid>.csv``)."""
    path = os.path.join(bundle, "video", sid + ".csv")
    try:
        with open(path, encoding="utf-8") as fh:
            n = sum(1 for ln in fh if ln.strip() and not ln.startswith("#"))
    except OSError:
        return None
    return max(n - 1, 0)


def choose(states: Dict[str, str], *, sessions: Sequence[str] = (), include_stale: bool = True,
           limit: Optional[int] = None) -> List[str]:
    include = [D.STATE_PENDING, D.STATE_PARTIAL] + ([D.STATE_STALE] if include_stale else [])
    if sessions:
        wanted = set(sessions)
        states = {s: st for s, st in states.items() if s in wanted}
    todo = D.to_run(states, include)
    return todo[:limit] if limit else todo


# ------------------------------------------------------------ rig guard
def rig_running(procs: Optional[Iterable] = None) -> Optional[int]:
    """The pid of a running rig GUI, else None."""
    import psutil
    me = os.getpid()
    for p in (procs if procs is not None else psutil.process_iter(["pid", "cmdline"])):
        try:
            cmd = p.info.get("cmdline") or []
            if p.info["pid"] != me and any(RIG_MARKER in str(a) for a in cmd):
                return p.info["pid"]
        except (psutil.Error, KeyError, TypeError):
            continue
    return None


def pin_off(pid: int, avoid: Sequence[int]) -> None:
    """Keep a process off the logical processors the rig uses."""
    try:
        import psutil
        cpus = [c for c in range(psutil.cpu_count() or 1) if c not in set(avoid)]
        if cpus:
            psutil.Process(pid).cpu_affinity(cpus)
    except Exception:
        pass


def kill_tree(pid: int) -> None:
    import psutil
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)


# -------------------------------------------------------- per session
def csv_name(sid: str) -> str:
    return f"{sid}_dlc.csv"


def finish(folder: str, sid: str, work: str, keep_raw: bool = True,
           no_animal: bool = False) -> List[dict]:
    """Move the worker's results from ``_work/`` into the session folder under
    their fixed names, keep DeepLabCut's pickles under ``raw/`` (or drop
    them), and return the manifest's ``outputs``."""
    names = []
    if not no_animal:
        csv = os.path.join(work, sid + ".csv")
        if not os.path.isfile(csv):
            raise RuntimeError(f"worker left no {sid}.csv in {work}")
        os.replace(csv, os.path.join(folder, csv_name(sid)))
        names.append(csv_name(sid))
    if keep_raw:
        raw = os.path.join(folder, "raw")
        for p in sorted(glob.glob(os.path.join(work, f"{sid}DLC*"))):
            os.makedirs(raw, exist_ok=True)
            os.replace(p, os.path.join(raw, os.path.basename(p)))
            names.append("raw/" + os.path.basename(p))
    shutil.rmtree(work, ignore_errors=True)
    return [DIO.output_entry(folder, n) for n in names]


def read_worker_info(work: str) -> dict:
    try:
        with open(os.path.join(work, "worker_info.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def launch_worker(cfg: OfflineConfig, job_path: str, log_path: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = HOST_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    log = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen([cfg.python, "-m", "tmaze.tools.dlc_offline_worker", job_path],
                                stdout=log, stderr=subprocess.STDOUT, env=env, cwd=HOST_DIR,
                                creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    finally:
        log.close()
    pin_off(proc.pid, cfg.avoid_cpus)
    return proc


Launcher = Callable[[OfflineConfig, str, str], "subprocess.Popen"]


class Stopped(Exception):
    pass


def run_job(cfg: OfflineConfig, job: dict, work: str, log_path: str, stop_file: str,
            launch: Launcher = launch_worker,
            rig: Callable[[], Optional[int]] = rig_running) -> dict:
    """Run one worker job to the end; return its worker_info. Raises Stopped
    (after killing the worker) on a stop request or when the rig GUI opens."""
    os.makedirs(work, exist_ok=True)
    job_path = os.path.join(work, "job.json")
    with open(job_path, "w", encoding="utf-8") as fh:
        json.dump(job, fh, indent=2)
    proc = launch(cfg, job_path, log_path)
    while True:
        try:
            rc = proc.wait(timeout=cfg.rig_check_s)
            break
        except subprocess.TimeoutExpired:
            pid = None if os.path.exists(stop_file) else rig()
            why = ("stop requested" if os.path.exists(stop_file) else
                   f"rig GUI opened (pid {pid})" if pid else None)
            if why:
                kill_tree(proc.pid)
                raise Stopped(why)
    if rc != 0:
        raise RuntimeError(f"worker exited {rc}; see {log_path}")
    return read_worker_info(work)


def run_session(cfg: OfflineConfig, root: str, sid: str, bundle: str, video: str, model: dict,
                logs: str, stop_file: str, launch: Launcher = launch_worker,
                rig: Callable[[], Optional[int]] = rig_running) -> None:
    folder = D.session_dir(root, cfg.pipeline, sid)
    with DIO.SessionLock(folder):
        DIO.remove_manifest(folder)
        work = os.path.join(folder, WORK_NAME)
        shutil.rmtree(work, ignore_errors=True)
        inputs = current_inputs(bundle, video)
        started = DIO.now_iso()
        job = {"video": video, "dest": work, "dlc_config": cfg.dlc_config,
               "shuffle": cfg.shuffle, "snapshot_index": cfg.snapshot_index,
               "device": cfg.device, "extra_site_packages": cfg.extra_site_packages}
        log_path = os.path.join(logs, f"{sid}_{datetime.now():%Y%m%d_%H%M%S}.log")
        info = run_job(cfg, job, work, log_path, stop_file, launch, rig)
        outputs = finish(folder, sid, work, cfg.keep_raw, bool(info.get("no_animal")))
        DIO.write_manifest(folder, _manifest(cfg, sid, bundle, inputs, outputs, started,
                                             model, info, adopted=False))


def _manifest(cfg: OfflineConfig, sid: str, bundle: str, inputs: List[dict],
              outputs: List[dict], started: str, model: dict, info: dict,
              adopted: bool) -> dict:
    params = {"shuffle": cfg.shuffle, "snapshot_index": cfg.snapshot_index,
              "device": cfg.device, "track": "best_per_frame",
              "frames": info.get("rows"), "found_frames": info.get("found_frames"),
              "sidecar_rows": sidecar_rows(bundle, sid),
              "no_animal": bool(info.get("no_animal"))}
    software = {k: info[k] for k in ("deeplabcut", "torch", "gpu") if info.get(k)}
    return D.new_manifest(cfg.pipeline, sid, inputs=inputs, outputs=outputs,
                          started_at=started, finished_at=DIO.now_iso(), params=params,
                          model=model, software=software, adopted=adopted)


# --------------------------------------------------------------- status
def running_pid(pdir: str, alive: Callable[[int], Optional[bool]] = DIO._process_alive
                ) -> Optional[int]:
    """The pid of the run holding the pipeline lock, if it is still alive (a
    lock from another computer counts as running)."""
    try:
        with open(os.path.join(pdir, D.LOCK_NAME), encoding="utf-8") as fh:
            owner = json.load(fh)
    except (OSError, ValueError):
        return None
    pid = owner.get("pid")
    if not isinstance(pid, int):
        return None
    if owner.get("host") != socket.gethostname():
        return pid
    return pid if alive(pid) else None


def status_text(counts: Dict[str, int], running: Optional[int]) -> str:
    """One line for the Records Manager."""
    todo = counts.get(D.STATE_PENDING, 0) + counts.get(D.STATE_PARTIAL, 0) \
        + counts.get(D.STATE_STALE, 0)
    text = f"DLC: {counts.get(D.STATE_DONE, 0)} done, {todo} to do"
    return text + (f" - running (pid {running})" if running else "")


def status(cfg: OfflineConfig, root: str) -> Tuple[Dict[str, int], Optional[int]]:
    """(state counts over the sessions with video, pid of a running job)."""
    sessions = dlc_sessions(DIO.session_bundles())
    states = DIO.session_states(root, cfg.pipeline,
                                {s: current_inputs(b, v) for s, (b, v) in sessions.items()})
    return D.counts(states), running_pid(D.pipeline_dir(root, cfg.pipeline))


def request_stop(cfg: OfflineConfig, root: str) -> str:
    pdir = D.pipeline_dir(root, cfg.pipeline)
    os.makedirs(pdir, exist_ok=True)
    path = os.path.join(pdir, STOP_NAME)
    open(path, "w").close()
    return path


# ---------------------------------------------------------------- adopt
def adopt_problem(dlc_rows: Optional[int], sidecar: Optional[int],
                  project_video_size: Optional[int], bundle_video_size: int) -> Optional[str]:
    """An earlier result may be adopted only if it is provably of this
    session's video: the DLC project's copy has the same size, or (no copy)
    the frame count equals the sidecar's."""
    if project_video_size is not None:
        return None if project_video_size == bundle_video_size else \
            f"the DLC project's video is {project_video_size} bytes, the bundle's {bundle_video_size}"
    if dlc_rows is not None and sidecar is not None and dlc_rows == sidecar:
        return None
    return f"cannot confirm it is this video (DLC rows {dlc_rows}, sidecar rows {sidecar})"


def adopt_dir(cfg: OfflineConfig, root: str, src: str, sessions: Dict[str, Tuple[str, str]],
              model: dict, logs: str, stop_file: str, launch: Launcher = launch_worker) -> int:
    snap_tag = "snapshot_" + model["snapshot"][len("snapshot-"):-len(".pt")]
    failed = 0
    pattern = f"*DLC*shuffle{cfg.shuffle}_{snap_tag}_assemblies.pickle"
    for asm in sorted(glob.glob(os.path.join(src, pattern))):
        sid = os.path.basename(asm).split("DLC", 1)[0]
        prefix = os.path.basename(asm)[:-len("_assemblies.pickle")]
        if sid not in sessions:
            print(f"  {sid}: no bundle with a video - skipped")
            continue
        bundle, video = sessions[sid]
        folder = D.session_dir(root, cfg.pipeline, sid)
        if DIO.read_manifest(folder) is not None:
            print(f"  {sid}: already has a result - skipped")
            continue
        proj = os.path.join(os.path.dirname(cfg.dlc_config), "videos", os.path.basename(video))
        refused = None
        try:
            with DIO.SessionLock(folder):
                work = os.path.join(folder, WORK_NAME)
                shutil.rmtree(work, ignore_errors=True)
                os.makedirs(work)
                for kind in RAW_KINDS:
                    p = os.path.join(src, f"{prefix}_{kind}.pickle")
                    if os.path.isfile(p):
                        shutil.copyfile(p, os.path.join(work, os.path.basename(p)))
                log_path = os.path.join(logs, f"{sid}_adopt_{datetime.now():%Y%m%d_%H%M%S}.log")
                info = run_job(cfg, {"assemblies": os.path.join(work, os.path.basename(asm)),
                                     "dest": work, "dlc_config": cfg.dlc_config,
                                     "extra_site_packages": cfg.extra_site_packages},
                               work, log_path, stop_file, launch, rig=lambda: None)
                why = adopt_problem(info.get("rows"), sidecar_rows(bundle, sid),
                                    os.path.getsize(proj) if os.path.isfile(proj) else None,
                                    os.path.getsize(video))
                if why:
                    refused = why
                    raise RuntimeError(why)
                started = datetime.fromtimestamp(os.path.getmtime(asm)).astimezone() \
                    .isoformat(timespec="seconds")
                outputs = finish(folder, sid, work, cfg.keep_raw, bool(info.get("no_animal")))
                DIO.write_manifest(folder, _manifest(cfg, sid, bundle, current_inputs(bundle, video),
                                                     outputs, started, model, info, adopted=True))
            print(f"  {sid}: adopted")
        except (RuntimeError, OSError, DIO.LockedError) as e:
            failed += 1
            print(f"  {sid}: NOT adopted - {e}")
        if refused:
            shutil.rmtree(folder, ignore_errors=True)
    return EXIT_FAILED if failed else EXIT_OK


# ----------------------------------------------------------------- main
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--root", help="derived-data root (default: host/derivatives_root.json)")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, then exit")
    ap.add_argument("--session", action="append", default=[], help="only this session (repeatable)")
    ap.add_argument("--limit", type=int, help="at most this many sessions")
    ap.add_argument("--no-stale", action="store_true",
                    help="skip sessions whose video changed since their result")
    ap.add_argument("--no-raw", action="store_true",
                    help="delete DeepLabCut's pickles instead of keeping them under raw/")
    ap.add_argument("--stop", action="store_true", help="ask a running job to stop, then exit")
    ap.add_argument("--adopt", metavar="DIR",
                    help="build results from _assemblies.pickle files made earlier by the same model")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    if a.no_raw:
        cfg.keep_raw = False
    root = a.root or str(derivatives_root())
    pdir = D.pipeline_dir(root, cfg.pipeline)
    stop_file = os.path.join(pdir, STOP_NAME)
    if a.stop:
        request_stop(cfg, root)
        print(f"stop requested; the job stops within {cfg.rig_check_s:.0f} s")
        return EXIT_OK

    sessions = dlc_sessions(DIO.session_bundles())
    states = DIO.session_states(root, cfg.pipeline,
                                {s: current_inputs(b, v) for s, (b, v) in sessions.items()})
    todo = choose(states, sessions=a.session, include_stale=not a.no_stale, limit=a.limit)
    c = D.counts(states)
    print(f"{cfg.pipeline} in {root}: {len(sessions)} sessions with video - "
          f"{c['done']} done, {c['stale']} stale, {c['partial']} partial, "
          f"{c['pending']} pending; {len(todo)} to run")
    if a.dry_run:
        for sid in todo:
            print(f"  {sid}  ({states[sid]})")
        return EXIT_OK

    pid = rig_running()
    if pid:
        print(f"the rig GUI is open (pid {pid}); not starting")
        return EXIT_NOSTART
    model, why = model_info(cfg)
    if why:
        print(f"cannot run: {why}")
        return EXIT_NOSTART
    logs = os.path.join(pdir, LOGS_NAME)
    os.makedirs(logs, exist_ok=True)
    if os.path.exists(stop_file):
        os.remove(stop_file)
    pin_off(os.getpid(), cfg.avoid_cpus)
    try:
        with DIO.SessionLock(pdir):
            if a.adopt:
                return adopt_dir(cfg, root, a.adopt, sessions, model, logs, stop_file)
            return _run_all(cfg, root, todo, sessions, model, logs, stop_file)
    except DIO.LockedError as e:
        print(f"another run is active: {e}")
        return EXIT_NOSTART


def _run_all(cfg, root, todo, sessions, model, logs, stop_file) -> int:
    failed = 0
    for i, sid in enumerate(todo, 1):
        if os.path.exists(stop_file):
            print("stop requested")
            return EXIT_STOPPED
        pid = rig_running()
        if pid:
            print(f"the rig GUI is open (pid {pid}); stopping")
            return EXIT_STOPPED
        bundle, video = sessions[sid]
        t0 = time.time()
        print(f"[{i}/{len(todo)}] {sid}: start {datetime.now():%H:%M:%S}", flush=True)
        try:
            run_session(cfg, root, sid, bundle, video, model, logs, stop_file)
            m = DIO.read_manifest(D.session_dir(root, cfg.pipeline, sid)) or {}
            note = " (no animal found)" if m.get("params", {}).get("no_animal") else ""
            print(f"[{i}/{len(todo)}] {sid}: done in {(time.time() - t0) / 60:.0f} min{note}",
                  flush=True)
        except Stopped as e:
            print(f"[{i}/{len(todo)}] {sid}: stopped ({e}); it will be redone next run")
            return EXIT_STOPPED
        except (RuntimeError, OSError, DIO.LockedError) as e:
            failed += 1
            print(f"[{i}/{len(todo)}] {sid}: FAILED - {e}", flush=True)
    return EXIT_FAILED if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
