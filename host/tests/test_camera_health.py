"""
test_camera_health.py - headless unit tests for the pure camera-health core
(camera_health.py). No hardware, no Tk. See docs/decisions/0147.

The one thing worth pinning: an incomplete grab must ADVANCE last_block so it
does not manufacture a phantom source drop for the next good frame (the pre-0147
double-count where src-drop mirrored incomplete one-for-one, bug #260810).

Run from host/:
    python -m unittest test_camera_health
"""

from __future__ import annotations

import unittest

from tmaze.core.camera_health import account_block_gap, recording_deltas


class TestAccountBlockGap(unittest.TestCase):
    def test_first_frame_seeds_last_block_no_drop(self):
        self.assertEqual(account_block_gap(None, 10), (10, 0))

    def test_consecutive_frames_no_drop(self):
        self.assertEqual(account_block_gap(10, 11), (11, 0))

    def test_single_frame_gap_counts_one(self):
        # block 10 -> 12 means block 11 was never received.
        self.assertEqual(account_block_gap(10, 12), (12, 1))

    def test_multi_frame_gap_counts_the_whole_run(self):
        # block 10 -> 15 means 11,12,13,14 were never received.
        self.assertEqual(account_block_gap(10, 15), (15, 4))

    def test_missing_block_id_leaves_tally_untouched(self):
        self.assertEqual(account_block_gap(10, None), (10, 0))

    def test_missing_block_id_on_first_frame_stays_unseeded(self):
        self.assertEqual(account_block_gap(None, None), (None, 0))

    def test_duplicate_block_id_counts_nothing(self):
        self.assertEqual(account_block_gap(10, 10), (10, 0))

    def test_out_of_order_keeps_running_max_no_negative(self):
        # A lower BlockID (out-of-order / counter wrap) must not yield a negative
        # drop or rewind last_block.
        self.assertEqual(account_block_gap(10, 7), (10, 0))

    def test_incomplete_grab_does_not_double_count(self):
        # Walk a good frame, then an INCOMPLETE grab (still a delivered buffer,
        # so its BlockID is folded in the same way), then the next good frame.
        # The incomplete grab advances last_block, so the next good frame sees no
        # gap - i.e. incomplete is counted once (by the caller), never twice.
        last = 100
        last, gap = account_block_gap(last, 101)   # good frame
        self.assertEqual((last, gap), (101, 0))
        last, gap = account_block_gap(last, 102)   # incomplete grab (delivered)
        self.assertEqual((last, gap), (102, 0))
        last, gap = account_block_gap(last, 103)   # next good frame
        self.assertEqual((last, gap), (103, 0))

    def test_true_source_drop_around_incomplete_still_counted(self):
        # If frames are genuinely lost, the gap is still counted even though an
        # incomplete grab sits in the middle.
        last = 100
        last, gap = account_block_gap(last, 103)   # 101,102 truly lost
        self.assertEqual((last, gap), (103, 2))


class TestRecordingDeltas(unittest.TestCase):
    def test_deltas_are_current_minus_baseline(self):
        base = {"grabbed": 1000, "src_drops": 5, "incomplete": 2,
                "queue_drops": 1, "track_skips": 3, "saved": 0}
        cur = {"grabbed": 1600, "src_drops": 8, "incomplete": 2,
               "queue_drops": 4, "track_skips": 3, "saved": 590}
        self.assertEqual(
            recording_deltas(cur, base),
            {"grabbed": 600, "src_drops": 3, "incomplete": 0,
             "queue_drops": 3, "track_skips": 0, "saved": 590})

    def test_missing_baseline_key_treated_as_zero(self):
        self.assertEqual(recording_deltas({"grabbed": 10}, {}), {"grabbed": 10})

    def test_negative_delta_clamped_to_zero(self):
        # A counter reset (current below baseline) must never report a negative.
        self.assertEqual(recording_deltas({"src_drops": 2}, {"src_drops": 9}),
                         {"src_drops": 0})


if __name__ == "__main__":
    unittest.main()
