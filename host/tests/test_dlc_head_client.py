"""
Headless tests for the DeepLabCut-Live head preview's two processes talking:
the rig-side client (tmaze.io.dlc_head_client) against the real helper loop
(tmaze.tools.dlc_head_server.serve_one) with a FAKE model, so no GPU, torch or
dlclive is needed. See docs/decisions/0224.
"""

import os
import socket
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

from tmaze.core import dlc_head as DH
from tmaze.io import dlc_head_client as C
from tmaze.tools import dlc_head_server as S

PARTS = ["nose", "head_center", "neck"]


def fake_predict(crop):
    """Two 'animals'; the second is more confident. Points sit at fixed spots
    inside the crop, with head_center's likelihood = the crop's first pixel/255."""
    lik = crop[0, 0, 0] / 255.0
    weak = [[1, 1, 0.1]] * 3
    strong = [[10, 20, 0.9], [30, 40, lik], [50, 60, 0.9]]
    return np.array([weak, strong], dtype=float)


class _FakeHelper:
    """serve_one on a free localhost port, on a thread."""

    def __init__(self, predict=fake_predict):
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        info = {"parts": PARTS, "model_file": "fake.pt", "device": "cpu"}

        def run():
            conn, _ = self._srv.accept()
            with conn:
                S.serve_one(conn, predict, info, 0.6)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def close(self):
        self._srv.close()


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _client(port, **kw):
    cfg = C.DlcHeadConfig(python="", model_path="", port=port, crop_px=64,
                          start_timeout_s=5.0, **kw)
    c = C.DlcHeadClient(cfg, spawn=False)
    c.start()
    return c


class TestServerHelpers(unittest.TestCase):
    def test_pick_rat_takes_most_confident(self):
        rat = S.pick_rat(fake_predict(np.full((4, 4, 3), 255, np.uint8)))
        self.assertEqual(rat[0].tolist(), [10, 20, 0.9])

    def test_answer_adds_crop_corner_and_grades(self):
        status, pts = S.answer(fake_predict(np.zeros((4, 4, 3), np.uint8)), PARTS, 100, 200, 0.6)
        self.assertEqual(status, DH.STATUS_LOW)          # head_center likelihood 0
        self.assertEqual(pts[1][:2], (130.0, 240.0))


class TestClientAgainstHelper(unittest.TestCase):
    def setUp(self):
        self.helper = _FakeHelper()
        self.client = _client(self.helper.port)
        self.assertTrue(_wait(lambda: self.client.ready), self.client.error)

    def tearDown(self):
        self.client.close()
        self.helper.close()

    def test_round_trip_full_frame_coordinates(self):
        frame = np.full((480, 640, 3), 255, np.uint8)
        self.client.submit(7, frame, (320.0, 240.0))
        self.assertTrue(_wait(lambda: self.client.latest() is not None))
        s = self.client.latest()
        self.assertEqual((s.block_id, s.status), (7, DH.STATUS_OK))
        x0, y0 = DH.crop_box(320.0, 240.0, 64, 640, 480)
        self.assertEqual((s.crop_x0, s.crop_y0), (x0, y0))
        self.assertAlmostEqual(s.head_center[0], x0 + 30)
        self.assertAlmostEqual(s.head_center[1], y0 + 40)
        self.assertTrue(s.valid)

    def test_busy_helper_skips_instead_of_waiting(self):
        frame = np.zeros((480, 640, 3), np.uint8)
        pending = DH.CropPlan(0, 0, 64, 64, DH.SRC_COM)
        self.client._in_flight = (1, pending, time.monotonic())   # pretend one is pending
        t0 = time.perf_counter()
        self.client.submit(2, frame, (100.0, 100.0))
        self.assertLess(time.perf_counter() - t0, 0.05)
        st = self.client.stats()
        self.assertEqual((st["total"]["skipped"], st["recent"]["skipped"]), (1, 1))
        self.assertEqual(st["total"]["skipped_frac"], 1.0)
        # Ten seconds on, the skip has left the recent window but not the total.
        later = self.client.stats(now=time.monotonic() + st["window_s"] + 1)
        self.assertEqual((later["total"]["skipped"], later["recent"]["skipped"]), (1, 0))

    def _answered(self, block_id, frame, com, roi=None):
        sent = self.client.stats()["total"]["sent"]
        self.client.submit(block_id, frame, com, roi)
        self.assertTrue(_wait(lambda: self.client.stats()["total"]["sent"] == sent + 1 and
                              self.client._in_flight is None))
        return self.client.latest()

    def test_lost_rat_holds_last_com_then_uses_roi(self):
        frame = np.full((480, 640, 3), 255, np.uint8)
        roi = [(0.25, 0.25), (0.75, 0.25), (0.5, 0.75)]
        first = self._answered(1, frame, (320.0, 240.0), roi)
        self.assertEqual(first.crop_src, DH.SRC_COM)
        held = self._answered(2, frame, None, roi)
        self.assertEqual((held.crop_src, held.crop_x0, held.crop_y0),
                         (DH.SRC_HOLD, first.crop_x0, first.crop_y0))
        self.client._planner._last_com_t -= 2.0          # the hold has run out
        box = self._answered(3, frame, None, roi)
        self.assertEqual((box.crop_src, box.crop_x0, box.crop_y0, box.crop_w, box.crop_h),
                         (DH.SRC_ROI, 160, 120, 320, 240))
        self.assertAlmostEqual(box.head_center[0], 160 + 30)

    def test_recording_writes_every_frame_status(self):
        path = os.path.join(tempfile.mkdtemp(), "s_dlc_head.csv")
        self.assertEqual(self.client.start_recording(path), path)
        frame = np.full((480, 640, 3), 255, np.uint8)
        self._answered(1, frame, (320.0, 240.0))
        self.client._planner._last_com_t -= 2.0
        self.client.submit(2, frame, None)                   # lost, no ROI set
        self.client.stop_recording()
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        body = [l for l in lines if not l.startswith("#")]
        self.assertEqual(body[0].split(","), DH.columns(PARTS))
        self.assertTrue(body[1].startswith("1,ok,"))
        self.assertTrue(body[1].split(",")[7] == DH.SRC_COM)
        self.assertTrue(body[2].startswith("2,no_blob,"))
        self.assertTrue(any(l.startswith("# model_file") for l in lines))
        self.assertIn("# roi_fallback: on", lines)


class TestFailures(unittest.TestCase):
    def test_hung_helper_turns_to_error(self):
        stop = threading.Event()
        helper = _FakeHelper(predict=lambda crop: (stop.wait(5), fake_predict(crop))[1])
        client = _client(helper.port)
        try:
            self.assertTrue(_wait(lambda: client.ready))
            frame = np.zeros((480, 640, 3), np.uint8)
            client.submit(1, frame, (100.0, 100.0))
            old = C.HUNG_AFTER_S
            C.HUNG_AFTER_S = 0.05
            try:
                time.sleep(0.1)
                client.submit(2, frame, (100.0, 100.0))
            finally:
                C.HUNG_AFTER_S = old
            self.assertEqual(client.state, C.STATE_ERROR)
            self.assertIn("stopped answering", client.error)
        finally:
            stop.set()
            client.close()
            helper.close()

    def test_helper_disconnect_turns_to_error(self):
        helper = _FakeHelper()
        client = _client(helper.port)
        self.assertTrue(_wait(lambda: client.ready))
        client._sock.shutdown(socket.SHUT_RDWR)
        self.assertTrue(_wait(lambda: client.state == C.STATE_ERROR))
        client.close()
        helper.close()

    def test_no_helper_listening_times_out(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cfg = C.DlcHeadConfig(python="", model_path="", port=port, start_timeout_s=1.0)
        client = C.DlcHeadClient(cfg, spawn=False)
        client.start()
        self.assertTrue(_wait(lambda: client.state == C.STATE_ERROR, 5.0))
        self.assertIn("did not answer", client.error)

    def test_missing_model_reports_before_spawning(self):
        cfg = C.DlcHeadConfig(python=sys.executable, model_path="no_such.pt", port=1)
        client = C.DlcHeadClient(cfg)
        client.start()
        self.assertTrue(_wait(lambda: client.state == C.STATE_ERROR))
        self.assertIn("model not found", client.error)


class TestConfig(unittest.TestCase):
    def test_committed_config_loads(self):
        cfg = C.load_config()
        self.assertEqual(cfg.crop_px, 448)
        self.assertTrue(os.path.isabs(cfg.model_path))
        self.assertEqual((cfg.hold_s, cfg.roi_fallback), (1.0, True))


if __name__ == "__main__":
    unittest.main()
