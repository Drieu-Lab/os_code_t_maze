# ADR 0121: Surface Phase 3 data-file write failures instead of swallowing them

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/phase3_controller.py` (`_write_log`, `_write_excursion_log`, new `_record_write_result` / `_write_warn`, `_publish_status`), `host/tests/test_session_phase3_wiring.py`
- **Supersedes / Superseded by:** —

## Context

The Phase 3 controller persists two files as the session runs — the per-trial
CSV (`_write_log`) and the per-excursion event log (`_write_excursion_log`) —
each via a crash-safe atomic write (write a `.tmp`, then `os.replace`). Both
wrapped the whole write in `except OSError: pass`.

That silently hid the one failure that matters most for data integrity: if the
disk is full, the file is locked (e.g. open in Excel / OneDrive sync), or the
path becomes unwritable, the write fails and **the session's data is not on
disk** — but the task keeps running, the ribbon and status line look normal, and
the operator has no idea the recording is being lost. As neural-recording
integration approaches, a silently missing behavior CSV is a silently missing
alignment stream.

## Decision

Do not swallow the write failure — record it per-file and surface it loudly.

- A small `_record_write_result(slot, path, exc)` tracks each persist attempt in
  `self._write_errors` (a dict `slot -> failing file name`, `slot` being `"task"`
  or `"excursions"`). A failure records the file name; a success clears that
  slot, so a transient hiccup that recovers stops warning. It is **per-file** so
  a persistently failing file is never masked by the other file writing fine.
- `_write_log` / `_write_excursion_log` keep the same atomic write but use
  `try/except OSError/else` to call `_record_write_result` with the outcome
  instead of `pass`.
- `_write_warn()` builds a loud status-line prefix naming the at-risk files
  (`!! DISK WRITE FAILED: <files> - data not saved !!`), prepended in every
  `_publish_status` state (gated / paused / armed) since a failure can outlast
  the moment it happened. This follows the module's existing convention of
  surfacing problems in the status line (as `_config_error` and the `drops:`
  counter already do) rather than introducing logging the operator won't see.

The failure is still non-fatal: the actuation thread keeps running (a raised
exception there would take down the task), and the next tick re-renders and
re-writes the WHOLE table, so a recovered disk immediately re-persists the full
session with no gap.

## Alternatives considered

- **Keep `except OSError: pass` (status quo).** Rejected — it is the exact
  failure mode that loses a session's data with zero operator feedback.
- **Log to a file/logger instead of the status line.** Rejected as the primary
  surface: the module has no logging today, and the operator watches the Rig-view
  status line, not a log file. (Logging could be added later without changing
  this contract.)
- **Raise / stop the session on a write failure.** Rejected — a raise on the
  actuation thread would crash the task, and a transient lock (Excel/OneDrive)
  shouldn't end a run; re-rendering the whole table on the next tick self-heals
  once the disk is writable.

## Consequences

- A lost write is now impossible to miss: the status line shows which file is not
  saving, and clears itself once writing succeeds again.
- No behavior change on the happy path (successful writes clear an empty slot).
- The warning is best-effort feedback, not a guarantee of durability; pairing it
  with the atomic-write pattern means partial/corrupt files still never appear.
- Same pattern is now available for other actuation-thread writers if they need
  it (currently only these two use it).

## Evidence / links

- Builds on ADR-0047 (the trial CSV) and ADR-0109 (the excursion log); relates to
  the Records-integrity write-clobber guard on the records side (bug
  `#260810_RecordsIntegrity`).
- Test: `test_session_phase3_wiring.test_disk_write_failure_is_surfaced_and_clears_on_recovery`
  (a mocked `OSError` on write records the failure and shows the banner; a later
  successful write clears it). Full host suite green.
