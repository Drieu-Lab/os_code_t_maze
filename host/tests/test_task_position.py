"""Headless tests for the task-position choice (tmaze.core.task_position;
docs/decisions/0230).

Covers: the selector (switch off = CoM; a fresh trusted head = dlc; too old /
low likelihood / no block ids = not fresh; the hold window, then the CoM
fallback; per-source counts); the sidecar columns and header; and that the
maze-state estimator and the rules engine take a DLC point (even while the
blob tracker coasts) but read the tracker exactly as before for CoM sources.

Run from host/:  python -m unittest tests.test_task_position
"""

from __future__ import annotations

import types
import unittest

from tmaze.core import dlc_head as DH
from tmaze.core import task_position as TP
from tmaze.core.phase3 import bundle_format
from tmaze.core.maze_state import CENTRAL_STEM, LEFT_CHOICE, MazeStateEstimator, MazeStateParams
from tmaze.core.rules_engine import EngineDefaults, RulesEngine
from tmaze.tools.maze_state_validate import make_zone_map

FRAME = (1000, 1000)
P_STEM = (500.0, 650.0)
P_LCHOICE = (200.0, 350.0)


def sample(block_id, x=10.0, y=20.0, lik=0.9, status=DH.STATUS_OK):
    return DH.DlcHeadSample(block_id, status, points={DH.HEAD_PART: (x, y, lik)})


def tr(xy, state="TRACKING", conf=0.9, task_point=None):
    return types.SimpleNamespace(detected=True, state=state, confidence=conf,
                                 frame_size=FRAME, centroid=xy, smoothed=xy,
                                 task_point=task_point)


def on(**kw) -> TP.TaskPositionSelector:
    sel = TP.TaskPositionSelector(**kw)
    sel.set_enabled(True)
    return sel


class TestSelector(unittest.TestCase):
    def test_off_is_com_even_with_dlc(self):
        sel = TP.TaskPositionSelector()
        p = sel.select(10, 0.0, (1.0, 2.0), sample(10))
        self.assertEqual((p.source, p.xy), (TP.SRC_COM, (1.0, 2.0)))
        self.assertEqual(sel.mode, TP.MODE_COM)

    def test_fresh_trusted_head(self):
        sel = on()
        p = sel.select(11, 0.0, (1.0, 2.0), sample(10))
        self.assertEqual((p.source, p.xy, p.likelihood), (TP.SRC_DLC, (10.0, 20.0), 0.9))
        self.assertEqual(sel.mode, TP.MODE_DLC_HEAD)

    def test_lag_limits(self):
        sel = on(max_lag_frames=2)
        self.assertEqual(sel.select(12, 0.0, None, sample(10)).source, TP.SRC_DLC)
        sel = on(max_lag_frames=2)
        self.assertEqual(sel.select(13, 0.0, None, sample(10)).source, TP.SRC_COM_FALLBACK)
        sel = on(max_lag_frames=2)   # an answer "from the future" is not trusted
        self.assertEqual(sel.select(9, 0.0, None, sample(10)).source, TP.SRC_COM_FALLBACK)

    def test_untrusted_or_unkeyed_is_not_fresh(self):
        for s, bid in ((sample(10, lik=0.3, status=DH.STATUS_LOW), 10),
                       (DH.DlcHeadSample(10, DH.STATUS_SKIPPED), 10),
                       (sample(None), 10), (sample(10), None),
                       (sample(10, x=float("nan")), 10), (None, 10)):
            p = on().select(bid, 0.0, (1.0, 2.0), s)
            self.assertEqual((p.source, p.xy), (TP.SRC_COM_FALLBACK, (1.0, 2.0)))

    def test_hold_then_fallback(self):
        sel = on(hold_s=0.15)
        sel.select(10, 1.00, (1.0, 2.0), sample(10))
        p = sel.select(11, 1.10, (1.0, 2.0), sample(10, status=DH.STATUS_LOW, lik=0.2))
        self.assertEqual((p.source, p.xy), (TP.SRC_DLC_HOLD, (10.0, 20.0)))
        p = sel.select(12, 1.20, (1.0, 2.0), None)
        self.assertEqual((p.source, p.xy), (TP.SRC_COM_FALLBACK, (1.0, 2.0)))
        # The expired hold is dropped: a later gap does not revive it.
        self.assertEqual(sel.select(13, 1.21, None, None).source, TP.SRC_COM_FALLBACK)

    def test_switching_off_drops_the_hold(self):
        sel = on()
        sel.select(10, 0.0, None, sample(10))
        sel.set_enabled(False)
        sel.set_enabled(True)
        self.assertEqual(sel.select(11, 0.01, None, None).source, TP.SRC_COM_FALLBACK)

    def test_counts(self):
        sel = on()
        sel.select(10, 0.0, None, sample(10))
        sel.select(11, 0.0, None, None)
        sel.select(12, 9.0, None, None)
        c = sel.counts()
        self.assertEqual((c[TP.SRC_DLC], c[TP.SRC_DLC_HOLD], c[TP.SRC_COM_FALLBACK]), (1, 1, 1))
        tally = sel.tally(11.0)
        self.assertEqual(tally.recent, {TP.SRC_COM_FALLBACK: 1})   # the 0.0 frames aged out
        self.assertEqual(sum(tally.total.values()), 3)
        sel.reset_counts()
        self.assertEqual(sum(sel.counts().values()), 0)


class TestSidecar(unittest.TestCase):
    def test_rows(self):
        self.assertEqual(TP.task_row(None), ("", "", ""))
        self.assertEqual(TP.task_row(TP.TaskPoint(None, None, TP.SRC_COM_FALLBACK)),
                         ("com_fallback", "", ""))
        self.assertEqual(TP.task_row(TP.TaskPoint(1.25, 2.0, TP.SRC_DLC, 0.9)),
                         ("dlc", "1.2", "2.0"))
        self.assertEqual(tuple(TP.column_doc()), TP.TASK_COLUMNS)

    def test_format_manifest_documents_every_column(self):
        doc = bundle_format.format_manifest()["sidecar_task_columns"]
        self.assertEqual(tuple(doc), TP.TASK_COLUMNS)
        self.assertTrue(all(doc.values()))

    def test_header(self):
        self.assertEqual(TP.header_fields(None), {"task_position": "com"})
        self.assertEqual(TP.header_fields(TP.TaskPositionSelector()), {"task_position": "com"})
        h = TP.header_fields(on(max_lag_frames=2, hold_s=0.15), pcutoff=0.6)
        self.assertEqual((h["task_position"], h["task_position_part"],
                          h["task_position_max_lag_frames"], h["task_position_hold_s"],
                          h["task_position_pcutoff"]),
                         ("dlc_head", "head_center", "2", "0.15", "0.6"))


class TestConsumers(unittest.TestCase):
    def setUp(self):
        self.est = MazeStateEstimator(make_zone_map(), MazeStateParams(min_dwell_ticks=1))

    def test_com_sources_read_the_tracker_as_before(self):
        for src in (TP.SRC_COM, TP.SRC_COM_FALLBACK):
            est = MazeStateEstimator(make_zone_map(), MazeStateParams(min_dwell_ticks=1))
            tp = TP.TaskPoint(*P_LCHOICE, src)   # ignored: not a DLC source
            self.assertEqual(est.update(tr(P_STEM, task_point=tp), 0.0).zone, CENTRAL_STEM)
            # ...including the tracker's coasting state making the frame invalid.
            self.assertFalse(est.update(tr(P_STEM, state="COASTING", task_point=tp), 0.1).valid)

    def test_dlc_point_drives_the_zone(self):
        self.est.update(tr(P_STEM), 0.0)
        tp = TP.TaskPoint(*P_LCHOICE, TP.SRC_DLC, 0.9)
        s = self.est.update(tr(P_STEM, task_point=tp), 0.1)
        self.assertEqual((s.zone, s.confidence), (LEFT_CHOICE, 0.9))

    def test_dlc_point_counts_while_blob_tracker_coasts(self):
        tp = TP.TaskPoint(*P_LCHOICE, TP.SRC_DLC_HOLD, 0.8)
        s = self.est.update(tr(None, state="COASTING", conf=0.0, task_point=tp), 0.0)
        self.assertTrue(s.valid)
        self.assertEqual(s.zone, LEFT_CHOICE)

    def test_rules_frame(self):
        eng = RulesEngine([], EngineDefaults())
        f = eng._make_frame(tr(P_STEM), [], None, 0.0)
        self.assertEqual((f.centroid_px, f.state), (P_STEM, "TRACKING"))
        tp = TP.TaskPoint(*P_LCHOICE, TP.SRC_DLC, 0.7)
        f = eng._make_frame(tr(None, state="LOST", conf=0.0, task_point=tp), [], None, 0.0)
        self.assertEqual((f.centroid_px, f.state, f.confidence), (P_LCHOICE, "TRACKING", 0.7))


if __name__ == "__main__":
    unittest.main()
