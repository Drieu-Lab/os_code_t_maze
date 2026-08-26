"""
test_analysis_trajectories.py - headless tests for the per-frame trajectory core.

Covers the pure helpers in ``tmaze.analysis.trajectories`` over small synthetic
inputs: the ISO<->POSIX time conversion (the join key onto the sidecar), sidecar
loading + validity filtering + normalization, the cue->choice outbound windows,
time slicing, the VTE dwell integral against a hand-built ZoneMap, the
consecutive-block cut + low/high pick, and the SciPy-free Mann-Whitney U. No
hardware / Tk / matplotlib.

Run from host/tests:  python -m unittest test_analysis_trajectories
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime

import pandas as pd

from tmaze.analysis import trajectories as tj
from tmaze.core import maze_state


def _square_zone_map(name=maze_state.CENTRAL_STEM):
    """A ZoneMap with one axis-aligned square [0.4,0.6]x[0.3,0.6] named ``name``."""
    poly = ((0.4, 0.3), (0.6, 0.3), (0.6, 0.6), (0.4, 0.6))
    return maze_state.ZoneMap(zones=(maze_state.Zone(name, poly),))


def _write_sidecar(rows, width=1000, height=1000):
    """Write a temp sidecar CSV (header + rows) and return its path."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    lines = [
        "# animal: 600",
        "# session_id: 600_20260101_120000",
        f"# width: {width}",
        f"# height: {height}",
        "# start_wall_clock: 2026-01-01 12:00:00",
        ("frame_idx,host_unix_s,track_state,track_detected,track_smooth_cx,"
         "track_smooth_cy,track_confidence"),
    ]
    for i, r in enumerate(rows):
        lines.append(
            f"{i},{r['t']},{r.get('state', 'TRACKING')},1,"
            f"{r['cx']},{r['cy']},{r.get('conf', 1.0)}"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


class TestIsoToUnix(unittest.TestCase):
    def test_round_trip_local(self):
        dt = datetime(2026, 8, 25, 13, 33, 1, 65000)
        self.assertAlmostEqual(tj.iso_to_unix(dt.isoformat()), dt.timestamp(), places=6)

    def test_blank_and_bad_are_nan(self):
        self.assertTrue(pd.isna(tj.iso_to_unix("")))
        self.assertTrue(pd.isna(tj.iso_to_unix(None)))
        self.assertTrue(pd.isna(tj.iso_to_unix("not-a-date")))

    def test_series(self):
        dt = datetime(2026, 1, 1, 0, 0, 0)
        s = tj.iso_series_to_unix(pd.Series([dt.isoformat(), ""]))
        self.assertAlmostEqual(s.iloc[0], dt.timestamp(), places=6)
        self.assertTrue(pd.isna(s.iloc[1]))


class TestLoadSidecar(unittest.TestCase):
    def test_filter_and_normalize(self):
        path = _write_sidecar(
            [
                {"t": 100.0, "cx": 500, "cy": 300},                       # keep
                {"t": 100.04, "cx": 500, "cy": 300, "state": "COASTING"},  # drop: state
                {"t": 100.08, "cx": 500, "cy": 300, "conf": 0.1},          # drop: conf
                {"t": 100.12, "cx": 250, "cy": 750},                       # keep
            ]
        )
        try:
            sc = tj.load_sidecar(path)
        finally:
            os.remove(path)
        self.assertEqual(sc.width, 1000.0)
        self.assertEqual(len(sc.frames), 2)
        self.assertAlmostEqual(sc.frames["cx_n"].iloc[0], 0.5)
        self.assertAlmostEqual(sc.frames["cy_n"].iloc[1], 0.75)


class TestOutboundWindows(unittest.TestCase):
    def _trials(self):
        return pd.DataFrame([
            {"session_id": "s", "run_idx": 0, "trial": 1, "choice_cue": "HIGH",
             "chosen_arm": "LEFT", "outcome": "CORRECT", "completed": True,
             "correct": True, "trial_start_iso": "2026-01-01T12:00:00",
             "choice_time_iso": "2026-01-01T12:00:02"},
            {"session_id": "s", "run_idx": 0, "trial": 2, "choice_cue": "LOW",
             "chosen_arm": "RIGHT", "outcome": "INCORRECT", "completed": True,
             "correct": False, "trial_start_iso": "2026-01-01T12:00:10",
             "choice_time_iso": ""},  # missing bound -> dropped
            {"session_id": "s", "run_idx": 0, "trial": 3, "choice_cue": "LOW",
             "chosen_arm": "", "outcome": "ABANDONED", "completed": False,
             "correct": False, "trial_start_iso": "2026-01-01T12:00:20",
             "choice_time_iso": "2026-01-01T12:00:21"},  # not completed -> dropped
        ])

    def test_windows_and_cue_labels(self):
        w = tj.outbound_windows(self._trials(), end_pad_s=0.5)
        self.assertEqual(len(w), 1)
        row = w.iloc[0]
        self.assertEqual(row["cue"], tj.CUE_A)  # HIGH -> A
        self.assertAlmostEqual(row["t_end"] - row["t_choice"], 0.5, places=6)
        self.assertAlmostEqual(row["t_choice"] - row["t_start"], 2.0, places=6)

    def test_keep_incomplete_when_asked(self):
        w = tj.outbound_windows(self._trials(), completed_only=False)
        # trial 2 still dropped (no choice_time), trial 3 now kept.
        self.assertEqual(set(w["trial"]), {1, 3})


class TestSliceAndDwell(unittest.TestCase):
    def test_slice_xy_bounds(self):
        path = _write_sidecar([{"t": float(i), "cx": 500, "cy": 400} for i in range(5)])
        try:
            sc = tj.load_sidecar(path)
        finally:
            os.remove(path)
        seg = tj.slice_xy(sc, 1.0, 3.0)
        self.assertEqual(list(seg["host_unix_s"]), [1.0, 2.0, 3.0])
        self.assertTrue(tj.slice_xy(sc, 3.0, 1.0).empty)

    def test_dwell_seconds_left_riemann(self):
        # Samples at t=0,1,2,3 (dt=1 each). Inside the square: t0,t1 (0.5,0.45),
        # outside: t2,t3 (0.9,0.9). Left-Riemann credits gaps to earlier sample:
        # gap[0]=inside, gap[1]=inside (t1 inside), gap[2]=outside -> 2.0s.
        zm = _square_zone_map()
        seg = pd.DataFrame({
            "host_unix_s": [0.0, 1.0, 2.0, 3.0],
            "cx_n": [0.5, 0.45, 0.9, 0.9],
            "cy_n": [0.45, 0.4, 0.9, 0.9],
        })
        self.assertAlmostEqual(tj.dwell_seconds(seg, zm), 2.0, places=6)

    def test_dwell_zero_for_short_segment(self):
        zm = _square_zone_map()
        seg = pd.DataFrame({"host_unix_s": [0.0], "cx_n": [0.5], "cy_n": [0.45]})
        self.assertEqual(tj.dwell_seconds(seg, zm), 0.0)


class TestBlocks(unittest.TestCase):
    def _signals(self):
        # Block 1 (trials 1-4): 1/4 correct. Block 2 (trials 5-8): 3/4 correct.
        rows = []
        pattern = [True, False, False, False, True, True, True, False]
        for i, ok in enumerate(pattern, start=1):
            rows.append({
                "session_id": "s", "run_idx": 0, "trial": i,
                "choice_cue": "HIGH" if i % 2 else "LOW",
                "outcome": "CORRECT" if ok else "INCORRECT",
                "completed": True, "correct": ok,
            })
        return pd.DataFrame(rows)

    def test_make_and_select(self):
        blocks = tj.make_blocks(self._signals(), block=4)
        self.assertEqual(len(blocks), 2)
        self.assertAlmostEqual(blocks[0].accuracy, 0.25)
        self.assertAlmostEqual(blocks[1].accuracy, 0.75)
        low, high = tj.select_blocks(self._signals(), block=4)
        self.assertAlmostEqual(low.accuracy, 0.25)
        self.assertAlmostEqual(high.accuracy, 0.75)
        self.assertEqual(low.n_a + low.n_b, 4)

    def test_partial_trailing_block_dropped(self):
        sig = self._signals().iloc[:6]  # 6 trials, block=4 -> one full block
        self.assertEqual(len(tj.make_blocks(sig, block=4)), 1)

    def test_empty(self):
        self.assertEqual(tj.select_blocks(pd.DataFrame(), block=4), (None, None))


class TestMannWhitney(unittest.TestCase):
    def test_separated_groups_small_p(self):
        u, p = tj.mann_whitney_u([1, 2, 3, 4, 5], [10, 11, 12, 13, 14])
        self.assertEqual(u, 0.0)
        self.assertLess(p, 0.05)

    def test_identical_groups_large_p(self):
        _, p = tj.mann_whitney_u([1, 2, 3, 4], [1, 2, 3, 4])
        self.assertGreater(p, 0.5)

    def test_empty_group_nan(self):
        _, p = tj.mann_whitney_u([], [1, 2, 3])
        self.assertTrue(pd.isna(p))


if __name__ == "__main__":
    unittest.main()
