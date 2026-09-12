"""
records_git.py - the "Commit records" step: put the records files into the
resources repo's git history, and nothing else (ADR-0155, decision 4).

Since ADR-0155 the four records files under ``records/animal/``
(``daily_log.csv``, ``session_annotations.csv``, ``animals.json`` and the
append-only ``records_changes.csv``) are git-tracked in the resources repo, so
every past state can be recovered. Committing by hand from a terminal is error
prone for the people keeping the records, so the Records Manager has a button.
This module is what the button runs. It

- stages ONLY the four records files (``git add -- <those paths>``), and
  REFUSES if anything else is already staged, so someone's unrelated
  in-progress work in the resources repo is never swept into a records commit;
- builds the commit message from the change-log entries that are not yet in
  ``HEAD`` (the log is append-only, so "not yet committed" is simply "the
  entries after the ones in the committed copy" - no clock comparison);
- commits with the machine's configured git identity (the author of record,
  decision 5). It never pushes (FW-0064).

Layering: the decision logic (what is staged, is the commit allowed, what the
message says) is pure and tested on fake ``git`` output; the only edge is
:func:`git_runner`, a tiny ``subprocess`` wrapper the shell passes in, so the
tests never need a real repository.

Headless tests: python -m unittest test_records_git.
"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from tmaze.core.records.records_changelog import ChangeEntry, parse_entries

# Repo-relative, forward-slash paths (what ``git status --porcelain`` prints).
RECORDS_FILES: Tuple[str, ...] = (
    "records/animal/daily_log.csv",
    "records/animal/session_annotations.csv",
    "records/animal/animals.json",
    "records/animal/records_changes.csv",
)
CHANGELOG_REL = "records/animal/records_changes.csv"

# run(args, stdin_text) -> (returncode, stdout, stderr); ``args`` are the
# arguments AFTER ``git``. A missing git binary is reported as rc 127.
GitRun = Callable[[Sequence[str], Optional[str]], Tuple[int, str, str]]

_GIT_TIMEOUT_S = 60


def git_runner(repo_dir: str) -> GitRun:
    """The one subprocess edge: a runner that executes ``git`` in ``repo_dir``."""
    def run(args: Sequence[str], stdin_text: Optional[str] = None
            ) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=repo_dir, input=stdin_text,
                capture_output=True, text=True, encoding="utf-8",
                timeout=_GIT_TIMEOUT_S)
        except FileNotFoundError:
            return 127, "", "git was not found on this machine's PATH"
        except (OSError, subprocess.SubprocessError) as e:
            return 1, "", str(e)
        return proc.returncode, proc.stdout, proc.stderr
    return run


# ----------------------------------------------------------------- status
@dataclass
class StatusEntry:
    """One ``git status --porcelain`` line: index (staged) state, work-tree
    state, and the path (for a rename, the NEW path)."""
    index: str
    worktree: str
    path: str

    @property
    def staged(self) -> bool:
        return self.index not in (" ", "?", "!")

    @property
    def changed(self) -> bool:
        """Anything at all to commit: staged, modified in the work tree, or new."""
        return self.index != " " or self.worktree != " "


def parse_porcelain(text: str) -> List[StatusEntry]:
    """Parse ``git status --porcelain`` (v1) output.

    Each line is two status letters, a space, then the path; renames are
    ``old -> new`` (we keep ``new``); paths with special characters come
    double-quoted, which we strip (escape sequences inside are left as-is -
    they do not occur for the fixed records paths this module cares about).
    """
    out: List[StatusEntry] = []
    for line in text.splitlines():
        if len(line) < 4:
            continue
        xy, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
            path = path[1:-1]
        out.append(StatusEntry(xy[0], xy[1], path))
    return out


def classify(entries: Sequence[StatusEntry],
             records_files: Sequence[str] = RECORDS_FILES
             ) -> Tuple[List[str], List[str]]:
    """Split a status into (paths staged that are NOT records files,
    records files that have anything to commit)."""
    wanted = set(records_files)
    staged_other = [e.path for e in entries
                    if e.staged and e.path not in wanted]
    records_changed = [e.path for e in entries
                       if e.path in wanted and e.changed]
    return staged_other, records_changed


@dataclass
class RecordsStatus:
    """What the GUI needs to show its banner and decide whether Commit may run."""
    ok: bool                       # git usable and this is a repo
    error: str = ""                # why not, when ok is False
    staged_other: List[str] = field(default_factory=list)
    records_changed: List[str] = field(default_factory=list)
    pending: List[ChangeEntry] = field(default_factory=list)  # log entries not in HEAD
    head: Optional[str] = None     # short sha, None for an unborn branch

    @property
    def can_commit(self) -> bool:
        return self.ok and not self.staged_other and bool(self.records_changed)


def committed_entries(run: GitRun) -> List[ChangeEntry]:
    """The change-log entries already in ``HEAD`` ([] if the file is not
    committed yet or there is no commit at all)."""
    rc, out, _err = run(["show", f"HEAD:{CHANGELOG_REL}"], None)
    if rc != 0:
        return []
    return parse_entries(out)


def pending_entries(all_entries: Sequence[ChangeEntry],
                    committed: Sequence[ChangeEntry]) -> List[ChangeEntry]:
    """Entries in the working copy of the log that ``HEAD`` lacks.

    The log is append-only, so the committed copy is a prefix of the working
    copy and the pending entries are simply the tail. If the working copy is
    somehow SHORTER than HEAD (the file was hand-edited or reverted), nothing
    is pending - the commit will still record whatever the file now holds.
    """
    n = len(committed)
    if len(all_entries) < n:
        return []
    return list(all_entries[n:])


def records_status(run: GitRun, all_entries: Sequence[ChangeEntry]
                   ) -> RecordsStatus:
    """Query git once and summarise: is a commit possible, what would go in,
    and how many logged edits are not yet committed."""
    rc, out, err = run(["rev-parse", "--is-inside-work-tree"], None)
    if rc != 0 or out.strip() != "true":
        return RecordsStatus(ok=False, error=(
            err.strip() or "the resources folder is not a git repository"))
    # Whole-repo status: we must see OTHER staged files, not just ours. The
    # default untracked mode still lists a new records_changes.csv by name
    # because records/animal/ already holds tracked files.
    rc, out, err = run(["status", "--porcelain"], None)
    if rc != 0:
        return RecordsStatus(ok=False, error=err.strip() or "git status failed")
    staged_other, records_changed = classify(parse_porcelain(out))
    rc, head, _ = run(["rev-parse", "--short", "HEAD"], None)
    return RecordsStatus(
        ok=True, staged_other=staged_other, records_changed=records_changed,
        pending=pending_entries(all_entries, committed_entries(run)),
        head=head.strip() if rc == 0 else None)


# ---------------------------------------------------------------- message
def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def build_message(pending: Sequence[ChangeEntry],
                  records_changed: Sequence[str]) -> str:
    """The commit message: a one-line subject, then a body summarising the
    pending change-log entries (per file / operator / source / time span).

    The per-field detail stays in ``records_changes.csv`` itself (it is in the
    same commit), so the message only needs to say who, what, roughly how much.
    """
    files_short = [os.path.basename(p) for p in records_changed]
    ops = Counter(e.operator or "(unknown)" for e in pending)
    if pending:
        who = ", ".join(sorted(ops))
        subject = f"records: {_plural(len(pending), 'logged edit')} by {who}"
    else:
        subject = "records: update ({})".format(
            ", ".join(files_short) if files_short else "no changes")
    lines = [subject, ""]
    if pending:
        per_file = Counter(e.file for e in pending)
        rows_per_file = {f: len({e.key for e in pending if e.file == f})
                         for f in per_file}
        lines.append("Per file: " + "; ".join(
            f"{f} {_plural(n, 'edit')} in {_plural(rows_per_file[f], 'row')}"
            for f, n in sorted(per_file.items())))
        lines.append("Operators: " + "; ".join(
            f"{o} ({n})" for o, n in sorted(ops.items())))
        srcs = Counter(e.source or "(unknown)" for e in pending)
        lines.append("Sources: " + "; ".join(
            f"{s} ({n})" for s, n in sorted(srcs.items())))
        ts = sorted(e.ts for e in pending)
        lines.append(f"When: {ts[0]}" + (f" .. {ts[-1]}" if ts[-1] != ts[0]
                                          else ""))
    unlogged = [f for f in files_short
                if f != os.path.basename(CHANGELOG_REL)
                and f not in {e.file for e in pending}]
    if unlogged:
        lines.append("Changed without change-log entries (edited outside the "
                     "Records Manager): " + ", ".join(unlogged))
    lines += ["", "Committed from the Records Manager; per-field detail in "
                  "records/animal/records_changes.csv (os_code_t_maze "
                  "ADR-0155)."]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- commit
@dataclass
class CommitResult:
    ok: bool
    summary: str
    sha: Optional[str] = None


def refusal(status: RecordsStatus) -> Optional[str]:
    """Why a commit must not proceed, or None if it may."""
    if not status.ok:
        return f"Cannot commit: {status.error}"
    if status.staged_other:
        return ("Cannot commit: other files are already staged in the "
                "resources repo, and this button only commits the records "
                "files. Unstage them (git restore --staged <file>) or commit "
                "them yourself first:\n  " + "\n  ".join(status.staged_other))
    if not status.records_changed:
        return "Nothing to commit: the records files match the last commit."
    return None


def commit_records(run: GitRun, status: RecordsStatus, message: str
                   ) -> CommitResult:
    """Stage exactly the changed records files and commit them with ``message``.

    ``status`` must come from :func:`records_status` moments before (the GUI
    re-queries right before calling, so a file staged in between is caught by
    the same refusal). Never pushes.
    """
    why = refusal(status)
    if why:
        return CommitResult(False, why)
    rc, _out, err = run(["add", "--", *status.records_changed], None)
    if rc != 0:
        return CommitResult(False, f"git add failed: {err.strip()}")
    rc, out, err = run(["commit", "-q", "--only", "-F", "-", "--",
                        *status.records_changed], message)
    if rc != 0:
        return CommitResult(False, "git commit failed: "
                            + (err.strip() or out.strip()))
    rc, sha, _ = run(["rev-parse", "--short", "HEAD"], None)
    sha_s = sha.strip() if rc == 0 else None
    return CommitResult(
        True, f"Committed {_plural(len(status.records_changed), 'file')} as "
              f"{sha_s or '(new commit)'}: "
              + ", ".join(os.path.basename(p) for p in status.records_changed),
        sha_s)
