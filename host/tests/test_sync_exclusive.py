"""test_sync_exclusive.py - the one-active rule of the per-session LED sync.

``tmaze.core.sync.exclusive`` (at most one channel of a group active per frame,
scored against the row's typical change), the ``exclusive_groups`` field of the
board layout, and the ``sync_fit.apply_one_active`` glue that feeds it the
located map LEDs. See docs/decisions/0201.

Run from host/:  python -m unittest tests.test_sync_exclusive
"""
from __future__ import annotations

import unittest
from collections import namedtuple

from tmaze.core.sync import board_layout as BL, exclusive as EX
from tmaze.core.sync import led_map as LM

N = 200
REST = 20.0
BRIGHT, DIM = 100.0, 35.0          # each row's full change when its channel fires


def _flat() -> list:
    return [REST] * N


def _lick_board():
    """Three lick channels, a bright (_a) and a dim (_b) LED each, columns 0-5.
    L0 fires on frames 100-109, L1 on 50-59, L2 on 150-159."""
    tr = {c: _flat() for c in range(6)}
    for ch, (k0, k1) in enumerate(((100, 110), (50, 60), (150, 160))):
        for k in range(k0, k1):
            tr[2 * ch][k] = REST + BRIGHT
            tr[2 * ch + 1][k] = REST + DIM
    leds = [EX.GroupLed(2 * i + j, f"L{i}", "ab"[j], EX.LIT) for i in range(3) for j in range(2)]
    return tr, {"licks": leds}


class TestEnforce(unittest.TestCase):
    def test_spill_into_a_neighbour_is_returned_to_rest(self):
        tr, groups = _lick_board()
        for k in range(50, 60):               # L1 spills into L0 (as measured: 32 % / 46 %)
            tr[0][k] = REST + 0.32 * BRIGHT
            tr[1][k] = REST + 0.46 * DIM
        out, rep = EX.enforce(tr, groups)
        self.assertEqual(out[0][50:60], [REST] * 10)
        self.assertEqual(out[1][50:60], [REST] * 10)
        self.assertEqual(out[2], tr[2], "the winner is untouched")
        self.assertEqual(out[0][100:110], tr[0][100:110], "L0's own events survive")
        r = rep["licks"]
        self.assertEqual(r.frames_active, 30)
        self.assertEqual(r.frames_conflict, 0)
        self.assertEqual(tr[0][50], REST + 0.32 * BRIGHT, "input not mutated")

    def test_strong_spill_is_counted_as_suppressed(self):
        tr, groups = _lick_board()
        for k in range(50, 60):
            tr[0][k] = REST + 0.6 * BRIGHT
            tr[1][k] = REST + 0.6 * DIM
        _out, rep = EX.enforce(tr, groups)
        self.assertEqual(rep["licks"].frames_suppressed, 10)
        self.assertEqual(rep["licks"].suppressed_by_channel, {"L0": 10})
        self.assertEqual(rep["licks"].frames_conflict, 0)

    def test_a_channel_that_never_fired_does_not_tie_with_the_real_one(self):
        """Scored against its OWN change, L2's spill would read as 100 %; against
        the row's typical change it stays below the active level."""
        tr, groups = _lick_board()
        for k in range(150, 160):              # L2 never fires this session ...
            tr[4][k] = tr[5][k] = REST
        for k in range(50, 60):                # ... but L1 spills into it
            tr[4][k] = REST + 30.0
            tr[5][k] = REST + 20.0
        out, rep = EX.enforce(tr, groups)
        self.assertEqual(out[2][50:60], tr[2][50:60], "L1 keeps its events")
        self.assertEqual(out[4][50:60], [REST] * 10)
        self.assertEqual(rep["licks"].frames_suppressed, 0)

    def test_two_channels_really_lit_is_a_conflict(self):
        tr, groups = _lick_board()
        for k in range(100, 110):              # L2 fully lit together with L0
            tr[4][k] = REST + BRIGHT
            tr[5][k] = REST + DIM
        out, rep = EX.enforce(tr, groups)
        r = rep["licks"]
        self.assertEqual(r.frames_conflict, 10)
        self.assertEqual(r.first_conflicts, list(range(100, 110)))
        self.assertEqual(r.frames_suppressed, 10)
        forced = [c for c in (0, 4) if out[c][100:110] == [REST] * 10]
        self.assertEqual(len(forced), 1, "the rule still keeps only one")

    def test_dark_leds_score_by_dimming(self):
        """Beam LEDs rest lit and go dark while broken."""
        tr = {c: [120.0] * N for c in range(4)}
        for k in range(20, 30):                # S0 broken
            tr[0][k], tr[1][k] = 20.0, 85.0
            tr[2][k], tr[3][k] = 60.0, 100.0   # spill: S1 dims too
        for k in range(80, 90):                # S1 broken
            tr[2][k], tr[3][k] = 20.0, 85.0
        groups = {"beams": [EX.GroupLed(0, "S0", "a", EX.DARK), EX.GroupLed(1, "S0", "b", EX.DARK),
                            EX.GroupLed(2, "S1", "a", EX.DARK), EX.GroupLed(3, "S1", "b", EX.DARK)]}
        out, rep = EX.enforce(tr, groups)
        self.assertEqual(out[2][20:30], [120.0] * 10)
        self.assertEqual(out[0][20:30], tr[0][20:30])
        self.assertEqual(rep["beams"].frames_active, 20)

    def test_nothing_active_leaves_traces_alone(self):
        tr, groups = _lick_board()
        tr[0][5] = REST + 0.3 * BRIGHT          # a small blip, nobody active
        out, rep = EX.enforce(tr, groups)
        self.assertEqual(out[0][5], REST + 0.3 * BRIGHT)
        self.assertEqual(rep["licks"].frames_active, 30)

    def test_columns_outside_groups_and_single_channel_groups_pass_through(self):
        tr, groups = _lick_board()
        tr[9] = [float(k) for k in range(N)]
        solo = {"only_l0": [g for g in groups["licks"] if g.channel == "L0"]}
        out, rep = EX.enforce(tr, solo)
        self.assertEqual(out, tr)
        self.assertEqual(rep["only_l0"].frames_active, 0)


class TestGroupsFrom(unittest.TestCase):
    def test_builds_groups_from_located_leds(self):
        leds = [(0, "L0_a", "L0=1", "lit"), (1, "L0_b", "L0=1", "lit"),
                (2, "S3_a", "S3=1", "dark"), (3, "SOL2.4_a", "SOL2.4", "lit"),
                (4, "L1_a", None, None)]
        g = EX.groups_from({"licks": ["L0", "L1"], "beams": ["S3"]}, leds)
        self.assertEqual(g["licks"], [EX.GroupLed(0, "L0", "a", "lit"),
                                      EX.GroupLed(1, "L0", "b", "lit")])
        self.assertEqual(g["beams"], [EX.GroupLed(2, "S3", "a", "dark")])

    def test_row_of(self):
        self.assertEqual(EX.row_of("S3_b"), "b")
        self.assertEqual(EX.row_of("SOL2.4"), "")


class TestLayoutGroups(unittest.TestCase):
    def test_parsed_and_optional(self):
        lay = BL.from_dict({"schema_version": 1,
                            "exclusive_groups": {"licks": ["L0", "L1", "L2"]}})
        self.assertEqual(lay.exclusive_groups, {"licks": ["L0", "L1", "L2"]})
        self.assertEqual(BL.from_dict({"schema_version": 1}).exclusive_groups, {})

    def test_a_channel_in_two_groups_is_refused(self):
        with self.assertRaises(ValueError):
            BL.from_dict({"schema_version": 1,
                          "exclusive_groups": {"a": ["L0"], "b": ["L0", "L1"]}})

    def test_bad_shape_is_refused(self):
        with self.assertRaises(ValueError):
            BL.from_dict({"schema_version": 1, "exclusive_groups": {"a": "L0"}})

    def test_committed_layout_lists_beams_and_licks(self):
        lay = BL.load()
        self.assertEqual(lay.exclusive_groups.get("licks"), ["L0", "L1", "L2"])
        self.assertEqual(lay.exclusive_groups.get("beams"), [f"S{i}" for i in range(6)])


class TestApplyOneActive(unittest.TestCase):
    """The sync_fit glue: map LEDs -> groups, direction from the map's reference."""

    def setUp(self):
        try:
            import numpy as np
        except ImportError:                                  # pragma: no cover
            self.skipTest("numpy not installed")
        from tmaze.tools import sync_fit
        self.np, self.sf = np, sync_fit
        Blob = namedtuple("Blob", "id x y")
        self.blobs = [Blob(i, 0.0, 0.0) for i in range(4)]
        self.led_ids = {0: "S0_a", 1: "S0_b", 2: "S1_a", 3: "S1_b"}
        self.layout = BL.BoardLayout(exclusive_groups={"beams": ["S0", "S1"]})

    def _map(self, channel_state: str, polarity: str, ref: str = "corrected") -> LM.LedMap:
        leds = [LM.LedEntry(lid, (0.0, 0.0), channel=f"{lid[:2]}={channel_state}",
                            polarity=polarity) for lid in self.led_ids.values()]
        return LM.LedMap("ref", (0, 0, 10, 10), leds=leds, sensor_polarity=ref)

    def _grey(self):
        g = self.np.full((N, 4), 120.0, self.np.float32)
        g[20:30, 0:2] = 20.0                  # S0 broken: its LEDs go dark
        g[20:30, 2:4] = 60.0                  # spill into S1
        g[80:90, 2:4] = 20.0                  # S1 broken
        return g

    def test_enforced_in_place_and_recorded(self):
        g = self._grey()
        info = self.sf.apply_one_active(g, self.blobs, self.led_ids,
                                        self._map("1", "dark"), self.layout)
        self.assertTrue(info["applied"])
        self.assertTrue((g[20:30, 2:4] == 120.0).all())
        self.assertEqual(info["groups"]["beams"]["frames_active"], 20)

    def test_direction_is_flipped_for_the_resting_state_channel(self):
        """``S0=0 lit`` in a corrected-polarity map = lit while RESTING, i.e.
        dark while active: the same physical LED as ``S0=1 dark``."""
        g = self._grey()
        self.sf.apply_one_active(g, self.blobs, self.led_ids, self._map("0", "lit"), self.layout)
        self.assertTrue((g[20:30, 2:4] == 120.0).all())

    def test_not_applied_reasons(self):
        g = self._grey()
        m = self._map("1", "dark")
        off = self.sf.apply_one_active(g, self.blobs, self.led_ids, m, self.layout, enabled=False)
        self.assertFalse(off["applied"])
        self.assertIn("--no-one-active", off["reason"])
        none = self.sf.apply_one_active(g, self.blobs, self.led_ids, m, BL.BoardLayout())
        self.assertIn("no exclusive_groups", none["reason"])
        old = self.sf.apply_one_active(g, self.blobs, self.led_ids,
                                       self._map("1", "dark", ref=None), self.layout)
        self.assertIn("v4", old["reason"])
        self.assertTrue((g == self._grey()).all(), "untouched when not applied")

    def test_report_lines_warn_on_conflict(self):
        info = {"applied": True, "groups": {"licks": {
            "n_leds": 6, "frames_active": 5, "frames_suppressed": 2, "frames_conflict": 2,
            "suppressed_by_channel": {"L2": 2}, "first_conflicts": [3, 4]}}}
        lines = self.sf.one_active_lines(info)
        self.assertTrue(any("WARNING" in s and "[licks]" in s for s in lines))


if __name__ == "__main__":
    unittest.main()
