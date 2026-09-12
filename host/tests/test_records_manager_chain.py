"""
test_records_manager_chain.py - the Records Manager's structured build results.

Generate / Import used to return one text blob the GUI sniffed for "FAILED" /
"rc=" to decide whether the run succeeded. ADR-0158 replaces that with data:
one ``_StepResult`` per builder step and a ``_ChainOutcome`` that knows whether
the chain halted early or the registry build refused to shrink. These tests
cover that pure part without opening a window.

Run from host/:  python -m unittest tests.test_records_manager_chain
"""

from __future__ import annotations

import unittest

from tmaze.core.session import session_registry
from tmaze.ui import records_manager as rm


class TestChainOutcome(unittest.TestCase):
    def test_all_ok(self):
        out = rm._ChainOutcome(steps=[
            rm._StepResult("session registry", True, "110 sessions"),
            rm._StepResult("build_daily_log.py", True, "wrote xlsx")])
        self.assertTrue(out.ok)
        self.assertEqual(out.text().splitlines(), [
            "[session registry OK] 110 sessions",
            "[build_daily_log.py OK] wrote xlsx"])

    def test_failed_step_halts_and_is_not_ok(self):
        out = rm._ChainOutcome(steps=[
            rm._StepResult("session registry", False, "FAILED: boom")],
            halted=True)
        self.assertFalse(out.ok)
        self.assertIn("[session registry FAILED] FAILED: boom", out.text())
        self.assertIn("remaining steps were not run", out.text())

    def test_one_failure_among_successes_is_not_ok(self):
        # Import runs both builders regardless; one rc!=0 still marks the whole
        # chain not-ok so the xlsx sync point is NOT recorded.
        out = rm._ChainOutcome(steps=[
            rm._StepResult("build_daily_log.py", True, ""),
            rm._StepResult("build_session_training_log.py", False, "rc=1")])
        self.assertFalse(out.ok)
        self.assertFalse(out.halted)

    def test_empty_chain_is_not_ok(self):
        self.assertFalse(rm._ChainOutcome().ok)

    def test_shrink_refusal_carries_the_report(self):
        rep = session_registry.ShrinkReport(vanished=["a", "b"], added=[])
        out = rm._ChainOutcome(
            steps=[rm._StepResult("session registry", False, "REFUSED ...")],
            halted=True, shrink=rep, shrink_refused=True)
        self.assertFalse(out.ok)
        self.assertEqual(out.shrink.vanished, ["a", "b"])

    def test_step_line_strips_blank_detail(self):
        self.assertEqual(rm._StepResult("x", True).line(), "[x OK]")


if __name__ == "__main__":
    unittest.main()
