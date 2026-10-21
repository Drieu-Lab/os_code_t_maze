"""Headless tests for the DeepLabCut-Live head preview's pure core
(tmaze.core.dlc_head + tmaze.core.dlc_head_protocol; docs/decisions/0224).

Covers: the crop box (centred, shifted not shrunk at each edge, frame smaller
than the box); ok/low status from the head centre's likelihood; the trial-file
columns, rows and header; and every protocol message round-tripping, plus the
guards against a wrong-size crop, a part-count mismatch and a corrupt length.

Run from host/:  python -m unittest tests.test_dlc_head
"""

from __future__ import annotations

import io
import unittest

from tmaze.core import dlc_head as DH
from tmaze.core import dlc_head_protocol as P

PARTS = ("nose", "right_ear", "left_ear", "head_center", "neck")


class TestCropBox(unittest.TestCase):
    def test_centred(self):
        self.assertEqual(DH.crop_box(640, 512, 448, 1280, 1024), (416, 288))

    def test_shifted_at_each_edge(self):
        self.assertEqual(DH.crop_box(10, 10, 448, 1280, 1024), (0, 0))
        self.assertEqual(DH.crop_box(1275, 1020, 448, 1280, 1024), (832, 576))

    def test_frame_smaller_than_box(self):
        self.assertEqual(DH.crop_box(100, 100, 448, 320, 240), (0, 0))


class TestSampleAndFile(unittest.TestCase):
    def test_status_from_head_likelihood(self):
        self.assertEqual(DH.status_for({"head_center": (1, 2, 0.6)}, 0.6), DH.STATUS_OK)
        self.assertEqual(DH.status_for({"head_center": (1, 2, 0.59)}, 0.6), DH.STATUS_LOW)
        self.assertEqual(DH.status_for({}, 0.6), DH.STATUS_LOW)

    def test_valid_needs_ok_and_head(self):
        ok = DH.DlcHeadSample(5, DH.STATUS_OK, points={"head_center": (3.0, 4.0, 0.9)})
        self.assertTrue(ok.valid)
        self.assertEqual(ok.head_center, (3.0, 4.0))
        self.assertFalse(DH.DlcHeadSample(5, DH.STATUS_LOW, points=ok.points).valid)
        self.assertIsNone(DH.DlcHeadSample(5, DH.STATUS_SKIPPED).head_center)

    def test_columns_and_row(self):
        cols = DH.columns(PARTS)
        self.assertEqual(cols[:5], list(DH.FIXED_COLUMNS))
        self.assertEqual(cols[5:8], ["nose_x", "nose_y", "nose_likelihood"])
        self.assertEqual(len(cols), 5 + 3 * len(PARTS))
        s = DH.DlcHeadSample(12, DH.STATUS_OK, 16.25, 100, 200,
                             {"nose": (101.234, 202.5, 0.98765)})
        r = dict(zip(cols, DH.row(s, PARTS)))
        self.assertEqual((r["cam_block_id"], r["dlc_status"], r["dlc_latency_ms"]),
                         ("12", "ok", "16.25"))
        self.assertEqual((r["crop_x0"], r["crop_y0"]), ("100", "200"))
        self.assertEqual((r["nose_x"], r["nose_y"], r["nose_likelihood"]),
                         ("101.23", "202.50", "0.988"))
        self.assertEqual(r["neck_x"], "")

    def test_skipped_row_is_blank_after_status(self):
        r = DH.row(DH.DlcHeadSample(None, DH.STATUS_SKIPPED), PARTS)
        self.assertEqual(r[:2], ["", "skipped"])
        self.assertTrue(all(v == "" for v in r[2:]))

    def test_header_lines(self):
        lines = DH.header_lines({"model_file": "m.pt", "model_sha256": "abc"}, 448, 0.6)
        self.assertIn("# crop_px: 448", lines)
        self.assertIn("# model_file: m.pt", lines)
        self.assertIn("# torch_version: ", lines)
        self.assertTrue(all(l.startswith("# ") for l in lines))


def _reader(data: bytes):
    buf = io.BytesIO(data)

    def read_exact(n):
        b = buf.read(n)
        if len(b) != n:
            raise ConnectionError("closed")
        return b
    return read_exact


class TestProtocol(unittest.TestCase):
    def test_hello_round_trip(self):
        info = {"parts": list(PARTS), "model_file": "m.pt"}
        tag, payload = P.read_message(_reader(P.encode_hello(info)))
        self.assertEqual(tag, P.TAG_HELLO)
        self.assertEqual(P.decode_hello(payload), info)

    def test_crop_round_trip(self):
        pix = bytes(range(2 * 3 * 3)) * 1   # 2x3 crop
        tag, payload = P.read_message(_reader(P.encode_crop(77, 10, 20, 2, 3, pix)))
        self.assertEqual(tag, P.TAG_CROP)
        self.assertEqual(P.decode_crop(payload), (77, 10, 20, 2, 3, pix))
        _, payload = P.read_message(_reader(P.encode_crop(None, 0, 0, 1, 1, b"abc")))
        self.assertIsNone(P.decode_crop(payload)[0])

    def test_crop_size_is_checked(self):
        with self.assertRaises(ValueError):
            P.encode_crop(1, 0, 0, 2, 2, b"short")

    def test_pose_round_trip(self):
        pts = [(1.5, 2.5, 0.9), (3.0, 4.0, 0.1), (5, 6, 0.5), (7, 8, 0.99), (9, 10, 0.7)]
        msg = P.encode_pose(42, DH.STATUS_OK, 16.5, pts)
        tag, payload = P.read_message(_reader(msg))
        self.assertEqual(tag, P.TAG_POSE)
        s = P.decode_pose(payload, PARTS)
        self.assertEqual((s.block_id, s.status), (42, "ok"))
        self.assertAlmostEqual(s.latency_ms, 16.5, places=4)
        self.assertAlmostEqual(s.points["head_center"][0], 7.0, places=4)
        self.assertAlmostEqual(s.points["right_ear"][2], 0.1, places=4)

    def test_pose_part_count_mismatch(self):
        _, payload = P.read_message(_reader(P.encode_pose(1, "low", 1.0, [(0, 0, 0)])))
        with self.assertRaises(ValueError):
            P.decode_pose(payload, PARTS)

    def test_bye_and_back_to_back_messages(self):
        data = P.encode_bye() + P.encode_pose(None, "error", 0.0, [])
        read = _reader(data)
        self.assertEqual(P.read_message(read), (P.TAG_BYE, b""))
        tag, payload = P.read_message(read)
        self.assertEqual(P.decode_pose(payload, ()).status, "error")

    def test_corrupt_length_rejected(self):
        bad = P.TAG_POSE + (P.MAX_PAYLOAD + 1).to_bytes(4, "little")
        with self.assertRaises(ValueError):
            P.read_message(_reader(bad))

    def test_closed_stream_raises(self):
        with self.assertRaises(ConnectionError):
            P.read_message(_reader(b"PO"))


if __name__ == "__main__":
    unittest.main()
