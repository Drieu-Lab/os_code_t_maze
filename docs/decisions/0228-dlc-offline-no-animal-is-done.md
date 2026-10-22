# ADR 0228: `dlc_offline` - a video with no animal counts as done

- **Status:** Accepted
- **Date:** 2026-10-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/tools/dlc_offline_worker.py`, `host/tmaze/tools/dlc_offline.py`,
  `docs/derivatives_format.md`
- **Supersedes / Superseded by:** — (refines [ADR-0227](0227-offline-dlc-over-all-sessions.md))

## Context

The first one-session test of `dlc_offline` picked `600_20260826_132348`, a
25 s clip (623 frames) with no rat in it. DeepLabCut's pose step ran, but its
track stitching raised `OSError: Tracklets are empty.`, so the worker failed and
the session stayed partial. As ADR-0227 stood, every later run would retry it
(about a minute of GPU) and count it as a failure, so a "some sessions failed"
exit would no longer mean anything went wrong.

## Decision

When DeepLabCut's stitching raises "Tracklets are empty", the worker reports
`no_animal` instead of failing. `dlc_offline` then writes a complete manifest
with no output files and `params.no_animal: true`, so the session counts as
done and is not retried. The run prints "(no animal found)" beside it. Any
other error still fails the session.

The session stays visible everywhere else (registry, records, other
pipelines): an empty video is a fact about that session, not a reason to hide it.

## Alternatives considered

- **Add such sessions to `host/session_ignore.json`**: right for recordings
  that aren't real sessions, but it hides them from every tool, and someone has
  to find and list each one by hand.
- **Leave it failing**: about a minute per run, but it turns every full run's
  exit code into "some failed".
- **Write an empty CSV**: readers would have to tell "empty table" from "no rat"
  anyway; a flag in the manifest says it directly, and readers already treat a
  missing frame as not detected.

## Consequences

- Readers of `dlc_full_iter1` must expect a done session with no
  `<sid>_dlc.csv` (check `params.no_animal`). `head_point_dataset` already
  only reads CSVs that exist.
- If the message DeepLabCut uses changes in a later version, such videos go back
  to failing, which is visible, not silent.
- A video where the network misses a rat that is really there would also be
  marked done. With this model that has only been seen on the empty clip; the
  manifest flag makes such sessions easy to list and re-check.

## Evidence / links

- `600_20260826_132348`, 2026-10-22: failed with "Tracklets are empty." at
  01:07, then done "(no animal found)" in 1 min at 10:30 with this change.
- Test: `host/tests/test_dlc_offline.py`
  (`test_no_animal_is_done_with_no_files`).
