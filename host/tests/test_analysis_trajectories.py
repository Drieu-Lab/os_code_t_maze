"""
test_analysis_trajectories.py - headless tests for the per-frame trajectory core.

Covers the pure helpers in ``tmaze.analysis.trajectories`` over small synthetic
inputs: the ISO<->POSIX time conversion (the join key onto the sidecar), sidecar
loading + validity filtering + normalization, the cue->choice outbound windows,
time slicing, the VTE dwell integral against a hand-built ZoneMap, the
consecutive-block cut + low/high pick, the SciPy-free Mann-Whitney U, and the
peri-junction velocity kinematics (scale load, stem axis, constant-velocity speed
+ signed distance projection, the junction slowing/pause scalars, mean profile
binning). No hardware / Tk / matplotlib.

Run from host/tests:  python -m unittest test_analysis_trajectories
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime

import numpy as np
import pandas as pd

from tmaze.analysis import trajectories as tj
from tmaze.core import maze_state


def _square_zone_map(name=maze_state.CENTRAL_STEM):
    """A ZoneMap with one axis-aligned square [0.4,0.6]x[0.3,0.6] named ``name``."""
    poly = ((0.4, 0.3), (0.6, 0.3), (0.6, 0.6), (0.4, 0.6))
    return maze_state.ZoneMap(zones=(maze_state.Zone(name, poly),))


def _stem_junction_zone_map():
    """ZoneMap with a CENTRAL_STEM square (upper) and a JUNCTION square (lower)."""
    stem = ((0.4, 0.5), (0.6, 0.5), (0.6, 0.7), (0.4, 0.7))
    junc = ((0.4, 0.2), (0.6, 0.2), (0.6, 0.4), (0.4, 0.4))
    return maze_state.ZoneMap(zones=(
        maze_state.Zone(maze_state.CENTRAL_STEM, stem),
        maze_state.Zone(maze_state.JUNCTION, junc),
    ))


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

    def test_custom_events_and_pads(self):
        trials = pd.DataFrame([{
            "session_id": "s", "run_idx": 0, "trial": 1, "choice_cue": "HIGH",
            "chosen_arm": "LEFT", "outcome": "CORRECT", "completed": True,
            "correct": True, "trial_start_iso": "2026-01-01T12:00:00",
            "choice_time_iso": "2026-01-01T12:00:02",
            "trial_end_iso": "2026-01-01T12:00:05",
        }])
        w = tj.outbound_windows(trials, start_event="trial_start_iso",
                                end_event="trial_end_iso", start_pad_s=0.25,
                                end_pad_s=0.5)
        row = w.iloc[0]
        # window = [start + 0.25, trial_end + 0.5]; t_choice kept for reference.
        self.assertAlmostEqual(row["t_end"] - row["t_start"], 5.0 + 0.5 - 0.25, places=6)
        self.assertAlmostEqual(row["t_end"] - row["t_choice"], 3.0 + 0.5, places=6)

    def test_unknown_event_raises(self):
        with self.assertRaises(ValueError):
            tj.outbound_windows(self._trials(), end_event="not_an_event_iso")


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

    def test_step_defaults_to_non_overlapping(self):
        # step=None (default) must equal an explicit step=block: tiling.
        a = tj.make_blocks(self._signals(), block=4)
        b = tj.make_blocks(self._signals(), block=4, step=4)
        self.assertEqual([bl.start_trial for bl in a], [bl.start_trial for bl in b])
        self.assertEqual([bl.start_trial for bl in a], [1, 5])

    def test_sliding_step_one_overlaps(self):
        sig = self._signals().iloc[:6]  # 6 trials, block=4, step=1 -> 3 windows
        blocks = tj.make_blocks(sig, block=4, step=1)
        self.assertEqual([b.start_trial for b in blocks], [1, 2, 3])
        self.assertEqual([b.end_trial for b in blocks], [4, 5, 6])
        self.assertTrue(tj._blocks_overlap(blocks[0], blocks[1]))   # share 2,3,4

    def test_exclude_intervention_skips_straddling_window(self):
        # 8 trials, trial 3 is an intervention (in_block). block=4, step=1.
        sig = self._signals()
        sig["in_block"] = [False, False, True, False, False, False, False, False]
        # Default (exclude): windows [1-4],[2-5],[3-6] all contain trial 3 -> dropped;
        # only [4-7] and [5-8] survive.
        starts = [b.start_trial for b in tj.make_blocks(sig, block=4, step=1)]
        self.assertEqual(starts, [4, 5])
        # Opt out: every sliding window is kept.
        starts_all = [b.start_trial for b in
                      tj.make_blocks(sig, block=4, step=1, exclude_intervention=False)]
        self.assertEqual(starts_all, [1, 2, 3, 4, 5])

    def test_exclude_intervention_no_op_without_column(self):
        # No in_block column -> the flag has no effect (backward compatible).
        a = tj.make_blocks(self._signals(), block=4, step=1, exclude_intervention=True)
        b = tj.make_blocks(self._signals(), block=4, step=1, exclude_intervention=False)
        self.assertEqual([bl.start_trial for bl in a], [bl.start_trial for bl in b])

    def test_select_sliding_returns_disjoint_pair(self):
        # 8 trials, block=4, step=1 -> the only disjoint pair is [1-4] vs [5-8].
        blocks = tj.make_blocks(self._signals(), block=4, step=1)
        self.assertEqual(len(blocks), 5)
        low, high = tj.select_blocks(self._signals(), block=4, step=1)
        self.assertFalse(tj._blocks_overlap(low, high))
        self.assertLessEqual(low.accuracy, high.accuracy)
        self.assertEqual({low.start_trial, high.start_trial}, {1, 5})

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


class TestLoadScale(unittest.TestCase):
    def test_reads_cm_per_px(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "config"))
        with open(os.path.join(d, "config", "rig_scale.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"cm_per_px": 0.42}, fh)
        self.assertAlmostEqual(tj.load_scale(d), 0.42)

    def test_missing_falls_back(self):
        d = tempfile.mkdtemp()  # no config/rig_scale.json
        self.assertAlmostEqual(tj.load_scale(d), tj.DEFAULT_CM_PER_PX)


class TestStemAxis(unittest.TestCase):
    def test_derives_hub_to_junction_centroids(self):
        hub = ((0.4, 0.8), (0.6, 0.8), (0.6, 1.0), (0.4, 1.0))    # centroid (0.5, 0.9)
        junc = ((0.4, 0.2), (0.6, 0.2), (0.6, 0.4), (0.4, 0.4))   # centroid (0.5, 0.3)
        zm = maze_state.ZoneMap(zones=(
            maze_state.Zone(maze_state.HUB, hub),
            maze_state.Zone(maze_state.JUNCTION, junc),
        ))
        base, tip = tj.stem_axis(zm)
        self.assertAlmostEqual(base[0], 0.5)
        self.assertAlmostEqual(base[1], 0.9)
        self.assertAlmostEqual(tip[0], 0.5)
        self.assertAlmostEqual(tip[1], 0.3)

    def test_prefers_explicit_axis(self):
        zm = maze_state.ZoneMap(
            zones=(maze_state.Zone(maze_state.JUNCTION,
                                   ((0.4, 0.2), (0.6, 0.2), (0.5, 0.4))),),
            stem_base=(0.1, 0.2), stem_tip=(0.3, 0.4),
        )
        self.assertEqual(tj.stem_axis(zm), ((0.1, 0.2), (0.3, 0.4)))

    def test_missing_zones_none(self):
        self.assertIsNone(tj.stem_axis(_square_zone_map(maze_state.LEFT_CHOICE)))


class TestAddKinematics(unittest.TestCase):
    def test_constant_velocity_speed_and_projection(self):
        # cx sweeps 0->100 px over 1 s (11 samples), cy fixed. cm_per_px = 0.5 ->
        # speed = 100 px/s * 0.5 = 50 cm/s everywhere.
        t = np.linspace(0.0, 1.0, 11)
        cx = np.linspace(0.0, 100.0, 11)
        frames = pd.DataFrame({
            "host_unix_s": t,
            "track_smooth_cx": cx,
            "track_smooth_cy": np.full(11, 50.0),
        })
        axis = ((0.0, 0.5), (1.0, 0.5))  # horizontal axis, junction at x=100 px
        out = tj.add_kinematics(frames, cm_per_px=0.5, axis=axis,
                                width=100.0, height=100.0, smooth_n=1)
        self.assertTrue(np.allclose(out["speed_cms"], 50.0))
        # s = 0 at the tip (cx=100), negative behind it (cx=50 -> (50-100)*0.5).
        self.assertAlmostEqual(out["s_junction_cm"].iloc[-1], 0.0, places=6)
        self.assertAlmostEqual(out["s_junction_cm"].iloc[5], -25.0, places=6)

    def test_no_axis_leaves_nan(self):
        frames = pd.DataFrame({
            "host_unix_s": [0.0, 0.1],
            "track_smooth_cx": [0.0, 10.0],
            "track_smooth_cy": [0.0, 0.0],
        })
        out = tj.add_kinematics(frames, cm_per_px=1.0, axis=None,
                                width=100.0, height=100.0)
        self.assertTrue(out["s_junction_cm"].isna().all())


class TestJunctionKinematics(unittest.TestCase):
    def _seg(self):
        # A run that decelerates to its MINIMUM (3 cm/s) while still up-stem
        # (s_junction_cm = -8, inside the [-10, +5] band but OUTSIDE the JUNCTION
        # polygon, cy_n in the stem square), proving the spatial band catches a
        # pre-border slowdown that JUNCTION-zone membership would miss (that would
        # return 8, the slowest in-polygon frame). Two fast up-stem frames precede it
        # (s < -10, outside the band); two junction frames follow (s = -3, +3).
        return pd.DataFrame({
            "host_unix_s": [0.0, 0.1, 0.2, 0.3, 0.4],
            "cx_n": [0.5, 0.5, 0.5, 0.5, 0.5],
            "cy_n": [0.60, 0.55, 0.52, 0.35, 0.30],  # stem,stem,stem,junc,junc
            "s_junction_cm": [-20.0, -15.0, -8.0, -3.0, 3.0],
            "speed_cms": [100.0, 100.0, 3.0, 8.0, 40.0],
        })

    def test_scalars(self):
        m = tj.junction_kinematics(self._seg(), _stem_junction_zone_map(),
                                   pause_speed_cms=10.0)
        # Min over the band [-10, +5] = the up-stem dip (3), NOT the in-polygon 8.
        self.assertAlmostEqual(m["min_speed_cms"], 3.0)
        self.assertAlmostEqual(m["peak_stem_speed_cms"], 100.0)
        self.assertAlmostEqual(m["slowing_index"], 1.0 - 3.0 / 100.0)
        # Left-Riemann: gaps after the two below-threshold band frames (s=-8 @3,
        # s=-3 @8) -> 0.2 s.
        self.assertAlmostEqual(m["pause_time_s"], 0.2, places=6)
        self.assertEqual(m["n_samples"], 5)

    def test_band_width_is_configurable(self):
        # Widen the "after" edge to +5 only vs shrink "before" to 5 cm: the s=-8 dip
        # now falls outside the band, so the min comes from the in-band frames (8).
        m = tj.junction_kinematics(self._seg(), _stem_junction_zone_map(),
                                   pause_speed_cms=10.0, band_before_cm=5.0,
                                   band_after_cm=5.0)
        self.assertAlmostEqual(m["min_speed_cms"], 8.0)

    def test_no_band_frames_nan(self):
        # All frames far up-stem (s < -10) -> nothing in the band -> min/slowing NaN,
        # but peak_stem is still defined from the stem zone.
        seg = pd.DataFrame({
            "host_unix_s": [0.0, 0.1],
            "cx_n": [0.5, 0.5],
            "cy_n": [0.60, 0.55],
            "s_junction_cm": [-30.0, -25.0],
            "speed_cms": [100.0, 90.0],
        })
        m = tj.junction_kinematics(seg, _stem_junction_zone_map())
        self.assertTrue(pd.isna(m["min_speed_cms"]))
        self.assertTrue(pd.isna(m["slowing_index"]))
        self.assertAlmostEqual(m["peak_stem_speed_cms"], 100.0)
        self.assertEqual(m["pause_time_s"], 0.0)

    def test_missing_s_column_nan(self):
        # Without s_junction_cm (axis was None) the band is empty -> min NaN.
        seg = pd.DataFrame({
            "host_unix_s": [0.0, 0.1, 0.2],
            "cx_n": [0.5, 0.5, 0.5],
            "cy_n": [0.35, 0.35, 0.30],
            "speed_cms": [5.0, 5.0, 40.0],
        })
        m = tj.junction_kinematics(seg, _stem_junction_zone_map())
        self.assertTrue(pd.isna(m["min_speed_cms"]))


class TestMeanProfile(unittest.TestCase):
    def test_binning_mean_and_n(self):
        p1 = pd.DataFrame({"s_junction_cm": [-10.0, -10.0, 0.0],
                           "speed_cms": [10.0, 20.0, 30.0]})
        p2 = pd.DataFrame({"s_junction_cm": [0.0], "speed_cms": [50.0]})
        prof = tj.mean_profile([p1, p2], bin_edges=np.array([-15.0, -5.0, 5.0]))
        self.assertEqual(list(prof["s_cm"]), [-10.0, 0.0])
        self.assertAlmostEqual(prof["mean"].iloc[0], 15.0)  # (10+20)/2
        self.assertAlmostEqual(prof["mean"].iloc[1], 40.0)  # (30+50)/2
        self.assertEqual(list(prof["n"]), [2, 2])

    def test_empty_returns_empty(self):
        prof = tj.mean_profile([], bin_edges=np.array([-5.0, 5.0]))
        self.assertTrue(prof.empty)


class TestSpeedOccupancyGrid(unittest.TestCase):
    def test_counts_and_masked_mean_speed(self):
        # Bin A (~0.25, 0.25): 4 frames at speed 10; bin B (~0.75, 0.75): 2 frames
        # at speed 50. With min_count=3, A keeps its mean (10), B is masked (n<3).
        cx = [0.25, 0.25, 0.25, 0.25, 0.75, 0.75]
        cy = [0.25, 0.25, 0.25, 0.25, 0.75, 0.75]
        sp = [10.0, 10.0, 10.0, 10.0, 50.0, 50.0]
        occ, mean_sp, xe, ye = tj.speed_occupancy_grid(cx, cy, sp, bins=4, min_count=3)
        self.assertEqual(occ.shape, (4, 4))
        self.assertEqual(occ.sum(), 6)
        ia = (np.searchsorted(xe, 0.25, side="right") - 1,
              np.searchsorted(ye, 0.25, side="right") - 1)
        ib = (np.searchsorted(xe, 0.75, side="right") - 1,
              np.searchsorted(ye, 0.75, side="right") - 1)
        self.assertEqual(occ[ia], 4)
        self.assertAlmostEqual(mean_sp[ia], 10.0)
        self.assertTrue(np.isnan(mean_sp[ib]))   # 2 frames < min_count -> masked

    def test_ignores_nan_speed_and_position(self):
        cx = [0.5, 0.5, 0.5, np.nan]
        cy = [0.5, 0.5, 0.5, 0.5]
        sp = [20.0, 20.0, np.nan, 20.0]
        occ, mean_sp, xe, ye = tj.speed_occupancy_grid(cx, cy, sp, bins=2, min_count=1)
        self.assertEqual(occ.sum(), 3)          # the NaN-position frame dropped
        i = (np.searchsorted(xe, 0.5, side="right") - 1,
             np.searchsorted(ye, 0.5, side="right") - 1)
        self.assertAlmostEqual(mean_sp[i], 20.0)  # mean over the 2 finite-speed frames


class TestQuartileStats(unittest.TestCase):
    def test_wilson_brackets_proportion(self):
        lo, hi = tj.wilson_interval(5, 10)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 1.0)
        self.assertTrue(all(pd.isna(v) for v in tj.wilson_interval(0, 0)))

    def test_two_proportion_z(self):
        # Equal proportions -> ~0 z, large p.
        z, p = tj.two_proportion_z(5, 10, 5, 10)
        self.assertAlmostEqual(z, 0.0, places=6)
        self.assertGreater(p, 0.9)
        # Strong split (2/50 vs 48/50) -> small p.
        _, p2 = tj.two_proportion_z(2, 50, 48, 50)
        self.assertLess(p2, 1e-6)
        # Degenerate pooled proportion (all correct) -> nan.
        self.assertTrue(pd.isna(tj.two_proportion_z(10, 10, 10, 10)[1]))

    def test_zscore_by_group_centres_each_group(self):
        df = pd.DataFrame({
            "s": ["a", "a", "a", "b", "b", "b"],
            "v": [10.0, 20.0, 30.0, 100.0, 200.0, 300.0],
        })
        z = tj.zscore_by_group(df, "v", "s")
        # Each group's z-scores are mean 0; the group middle value maps to 0.
        self.assertAlmostEqual(z.iloc[1], 0.0, places=6)   # a-middle
        self.assertAlmostEqual(z.iloc[4], 0.0, places=6)   # b-middle
        self.assertAlmostEqual(z.groupby(df["s"]).mean().abs().max(), 0.0, places=6)

    def test_zscore_zero_variance_group_is_zero(self):
        df = pd.DataFrame({"s": ["a", "a"], "v": [5.0, 5.0]})
        self.assertTrue((tj.zscore_by_group(df, "v", "s") == 0.0).all())

    def test_accuracy_by_quartile_equal_groups_with_ties(self):
        # 8 trials; heavy ties (many zeros) still split into 4 groups of 2. Values
        # ascend with correctness so Q1 (lowest) is least accurate, Q4 most.
        df = pd.DataFrame({
            "v": [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0],
            "correct": [False, False, False, True, False, True, True, True],
        })
        out = tj.accuracy_by_quartile(df, "v", q=4)
        self.assertEqual(list(out["quartile"]), [1, 2, 3, 4])
        self.assertEqual(list(out["n"]), [2, 2, 2, 2])
        self.assertLessEqual(out["accuracy"].iloc[0], out["accuracy"].iloc[-1])
        # CI columns present and ordered lo <= hi.
        self.assertTrue((out["ci_lo"] <= out["ci_hi"]).all())

    def test_accuracy_by_quartile_drops_nan_only_in_value(self):
        df = pd.DataFrame({"v": [1.0, np.nan, 2.0, 3.0, 4.0], "correct": [True] * 5})
        out = tj.accuracy_by_quartile(df, "v", q=4)
        self.assertEqual(int(out["n"].sum()), 4)  # the NaN-value row dropped

    def test_accuracy_by_quartile_too_few_rows_empty(self):
        df = pd.DataFrame({"v": [1.0, 2.0], "correct": [True, False]})
        self.assertTrue(tj.accuracy_by_quartile(df, "v", q=4).empty)


if __name__ == "__main__":
    unittest.main()
