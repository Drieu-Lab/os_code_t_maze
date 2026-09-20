# ADR 0189: The post-Stop manifest edit contract (offline tools log to `post_hoc`)

- **Status:** Accepted
- **Date:** 2026-09-20
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/tools/sync_fit.py` (`register_sync_in_manifest`),
  `host/tmaze/core/session/session_log.py` (`add_post_hoc` docstring / contract),
  `host/tests/test_sync_fit.py`, `PROGRAMS.md`; completes the wiring ADR-0180
  deferred to Increment 7d
- **Supersedes / Superseded by:** —

## Context

A session's `session_<id>.json` manifest is written by the rig during the
session and finalized at Stop. But it is not frozen forever: an **offline** tool
legitimately edits it afterwards. `sync_fit` (ADR-0153/0154) is the one that does
so today — after fitting a bundle's video↔firmware clocks it registers the two
`sync/` streams and (on a genuine fit) flips the time-anchor `fiducial.method`
from `host-t0` to `led-fit`.

ADR-0180 anticipated this and added an append-only `post_hoc` list to the
manifest plus `add_post_hoc({tool, tool_version, at, fields_changed})`, but the
rig writes the list **empty** and the actual appends were explicitly deferred:
*"Follow-up: wire the `post_hoc` appends in `sync_fit` (7d)."* So until now a
post-Stop edit was **silent** — the file the rig wrote was modified with no record
that a tool touched it, when, or what it changed. For a data-provenance system
that is exactly the gap `post_hoc` exists to close.

Two things needed settling to close it: *what an offline tool is allowed to
change* in a rig-written manifest, and *what it must record when it does*.

## Decision

Write down a **post-Stop manifest edit contract** and make `sync_fit` obey it.

**What may change.** After Stop, an offline tool may modify a manifest ONLY by:
(a) setting the `sync_*` stream pointers (`sync_led_events` / `sync_clock_fit`),
(b) flipping `fiducial.method` to `led-fit` — and only on a genuine fit (the
honest-flip rule of ADR-0188; a failed fit records the files but leaves the
anchor `host-t0`), and (c) **appending** a `post_hoc` entry. Everything else the
rig wrote — session identity, `t0`/`t_end`, `provenance`, `firmware_resets`, and
the task/video/serial stream paths — is **immutable** post-Stop.

**What must be recorded.** Every such edit appends exactly one `post_hoc` entry
carrying at least `{tool, tool_version, at, fields_changed}` (`at` is an
offset-aware ISO stamp, ADR-0182; `fields_changed` is the list of dotted field
paths actually written). A tool may add its own keys: `sync_fit` includes a
`result` summary `{method, verdict, residual_mad_ms}` so a reader scanning
`post_hoc` sees the outcome inline without opening `clock_fit_<id>.json`.

**Append-only.** The list is never rewritten. A `--refit` (or any repeated run)
appends **another** entry, so `post_hoc` is a faithful history of edits. Readers
must tolerate zero-or-more entries. `register_sync_in_manifest` implements this:
the stream/fiducial writes are idempotent, one fresh `post_hoc` entry is appended
per run, and the whole thing is an atomic temp-file+replace so the rig's file is
never clobbered mid-write.

No schema/format/proto change: the `post_hoc` field and manifest
`schema_version` already exist (ADR-0180); the rig still writes the list empty,
so the golden fixture and the analysis mirror are untouched.

## Alternatives considered

- **Leave the edits unlogged (status quo).** — Rejected: it's the provenance gap
  `post_hoc` was added for; a copied bundle gives no hint that a tool rewrote its
  anchor.
- **Overwrite / de-duplicate `post_hoc` on a re-fit (keep only the latest).** —
  Rejected: an audit trail that forgets its own history isn't one; append-only is
  cheap and the entries are tiny. A reader that wants "the current fit" reads the
  `clock_fit` json, not the log.
- **Minimal entry, no `result` summary (strict ADR-0180 four-key shape).** —
  Rejected per the operator's call: embedding `method`/`verdict`/`residual_mad_ms`
  lets someone skim `post_hoc` and see whether each edit produced a usable fit
  without opening a second file. The four base keys are still present, so the
  contract shape is a superset, not a departure.
- **Enforce immutability in code (reject writes to protected fields).** —
  Rejected as overkill: there is exactly one writer path (`sync_fit`) and it is
  the tool the contract is written for; a documented contract + the append
  breadcrumb is proportionate. A future second editor should follow this ADR.

## Consequences

- Every post-Stop manifest edit now self-documents: who, when, what fields, and
  (for `sync_fit`) the result — so a bundle carries its own edit history.
- The rules for touching a finalized manifest are written down in one place
  (this ADR + the `add_post_hoc` docstring where it's enforced), so the next
  offline tool has a pattern to follow instead of reinventing one.
- A `--refit` grows `post_hoc` by one entry each time (intended); routine
  `sync_fit --all` runs skip already-fit bundles, so the list only grows on
  genuine re-edits.
- Nothing changes for the rig's own writes or for readers of a clean bundle
  (`post_hoc == []`), so no format/schema bump and no fixture churn.

## Evidence / links

- `host/tmaze/tools/sync_fit.py` `register_sync_in_manifest` (the append + the
  `fields_changed`/`result` payload), the single-bundle status print;
  `host/tests/test_sync_fit.py` `TestPostHocLog` (good/failed/refit).
- `host/tmaze/core/session/session_log.py` `add_post_hoc` (the contract, stated
  where it's enforced) + `SessionManifest.post_hoc` (ADR-0180).
- Completes the 7d wiring deferred by ADR-0180; builds on ADR-0182 (offset-aware
  `at`), ADR-0188 (the honest `fiducial.method` flip + `--refit`),
  ADR-0153/0154 (the LED sync tool + its manifest registration).
