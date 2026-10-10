"""
test_legacy_import.py - importing pre-bundle recordings (ADR-0217).

- the loose files group into recordings (the video may carry a label; the
  background may be renamed);
- the rat comes from the label, else ``unk``;
- the model proposal: the lighting's model saved before the recording ends is
  "ok"; saved after, or another lighting, is "approximate"; no rat, "none";
- the draft skips videos with no undistortion or under the size limit;
- the table round-trips and an edited table is checked;
- the tool drafts, then builds hard-linked bundles the offline tracker accepts,
  never touching the originals, and leaves an existing bundle alone.

Run from host/:  python -m unittest tests.test_legacy_import
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tmaze.core import legacy_import as LI
from tmaze.core import offline_track as OT
from tmaze.tools import import_legacy_recordings as TOOL

GB = 1_000_000_000
T_END = 1_783_500_000.0          # a recording's last frame time


def _rec(label="rat600_lowlight", size=2 * GB, bg=True):
    stem = "animal_20260707_164553"
    return LI.LooseRecording(stem=stem, sidecar=stem + ".csv",
                             video=f"{stem}_{label}.mp4" if label else stem + ".mp4",
                             video_bytes=size,
                             background=f"{stem}_background_x.png" if bg else None)


class TestFindRecordings(unittest.TestCase):
    def test_groups_labelled_video_and_renamed_background(self):
        files = {"animal_20260707_164553.csv": 10,
                 "animal_20260707_164553_rat600_lowlight.mp4": 5 * GB,
                 "animal_20260707_164553_background_rat601_lowlight.png": 2,
                 "000_20260723_171911.csv": 10, "000_20260723_171911.mp4": 3,
                 "notes.csv": 1, "animal_20260707_164553_camera_stats.json": 1}
        recs = LI.find_recordings(files)
        self.assertEqual([r.stem for r in recs], ["000_20260723_171911", "animal_20260707_164553"])
        r = recs[1]
        self.assertEqual(r.video, "animal_20260707_164553_rat600_lowlight.mp4")
        self.assertEqual(r.label, "rat600_lowlight")
        self.assertEqual(r.background, "animal_20260707_164553_background_rat601_lowlight.png")
        self.assertEqual(recs[0].label, "")

    def test_rat_and_session_id(self):
        self.assertEqual(LI.rat_from_label("rat602_twolamps_rightlight"), "602")
        self.assertIsNone(LI.rat_from_label("lowlight"))
        self.assertIsNone(LI.rat_from_label("pirate7"))
        self.assertEqual(LI.session_id("animal_20260702_153140", "602"), "602_20260702_153140")
        self.assertEqual(LI.session_id("animal_20260702_153140", None), "unk_20260702_153140")


class TestProposeModel(unittest.TestCase):
    def test_model_saved_before_end_is_ok(self):
        models = {"animal_ratmodel_rat600_lowlight.npz": T_END - 600}
        self.assertEqual(LI.propose_model("600", "rat600_lowlight", T_END, models)[:2],
                         ("animal_ratmodel_rat600_lowlight.npz", LI.QUALITY_OK))

    def test_model_saved_after_recording_is_approximate(self):
        models = {"animal_ratmodel_rat600_lowlight.npz": T_END + 86400}
        name, q, note = LI.propose_model("600", "rat600_lowlight", T_END, models)
        self.assertEqual(q, LI.QUALITY_APPROX)
        self.assertIn("after", note)

    def test_two_lamps_falls_back_to_lowlight(self):
        models = {"animal_ratmodel_rat600_lowlight.npz": 0.0,
                  "animal_ratmodel_rat600.npz": 0.0}
        name, q, _ = LI.propose_model("600", "rat600_twolamps_rightlight", T_END, models)
        self.assertEqual((name, q), ("animal_ratmodel_rat600_lowlight.npz", LI.QUALITY_APPROX))

    def test_plain_label_prefers_plain_model(self):
        models = {"animal_ratmodel_rat601_lowlight.npz": 0.0, "animal_ratmodel_rat601.npz": 0.0}
        self.assertEqual(LI.propose_model("601", "rat601", T_END, models)[:2],
                         ("animal_ratmodel_rat601.npz", LI.QUALITY_OK))

    def test_unknown_rat_or_no_model_is_none(self):
        self.assertEqual(LI.propose_model(None, "", T_END, {})[:2], ("", LI.QUALITY_NONE))
        self.assertEqual(LI.propose_model("609", "rat609", T_END, {})[:2], ("", LI.QUALITY_NONE))


class TestDraftAndTable(unittest.TestCase):
    models = {"animal_ratmodel_rat600_lowlight.npz": T_END - 1}

    def test_draft_includes_large_undistorted_video(self):
        row = LI.draft_row(_rec(), LI.SidecarInfo(True, T_END), self.models)
        self.assertEqual(row["include"], LI.YES)
        self.assertEqual(row["session_id"], "600_20260707_164553")
        self.assertEqual(row["model_quality"], LI.QUALITY_OK)

    def test_draft_skips_small_or_distorted(self):
        small = LI.draft_row(_rec(size=GB // 2), LI.SidecarInfo(True, T_END), self.models)
        self.assertEqual(small["include"], LI.NO)
        self.assertIn("under 1 GB", small["notes"])
        raw = LI.draft_row(_rec(), LI.SidecarInfo(False, T_END), self.models)
        self.assertEqual(raw["include"], LI.NO)
        self.assertIn("undistortion", raw["notes"])

    def test_unlabelled_video_is_unknown_rat(self):
        row = LI.draft_row(_rec(label=""), LI.SidecarInfo(True, T_END), self.models)
        self.assertEqual((row["rat"], row["session_id"]), ("unk", "unk_20260707_164553"))
        self.assertEqual(row["model_quality"], LI.QUALITY_NONE)

    def test_table_round_trip_and_validation(self):
        row = LI.draft_row(_rec(), LI.SidecarInfo(True, T_END), self.models)
        rows = LI.parse_table(LI.format_table([row]))
        self.assertEqual(rows, [row])
        self.assertEqual(LI.validate_table(rows), [])
        bad = [dict(row), dict(row, stem="other"),
               dict(row, stem="x", session_id="600-bad"),
               dict(row, stem="y", session_id="600_20260101_000000", model_quality="sure"),
               dict(row, stem="z", include="maybe")]
        errors = "\n".join(LI.validate_table(bad))
        self.assertIn("also used by", errors)
        self.assertIn("600-bad", errors)
        self.assertIn("model_quality must be", errors)
        self.assertIn("include must be", errors)
        self.assertEqual(LI.validate_table([dict(row, include="no", session_id="")]), [])

    def test_plan_names_background_as_the_header_says(self):
        row = LI.draft_row(_rec(), LI.SidecarInfo(True, T_END), self.models)
        steps = LI.plan_bundle(row, root="R", models_dir="M", host_dir="H",
                               background_name="animal_20260707_164553_background.png")
        dsts = {s.dst.replace(os.sep, "/"): s for s in steps}
        self.assertEqual(dsts["video/600_20260707_164553.mp4"].action, "link")
        self.assertIn("video/600_20260707_164553.csv", dsts)
        self.assertIn("video/animal_20260707_164553_background.png", dsts)
        self.assertEqual(dsts["config/model_animal_ratmodel_rat600_lowlight.npz"].action, "copy")
        for name in LI.HOST_CONFIG_FILES:
            self.assertIn(f"config/{name}", dsts)


class TestTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="legacy_import_")
        self.root = os.path.join(self.tmp, "recordings")
        self.models = os.path.join(self.tmp, "ratmodels")
        self.host = os.path.join(self.tmp, "host")
        for d in (self.root, self.models, self.host):
            os.makedirs(d)
        stem = "animal_20260707_164553"
        self._write(self.root, stem + ".csv",
                    f"# fps: 15\n# undistorted: True\n# background: {stem}_background.png\n"
                    "frame_idx,host_unix_s\n0,1783450000.0\n1,1783450000.1\n")
        self._write(self.root, stem + "_rat600_lowlight.mp4", "video")
        self._write(self.root, stem + "_background_rat600_lowlight.png", "png")
        self._write(self.models, "animal_ratmodel_rat600_lowlight.npz", "model")
        os.utime(os.path.join(self.models, "animal_ratmodel_rat600_lowlight.npz"),
                 (1783440000, 1783440000))
        for name in LI.HOST_CONFIG_FILES:
            self._write(self.host, name, "{}")
        self.patch = mock.patch.object(TOOL, "host_dir", lambda: Path(self.host))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _write(folder, name, text):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _main(self, *extra):
        return TOOL.main(["--root", self.root, "--models-dir", self.models, *extra])

    def test_draft_then_build(self):
        self.assertEqual(self._main("--draft", "--min-gb", "0"), 0)
        out = os.path.join(self.root, TOOL.OUT_SUBDIR)
        table = os.path.join(out, LI.TABLE_NAME)
        self.assertEqual(self._main("--draft"), 2)          # never overwrites the table
        with open(table, encoding="utf-8") as fh:
            rows = LI.parse_table(fh.read())
        self.assertEqual([r["session_id"] for r in rows], ["600_20260707_164553"])

        self.assertEqual(self._main("--dry-run"), 0)
        self.assertFalse(os.path.exists(os.path.join(out, "600_20260707_164553")))
        self.assertEqual(self._main(), 0)
        bundle = os.path.join(out, "600_20260707_164553")
        video = os.path.join(bundle, "video", "600_20260707_164553.mp4")
        self.assertTrue(os.path.samefile(
            video, os.path.join(self.root, "animal_20260707_164553_rat600_lowlight.mp4")))
        self.assertTrue(OT.is_bundle(bundle))
        settings = OT.resolve_settings(bundle, ratmodels_dir=self.models)
        self.assertEqual(settings.model.source, OT.SRC_BUNDLE)
        self.assertEqual(settings.background.source, OT.SRC_BUNDLE)
        self.assertEqual(settings.roi.source, OT.SRC_BUNDLE)
        self.assertIsNotNone(settings.sidecar)
        with open(os.path.join(bundle, "config", LI.PROVENANCE_NAME), encoding="utf-8") as fh:
            prov = json.load(fh)
        self.assertEqual(prov["model_quality"], LI.QUALITY_OK)
        self.assertEqual(prov["source_stem"], "animal_20260707_164553")

        self._write(self.host, "roi_polygon.json", '{"changed": true}')
        self.assertEqual(self._main(), 0)                   # existing bundle left alone
        with open(os.path.join(bundle, "config", "roi_polygon.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{}")

    def test_bad_table_is_refused(self):
        self.assertEqual(self._main("--draft", "--min-gb", "0"), 0)
        table = os.path.join(self.root, TOOL.OUT_SUBDIR, LI.TABLE_NAME)
        with open(table, encoding="utf-8") as fh:
            text = fh.read()
        with open(table, "w", encoding="utf-8") as fh:
            fh.write(text.replace(",yes,", ",perhaps,"))
        self.assertEqual(self._main(), 2)
        self.assertFalse(os.path.exists(os.path.join(self.root, TOOL.OUT_SUBDIR,
                                                     "600_20260707_164553")))


if __name__ == "__main__":
    unittest.main()
