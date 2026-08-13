"""
test_session_detail.py - the Sessions-tab presentation core (docs/decisions/0089).

Covers the pure formatting used by the Records Manager Sessions browser: the
per-session metrics block (full + blank rows), the curated list-row mapping, and
the best-effort read of the bundle's rendered summary text.

Run from host/tests:  python -m unittest test_session_detail
"""

from __future__ import annotations

import os
import tempfile
import unittest

from tmaze.core.session import session_detail


_FULL_ROW = {
    "session_id": "600_20260807_164732",
    "animal": "600",
    "date": "2026-08-07",
    "start_iso": "2026-08-07T16:47:32.081",
    "duration_s": "2765.879",
    "weight_g": "372",
    "baseline_g": "348",
    "pct_baseline": "1.069",
    "task_reward_ml": "4.75",
    "manual_reward_ml": "7.49",
    "supp_water": "",
    "trials_total": "200",
    "completed": "108",
    "correct": "49",
    "incorrect": "59",
    "accuracy_cum": "0.4537037",
    "side_bias_left": "0.5462963",
    "side_bias_right": "0.4537037",
    "stem_compliance": "0.965035",
    "trainer": "RJ",
    "stage": "Phase 3",
    "stage_source": "auto",
    "reward_type": "10% sucrose",
    "bundle": r"C:\x\600_20260807_164732",
}


class TestMetricsLines(unittest.TestCase):
    def test_full_row_formats_units_and_percents(self):
        block = "\n".join(session_detail.metrics_lines(_FULL_ROW))
        self.assertIn("Duration: 0:46:05", block)          # seconds -> H:MM:SS
        self.assertIn("(107% baseline)", block)            # 1.069 -> 107%
        self.assertIn("acc 45%", block)                    # 0.4537 -> 45%
        self.assertIn("108/200 completed", block)
        self.assertIn("task 4.75 mL", block)
        self.assertIn("manual 7.49 mL", block)
        self.assertIn("L 55% / R 45%", block)              # side bias percents
        self.assertIn("Stage: Phase 3 (auto)", block)
        self.assertIn("Reward: 10% sucrose", block)
        self.assertIn("Time: 16:47", block)

    def test_blank_row_uses_dashes_not_crashes(self):
        block = "\n".join(session_detail.metrics_lines({"session_id": "000_x"}))
        self.assertIn("Duration: -", block)
        self.assertIn("acc -", block)
        self.assertIn("Animal:   -", block)

    def test_hours_since_water_line(self):
        row = dict(_FULL_ROW, hours_since_water="15.5")
        block = "\n".join(session_detail.metrics_lines(row))
        self.assertIn("Hrs since water: 15.5", block)

    def test_tag_tally_reads_wide_tag_columns(self):
        row = dict(_FULL_ROW, tag_seizure="2", tag_health="1", tag_none="0")
        self.assertEqual(session_detail.tag_tally(row), "health x 1, seizure x 2")
        line = session_detail.metrics_lines(row, selected=["tags"])[0]
        self.assertIn("Tags:", line)
        self.assertIn("seizure x 2", line)

    def test_tag_tally_dash_when_no_tags(self):
        self.assertEqual(session_detail.tag_tally(_FULL_ROW), "-")


class TestSelectableMetrics(unittest.TestCase):
    def test_available_and_default_align(self):
        ids = [mid for mid, _label in session_detail.available_metrics()]
        self.assertEqual(ids, session_detail.default_metric_ids())
        # Every id has a non-empty human label.
        self.assertTrue(all(label for _mid, label in
                            session_detail.available_metrics()))

    def test_selection_filters_and_keeps_catalog_order(self):
        # Ask out of order; result must follow the catalog order, not the request.
        lines = session_detail.metrics_lines(
            _FULL_ROW, selected=["water", "session"])
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("Session:"))   # catalog order
        self.assertTrue(lines[1].startswith("Water:"))

    def test_unknown_ids_ignored_and_empty_selection(self):
        self.assertEqual(
            session_detail.metrics_lines(_FULL_ROW, selected=["nope"]), [])
        self.assertEqual(
            session_detail.metrics_lines(_FULL_ROW, selected=[]), [])

    def test_none_selection_is_all(self):
        self.assertEqual(
            len(session_detail.metrics_lines(_FULL_ROW, selected=None)),
            len(session_detail.default_metric_ids()))


class TestSessionTime(unittest.TestCase):
    def test_prefers_start_iso(self):
        self.assertEqual(session_detail.session_time(
            {"start_iso": "2026-08-07T16:47:32.081"}), "16:47")

    def test_falls_back_to_session_id(self):
        self.assertEqual(session_detail.session_time(
            {"start_iso": "", "session_id": "600_20260807_164732"}), "16:47")

    def test_dash_when_unknown(self):
        self.assertEqual(session_detail.session_time({"session_id": "weird"}), "-")


class TestListRow(unittest.TestCase):
    def test_columns_and_values_align(self):
        cols = session_detail.list_columns()
        vals = session_detail.list_row(_FULL_ROW)
        self.assertEqual(len(cols), len(vals))
        mapping = dict(zip(cols, vals))
        self.assertEqual(mapping["date"], "2026-08-07")
        self.assertEqual(mapping["time"], "16:47")
        self.assertEqual(mapping["animal"], "600")
        self.assertEqual(mapping["duration"], "0:46:05")
        self.assertEqual(mapping["trials"], "108/200")
        self.assertEqual(mapping["accuracy"], "45%")
        self.assertEqual(mapping["stage"], "Phase 3")

    def test_blank_trials_shows_dash(self):
        mapping = dict(zip(session_detail.list_columns(),
                           session_detail.list_row({"animal": "600"})))
        self.assertEqual(mapping["trials"], "-")
        self.assertEqual(mapping["duration"], "-")


class TestSummaryText(unittest.TestCase):
    def test_reads_summary_txt(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdir = os.path.join(tmp, "summary")
            os.makedirs(sdir)
            with open(os.path.join(sdir, "session_summary_600_x.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("Session behavioral summary\nAnimal: 600\n")
            text = session_detail.summary_text(tmp)
            self.assertIsNotNone(text)
            self.assertIn("Animal: 600", text)

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(session_detail.summary_text(tmp))

    def test_none_when_bundle_missing(self):
        self.assertIsNone(session_detail.summary_text(""))
        self.assertIsNone(session_detail.summary_text(None))


if __name__ == "__main__":
    unittest.main()
