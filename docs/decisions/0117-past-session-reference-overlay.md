# ADR 0117: Past-session reference (ghost) overlay on the Metrics tab

- **Status:** Accepted
- **Date:** 2026-08-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/tmaze/core/phase3/phase3_log_reader.py` +
  `host/tests/test_phase3_log_reader.py`, `host/tmaze/ui/phase3_traces_view.py`
  (reference dropdown + ghost draw + mismatch caption), `host/tmaze/ui/experiment_control.py`
  (`animal_getter` wiring + lifted `task_animal_id`), `host/tmaze/ui/tooltip_text.py`,
  `PROGRAMS.md`, `docs/glossary.md`
- **Supersedes / Superseded by:** builds on ADR-0114 (live Metrics tab) + ADR-0116
  (interactive viewport)

## Context

The live Metrics tab (ADR-0114) shows the current session's rolling traces. To
judge whether a run is going better or worse than usual, an operator wants to see
a PRIOR session of the same rat drawn behind the live one — e.g. "is today's ITI
climbing earlier than yesterday's?", or "did accuracy plateau lower after we
turned the return cue off?". Every past session is already on disk as a recorded
`phase3_*.csv` in its bundle's `task/` folder, and it carries everything
`build_traces` needs (the ITI stamps, choice/outcome/cue, intervention context,
the probe marker) plus the run config in its `#` header (cue mapping, tones,
generator). So the overlay needs no new logging — only a reader that turns a
recorded CSV back into the same `Phase3StateSnapshot` the live line is computed
from, and some view plumbing.

## Decision

Add a past-session **reference (ghost) overlay** as pure core + thin shell.

1. **Pure reader `phase3_log_reader`** (`host/tmaze/core/phase3/`):
   `snapshot_from_csv(path) -> (Phase3StateSnapshot, SessionConfig)` parses the
   `#` header (cue mapping / tones / generator / seed) and maps each data row 1:1
   back onto a `Phase3RowView` (driven off the public `Phase3RowView` fields, so
   it can't drift from the live schema; empty cells become the live "unrecorded"
   `None`). The reconstructed snapshot holds exactly what `build_traces` reads, so
   **the reference line is computed by the identical core as the live line** — the
   round-trip test asserts `build_traces(reconstructed) == build_traces(live)`.
   Stdlib only (`csv`/`io`); the sole impurity is the best-effort file read.

2. **The ghost is computed by `build_traces`, cached, and drawn faded.** On
   selection the CSV is read + reconstructed ONCE (off the Tk poll), then its
   `TraceResult` is cached and rebuilt only when the window sizes (`window_n` /
   `iti_window_n`) change. The same per-subplot `_SERIES` table drives both lines,
   so each ghost series pairs with its live counterpart (same colour) but dashed,
   thinner, and translucent, drawn behind (lower z-order). The ghost is aligned on
   ITS OWN elapsed minutes, and the ADR-0116 viewport extent becomes
   `max(current, reference)` so a longer reference's full arc scrolls into view.
   The reference's own intervention/probe spans are NOT shaded (only the live
   session's context is), to avoid confusing the two runs.

3. **Discovery reuses the existing scan.** `phase3_log_reader.discover_sessions(
   animal)` lists the rat's real recorded sessions newest-first via
   `session_registry.scan_roots(paths.phase3_recordings_roots())` (primary +
   archive roots, ADR-0113), honouring the `session_ignore` list (ADR-0097) and
   naturally excluding manual `host/data/` runs (they aren't bundles). The rat id
   comes from the Task tab's shared field: `task_animal_id` was lifted to the top
   of `main()` (like the `phase3_state` seam) and passed as the panel's
   `animal_getter`. The dropdown defaults to "None" (ghost off) and re-scans on
   open / refresh — no disk read happens until a session is actually picked. Each
   entry is labelled `<animal> · <date> <time>`: the list is already filtered to
   the current rat, so the animal prefix is visible confirmation the ghost is
   animal-matched rather than a disambiguator.

4. **Config-mismatch caption.** A pure `describe_config_mismatch(current,
   reference)` lists exactly which run settings differ (cue→side mapping,
   generator, tone frequencies, return-cue use, probe presence), read from a small
   `SessionConfig` derived by ONE function (`session_config_from_snapshot`) applied
   to both the live snapshot and the reconstructed one — a symmetric compare. It
   stays quiet when they match. Return-cue use and probe presence are DERIVED from
   the rows (older CSV headers don't record them), which is why the derivation is
   symmetric; on a freshly-armed live session (no completed trials) both read
   "off/absent" until trials accumulate — the caption is informational, never a
   gate.

## Alternatives considered

- **Re-derive the reference from the built `session_registry.csv`.** The registry
  holds per-session SCALARS (final accuracy, bias …), not the per-trial series a
  trace needs. Reading the raw CSV back through `build_traces` is the only way to
  get an identically-computed line. Rejected.
- **A separate `config_getter` wired from the live controller for the mismatch
  compare (the plan's first sketch).** Rejected as asymmetric: past CSVs can't
  record `return_enabled`/probe in the header, so the reference side must derive
  those from data anyway — comparing a live *config* value against a reference
  *data-derived* value would be apples-to-oranges. Deriving BOTH from the snapshot
  rows via one function keeps the compare honest (and needs no new getter).
- **Eagerly parse every session to mark "config differs" in the dropdown.** Would
  read every rat CSV on each refresh; the on-selection caption already answers
  "how does THIS reference differ", so the eager marker was dropped for now.
- **Auto-select the most recent prior session on open.** The animal id is often
  blank at construction and auto-loading a ghost the operator didn't ask for is
  surprising + does disk I/O unprompted; default "None" keeps reads on explicit
  action.

## Consequences

- No new logging or protocol change; the overlay is a read-only consumer of files
  the rig already wrote and of the ADR-0050 snapshot, so it can never perturb a run.
- One reader core to keep in step with the CSV schema, but it reads the field list
  off `Phase3RowView`, so a new column flows through without edits; the round-trip
  test guards against drift.
- Disk reads happen only on selection/refresh (never on the Tk poll); the ghost
  `TraceResult` is cached and rebuilt only on a window-size change.
- Comparing across DIFFERENT run configs is possible (and sometimes wanted); the
  mismatch caption makes such a comparison explicit rather than silent.

## Evidence / links

- `host/tests/test_phase3_log_reader.py`: build_traces round-trip equality
  (reconstructed == live) across window sizes and both cue mappings; `SessionConfig`
  header read + row-derived flags; symmetric derivation live-vs-reconstructed;
  `describe_config_mismatch` (no-diff + each differing field); discovery
  (newest-first, animal filter, ignore-list) over a temp bundle tree.
- Builds on ADR-0114 (Metrics tab / `build_traces`), ADR-0116 (viewport extent),
  ADR-0050 (published snapshot), ADR-0113 (multi-root discovery), ADR-0097
  (session ignore), ADR-0083 (pure-core/thin-shell).
