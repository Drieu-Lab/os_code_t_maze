# ADR 0119: Trial edits (Repeat/Back) preserve history append-only, never erase it

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_log.py`, `host/tmaze/ui/phase3_controller.py`, `host/tmaze/core/phase3/phase3_log_reader.py`, `host/tmaze/analysis/bundle_reader.py`, `host/tests/test_phase3_log.py`, `host/tests/test_phase3_log_reader.py`, `host/tests/test_analysis_bundle_reader.py`
- **Supersedes / Superseded by:** supersedes the Repeat/Back *data-erasure* semantics of ADR-0058 (the rest of ADR-0058 stands)

## Context

During a host-owned Phase 3 run the operator has Rig-view trial-control buttons
(ADR-0058). Two of them — **Repeat** (re-run the current trial) and **Back**
(step to the previous trial and re-run it) — called `Phase3Log.reset_trial`,
which *cleared* the row's live fields (chosen arm, outcome, all the event
timestamps) so the trial could be re-scored into the same row.

That erased what the animal actually did. If the rat ran the trial, was scored
CORRECT, and the operator pressed Repeat, the recorded CORRECT (and its event
times) was overwritten by the re-run — the real behavior was gone from the file,
with no trace that an edit had happened. Captured as bug
`#260810_TrialEditErasesHistory`: *"Pressing the manual trial-modification
buttons erases the log of what the animal actually did. Want: preserve the
animal's real behavior history AND log the injected trial (the one created by the
manual button) as its own event, so past history is never overwritten."*

This matters most now, as neural recording integration approaches: the behavior
CSV is one of the streams we align spikes against, and a silently overwritten
trial is a silent hole in that alignment.

## Decision

Make trial edits **append-only**. The live table stays one row per trial INDEX
(so the ribbon, live stats, snapshot, and readers keep keying on it unchanged),
but a Repeat/Back on a trial that recorded any behavior no longer clears it —
it **archives** the animal's real attempt and opens a fresh attempt in the live
row. The recorded file becomes **one row per (trial, attempt)**.

Concretely, in `phase3_log.py`:

- Three lifecycle columns are appended to the schema (`proto` 8 → 9):
  `attempt` (1-based attempt number per trial), `superseded` (`"1"` on a
  preserved attempt that a later edit replaced; blank on the current one), and
  `edit` (the operator action that created a >1 attempt — `repeat` / `back`;
  blank on the natural first attempt). These are *row lifecycle metadata*, not
  trial data, so they live on the rendered row, not on `_Row` (which stays the
  pure per-trial data record consumed by the live snapshot).
- `supersede_trial(index, edit)` replaces the destructive clear on the edit
  path: if the live row has recorded behavior it appends a copy to an
  append-only `_archive` (tagged with its attempt number and the edit that
  preceded it), bumps the trial's attempt counter, sets its `edit`, then clears
  the live row for the re-run. A row with nothing recorded yet is just cleared
  (nothing to preserve). It returns what it archived so the shell can reconcile
  tallies. The old `reset_trial` (plain destructive clear) stays for callers/
  tests that want it.
- `render_csv` emits every attempt — the current live rows plus the archived
  (superseded) ones — ordered by `(trial, attempt)`, so a reader sees a trial's
  attempts together with the current one last.

In `phase3_controller.py`, Repeat/Back route through `_supersede_trial`, which
un-counts **only the accuracy tallies** (`correct`/`incorrect`, and the probe
tally) for a preserved *scored* attempt. It deliberately does **not** un-count
the reward count or dispensed volume: the prime + top-ups were physically
delivered and the rat drank them, so they stay in the session's water total
(a welfare figure, not a scoring figure). On a closed-loop run it also appends a
caveat to the operator note that the auto-schedule may be affected (see below).

Both CSV readers exclude superseded rows so downstream sees the trials that
*count* (one current attempt per trial): `phase3_log_reader.parse_csv_text`
(keeping the live-vs-reconstructed `build_traces` symmetry the reference overlay
relies on, ADR-0117) and `analysis/bundle_reader` (the tidy `trials` frame). The
raw CSV keeps the full edit history for anyone who wants it.

## Alternatives considered

- **Keep clearing in place (status quo).** Rejected — it is exactly the bug: the
  animal's real behavior is destroyed with no record an edit happened.
- **Reshape the live table to one row per attempt everywhere** (rows no longer
  1:1 with the trial index). Rejected as too large/risky for one change: the
  index→row mapping is baked into the task FSM, the snapshot, the ribbon, live
  stats/traces, and both readers. Keeping the live table index-keyed and adding
  an append-only archive preserves all of that and keeps the diff human-
  reviewable (AGENTS.md).
- **Also roll back the closed-loop scheduler on a scored re-run.** Deferred (see
  FW-0044): the debias/alternation/intervention controllers already consumed the
  outcome via `record_outcome` and have no single-step undo; adding one (or
  replaying history after `reset_debias`) is a separate, trickier change that
  should land with its own tests. For now the operator note flags that the
  auto-schedule may be affected when a *scored* trial is re-run in a closed-loop
  session.

## Consequences

- Real behavior is never lost: a Repeat/Back is now a first-class, inspectable
  event in the data (`attempt`/`superseded`/`edit`), and the "injected" re-run is
  its own row — exactly what the bug asked for.
- The recorded CSV can now have more rows than planned trials, and repeated
  `trial` numbers. Readers that want counted trials must drop `superseded` rows
  (both in-repo readers now do; a future external analysis must too — the
  `superseded` column is self-describing).
- Accuracy stays honest across an edit (superseded scored attempts are
  un-counted) while the water total stays physically truthful (dispensed reward
  is not un-counted).
- Closed-loop schedule reproducibility across a manual scored re-run is not yet
  reconciled (FW-0044); the header already warns the closed-loop schedule is not
  seed-reproducible, and the operator now gets a per-edit note.

## Evidence / links

- Bug `#260810_TrialEditErasesHistory` (removed from `docs/notes/Bugs.md` in this
  change).
- Builds on / partially supersedes ADR-0058 (trial-control buttons); relates to
  ADR-0047 (the timeline/columns), ADR-0117 (reconstruct-from-CSV symmetry),
  ADR-0112 (offline analysis frames), ADR-0086/0107 (closed-loop scheduling).
- Follow-up FW-0044 (scheduler rollback on a manual scored re-run).
- Tests: `test_phase3_log.TestTrialEditArchive`,
  `test_phase3_log_reader.test_superseded_attempts_are_skipped`,
  `test_analysis_bundle_reader.test_drops_superseded_trial_edit_rows`; full host
  suite green (878 tests, 17 skipped).
