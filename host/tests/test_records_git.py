"""
test_records_git.py - headless tests for the "Commit records" core.

The decision logic (``tmaze.core.records.records_git``; ADR-0155) is exercised
against a FAKE git runner (scripted replies, recorded calls), so no repository
is needed. One integration test then runs the whole thing against a throwaway
real repo in a temp dir (skipped when ``git`` is not installed), confirming
that only the records files are committed and nothing is pushed.

Run from host/tests:  python -m unittest test_records_git
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from typing import Dict, List, Optional, Sequence, Tuple

from tmaze.core.records import records_changelog
from tmaze.core.records.records_changelog import ChangeEntry
from tmaze.core.records.records_git import (
    CHANGELOG_REL,
    RECORDS_FILES,
    RecordsStatus,
    build_message,
    classify,
    commit_records,
    committed_entries,
    git_runner,
    parse_porcelain,
    pending_entries,
    records_status,
    refusal,
)

LOG_HEAD = "# schema_version=1\nts,operator,source,file,key,field,old,new\n"


def _entry(i: int, file="daily_log.csv", operator="RJ",
           source="records_manager") -> ChangeEntry:
    return ChangeEntry(f"2026-09-11T10:00:{i:02d}", operator, source, file,
                       f"600|2026-09-{i + 1:02d}", "weight_g", "", str(390 + i))


def _log_text(n: int) -> str:
    rows = "".join(",".join([_entry(i).ts, "RJ", "records_manager",
                             "daily_log.csv", f"600|2026-09-{i + 1:02d}",
                             "weight_g", "", str(390 + i)]) + "\n"
                   for i in range(n))
    return LOG_HEAD + rows


class FakeGit:
    """Scripted git: ``replies`` maps the first argument (subcommand) to
    (rc, out, err); every call is recorded."""

    def __init__(self, replies: Dict[str, Tuple[int, str, str]]):
        self.replies = replies
        self.calls: List[Tuple[Sequence[str], Optional[str]]] = []

    def __call__(self, args, stdin_text=None):
        self.calls.append((list(args), stdin_text))
        return self.replies.get(args[0], (0, "", ""))


class TestParsePorcelain(unittest.TestCase):
    def test_states_renames_and_quotes(self):
        text = (" M records/animal/daily_log.csv\n"
                "?? records/animal/records_changes.csv\n"
                "M  README.md\n"
                "R  old.txt -> new.txt\n"
                '?? "weird name.txt"\n')
        ents = parse_porcelain(text)
        self.assertEqual([(e.index, e.worktree, e.path) for e in ents], [
            (" ", "M", "records/animal/daily_log.csv"),
            ("?", "?", "records/animal/records_changes.csv"),
            ("M", " ", "README.md"),
            ("R", " ", "new.txt"),
            ("?", "?", "weird name.txt"),
        ])
        self.assertFalse(ents[0].staged)
        self.assertTrue(ents[0].changed)
        self.assertFalse(ents[1].staged)       # untracked is not "staged"
        self.assertTrue(ents[2].staged)

    def test_classify_splits_records_from_other_staged(self):
        ents = parse_porcelain(
            " M records/animal/daily_log.csv\n"
            "?? records/animal/records_changes.csv\n"
            " M records/animal/daily_log.xlsx\n"      # unstaged, not ours: ignored
            "M  AGENTS.md\n"                          # staged, not ours: blocks
            "A  records/animal/animals.json\n")
        other, ours = classify(ents)
        self.assertEqual(other, ["AGENTS.md"])
        self.assertEqual(ours, ["records/animal/daily_log.csv",
                                "records/animal/records_changes.csv",
                                "records/animal/animals.json"])


class TestPending(unittest.TestCase):
    def test_committed_entries_from_git_show(self):
        git = FakeGit({"show": (0, _log_text(2), "")})
        self.assertEqual(len(committed_entries(git)), 2)
        self.assertEqual(git.calls[0][0], ["show", f"HEAD:{CHANGELOG_REL}"])

    def test_committed_entries_absent_in_head(self):
        git = FakeGit({"show": (128, "", "fatal: path does not exist")})
        self.assertEqual(committed_entries(git), [])

    def test_pending_is_the_tail(self):
        all_e = [_entry(i) for i in range(5)]
        self.assertEqual(pending_entries(all_e, all_e[:3]), all_e[3:])
        self.assertEqual(pending_entries(all_e, []), all_e)
        self.assertEqual(pending_entries(all_e[:2], all_e), [])  # shorter than HEAD


class TestRecordsStatus(unittest.TestCase):
    def test_not_a_repo(self):
        git = FakeGit({"rev-parse": (128, "", "fatal: not a git repository")})
        st = records_status(git, [])
        self.assertFalse(st.ok)
        self.assertIn("not a git repository", st.error)
        self.assertFalse(st.can_commit)

    def test_git_missing(self):
        git = FakeGit({"rev-parse": (127, "", "git was not found")})
        st = records_status(git, [])
        self.assertFalse(st.ok)
        self.assertIn("not found", st.error)

    def test_full_status(self):
        all_e = [_entry(i) for i in range(3)]
        git = FakeGit({
            "rev-parse": (0, "true\n", ""),
            "status": (0, " M records/animal/daily_log.csv\n"
                          "?? records/animal/records_changes.csv\n", ""),
            "show": (0, _log_text(1), ""),
        })
        st = records_status(git, all_e)
        self.assertTrue(st.ok)
        self.assertEqual(st.staged_other, [])
        self.assertEqual(st.records_changed, ["records/animal/daily_log.csv",
                                              "records/animal/records_changes.csv"])
        self.assertEqual(len(st.pending), 2)
        self.assertTrue(st.can_commit)
        self.assertIsNone(refusal(st))

    def test_refusals(self):
        self.assertIn("Cannot commit", refusal(RecordsStatus(ok=False, error="x")))
        st = RecordsStatus(ok=True, staged_other=["AGENTS.md"],
                           records_changed=["records/animal/daily_log.csv"])
        self.assertIn("AGENTS.md", refusal(st))
        self.assertIn("git restore --staged", refusal(st))
        self.assertTrue(refusal(RecordsStatus(ok=True)).startswith("Nothing"))


class TestBuildMessage(unittest.TestCase):
    def test_summarises_pending(self):
        pend = [_entry(0), _entry(1), _entry(2, file="animals.json", operator="CD",
                                              source="records_manager import (x)")]
        msg = build_message(pend, ["records/animal/daily_log.csv",
                                   "records/animal/animals.json",
                                   CHANGELOG_REL])
        lines = msg.splitlines()
        self.assertEqual(lines[0], "records: 3 logged edits by CD, RJ")
        self.assertEqual(lines[1], "")
        self.assertIn("daily_log.csv 2 edits in 2 rows", lines[2])
        self.assertIn("animals.json 1 edit in 1 row", lines[2])
        self.assertIn("Operators: CD (1); RJ (2)", msg)
        self.assertIn("Sources: records_manager (2); records_manager import (x) (1)",
                      msg)
        self.assertIn("When: 2026-09-11T10:00:00 .. 2026-09-11T10:00:02", msg)
        self.assertNotIn("without change-log", msg)
        self.assertIn("ADR-0155", msg)
        self.assertTrue(msg.endswith("\n"))

    def test_unlogged_files_are_called_out(self):
        msg = build_message([], ["records/animal/session_annotations.csv"])
        self.assertEqual(msg.splitlines()[0],
                         "records: update (session_annotations.csv)")
        self.assertIn("Changed without change-log entries", msg)
        self.assertIn("session_annotations.csv", msg)


class TestCommitRecordsFake(unittest.TestCase):
    def test_refused_when_other_staged_makes_no_git_calls(self):
        git = FakeGit({})
        st = RecordsStatus(ok=True, staged_other=["x"], records_changed=["y"])
        res = commit_records(git, st, "m\n")
        self.assertFalse(res.ok)
        self.assertEqual(git.calls, [])

    def test_stages_only_records_files_and_commits_with_message(self):
        git = FakeGit({"rev-parse": (0, "abc1234\n", "")})
        st = RecordsStatus(ok=True, records_changed=[
            "records/animal/daily_log.csv", CHANGELOG_REL])
        res = commit_records(git, st, "records: hi\n")
        self.assertTrue(res.ok, res.summary)
        self.assertEqual(res.sha, "abc1234")
        subcmds = [c[0][0] for c in git.calls]
        self.assertEqual(subcmds, ["add", "commit", "rev-parse"])
        self.assertEqual(git.calls[0][0], ["add", "--", "records/animal/daily_log.csv",
                                           CHANGELOG_REL])
        self.assertIn("--only", git.calls[1][0])
        self.assertEqual(git.calls[1][1], "records: hi\n")   # message via stdin
        self.assertNotIn("push", subcmds)

    def test_commit_failure_is_reported(self):
        git = FakeGit({"commit": (128, "", "Author identity unknown")})
        st = RecordsStatus(ok=True, records_changed=[CHANGELOG_REL])
        res = commit_records(git, st, "m\n")
        self.assertFalse(res.ok)
        self.assertIn("Author identity unknown", res.summary)


@unittest.skipIf(shutil.which("git") is None, "git not installed")
class TestRealRepo(unittest.TestCase):
    """End to end against a throwaway repo: only the records files land in the
    commit; other staged work blocks it; the pending count follows HEAD."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.git = git_runner(self.d)
        for args in (["init", "-q"], ["config", "user.name", "Test"],
                     ["config", "user.email", "t@example.com"]):
            rc, _o, err = self.git(args)
            self.assertEqual(rc, 0, err)
        os.makedirs(os.path.join(self.d, "records", "animal"))
        self._write("README.md", "hi\n")
        self._write("records/animal/daily_log.csv", "# schema_version=3\na,b\n1,2\n")
        self.git(["add", "-A"])
        self.git(["commit", "-q", "-m", "init"])
        self.changelog = os.path.join(self.d, *CHANGELOG_REL.split("/"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, rel, text):
        p = os.path.join(self.d, *rel.split("/"))
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def _tracked_in_head(self) -> List[str]:
        rc, out, _ = self.git(["show", "--name-only", "--format=", "HEAD"])
        self.assertEqual(rc, 0)
        return sorted(out.split())

    def test_end_to_end(self):
        # Two logged edits; the daily CSV changes; an unrelated unstaged edit.
        records_changelog.append([("600|2026-09-11", "weight_g", "", "390"),
                                  ("600|2026-09-11", "notes", "", "ok")],
                                 "RJ", "records_manager", "daily_log.csv",
                                 path=self.changelog)
        self._write("records/animal/daily_log.csv", "# schema_version=3\na,b\n1,3\n")
        self._write("README.md", "changed but not staged\n")
        entries = records_changelog.load_entries(self.changelog)

        st = records_status(self.git, entries)
        self.assertTrue(st.ok, st.error)
        self.assertEqual(len(st.pending), 2)
        self.assertEqual(st.staged_other, [])
        self.assertEqual(sorted(st.records_changed),
                         ["records/animal/daily_log.csv", CHANGELOG_REL])

        # Staging README blocks the commit ...
        self.git(["add", "README.md"])
        st2 = records_status(self.git, entries)
        self.assertEqual(st2.staged_other, ["README.md"])
        self.assertIn("README.md", refusal(st2))
        self.git(["restore", "--staged", "README.md"])

        # ... unstaged it commits only the records files.
        st3 = records_status(self.git, entries)
        res = commit_records(self.git, st3, build_message(st3.pending,
                                                          st3.records_changed))
        self.assertTrue(res.ok, res.summary)
        self.assertEqual(self._tracked_in_head(),
                         ["records/animal/daily_log.csv", CHANGELOG_REL])
        rc, out, _ = self.git(["log", "-1", "--format=%s"])
        self.assertEqual(out.strip(), "records: 2 logged edits by RJ")
        # README's work-tree edit is untouched and unstaged.
        rc, out, _ = self.git(["status", "--porcelain"])
        self.assertEqual(out.strip(), "M README.md")
        # Nothing pending now; a new append is pending again.
        self.assertEqual(records_status(self.git, entries).pending, [])
        records_changelog.append([("601|2026-09-11", "weight_g", "", "380")],
                                 "RJ", "records_manager", "daily_log.csv",
                                 path=self.changelog)
        st4 = records_status(self.git, records_changelog.load_entries(self.changelog))
        self.assertEqual(len(st4.pending), 1)
        self.assertEqual(st4.records_changed, [CHANGELOG_REL])


if __name__ == "__main__":
    unittest.main()
