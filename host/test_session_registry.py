"""
test_session_registry.py - join-fixture tests for the session-registry core.

The registry is where "coordinated records" actually happens - it joins each
bundle's summary + animal_state + session_meta + notes into one row (and folds
the daily table for older bundles). These tests build a few synthetic bundles on
a temp recordings root and assert the join, the graceful-degradation blanks, the
four provenance-labelled water channels, and the per-tag tallies (wide + tidy).
See docs/decisions/0081. Pure stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_session_registry
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import unittest

from tmaze.core.records import daily_log
from tmaze.core.session import session_registry


def _make_bundle(root: str, session_id: str, *, summary=None,
                 animal_state=None, session_meta=None, notes_lines=None):
    """Write a synthetic bundle under ``root`` with the given (optional) files.

    Always writes the manifest so :func:`session_registry.is_bundle` sees it;
    each of summary / animal_state / session_meta / notes is written only when
    provided, so a test can exercise a bundle that lacks one."""
    bundle = os.path.join(root, session_id)
    os.makedirs(os.path.join(bundle, "summary"), exist_ok=True)
    os.makedirs(os.path.join(bundle, "config"), exist_ok=True)
    # Manifest (bundle marker).
    with open(os.path.join(bundle, f"session_{session_id}.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"session_id": session_id}, fh)
    if summary is not None:
        with open(os.path.join(bundle, "summary",
                               f"session_summary_{session_id}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(summary, fh)
    if animal_state is not None:
        with open(os.path.join(bundle, "config", "animal_state.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(animal_state, fh)
    if session_meta is not None:
        with open(os.path.join(bundle, "config", "session_meta.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(session_meta, fh)
    if notes_lines is not None:
        with open(os.path.join(bundle, "summary", f"notes_{session_id}.jsonl"),
                  "w", encoding="utf-8") as fh:
            for obj in notes_lines:
                fh.write(json.dumps(obj) + "\n")
    return bundle


def _summary(animal="600", **over):
    s = {
        "session_id": "x", "animal": animal, "seed": 123,
        "tones": {"high_tone_is_left": True, "high_tone_hz": 8000,
                  "low_tone_hz": 2000},
        "t0": {"iso": "2026-08-06T12:00:00.000", "wall": 1000.0},
        "duration_s": 123.0,
        "cued_task": {"total": 40, "completed": 30, "correct": 24,
                      "incorrect": 6, "accuracy_cum": 0.8,
                      "high_accuracy": 0.9, "low_accuracy": 0.7,
                      "reward_rate": 0.8, "lr_bias": [0.55, 0.45]},
        "estimated_water": {"ul": 1500.0, "pulses": 30},
        "manual_water": {"ul": 120.0, "pulses": 2},
        "locomotion": {"total_choices": 64, "stem_choices": 18,
                       "nonstem_choices": 46, "compliance_cum": 0.28},
    }
    s.update(over)
    return s


def _row_by_id(rows, session_id):
    for r in rows:
        if r["session_id"] == session_id:
            return r
    raise KeyError(session_id)


class TestJoinPrefersBundleState(unittest.TestCase):
    def test_full_bundle_row(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(
                root, "600_20260806_120000",
                summary=_summary(),
                animal_state={"animal": "600", "date": "2026-08-06",
                              "source": "daily_table",
                              "pre_session_weight_g": 330, "baseline_g": 348,
                              "pct_baseline": 0.9483, "last_water_on": "5:30pm",
                              "last_water_off": "6pm"},
                session_meta={"trainer": "Celine", "stage": "Phase 3",
                              "stage_source": "auto", "reward_type": "sucrose"},
                notes_lines=[
                    {"id": 0, "iso": "t", "text": "", "tag": "lift-backs",
                     "count": 1},
                    {"id": 1, "iso": "t", "text": "", "tag": "lift-backs",
                     "count": 1},
                    {"id": 2, "iso": "t", "text": "hi", "tag": None,
                     "count": 1}])
            rows, tag_rows = session_registry.build(root)
            self.assertEqual(len(rows), 1)
            r = rows[0]
            # Identity + husbandry from the bundle's frozen state.
            self.assertEqual(r["animal"], "600")
            self.assertEqual(r["date"], "2026-08-06")
            self.assertEqual(r["husbandry_source"], "daily_table")
            self.assertEqual(r["weight_g"], 330)
            self.assertEqual(r["pct_baseline"], 0.9483)
            # Behaviour verbatim from the summary (not recomputed).
            self.assertEqual(r["accuracy_cum"], 0.8)
            self.assertEqual(r["high_accuracy"], 0.9)
            self.assertEqual(r["side_bias_left"], 0.55)
            self.assertEqual(r["side_bias_right"], 0.45)
            # WS8 mapping: total_choices -> total_excursions, compliance_cum ->
            # stem_compliance.
            self.assertEqual(r["total_excursions"], 64)
            self.assertEqual(r["nonstem_choices"], 46)
            self.assertEqual(r["stem_compliance"], 0.28)
            # Metadata.
            self.assertEqual(r["trainer"], "Celine")
            self.assertEqual(r["reward_type"], "sucrose")

    def test_four_water_channels_separate(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(
                root, "600_20260806_120000", summary=_summary(),
                animal_state={"animal": "600", "date": "2026-08-06",
                              "source": "daily_table", "last_water_on": "5:30pm",
                              "last_water_off": "6pm"})
            # supp_water comes from the daily table (post-session), joined here.
            daily = daily_log.DailyLog([
                {"animal_id": "600", "date": "2026-08-06", "weight_g": "330",
                 "baseline_g": "348", "pct_baseline": "0.9483",
                 "water_on": "5:30pm", "water_off": "6pm", "water_ml": "",
                 "supp_water": "2 min", "notes": ""}])
            rows, _ = session_registry.build(root, daily=daily)
            r = rows[0]
            self.assertEqual(r["task_reward_ml"], 1.5)      # 1500 uL
            self.assertEqual(r["manual_reward_ml"], 0.12)   # 120 uL
            self.assertEqual(r["supp_water"], "2 min")
            self.assertEqual(r["water_on"], "5:30pm")
            self.assertEqual(r["water_off"], "6pm")


class TestDailyTableFallback(unittest.TestCase):
    def test_join_by_date_when_no_animal_state(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(root, "601_20260806_090000",
                         summary=_summary(animal="601"))  # no animal_state
            daily = daily_log.DailyLog([
                {"animal_id": "601", "date": "2026-08-06", "weight_g": "300",
                 "baseline_g": "320", "pct_baseline": "0.9375",
                 "water_on": "4pm", "water_off": "5pm", "water_ml": "",
                 "supp_water": "1 min", "notes": ""}])
            rows, _ = session_registry.build(root, daily=daily)
            r = rows[0]
            self.assertEqual(r["husbandry_source"], "daily_table_join")
            self.assertEqual(r["weight_g"], "300")
            self.assertEqual(r["water_on"], "4pm")
            self.assertEqual(r["supp_water"], "1 min")


class TestGracefulDegradation(unittest.TestCase):
    def test_missing_everything_leaves_blanks(self):
        with tempfile.TemporaryDirectory() as root:
            # Only a manifest - no summary/state/meta/notes, and no daily table.
            _make_bundle(root, "602_20260806_100000")
            rows, tag_rows = session_registry.build(root)
            self.assertEqual(len(rows), 1)
            r = rows[0]
            # Identity still recovered from the folder name.
            self.assertEqual(r["animal"], "602")
            self.assertEqual(r["date"], "2026-08-06")
            self.assertEqual(r["husbandry_source"], "none")
            self.assertIsNone(r["weight_g"])
            self.assertIsNone(r["accuracy_cum"])
            self.assertIsNone(r["task_reward_ml"])
            self.assertEqual(tag_rows, [])

    def test_non_bundle_dirs_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "not_a_bundle"))
            _make_bundle(root, "600_20260806_120000", summary=_summary())
            rows, _ = session_registry.build(root)
            self.assertEqual([r["session_id"] for r in rows],
                             ["600_20260806_120000"])


class TestTagOutputs(unittest.TestCase):
    def test_tidy_and_wide_tags(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(
                root, "600_20260806_120000", summary=_summary(),
                notes_lines=[
                    {"id": 0, "iso": "t", "text": "", "tag": "lift-backs",
                     "count": 1},
                    {"id": 1, "iso": "t", "text": "", "tag": "lift-backs",
                     "count": 1},
                    {"id": 2, "iso": "t", "text": "", "tag": "non-stem",
                     "count": 1}])
            rows, tag_rows = session_registry.build(root)
            # Tidy long rows: one per (session, tag) with summed counts.
            self.assertIn(("600_20260806_120000", "lift-backs", 2), tag_rows)
            self.assertIn(("600_20260806_120000", "non-stem", 1), tag_rows)
            # Wide projection uses the CURRENT vocabulary; a session without a
            # vocab tag gets 0, and a vocab tag not present is a 0 column.
            text = session_registry.render_registry_csv(
                rows, tag_names=["lift-backs", "non-stem", "unused"])
            body = [ln for ln in text.splitlines() if not ln.startswith("#")]
            reader = csv.DictReader(io.StringIO("\n".join(body)))
            r = next(reader)
            self.assertEqual(r["tag_lift-backs"], "2")
            self.assertEqual(r["tag_non-stem"], "1")
            self.assertEqual(r["tag_unused"], "0")

    def test_registry_csv_has_schema_comment_and_header(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(root, "600_20260806_120000", summary=_summary())
            rows, _ = session_registry.build(root)
            text = session_registry.render_registry_csv(rows, tag_names=[])
            self.assertTrue(text.startswith(
                f"# schema_version={session_registry.SCHEMA_VERSION}\n"))
            header = text.splitlines()[1]
            self.assertTrue(header.startswith("session_id,animal,date,"))


if __name__ == "__main__":
    unittest.main()
