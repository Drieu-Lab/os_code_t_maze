"""test_records_edits.py - headless tests for the Records Manager edit core.

Covers the pure logic extracted out of the GUI (docs/decisions/0163 follow-up):
turning a daily form into a writeable row, expanding a "Mark span" request into
per-day edits, the Generate/Import chain model, and the registry-staleness fold.
Pure stdlib; no Tk, no filesystem.

Run from host/tests:  python -m unittest test_records_edits
"""

from __future__ import annotations

import unittest

from tmaze.core.records import records_edits
from tmaze.core.records.daily_log import DailyLog


class TestDailyRowFromForm(unittest.TestCase):
    def test_recomputes_pct_and_normalises(self):
        raw = {"animal_id": " 600 ", "date": "2026-07-02", "weight_g": "332",
               "baseline_g": "348", "pct_baseline": "0.111",  # stale -> fixed
               "water_regime": "ad lib", "water_method": "timed access",
               "notes": "hi"}
        row = records_edits.daily_row_from_form(raw)
        self.assertEqual(row["animal_id"], "600")           # trimmed
        self.assertEqual(row["pct_baseline"], f"{round(332 / 348, 4):.4f}")
        self.assertEqual(row["water_regime"], "ad_lib")      # canonicalised
        self.assertEqual(row["water_method"], "timed_access")
        self.assertEqual(row["notes"], "hi")

    def test_blank_weight_blanks_pct(self):
        row = records_edits.daily_row_from_form(
            {"animal_id": "600", "date": "2026-07-02", "weight_g": "",
             "baseline_g": "348"})
        self.assertEqual(row["pct_baseline"], "")

    def test_projects_onto_fields_only(self):
        row = records_edits.daily_row_from_form(
            {"animal_id": "600", "date": "2026-07-02", "bogus": "x"})
        self.assertNotIn("bogus", row)
        from tmaze.core.records.daily_log import FIELDS
        self.assertEqual(set(row), set(FIELDS))


class TestValidateSpan(unittest.TestCase):
    def _spec(self, **over):
        spec = {"animal": "600", "start": "2026-07-01", "end": "2026-07-03",
                "regime": "ad_lib", "method": records_edits.SPAN_LEAVE}
        spec.update(over)
        return spec

    def test_ok(self):
        self.assertEqual(records_edits.validate_span(self._spec()), [])

    def test_missing_animal(self):
        errs = records_edits.validate_span(self._spec(animal=""))
        self.assertTrue(any("animal" in e.lower() for e in errs))

    def test_bad_dates(self):
        errs = records_edits.validate_span(self._spec(start="oops"))
        self.assertTrue(any("YYYY-MM-DD" in e for e in errs))

    def test_end_before_start(self):
        errs = records_edits.validate_span(
            self._spec(start="2026-07-05", end="2026-07-01"))
        self.assertTrue(any("before" in e.lower() for e in errs))

    def test_range_too_long(self):
        errs = records_edits.validate_span(
            self._spec(start="2026-07-01", end="2026-09-01"))
        self.assertTrue(any("shorter" in e.lower() for e in errs))

    def test_nothing_to_set(self):
        errs = records_edits.validate_span(
            self._spec(regime=records_edits.SPAN_LEAVE,
                       method=records_edits.SPAN_LEAVE))
        self.assertTrue(any("regime" in e.lower() for e in errs))


class TestSpanEdits(unittest.TestCase):
    def setUp(self):
        # One existing row inside the span carries a weight that must survive.
        self.daily = DailyLog([
            {"animal_id": "600", "date": "2026-07-02", "weight_g": "332",
             "baseline_g": "348", "notes": "keep me"},
        ])

    def test_one_edit_per_day_preserves_other_cells(self):
        spec = {"animal": "600", "start": "2026-07-01", "end": "2026-07-03",
                "regime": "ad_lib", "method": records_edits.SPAN_LEAVE}
        edits = records_edits.span_edits(self.daily, spec)
        self.assertEqual([e["date"] for e in edits],
                         ["2026-07-01", "2026-07-02", "2026-07-03"])
        # Every day got the regime; the existing row kept its weight/notes.
        self.assertTrue(all(e["water_regime"] == "ad_lib" for e in edits))
        mid = next(e for e in edits if e["date"] == "2026-07-02")
        self.assertEqual(mid["weight_g"], "332")
        self.assertEqual(mid["notes"], "keep me")

    def test_leave_unchanged_does_not_touch_method(self):
        spec = {"animal": "600", "start": "2026-07-01", "end": "2026-07-01",
                "regime": "ad_lib", "method": records_edits.SPAN_LEAVE}
        edits = records_edits.span_edits(self.daily, spec)
        self.assertEqual(edits[0].get("water_method", ""), "")

    def test_boundary_times_land_on_first_and_last(self):
        spec = {"animal": "600", "start": "2026-07-01", "end": "2026-07-03",
                "regime": records_edits.SPAN_LEAVE, "method": "task_earned",
                "start_t": "5pm", "end_t": "9am"}
        edits = records_edits.span_edits(self.daily, spec)
        self.assertEqual(edits[0]["water_on"], "5pm")
        self.assertEqual(edits[0].get("water_off", ""), "")
        self.assertEqual(edits[-1]["water_off"], "9am")
        self.assertEqual(edits[-1].get("water_on", ""), "")
        self.assertTrue(all(e["water_method"] == "task_earned" for e in edits))


class TestGenerateResult(unittest.TestCase):
    def test_ok_when_all_steps_ok(self):
        r = records_edits.GenerateResult(steps=[
            records_edits.StepResult("a", True), records_edits.StepResult("b", True)])
        self.assertTrue(r.ok)

    def test_not_ok_when_a_step_fails(self):
        r = records_edits.GenerateResult(steps=[
            records_edits.StepResult("a", True),
            records_edits.StepResult("b", False, "boom")])
        self.assertFalse(r.ok)
        self.assertIn("boom", r.text())

    def test_halted_is_not_ok_and_notes_stop(self):
        r = records_edits.GenerateResult(
            steps=[records_edits.StepResult("a", True)], halted=True)
        self.assertFalse(r.ok)
        self.assertIn("Stopped here", r.text())

    def test_empty_chain_is_not_ok(self):
        self.assertFalse(records_edits.GenerateResult().ok)


class TestRegistryStaleness(unittest.TestCase):
    def test_unindexed_marks_stale(self):
        st = records_edits.registry_staleness(
            on_disk_ids=["s1", "s2", "s3"], indexed_ids=["s1"],
            ignored_ids=set(), n_indexed=1)
        self.assertTrue(st.stale)
        self.assertEqual(set(st.unindexed), {"s2", "s3"})
        self.assertTrue(st.warn)

    def test_ignored_bundle_is_not_unindexed(self):
        st = records_edits.registry_staleness(
            on_disk_ids=["s1", "junk"], indexed_ids=["s1"],
            ignored_ids={"junk"}, n_indexed=1)
        self.assertFalse(st.stale)

    def test_all_indexed_not_stale(self):
        st = records_edits.registry_staleness(
            on_disk_ids=["s1", "s2"], indexed_ids=["s1", "s2"],
            ignored_ids=set(), n_indexed=2)
        self.assertFalse(st.stale)
        self.assertFalse(st.warn)

    def test_unreachable_roots_not_stale(self):
        # A None scan (no root reachable) must not claim everything is missing.
        st = records_edits.registry_staleness(
            on_disk_ids=None, indexed_ids=["s1"], ignored_ids=set(), n_indexed=1)
        self.assertFalse(st.roots_seen)
        self.assertFalse(st.stale)

    def test_excel_unsynced_warns_even_when_fresh(self):
        st = records_edits.registry_staleness(
            on_disk_ids=["s1"], indexed_ids=["s1"], ignored_ids=set(),
            n_indexed=1, excel_unsynced=True)
        self.assertFalse(st.stale)
        self.assertTrue(st.warn)


if __name__ == "__main__":
    unittest.main()
