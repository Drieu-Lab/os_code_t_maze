"""Records Manager "Run DLC on selected" choices, without a window
(docs/decisions/0232). Headless: python -m unittest test_records_manager_dlc."""

import unittest
from unittest import mock

from tmaze.core import derivatives as D
from tmaze.ui import records_manager as RM

STATES = {"600_20261027_152320": D.STATE_DONE, "601_20261027_143654": D.STATE_PENDING,
          "602_20261027_132208": D.STATE_DONE, "000_20260922_163830": D.STATE_PENDING}
PICKED = ["600_20261027_152320", "601_20261027_143654", "602_20261027_132208",
          "000_20260922_163830", "600_20990101_000000"]


class FakeManager:
    roster = [{"id": "600"}, {"id": "601"}, {"id": "602"}]

    def __init__(self):
        self.started = []

    _dlc_known = RM.RecordsManager._dlc_known

    def _dlc_ready(self, title):
        return "pdir"

    def _start_dlc(self, title, pdir, args):
        self.started.append(args)


class TestRunDlcOnSelected(unittest.TestCase):
    def _run(self, redo_answer, confirm=True):
        fake = FakeManager()
        with mock.patch.object(RM, "messagebox") as box:
            box.askyesnocancel.return_value = redo_answer
            box.askyesno.return_value = confirm
            RM.RecordsManager._run_dlc_on(fake, "t", PICKED, STATES)
        return fake, box

    def test_redo_yes_runs_done_sessions_too(self):
        fake, box = self._run(True)
        self.assertIn("2 of the selected sessions already have", box.askyesnocancel.call_args[0][1])
        text = box.askyesno.call_args[0][1]
        self.assertIn("on 3 sessions", text)
        self.assertIn("no video: 600_20990101_000000", text)
        self.assertIn("not in the roster: 000_20260922_163830", text)
        self.assertEqual(fake.started, [["--session", "600_20261027_152320",
                                         "--session", "601_20261027_143654",
                                         "--session", "602_20261027_132208", "--redo"]])

    def test_redo_no_runs_only_new_ones(self):
        fake, box = self._run(False)
        self.assertIn("on 1 session,", box.askyesno.call_args[0][1])
        self.assertNotIn("--redo", fake.started[0])

    def test_cancel_and_decline_start_nothing(self):
        self.assertEqual(self._run(None)[0].started, [])
        self.assertEqual(self._run(True, confirm=False)[0].started, [])


if __name__ == "__main__":
    unittest.main()
