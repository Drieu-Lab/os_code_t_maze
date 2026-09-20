# ADR 0188: Fit every bundle's clocks in batch and surface the sync status

- **Status:** Accepted
- **Date:** 2026-09-20
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/tools/sync_fit.py` (`--all`/`--refit`, honest
  manifest flip), `host/tmaze/core/session/session_registry.py` (v3→v4:
  `sync_method` + `sync_residual_ms`), `host/tmaze/core/records/records_audit.py`
  (`unfitted_sync` flag), `host/tmaze/ui/records_manager.py` ("Fit pending
  sessions"), `host/tests/test_sync_fit.py`, `host/tests/test_session_registry.py`,
  `host/tests/test_records_audit.py`, `docs/records-schema.md`, `PROGRAMS.md`
- **Supersedes / Superseded by:** —

## Context

`sync_fit` (ADR-0153/0154, FW-0045) lines up a session's video and Arduino
clocks from the board's indicator LEDs and writes two `sync/` artifacts +
registers them in the manifest. It existed only as a **one-bundle-at-a-time**
tool run by hand, and nothing recorded, per session, *whether* a bundle had been
fit or *how well*. So the cross-stream timeline the whole recording effort exists
to enable was, operationally, opt-in and invisible: a bundle could sit unfit
indefinitely and no surface would say so.

Two facts shaped the design. First, `register_sync_in_manifest` flipped the
time-anchor `fiducial.method` to `led-fit` **unconditionally** — even when the
fit did not actually take — so the manifest could claim a clock fit the numbers
didn't support. Second, LED-in-frame coverage across the rig's history is
**incomplete** (the entry/exit fiducials aren't reliably in the camera FOV yet;
that hardware step is 7b). A naive "flag every session that isn't `led-fit`"
audit would therefore fire on nearly the entire back-catalogue — pure noise —
because most old bundles have no fiducials to fit against at all.

## Decision

Operationalise the existing tool without changing the fit math, in four parts.

**1. Batch mode (`sync_fit --all [--refit] [<root>]`).** Fits every bundle under
the given root (or the tracked recordings roots when none is given), scanning the
same way the registry does. Each bundle lands in one of three buckets:
`fit` (LED fiducials seen **and** a usable fit produced — write `sync/` + register),
`failed` (fiducials seen but no usable fit), or `skipped` (no video/data, **no
fiducials in frame**, or already fit). `--all` skips a bundle that already carries
a `sync_clock_fit` stream unless `--refit`. It prints a per-bundle table +
summary, writes a run log, and exits **non-zero only when a bundle FAILED** — a
skip is never a failure (so CI/automation gets a clean signal that is silent
while LED coverage is incomplete).

**2. Honest manifest flip.** `register_sync_in_manifest` now flips
`fiducial.method` to `led-fit` **only on a good fit**; a failed fit still records
the two `sync/` files (so they're discoverable) but leaves the anchor `host-t0`.
Applies to single-bundle runs too. When no fiducials are detected, `--all` writes
**nothing** (no orphan `clock_fit` json), so "skipped" bundles stay clean.

**3. Registry v3→v4.** Two appended columns per session, derived from the
bundle's `sync/clock_fit_<id>.json` — its **own** numbers, not the manifest's
`fiducial.method` (which older bundles flipped even on failure), so the columns
are honest for historical bundles: `sync_method` ∈ `led-fit` (residual = the
fit's MAD in ms, in `sync_residual_ms`) | `failed` | `host-t0` (no fit file).

**4. Gated audit flag + operator button.** `records_check` gains an
`unfitted_sync` section flagging sessions whose `sync_method == "failed"` and
whose start is more than `UNFITTED_AGE_DAYS` (**14**) old — a grace period so a
just-recorded session isn't nagged. It is **gated on `failed` only**: `host-t0`
(no fiducials / never run) is never flagged, so the check is noise-free while
LED-in-FOV coverage is incomplete; genuinely unfittable sessions can be
acknowledged (ADR-0105). The Records Manager gains a "Fit pending sessions"
toolbar button that runs `sync_fit --all` off the UI thread and reports on its
own path (fitting touches bundle files, not the records CSVs).

No `format_version` / manifest `schema_version` / proto change (the registry is a
derived index, and the fit math and `sync/` artifact shapes are unchanged).

## Alternatives considered

- **Flag every non-`led-fit` session (`all_hostt0`)** — rejected: with LED
  coverage incomplete, this fires on almost the whole history, drowning the real
  signal. Gating on `failed` (fiducials *were* seen) keeps the flag actionable.
- **Keep flipping `fiducial.method` to led-fit on any run** — rejected: it lets
  the manifest assert a clock fit the residuals don't support. The registry now
  reads the fit's own numbers precisely so this can't mislead, but the manifest
  should be honest at the source too.
- **Exit non-zero on any skip (`strict`)** — rejected: a bundle with no
  fiducials isn't a failure, and CI would be red for the entire back-catalogue.
  Only a genuine `failed` fit fails the run.
- **Derive `sync_method` from the manifest `fiducial.method`** — rejected: older
  bundles set it to led-fit even when the fit failed, so the column would lie for
  exactly the history we most want to audit. The `clock_fit` json is the source
  of truth.
- **Route the "Fit pending" button through the Generate/Import chain
  (`_async_done`)** — rejected: that path reloads records data and re-marks the
  Excel workbook in-sync, neither of which a fit run should do; a dedicated
  queue keeps the concern isolated.
- **`--refit` on by default** — rejected: re-decoding LEDs from video for every
  already-fit bundle is minutes of work for no gain; skip-if-fit is the default,
  `--refit` is the opt-in.

## Consequences

- One command (or one button) brings the whole history's clocks into alignment,
  and the registry now says per session whether that happened and how tight the
  fit was — the sync status is a first-class, queryable datum instead of tribal
  knowledge.
- The audit stays quiet today (no `failed` rows until LEDs are reliably in the
  FOV — 7b) and turns into a useful "these need attention" list the moment they
  are, with a 14-day grace and an acknowledge escape for unfittable sessions.
- The manifest can no longer over-claim a fit; a reader can trust `led-fit`.
- The button reminds the operator to run "Generate files" afterwards to refresh
  the registry's sync columns (fitting writes into bundles; the registry is a
  separate rebuild).
- Not addressed here: getting the LED fiducials into the camera FOV (7b, needs a
  rig visit), exposure pinning in the sidecar (7c), and wiring `sync_fit` to
  append to the manifest `post_hoc` edit log (7d).

## Evidence / links

- `host/tmaze/tools/sync_fit.py` (`run_all`/`fit_one_bundle`/`needs_fit`/
  `summarize`/`_fit_is_good`, the conditional `register_sync_in_manifest`),
  `host/tests/test_sync_fit.py` (`TestBatchHelpers`).
- `host/tmaze/core/session/session_registry.py` (`_fill_sync`, `SYNC_*`,
  `SCHEMA_VERSION = 4`), `host/tests/test_session_registry.py` (`TestSyncColumns`);
  `docs/records-schema.md`.
- `host/tmaze/core/records/records_audit.py` (`unfitted_sessions`,
  `UNFITTED_AGE_DAYS`, the `unfitted_sync` section),
  `host/tests/test_records_audit.py` (`TestUnfittedSessions`).
- `host/tmaze/ui/records_manager.py` ("Fit pending sessions" button,
  `_on_fit_pending`/`_drain_fit_queue`).
- Builds on ADR-0153/0154 (the LED sync tool + its manifest registration),
  ADR-0081/0157/0160 (the registry + its schema history), ADR-0097/0105/0159
  (the records audit + acknowledgements), ADR-0163 (the Records Manager's
  off-thread build pattern). FW-0045; the reflash/hardware follow-ups are 7b/7c/7d.
