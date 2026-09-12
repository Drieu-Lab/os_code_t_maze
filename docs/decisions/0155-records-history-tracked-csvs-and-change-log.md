# ADR 0155: Records history - git-tracked CSVs plus an append-only change log

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `os_code_t_maze_resources/records/animal/` (`daily_log.csv`,
  `session_annotations.csv`, `animals.json`, new `records_changes.csv`,
  `records_check_<date>.txt`); `host/tmaze/ui/records_manager.py` and the
  `host/tmaze/core/records/` cores (Phases 1-2 of the hardening plan); the two
  resources builders `build_daily_log.py` / `build_session_training_log.py`; new
  tracked `host/operators.json`.
- **Supersedes / Superseded by:** refines ADR-0085 (reversible CSV/Excel
  surfaces) and ADR-0076 (daily husbandry CSV as source of truth); -

## Context

The coordinated records (ADR-0075/0076/0082/0085) made the husbandry and
annotation data machine-readable, but a 2026-09-11 review of the Records Manager
found that the *history* of that data was not being kept anywhere:

- `daily_log.csv` sat mostly uncommitted in the resources repo for a month and
  `session_annotations.csv` was never added to git at all. Every save is a keyed
  upsert (see the glossary) that rewrites the whole file in place, so a wrong
  value typed over a right one, or a whole column blanked by a header mismatch
  (a bug the review reproduced), left no trace and could not be undone.
- Nothing records *who* made an edit or *when*. The husbandry data are welfare
  records kept under an animal-use protocol (RA23A232) and will be joined onto the
  neural data later; a value that changes silently between two analyses is a
  reproducibility problem as well as a record-keeping one.
- A parallel set of hand-kept Excel files on OneDrive is being maintained "until
  the Records Manager is stable"; without a history there is no way to show it is.
- Where the history should live had not been decided (OneDrive? a new repo?).

The user settled the open questions during the review: the history lives in
`os_code_t_maze_resources/records/animal/`; both mechanisms below are wanted (not
one or the other); the editor identifies themself from a dropdown; a button in
the GUI makes the commit; the baseline-weight rule stays the fixed 90% for now.

## Decision

1. **The three records files are git-tracked in the resources repo** and are
   committed as-is (no rewrite, no normalisation) as a baseline snapshot in the
   same change as this ADR, together with a read-only `records_check` report
   (`records_check_2026-09-11.txt`) so the state of the data at that moment is on
   record. Git gives coarse, whole-file history that can be diffed and reverted.
2. **An append-only change log, `records_changes.csv`, sits beside them.** Every
   write path that edits the records - the Records Manager's Save / Mark span /
   Import / Roster edits and the builders' `--export` /
   `--import-annotations` - appends one line per changed *field*:
   `ts, operator, source, file, key, field, old, new`, behind a
   `# schema_version=1` comment line like the other CSVs. It is never rewritten;
   readers must tolerate a trailing partial line. This gives the fine-grained
   "who changed which cell from what to what" that a git diff of a rewritten
   file cannot, and it works even between commits.
3. **Operator identity is a dropdown, not free text.** A tracked
   `host/operators.json` (`{"operators": ["RJ"]}`, starting with the one trainer
   the registry knows) feeds an Operator combobox in the Records Manager toolbar;
   the choice is remembered per machine in the git-ignored
   `records_manager.json`; Save and Import refuse until one is chosen. The
   builders take `--operator`. New operators are added by editing the file.
4. **A "Commit records" button in the GUI makes the git commit.** It stages only
   `records/animal/{daily_log.csv,session_annotations.csv,animals.json,records_changes.csv}`
   (refusing if anything else is already staged, so unrelated in-progress work in
   the resources repo is never swept in), builds the message from the change-log
   entries since the last commit, and commits with the machine's configured git
   identity. It does **not** push; a banner shows how many records edits are not
   yet committed. Push policy is deferred (FW-0064).
5. **Author of record.** Commits made through the button, and the commits of this
   plan, are authored by the configured git identity on the rig PC (Ravi), who is
   the human reviewing the changes.

## Alternatives considered

- **Git only.** Cheapest, but a commit happens when someone remembers; a day of
  edits collapses into one diff with no per-edit attribution, and a bad save
  between commits is invisible. Rejected as insufficient alone.
- **Change log only.** Attribution without a recoverable prior state of the whole
  file; reconstructing "the file as of last Tuesday" means replaying the log.
  Rejected as insufficient alone.
- **OneDrive version history.** Already partly relied on for the parallel Excel
  files; opaque, per-file, not diffable, not attributable to an operator, and the
  resources folder is deliberately not under OneDrive sync. Rejected.
- **A separate records repo.** Cleaner separation of data from documents, but a
  third repo to keep in sync with the resolver (`OS_CODE_T_MAZE_RESOURCES`) and
  the builders for no gain at three rats. Can be revisited if the records grow or
  need a different access policy.
- **Free-text operator field.** Would drift (initials vs. names vs. blanks). A
  dropdown from a tracked list keeps the vocabulary closed.
- **Auto-commit on every save.** Removes a step but produces noisy, unreviewed
  commits and breaks the "a human decides what lands" rule in `AGENTS.md`. The
  button keeps the decision with the operator; the change log covers the gap.

## Consequences

- Records edits become attributable and reversible at two grains (per field via
  the log, per file via git). The OneDrive parallel-Excel period gains an exit
  criterion: once the log and the commits show the tool behaving, the Excel
  copies can be reconciled and retired (FW-0061).
- `records_changes.csv` grows without bound; at a few lines per day this is
  negligible for years. It is data about data, not a source of truth - if it and
  a records file disagree, the records file wins and the disagreement is a bug.
- The Records Manager and the resources builders now both write the log; until
  Phase 6 (builders import `tmaze.core.records`) the builder side duplicates the
  small append helper, which is a known temporary mirror.
- The GUI now shells out to `git` in the resources repo. A machine without git,
  or a resources checkout that is not a git working tree, gets a clear error and
  the edits are still saved and logged; only the commit step is unavailable.
- Generated workbooks (`daily_log.xlsx`, `session_training_log.xlsx`) remain
  tracked for now (pre-existing); they are not staged by the button. Whether to
  git-ignore them is left open.
- The records `.csv` files being tracked means the pre-existing uncommitted
  changes in the resources repo (builder edits, workbooks) must be committed
  separately by their author; this change does not fold them in.

## Evidence / links

- Review findings (2026-09-11): `daily_log.csv` uncommitted since 2026-08-13;
  `session_annotations.csv` untracked; header-mismatch column-blanking and
  `#`-line-in-notes loss reproduced in a temp dir; 43 `records_check` flags
  (snapshot in `records_check_2026-09-11.txt`).
- Plan of record: Records Manager hardening plan, Phases 0-9 (Phase 2 implements
  items 2-4 above).
- Related: ADR-0076, ADR-0082, ADR-0085, ADR-0097, ADR-0105; bug
  `#260810_RecordsIntegrity` in `docs/notes/Bugs.md`; FW-0033; new FW-0060 to
  FW-0064.
- Resources repo commit: the baseline snapshot of the CSVs + snapshot report +
  `AGENTS.md` change-log entry (same day).
