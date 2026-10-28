# ADR 0232: Choosing which sessions offline DeepLabCut runs on

- **Status:** Accepted
- **Date:** 2026-10-28
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/tools/dlc_offline.py` (`select`, `choose`, `--from` / `--to` /
  `--animal` / `--redo`), `host/tmaze/ui/records_manager.py` (Run DLC dialog,
  "Run DLC on selected")
- **Supersedes / Superseded by:** — (extends [ADR-0227](0227-offline-dlc-over-all-sessions.md))

## Context

`dlc_offline` ran every session without an up-to-date result, newest first
(ADR-0227). About 200 sessions are pending at roughly an hour each on the GPU,
and the operator wants the recent ones (from 2026-09-20, all rats) first and
sometimes a hand-picked few. The only way to choose was a long list of
`--session` arguments, and the Records Manager's Run DLC button could not
choose at all.

Test recordings also got queued: `000_20260921_174755` and the LED-mapping
session `000_20260922_163830` are not in `host/session_ignore.json`, so
`session_bundles()` (which honours that list) kept them.

## Decision

- **Only animals in `animals.json` are run.** A session's animal is the part of
  its id before the first `_`; ids whose animal is not in the canonical list
  (`000_...`, `123_...`, a mistyped animal) are listed as "not run" and skipped,
  also when named with `--session`. If `animals.json` can't be read, a warning is
  printed and no animal filter is applied, so a missing resources repo never
  blocks a run. `session_ignore.json` is left alone: the LED-mapping session
  stays visible in the registry and the Records Manager.
- **Date and animal choice:** `--from` / `--to` (`YYYY-MM-DD`, both inclusive,
  compared with the date in the session id) and `--animal` (repeatable). The
  order stays newest first.
- **Redo is per session only.** `--redo` is accepted only together with
  `--session`, so a date range can never redo anything by accident. A redone
  session keeps its old result until its own rerun starts (`run_session`
  removes the manifest at that point, as for any rerun); a run stopped earlier
  leaves the sessions it didn't reach as they were.
- **Records Manager:** Run DLC opens a dialog (from / to date, a tick box per
  animal, "redo sessions whose video changed", a live count); the Sessions tab
  gets "Run DLC on selected" for highlighted rows, which asks before redoing
  sessions that already have a result. Both start `dlc_offline` with the
  options above, so the CLI and the GUI pick sessions the same way.

## Alternatives considered

- **Add the test sessions to `session_ignore.json`**: hides them from the
  registry, the Records Manager and `records_check` too, and the next test id
  would be run until someone adds it.
- **A filter on the session-id pattern (`000_`)**: guesses at what a test is;
  the animal list already says which animals are real.
- **Redo by date range**: one typo would replace weeks of results.
- **Delete old results before a redo starts**: a stopped run would lose results
  it never replaced.

## Consequences

- A new animal must be added to `animals.json` (Roster tab) before its sessions
  get DLC results; its sessions are listed as "not run" until then.
- Sessions of a renamed animal (an alias in the id) are skipped: only canonical
  ids match.

## Evidence / links

- Dry run 2026-10-28: `--from 2026-09-20` gives 86 sessions to run, with the two
  `000_` sessions listed as not run.
- Tests: `host/tests/test_dlc_offline.py` (`test_choose_by_date_animal_and_known`,
  `test_redo_only_for_named_sessions`, `test_redo_not_reached_keeps_its_result`).
