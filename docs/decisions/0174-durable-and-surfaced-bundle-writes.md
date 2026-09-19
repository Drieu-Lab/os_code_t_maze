# ADR 0174: Make bundle writes durable (fsync) and surface every write failure

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/session_controller.py` (`_atomic_write`,
  `_write_manifest`, `_write_serial`, `_write_session_summary`, `_persist_notes`,
  `_write_animal_state`, `_write_session_meta`, Stop flow),
  `host/tmaze/ui/phase3_controller.py` (`_write_log`, `_write_excursion_log`),
  `host/tmaze/ui/camera_view.py` (`_encode_loop` sidecar flush),
  `host/tests/test_session_phase3_wiring.py`,
  `host/tests/test_session_control.py`
- **Supersedes / Superseded by:** —

## Context

Two gaps in how the session bundle reaches disk, both from the Sep-1 review
(L3 = write failures should be surfaced; L4 = writes should be durable).

**L4 - "crash-safe" only meant "not torn."** Every bundle writer uses the
temp-file + `os.replace` pattern: write `<file>.tmp`, then atomically rename it
over the real name. `os.replace` guarantees a reader sees *either* the whole old
file *or* the whole new file - never a half-and-half mix. But a write goes first
into the OS **page cache** (RAM); the OS flushes that to the physical device
later, on its own schedule, and filesystems may reorder the rename (metadata)
*ahead* of the data blocks. So in the window after `os.replace` returns but
before writeback completes, a power loss or OS crash can leave the new name
pointing at data that never reached the disk - a zero-length or garbage file.
The rename protected against a *torn* file during normal operation; it did
nothing to make the bytes *durable*. Separately, the camera **sidecar CSV** was
only flushed on close, so an unclean end lost the whole session's buffered
per-frame rows.

**L3 - write failures were silent (except Phase 3's two files).** The ADR-0121
per-slot pattern (`_write_errors` + `_record_write_result` + a loud `_write_warn`
status prefix) existed only in the Phase 3 controller, on the trial CSV and the
excursion log. Every **session-controller** writer - manifest, per-line serial,
end-of-session summary, notes JSONL, `animal_state`, `session_meta` - swallowed
its failure with a bare `pass`. If the recordings drive filled or dropped
mid-session, those files could silently fail to land and the operator got no
signal at all.

## Decision

**L4 (durability).** Add `fh.flush(); os.fsync(fh.fileno())` immediately before
`os.replace` in the live session-bundle writers: session-controller
`_atomic_write` (which backs the summary JSON+text, notes, `animal_state`,
`session_meta`) and `_write_manifest`; Phase 3 controller `_write_log` and
`_write_excursion_log`. `fsync` forces this file's cached data to the storage
device and waits for the device to acknowledge, so the ordering becomes
*data-on-disk -> then rename*: after a crash you get either the intact old file
or a complete new file. The camera `_encode_loop` now flushes+fsyncs the sidecar
CSV **at least once per second** (a `time.monotonic` check), bounding a crash's
sidecar loss to ~1 s regardless of frame rate.

Scope is the live bundle only. The per-line serial append keeps its per-line
`flush()` (a per-line `fsync` would be too heavy on a hot stream) and is not
fsync'd. Directory `fsync` (which would also make the *rename itself* durable) is
deliberately NOT done: it is not portable on the Windows rig, and the realistic
failure here is data-in-cache loss, which the file `fsync` already covers. The
many lower-stakes core/records/tools atomic writers (offline, re-runnable) are
left as-is.

**L3 (surfaced failures).** Give the session controller the same ADR-0121
pattern: a `_write_errors` dict + `_record_write_result(slot, path, exc)` across
its six writers (`manifest`, `serial`, `summary`, `notes`, `animal_state`,
`session_meta`); the hot `serial` slot records once, not per line. A failure is
surfaced three ways: (1) a loud, always-visible status-line prefix naming the
at-risk files (mirrors Phase 3's `_write_warn`), kept during the run since a
failure may outlast it, and repeated at Stop - the session still closes; (2) an
`auto:write_failed` timeline note per failure (slot + filename), which lives in
the notes JSONL and folds into the summary; (3) a `write_errors` list on the
manifest. (2) and (3) are best-effort **in-bundle** records - with the honest
caveat that if the storage is *completely* dead they cannot land either, which is
exactly why the RAM-resident status-line warning is the primary signal.

## Alternatives considered

- **Leave writes at `os.replace` only (L4).** Rejected: "atomic rename" is not
  "durable"; a power cut in the writeback window still loses recorded data, which
  for a rig under a live animal is a data-integrity loss, not a cosmetic one.
- **fsync every atomic writer in the repo, or route them through one shared
  durable helper.** Rejected for now: a broad refactor across ~10 call sites
  (records/tools/paths/led_map/...) that are offline and re-runnable; not worth
  the churn/review. Scoped to the live bundle writers that a crash actually
  endangers.
- **Directory fsync for full rename durability.** Rejected: not portable on the
  Windows rig; the dominant risk is data-in-cache loss, already covered.
- **Per-line `fsync` on the serial stream.** Rejected: too heavy on a hot stream;
  per-line `flush()` is retained.
- **Surface L3 failures only in the GUI (no in-bundle record).** Rejected: a
  transient or single-file failure should leave evidence in the bundle; the note
  + manifest field capture the common cases the status line alone would lose once
  the window closed.
- **Modal messagebox at Stop instead of the status line.** Rejected: blocks the
  between-sessions flow; the non-blocking, always-visible status prefix matches
  ADR-0121 and cannot be dismissed-and-forgotten while the fault persists.

## Consequences

- A normal, healthy session is byte-for-byte unchanged; the only added cost is
  one `fsync` per small-file write and one sidecar `fsync`/s - negligible for a
  few hundred trial rows and a 1 Hz sidecar flush.
- After a crash, the trial CSV, excursion log, manifest, summary, notes,
  `animal_state`, and `session_meta` are durable up to their last write; the
  sidecar is durable to within ~1 s of the newest recorded frame.
- A disk fault is now loud and named at the rig, and (storage permitting) leaves
  a timeline note + manifest field so the bundle carries evidence of what was at
  risk.
- The per-line serial append is durable only to its OS flush, not fsync (stated
  guarantee); the rename itself is not directory-fsync'd on the rig.

## Evidence / links

- Review findings L3 (surface write failures) and L4 (durable writes).
- Builds on ADR-0121 (per-file write-failure surfacing for the Phase 3 files) and
  ADR-0074 (notes JSONL survives an unclean end), ADR-0071/0072/0077/0079
  (the summary / config / animal_state / session_meta bundle streams).
- Tests: `test_trial_csv_is_fsynced_before_rename` (fsync precedes the rename),
  and the session-controller write-failure test (a raising `_atomic_write`
  records the slot, emits `auto:write_failed`, warns at Stop, session still
  closes).
