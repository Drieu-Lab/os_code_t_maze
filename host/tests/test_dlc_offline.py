"""Tests for tmaze.tools.dlc_offline (no DeepLabCut: the worker is faked)."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tmaze.core import derivatives as D
from tmaze.io import derivatives_io as DIO
from tmaze.tools import dlc_offline as O

SID = "602_20260916_145627"


PIPE = "dlc_best_iter1"
PREFIX = f"{SID}DLC_Resnet50_mShuffle1_snapshot_best-140"


def _cfg(**kw):
    base = dict(pipeline=PIPE, python="python", dlc_config="cfg.yaml",
                rig_check_s=0.01, avoid_cpus=[])
    base.update(kw)
    return O.OfflineConfig(**base)


class FakeWorker:
    """Writes what the real worker writes on its first wait(), or never finishes."""

    def __init__(self, job_path, finish=True):
        with open(job_path, encoding="utf-8") as fh:
            self.job = json.load(fh)
        self.finish, self.pid, self.returncode = finish, -1, None

    def wait(self, timeout=None):
        if not self.finish:
            raise subprocess.TimeoutExpired("worker", timeout)
        dest = self.job["dest"]
        for kind in ("full", "assemblies"):
            with open(os.path.join(dest, f"{PREFIX}_{kind}.pickle"), "wb") as fh:
                fh.write(b"big")
        with open(os.path.join(dest, SID + ".csv"), "w") as fh:
            fh.write("scorer,x\n0,1\n")
        with open(os.path.join(dest, "worker_info.json"), "w") as fh:
            json.dump({"deeplabcut": "3.0", "torch": "2.5", "rows": 3, "found_frames": 2}, fh)
        self.returncode = 0
        return 0


class TestChecks(unittest.TestCase):
    def test_iteration_and_pipeline_name(self):
        self.assertEqual(O.project_iteration("Task: m\niteration: 1\nsnapshotindex: -1\n"), 1)
        self.assertIsNone(O.project_iteration("Task: m\n"))
        self.assertIsNone(O.pipeline_problem("dlc_full_iter1", 1))
        self.assertIn("iteration 2", O.pipeline_problem("dlc_full_iter1", 2))
        self.assertIn("_iter<N>", O.pipeline_problem("dlc_full", 1))

    def test_config_file_loads(self):
        cfg = O.load_config()
        self.assertTrue(cfg.keep_raw)
        self.assertIsNone(O.pipeline_problem(cfg.pipeline, 1))

    def test_find_snapshot_picks_best_of_the_shuffle(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        for shuffle, name in (("1", "snapshot-best-140.pt"), ("1", "snapshot-200.pt"),
                              ("10", "snapshot-best-300.pt")):
            d = os.path.join(root, "dlc-models-pytorch", "iteration-1", f"mOct7-trainset95shuffle{shuffle}", "train")
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, name), "w").close()
        got = O.find_snapshot(os.path.join(root, "config.yaml"), 1, 1)
        self.assertTrue(got.endswith("snapshot-best-140.pt"))
        self.assertIsNone(O.find_snapshot(os.path.join(root, "config.yaml"), 2, 1))

    def test_choose(self):
        states = {"600_20260901_100000": D.STATE_DONE, "600_20260902_100000": D.STATE_STALE,
                  "601_20260903_100000": D.STATE_PENDING, "602_20260830_100000": D.STATE_PARTIAL}
        self.assertEqual(O.choose(states), ["601_20260903_100000", "600_20260902_100000",
                                            "602_20260830_100000"])
        self.assertEqual(O.choose(states, include_stale=False, limit=1), ["601_20260903_100000"])
        self.assertEqual(O.choose(states, sessions=["600_20260901_100000"]), [])

    def test_choose_by_date_animal_and_known(self):
        states = {"600_20260919_100000": D.STATE_PENDING, "600_20260920_100000": D.STATE_PENDING,
                  "601_20260925_100000": D.STATE_PENDING, "602_20261001_100000": D.STATE_DONE,
                  "000_20260922_163830": D.STATE_PENDING, "odd_name": D.STATE_PENDING}
        known = ["600", "601", "602"]
        self.assertEqual(O.choose(states, known=known, date_from=O.parse_day("2026-09-20")),
                         ["601_20260925_100000", "600_20260920_100000"])
        self.assertEqual(O.choose(states, known=known, date_to="20260919"),
                         ["600_20260919_100000"])
        self.assertEqual(O.choose(states, known=known, animals=["601"]), ["601_20260925_100000"])
        self.assertIn("000_20260922_163830", O.choose(states, known=None))
        self.assertEqual(O.unknown_animal(states, known), ["000_20260922_163830", "odd_name"])
        self.assertEqual(O.unknown_animal(states, None), [])
        self.assertEqual(len(O.select(states, known=known, date_from="20260920")), 3)
        self.assertEqual(O.parse_day(""), "")
        with self.assertRaises(ValueError):
            O.parse_day("2026-13-01")

    def test_redo_only_for_named_sessions(self):
        states = {"600_20260920_100000": D.STATE_DONE, "601_20260921_100000": D.STATE_DONE,
                  "000_20260922_100000": D.STATE_DONE}
        self.assertEqual(O.choose(states, redo=True), [])
        self.assertEqual(O.choose(states, sessions=["600_20260920_100000"], redo=True,
                                  known=["600", "601"]), ["600_20260920_100000"])
        self.assertEqual(O.choose(states, sessions=["000_20260922_100000"], redo=True,
                                  known=["600"]), [])
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            O.main(["--redo", "--dry-run"])
        self.assertIn("--redo needs --session", err.getvalue())

    def test_rig_running(self):
        class P:
            def __init__(self, pid, cmd):
                self.info = {"pid": pid, "cmdline": cmd}
        procs = [P(5, ["python", "-m", "tmaze.tools.dlc_offline"]), P(None, None)]
        self.assertIsNone(O.rig_running(procs))
        procs.append(P(7, ["python.exe", "-m", "tmaze.ui.experiment_control"]))
        self.assertEqual(O.rig_running(procs), 7)

    def test_status(self):
        self.assertEqual(O.status_text({"done": 7, "pending": 190, "partial": 1, "stale": 3}, None),
                         "DLC: 7 done, 194 to do")
        self.assertTrue(O.status_text({}, 42).endswith("running (pid 42)"))
        pdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, pdir)
        self.assertIsNone(O.running_pid(pdir))
        with open(os.path.join(pdir, D.LOCK_NAME), "w") as fh:
            json.dump({"pid": 42, "host": O.socket.gethostname()}, fh)
        self.assertEqual(O.running_pid(pdir, alive=lambda pid: True), 42)
        self.assertIsNone(O.running_pid(pdir, alive=lambda pid: False))

    def test_adopt_problem(self):
        self.assertIsNone(O.adopt_problem(10, 99, 1234, 1234))
        self.assertIn("bytes", O.adopt_problem(10, 10, 1, 1234))
        self.assertIsNone(O.adopt_problem(10, 10, None, 1234))
        self.assertIn("cannot confirm", O.adopt_problem(10, 9, None, 1234))


class TestRunSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "derivatives")
        self.bundle = os.path.join(self.tmp, "rec", SID)
        os.makedirs(os.path.join(self.bundle, "video"))
        self.video = os.path.join(self.bundle, "video", SID + ".mp4")
        with open(self.video, "wb") as fh:
            fh.write(b"video")
        with open(os.path.join(self.bundle, "video", SID + ".csv"), "w") as fh:
            fh.write("# camera=x\nframe_idx,t\n0,0\n1,1\n2,2\n")
        self.logs = os.path.join(self.root, "logs")
        os.makedirs(self.logs)
        self.stop = os.path.join(self.root, "_stop")
        self.folder = D.session_dir(self.root, PIPE, SID)

    def _run(self, launch, cfg=None):
        O.run_session(cfg or _cfg(), self.root, SID, self.bundle, self.video, {"snapshot": "s.pt"},
                      self.logs, self.stop, launch=launch, rig=lambda: None)

    def test_done_session_has_fixed_names_and_a_manifest(self):
        self._run(lambda cfg, job, log: FakeWorker(job))
        self.assertEqual(sorted(os.listdir(self.folder)), [f"{SID}_dlc.csv", "manifest.json", "raw"])
        self.assertEqual(sorted(os.listdir(os.path.join(self.folder, "raw"))),
                         [f"{PREFIX}_assemblies.pickle", f"{PREFIX}_full.pickle"])
        m = DIO.read_manifest(self.folder)
        self.assertEqual(D.manifest_problems(m, PIPE, SID), [])
        self.assertEqual(m["params"]["track"], "best_per_frame")
        self.assertEqual(m["params"]["frames"], 3)
        self.assertEqual(m["params"]["found_frames"], 2)
        self.assertEqual(m["params"]["sidecar_rows"], 3)
        self.assertEqual(m["inputs"][0]["bundle_rel"], f"video/{SID}.mp4")
        self.assertIn(f"raw/{PREFIX}_full.pickle", [o["file"] for o in m["outputs"]])
        self.assertEqual(D.classify(True, m, PIPE, SID,
                                    O.current_inputs(self.bundle, self.video)), D.STATE_DONE)

    def test_head_point_dataset_reads_finished_sessions_only(self):
        from tmaze.tools.head_point_dataset import prediction_csvs
        self._run(lambda cfg, job, log: FakeWorker(job))
        half = D.session_dir(self.root, PIPE, "600_20260901_100000")
        os.makedirs(half)
        open(os.path.join(half, "600_20260901_100000_dlc.csv"), "w").close()
        pdir = D.pipeline_dir(self.root, PIPE)
        self.assertEqual(prediction_csvs(pdir), [os.path.join(self.folder, f"{SID}_dlc.csv")])

    def test_no_animal_is_done_with_no_files(self):
        class NoAnimal(FakeWorker):
            def wait(self, timeout=None):
                with open(os.path.join(self.job["dest"], "worker_info.json"), "w") as fh:
                    json.dump({"no_animal": True, "rows": 0}, fh)
                self.returncode = 0
                return 0
        self._run(lambda cfg, job, log: NoAnimal(job))
        self.assertEqual(os.listdir(self.folder), ["manifest.json"])
        m = DIO.read_manifest(self.folder)
        self.assertTrue(m["params"]["no_animal"])
        self.assertEqual(m["outputs"], [])
        self.assertEqual(D.classify(True, m, PIPE, SID,
                                    O.current_inputs(self.bundle, self.video)), D.STATE_DONE)

    def test_no_raw(self):
        self._run(lambda cfg, job, log: FakeWorker(job), cfg=_cfg(keep_raw=False))
        self.assertEqual(sorted(os.listdir(self.folder)), [f"{SID}_dlc.csv", "manifest.json"])

    def test_redo_not_reached_keeps_its_result(self):
        self._run(lambda cfg, job, log: FakeWorker(job))
        open(self.stop, "w").close()
        rc = O._run_all(_cfg(), self.root, [SID], {SID: (self.bundle, self.video)},
                        {"snapshot": "s.pt"}, self.logs, self.stop)
        self.assertEqual(rc, O.EXIT_STOPPED)
        self.assertIsNotNone(DIO.read_manifest(self.folder))

    def test_stop_kills_and_leaves_partial(self):
        open(self.stop, "w").close()
        killed = []
        orig, O.kill_tree = O.kill_tree, killed.append
        try:
            with self.assertRaises(O.Stopped):
                self._run(lambda cfg, job, log: FakeWorker(job, finish=False))
        finally:
            O.kill_tree = orig
        self.assertEqual(killed, [-1])
        self.assertIsNone(DIO.read_manifest(self.folder))
        self.assertFalse(os.path.exists(os.path.join(self.folder, D.LOCK_NAME)))


if __name__ == "__main__":
    unittest.main()
