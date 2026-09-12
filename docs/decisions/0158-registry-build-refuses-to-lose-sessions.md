# ADR 0158: A registry build that would lose sessions is refused, not accepted

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/tools/build_session_registry.py` (`run_build`,
  `BuildOutcome`, `--allow-shrink`, exit code 2), `host/tmaze/ui/records_manager.py`
  (`_StepResult`, `_ChainOutcome`, `_do_generate`, `_async_done`,
  `_confirm_shrink`, `_scan_unindexed`, banner), `host/tmaze/core/session/session_registry.py`
  (`unindexed_sessions`), `host/tests/test_build_session_registry.py`,
  `host/tests/test_records_manager_chain.py`, `PROGRAMS.md`
- **Supersedes / Superseded by:** — (applies ADR-0157's `shrink_check`)

## Context

ADR-0157 gave the registry core a pure `shrink_check` but deliberately left the
*policy* - what to do when a rebuild would drop sessions - to the shells. This
ADR sets that policy, and fixes two related weaknesses found in the Records
Manager's "Generate files" chain during the 2026-09-11 review:

- **Success was inferred from text.** Each step returned a free-form line and
  the GUI decided the run had succeeded if the joined text contained neither
  `FAILED` nor `rc=`. A builder that printed the word "failed" in a benign
  message, or a new failure mode that printed neither token, would be
  misjudged; and the judgement gated a real side effect (recording the Excel
  workbook's mtime as "in sync", which mutes the "Excel edited - Import first"
  warning).
- **The chain ran on regardless.** If the registry step failed, the two
  workbook builders still ran and regenerated the View sheets from whatever
  `session_registry.csv` held - possibly the previous build, possibly a
  half-written one.

And there was no signal at all that the registry was *behind* the disk: at
review time it held 48 sessions while 110 real bundles existed across the two
roots, and the window showed "Registry: 48 sessions · built 2026-08-17" as if
that were the whole story.

## Decision

1. **Refuse to shrink by default.** `run_build` compares the new scan with the
   ids in the existing `session_registry.csv` (`shrink_check`). If any would
   vanish and `allow_shrink` is off, **nothing is written**: the CLI prints the
   missing ids (capped at 20), the likely cause (an unreachable root; check
   `host/recordings_roots.json`) and the override, and exits **2** (distinct
   from 1 = error, so scripts can tell "refused" from "broke"). `--allow-shrink`
   accepts the loss and the summary then says how many were dropped. A first
   build (no previous file) and any growth are never refused - the only
   suspicious direction is loss.

2. **The GUI asks, on the main thread, before accepting.** Generate's worker
   returns the refusal as data; `_async_done` shows the vanishing ids (capped
   at 15) with the same explanation and a default-No question. Yes re-runs the
   whole chain with `allow_shrink=True`; No leaves everything as it was and says
   so in the status line.

3. **Build results are data, not text.** The tool exposes `run_build(...) ->
   BuildOutcome` (`ok`, counts, `refused_shrink`, `shrink`, `error`,
   `unknown_animals`, `summary()`); `main()` is now a thin printer over it. The
   GUI calls `run_build` directly and folds each step into a `_StepResult`
   inside a `_ChainOutcome`. `ok` is a property of those objects; the
   "in-sync" Excel mtime is recorded only when the *whole* chain is ok.

4. **A failed step halts the chain.** Generate stops at the first failed step
   and the result dialog says the remaining steps were not run. (Import's two
   builders are independent, so both still run; one failure still marks the
   chain not-ok.)

5. **The banner says when the registry is behind the disk.** On every data load
   the GUI lists the configured roots (cheap: one `listdir` per root plus a
   manifest check per entry; cached until the next load, not recomputed per
   banner refresh, because an archive root may be a network drive) and shows
   "N bundles on disk not yet indexed - run Generate" (core
   `unindexed_sessions`, ignore-list excluded). The build time shown now comes
   from the v2 header's `built_at` when present (what the build recorded)
   rather than the file's mtime (which a copy or checkout changes).

## Alternatives considered

- **Warn but write anyway.** Rejected: the harm (View sheets and the session
  list silently losing sessions) is done by the time anyone reads the warning,
  and the previous file is gone. Refusing costs one re-run with a flag when the
  loss is genuine.
- **Keep the previous rows for the missing sessions (merge instead of
  rebuild).** Rejected: it would silently turn the full idempotent rebuild
  (ADR-0081) into an accumulating one, re-importing the stale-row problem, and
  would hide the actual fault (an unreachable root) instead of surfacing it.
- **Compare against a stored count rather than ids.** Rejected: ids are what
  the operator needs to see to judge whether the loss is real, and a count can
  stay equal while the set changes.
- **Sniff the text more carefully** (regexes for the failure tokens). Rejected:
  still a guess; returning the decision as data removes the class of bug.
- **Compute staleness from bundle mtimes vs `built_at`.** Rejected in favour of
  set difference: a bundle copied to the archive after the build has a fresh
  mtime but is not "new" if it was already indexed from the rig folder; and a
  set difference names the sessions, which the operator can act on.

## Consequences

- A real deletion (or a session moved to the ignore list) now needs
  `--allow-shrink` / a Yes in the GUI once; the summary and the CLI output
  record that the drop was accepted.
- Exit code 2 from the tool means "refused, nothing written"; the resources
  builder that follows it in a script should treat non-zero as "do not
  proceed" (the GUI already does).
- The staleness scan adds a directory listing of every root at load time (tens
  of ms; the archive root over the network was ~10 ms warm in testing).
- The next Generate on the rig PC will take the registry from 48 to 110
  sessions; that is growth, so it is not refused.
- Phase 8 of the hardening plan may move `_StepResult` / `_ChainOutcome` into a
  core module (`records_edits`) alongside the other extracted GUI logic; their
  shape is already pure.

## Evidence / links

- Headless smoke on the rig PC before the first v2 build: registry 48 rows
  (v1, built 2026-08-17), 62 bundles unindexed across the two roots, banner
  "Registry: 48 sessions · built 2026-08-17 13:44 · 62 bundles on disk not yet
  indexed - run Generate".
- ADR-0081 (full rebuild), ADR-0088 (scan/output split), ADR-0113 (archive
  roots), ADR-0156 (tracked roots), ADR-0157 (schema v2 + `shrink_check`).
- Records Manager hardening plan, Phase 3c.
