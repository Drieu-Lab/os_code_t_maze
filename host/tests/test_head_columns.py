"""Headless tests for the learned head-point sidecar columns (tmaze.core.head_columns).

Covers: blank row when head tracking is off; a "no prediction" row; a trusted
row's formatting; the column dictionary written into format.json; the header
lines for on/off; and that the column points match the network's targets.

Run from host/:  python -m unittest tests.test_head_columns
"""

from __future__ import annotations

import unittest

from tmaze.core import head_columns as HC
from tmaze.core.head_filter import NO_PREDICTION, OK, HeadSample
from tmaze.core.phase3 import bundle_format
from tmaze.vision import head_learned as HL


class TestHeadColumns(unittest.TestCase):
    def test_off_is_all_blank(self):
        self.assertEqual(HC.head_row(None), ("",) * len(HC.HEAD_COLUMNS))

    def test_no_prediction_has_status_only(self):
        row = dict(zip(HC.HEAD_COLUMNS, HC.head_row(HeadSample(False, NO_PREDICTION))))
        self.assertEqual(row["head_status"], "none")
        self.assertEqual(row["head_valid"], 0)
        self.assertEqual(row["head_x"], "")
        self.assertEqual(row["head_dir_deg"], "")

    def test_trusted_row(self):
        pts = {p: (10.0 + i, 20.04 + i) for i, p in enumerate(HC.POINTS)}
        row = dict(zip(HC.HEAD_COLUMNS, HC.head_row(HeadSample(True, OK, pts, -90.0))))
        self.assertEqual((row["head_status"], row["head_valid"]), ("ok", 1))
        self.assertEqual((row["head_x"], row["head_y"]), ("10.0", "20.0"))
        self.assertEqual((row["left_ear_x"], row["left_ear_y"]), ("15.0", "25.0"))
        self.assertEqual(row["head_dir_deg"], "-90.0")

    def test_points_match_the_network(self):
        self.assertEqual(HC.POINTS, HL.TARGETS)

    def test_format_manifest_documents_every_column(self):
        doc = bundle_format.format_manifest()["sidecar_head_columns"]
        self.assertEqual(tuple(doc), HC.HEAD_COLUMNS)
        self.assertTrue(all(doc.values()))

    def test_header_fields(self):
        self.assertEqual(HC.header_fields(None), {"head_tracking": "off"})
        h = HC.header_fields({"file": "head_net.onnx", "sha256": "ab", "trained": "2026-10-18",
                              "sessions": ["600_a", "600_b"]})
        self.assertEqual(h["head_tracking"], "on")
        self.assertEqual(h["head_model"], "head_net.onnx")
        self.assertEqual(h["head_model_sessions"], "600_a 600_b")


if __name__ == "__main__":
    unittest.main()
