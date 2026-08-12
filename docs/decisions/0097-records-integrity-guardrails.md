# ADR 0097: Records integrity guardrails - a session ignore-list and a read-only audit report

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_ignore.json` (new); `host/tmaze/core/records/session_ignore.py`, `records_audit.py` (new); `host/tmaze/core/session/session_registry.py` (`build` gains `ignore`); `host/tmaze/tools/build_session_registry.py`, `records_check.py` (new); `host/tmaze/ui/records_manager.py`; resources repo `records/animal/build_session_training_log.py`; tests `host/tests/test_session_ignore.py`, `test_records_audit.py`, `test_session_registry.py`
- **Supersedes / Superseded by:** — (builds on ADR-0075, 0076, 0081, 0082, 0085)

## Context

An audit of the coordinated records (bug `#260810_RecordsIntegrity`) found the
machine-readable surfaces - `animals.json`, `daily_log.csv`,
`session_annotations.csv`, the generated `session_registry.csv` and the Excel
views - drifting out of step in ways no single surface caught:

- **Non-real sessions were first-class.** Two bundles keyed to animal ids that
  aren't in `animals.json` - `000_20260727_132752` (a stray start/stop-glitch
  folder from the fixed bug `#260729_SessionTaskFolder`) and
  `123_20260727_104048` (a test run) - flowed all the way into
  `session_annotations.csv` and even got their own `View 000` / `View 123` sheets
  in `session_training_log.xlsx`. Nothing filtered to canonical ids.
- **Husbandry join gaps.** Real sessions on 2026-07-24 / 07-27 / 07-29 / 08-10
  had no `daily_log.csv` row for their `(animal, date)`, so they can't join
  husbandry.
- **Column misuse.** Rig water *volumes* and free text were typed into the
  `water_on` / `water_off` *time* columns (e.g. `8.46ml rig; No suppl`,
  `Till Monday evening`), so `water_ml` never captured the volume and the
  registry copies the non-time text verbatim.
- **Start/stop churn.** Bursts of near-zero-duration bundles (e.g. three on
  2026-08-03 of 12-44 s) become real registry/annotation/view rows.

The user's decision for this bug was **code/tooling only**: the code should make
these problems visible and keep non-real sessions out, but must **not** edit the
husbandry data (that needs the operator's domain knowledge). The one approved
data-touching action is a *non-destructive* exclusion of the junk sessions.

## Decision

Two guardrails, both pure-core + thin-shell, both non-destructive.

1. **A session ignore-list.** A tracked `host/session_ignore.json` (same `host/`
   JSON pattern as `note_tags.json`) lists session ids that are not real
   experiment sessions. A new pure `session_ignore` core loads it best-effort;
   `session_registry.build(root, ignore=...)` skips those bundles, so they
   produce no registry row, no tag rows, no View sheet, and no Records Manager
   entry. The **bundles stay on disk** - deleting an id from the list brings a
   session back (reversible), unlike deleting a folder. The list is seeded with
   the two known junk ids. Because the resources `build_session_training_log.py`
   reads the same file via the `OS_CODE_T_MAZE` resolver, and the Records Manager
   filters it from the session list, an already-generated registry can't
   resurface them either.

2. **A read-only audit report.** A new pure `records_audit` core detects the four
   drift classes above (non-canonical animal ids; sessions with no daily row;
   water cells that aren't clock times; sub-threshold-duration churn), and a thin
   `tmaze.tools.records_check` runner loads the files and prints them. It changes
   nothing - the fixes are the operator's. `build_session_registry` additionally
   prints a one-line non-canonical-id warning at Generate time so a typo is
   noticed immediately. The churn threshold is a named constant
   `records_audit.SHORT_SESSION_S = 60` s.

## Alternatives considered

- **Delete the junk bundles / annotation rows.** Rejected by the user in favour
  of a reversible ignore-list; deleting recorded data is irreversible and a
  mis-typed id might later turn out to matter.
- **Hard-block an unknown animal id at Session Start.** Rejected: ADR-0075
  deliberately never blocks Start (the rig must run); the guardrail stays
  advisory (warn, don't fail) and the exclusion happens later at registry build.
- **Auto-correct the water columns / backfill daily rows in code.** Rejected:
  the code can't know the true volumes or missing husbandry values; inventing
  them would corrupt the source of truth. The checker surfaces them for a human.
- **Auto-drop churn sessions by duration.** Rejected as too aggressive (a real
  session can be short); churn is *reported*, and the operator decides whether to
  add an id to the ignore-list.

## Consequences

- Non-real sessions disappear from every downstream surface without any data
  being deleted, and the decision is one shared, diffable file.
- `session_registry.build` has a new optional `ignore` parameter (default: none,
  so existing callers/tests are unaffected).
- The audit is opt-in (`records_check`) plus a Generate-time warning; it never
  fails a build, so it can't block record-keeping. Running it after "Generate
  files" is the recommended habit.
- The husbandry gaps and water-column misuse remain open **data** work for the
  operator (tracked in `docs/notes/Bugs.md` under `#260810`); this ADR only makes
  them visible. Deeper structural fixes (join-time id normalization, ghost
  annotations) are deferred to `docs/future-work.md` FW-0033.
- The `records_check` water-time recogniser is a heuristic; it will flag a
  legitimate free-text on/off note (e.g. "6:30PM till Sun") as "not a time".
  That is intentional - those are exactly the cells where the machine-readable
  value was lost - but it means the report is a prompt for a human, not a
  hard error.

## Evidence / links

- Cores + tests: `host/tmaze/core/records/{session_ignore,records_audit}.py`,
  `host/tests/test_{session_ignore,records_audit}.py`; ignore path exercised by
  `host/tests/test_session_registry.py::TestIgnoreList`.
- Runners: `host/tmaze/tools/{build_session_registry,records_check}.py`.
- Real-data run of `records_check` (2026-08-11) reproduced the audit findings
  (46 items): 13 sessions with no daily row (07-24/07-27/07-29/08-10), 30
  non-time water cells, 3 churn fragments on 08-03; zero non-canonical ids
  (000/123 ignored).
- Bug: `docs/notes/Bugs.md` `#260810_RecordsIntegrity`. Builds on ADR-0075
  (identity), 0076 (daily table), 0081 (registry), 0082 (training view), 0085
  (records manager).
