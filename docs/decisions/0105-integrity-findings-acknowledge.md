# ADR 0105: Stable integrity findings + an acknowledge/dismiss store

- **Status:** Accepted
- **Date:** 2026-08-13
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/records_audit.py`,
  `host/tmaze/core/records/records_audit_ack.py` (new),
  `host/tmaze/tools/records_check.py`, `host/tmaze/ui/records_manager.py`,
  `host/tests/test_records_audit.py`,
  `host/tests/test_records_audit_ack.py` (new)
- **Supersedes / Superseded by:** Builds on ADR-0097 (records audit) and
  ADR-0104 (`water_method`)

## Context

The read-only records-audit (ADR-0097) makes cross-record drift visible for the
operator to fix. But some findings can never be "fixed" and legitimately recur
every run: a genuinely short real session, an old bundle whose animal id predates
`animals.json`, or a husbandry cell whose free-text ("`Till Monday evening`",
"`No supp`") is a real note the operator won't move. On the current data the CLI
already flags ~45 items, most of them permanent. Left in the report, those bury
the *new, actionable* findings - so the audit gets ignored, defeating its point.
The operator asked to be able to "acknowledge lapses and dismiss them from future
integrity checks."

Two things blocked that: findings were plain strings (no identity to dismiss and
have the dismissal *stick* across runs), and there was nowhere to record a
dismissal. Separately, ADR-0104 added `water_method` but nothing checked that a
CSV cell actually holds a known method.

## Decision

Give every finding a **stable identity key** and add a small **acknowledge store**
the audit partitions against; also add a `water_method` sanity check.

- **Structured findings.** `records_audit` gains a `Finding(key, text)` and each
  `AuditSection` now carries an `id`, a list of active `findings`, and the
  `acknowledged` ones. The key is `"<section id>:<natural key>"`
  (e.g. `short_sessions:600_20260724_165906`,
  `suspect_water_cells:600:2026-07-14:water_on`) - stable across runs and
  reorderings, and it changes if the underlying problem moves to a different
  row/session so a real fix genuinely clears it. `.items`/`.count` still exist as
  properties over the *active* findings, so existing callers are unaffected.
- **Acknowledge store.** New `records_audit_ack.py` (a pure I/O core mirroring
  `session_ignore.py`) reads/writes `host/records_audit_ack.json`
  (tracked, diffable): `{"_schema":1,"acknowledged":{"<key>":{"reason","date"}}}`.
  Load is best-effort (a missing/malformed file dismisses nothing); writes are
  atomic (temp + `os.replace`). `build_report(..., acknowledged=<keys>)`
  partitions each section into active vs acknowledged; `total_flagged` counts
  ACTIVE only (dismissed items drop out of the headline count by design),
  `total_acknowledged` reports the hidden count.
- **`water_method` check.** A new detector flags a non-blank `water_method` cell
  that `daily_log.normalize_method` folds to `""` (a hand-typed CSV typo the GUI
  and Excel `--export` would have normalised), reusing the same vocabulary so it
  can't drift.
- **Surfaces.** `records_check` loads the store, hides acknowledged findings,
  notes the dismissed count, and gains `--show-acknowledged` to list them. The
  Records Manager integrity panel becomes interactive: active findings are grouped
  in a tree; "Acknowledge selected..." prompts for an optional reason and writes
  the key; "Manage dismissed..." lists dismissals and restores one
  (un-acknowledge). The records themselves are never written - only the ack store.

## Alternatives considered

- **Dismiss by matching the finding's display string.** Rejected: brittle - any
  wording change (a reformatted line, a duration re-rounded) would silently
  un-dismiss or mis-match. A stable structured key survives presentation changes.
- **Suppress whole sections/kinds, or per-animal.** Rejected as too coarse:
  acknowledging "all short sessions" would also hide a *new* churn burst, which is
  exactly the signal the audit exists to surface. Dismissal is per-finding.
- **Store dismissals in `session_ignore.json`.** Rejected: that list drops whole
  sessions from the pipeline (no registry row, no View sheet) - a heavier,
  different action. Acknowledging a finding must NOT remove the session; it only
  silences one report line. Separate concern, separate file.
- **Machine-local (git-ignored) store.** Rejected: acknowledgements are shared
  decisions ("this short session is real") that belong with the team, like
  `session_ignore.json`; tracked + diffable is the right default.

## Consequences

- The audit stays useful as data grows: permanent findings can be retired once,
  with a reason, and new drift stands out. Dismissals are reversible and reviewed
  in git.
- A dismissed finding whose underlying row later *moves* gets a new key and
  reappears - intended (a genuinely new problem shouldn't inherit an old
  dismissal), but it means re-acknowledging after a legitimate move.
- `records_audit` now depends on the sibling `daily_log` core (for the method
  vocabulary); still pure, no I/O, headlessly testable.
- `total_flagged` semantics changed to active-only; callers wanting the raw total
  add `total_acknowledged`.

## Evidence / links

- Core: `records_audit.py` (`Finding`, `AuditSection.id/findings/acknowledged`,
  `suspect_water_methods`, `build_report(acknowledged=)`, `total_acknowledged`),
  `records_audit_ack.py` (`load_ack`, `load_acked_keys`, `acknowledge`,
  `unacknowledge`).
- Shells: `tools/records_check.py` (`--show-acknowledged`),
  `ui/records_manager.py` (`_show_integrity_dialog`, `_manage_acknowledged`).
- Tests: `test_records_audit.py` (findings/keys, method section, ack partition),
  `test_records_audit_ack.py` (load/round-trip/restore) - full suite 735 passing.
