"""
test_excursion_log.py - headless tests for the Phase 3 excursion-event log.

Covers the pure ``excursion_log`` core (docs/decisions/0109): 1-based event
indexing in append order, side normalization, the scored-stem vs denied-non-stem
row shape (``linked_trial`` set vs blank), and the CSV rendering (header comment,
column row, per-cell formatting incl. blank Nones and rounded floats). Pure
stdlib; no hardware/Tk.

Run from host/:  python -m unittest test_excursion_log
"""

from __future__ import annotations

import unittest

from tmaze.core.phase3.excursion_log import (
    COLUMNS, ExcursionLog, ExcursionRow, OUTCOME_DENIED, OUTCOME_SCORED,
    TYPE_NON_STEM, TYPE_UP_STEM)


def _log():
    return ExcursionLog(animal="RAT600", session_id="600_20260723_120000",
                        session_start_iso="2026-07-23T12:00:00")


class TestAppend(unittest.TestCase):
    def test_event_index_is_one_based_in_order(self):
        log = _log()
        a = log.append(iso="2026-07-23T12:00:01", type=TYPE_UP_STEM,
                       side="left", outcome=OUTCOME_SCORED, linked_trial=1)
        b = log.append(iso="2026-07-23T12:00:05", type=TYPE_NON_STEM,
                       side="right", outcome=OUTCOME_DENIED)
        self.assertEqual((a.event_index, b.event_index), (1, 2))
        self.assertEqual(len(log), 2)

    def test_side_is_normalized_to_upper_arm_label(self):
        log = _log()
        row = log.append(iso="t", type=TYPE_UP_STEM, side="left",
                         outcome=OUTCOME_SCORED, linked_trial=3)
        self.assertEqual(row.side, "LEFT")

    def test_scored_stem_row_carries_linked_trial(self):
        log = _log()
        row = log.append(iso="t", type=TYPE_UP_STEM, side="right",
                         outcome=OUTCOME_SCORED, linked_trial=7,
                         sched_state="NORMAL", bias=0.25, cue_dwell_s=1.5)
        self.assertEqual(row.type, TYPE_UP_STEM)
        self.assertEqual(row.outcome, OUTCOME_SCORED)
        self.assertEqual(row.linked_trial, 7)
        self.assertEqual(row.cue_dwell_s, 1.5)

    def test_denied_nonstem_row_has_no_linked_trial(self):
        log = _log()
        row = log.append(iso="t", type=TYPE_NON_STEM, side="left",
                         outcome=OUTCOME_DENIED)
        self.assertEqual(row.type, TYPE_NON_STEM)
        self.assertEqual(row.outcome, OUTCOME_DENIED)
        self.assertIsNone(row.linked_trial)
        self.assertIsNone(row.cue_dwell_s)

    def test_rows_property_is_an_immutable_snapshot(self):
        log = _log()
        log.append(iso="t", type=TYPE_UP_STEM, side="left",
                   outcome=OUTCOME_SCORED, linked_trial=1)
        rows = log.rows
        self.assertIsInstance(rows, tuple)
        self.assertIsInstance(rows[0], ExcursionRow)
        # Appending after the read does not mutate the earlier tuple.
        log.append(iso="t2", type=TYPE_NON_STEM, side="right",
                   outcome=OUTCOME_DENIED)
        self.assertEqual(len(rows), 1)


class TestRenderCsv(unittest.TestCase):
    def test_header_and_columns_present(self):
        text = _log().render_csv()
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("# phase3_excursions animal=RAT600"))
        self.assertIn("session_id=600_20260723_120000", lines[0])
        # The clock caveat and the column row are both present.
        self.assertTrue(any("host actuation-decision" in ln for ln in lines))
        self.assertIn(",".join(COLUMNS), lines)

    def test_rows_render_with_blanks_and_rounded_floats(self):
        log = _log()
        log.append(iso="2026-07-23T12:00:01", mono=12.3456, type=TYPE_UP_STEM,
                   side="left", outcome=OUTCOME_SCORED, linked_trial=1,
                   sched_state="NORMAL", bias=0.25, cue_dwell_s=1.5)
        log.append(iso="2026-07-23T12:00:05", type=TYPE_NON_STEM, side="right",
                   outcome=OUTCOME_DENIED)
        lines = log.render_csv().splitlines()
        header_idx = lines.index(",".join(COLUMNS))
        scored, denied = lines[header_idx + 1], lines[header_idx + 2]
        # Scored stem: floats rounded (mono 3dp, bias 4dp, dwell 3dp), trial linked.
        self.assertEqual(
            scored,
            "1,2026-07-23T12:00:01,12.346,up-stem,LEFT,scored,1,NORMAL,0.2500,1.500")
        # Denied non-stem: linked_trial + sched_state + bias + dwell all blank.
        self.assertEqual(
            denied,
            "2,2026-07-23T12:00:05,,non-stem,RIGHT,denied,,,,")

    def test_empty_log_renders_header_only(self):
        lines = _log().render_csv().splitlines()
        self.assertIn(",".join(COLUMNS), lines)
        # No data rows after the column header.
        self.assertEqual(lines[-1], ",".join(COLUMNS))


if __name__ == "__main__":
    unittest.main()
