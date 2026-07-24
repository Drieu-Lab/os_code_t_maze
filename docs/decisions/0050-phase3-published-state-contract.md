# ADR 0050: Publish an immutable Phase 3 state snapshot for cross-tab readers

- **Status:** Accepted
- **Date:** 2026-07-23
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/phase3_log.py`, `host/phase3_controller.py`,
  `host/behavior_view.py`, `host/experiment_control.py`
- **Supersedes / Superseded by:** —

## Context

The host-owned Phase 3 task (ADR-0046) keeps its whole session table in memory in
the pure `phase3_log.Phase3Log` core (the planned Nx3 cue matrix plus the live
per-trial choices/outcomes/timestamps, ADR-0047). That table is mutated **only**
by the Phase 3 actuation thread (ADR-0043): the pipeline's fast callback enqueues
frames/licks and a single dedicated thread is the sole caller of the task cores
and the sole writer of `_log`. The Tk poll never touches that mutable state.

We now want to build read-only consumers of that live state on the Tk thread and
in other tabs: a phase-aware behavior stats panel in the Rig view bottom strip,
and an in-tab trial ribbon / full-sequence view. Those readers need a consistent
view of the table, but reading the live `_Row` list directly from the Tk thread
would race the actuation thread's mutations (a reader could observe a row
half-filled, or the list mutating mid-iteration).

## Decision

The controller publishes an **immutable snapshot** of the whole table, and
readers only ever see that snapshot:

- `phase3_log.py` gains two frozen dataclasses — `Phase3RowView` (one trial row,
  mirroring `_Row` plus a 1-based `trial` index) and `Phase3StateSnapshot` (the
  tuple of rows plus `current_index`, `total`, `armed`, `seed`, and the tone
  mapping) — and a `Phase3Log.snapshot(current_index, *, armed)` method that
  copies each row **by value** into `Phase3RowView`s. Because every field is an
  immutable `str`/`None`, the snapshot is fully decoupled from later mutation of
  the live rows.
- `Phase3ControlView` holds the latest snapshot in `self._state` and exposes it
  via `published_state()`. The actuation thread publishes a fresh snapshot by
  **atomic reference assignment** at exactly the points the table changes (on
  arm, on each `_write_log` change, on a recorded reward lick) and on the gate
  falling edge (`armed=False`). This mirrors the existing `_snapshot` Tk→thread
  hand-off (ADR-0043): a single immutable object swapped atomically, so no lock
  is needed — the reader just returns the current reference.
- Cross-tab exposure reuses the existing `phase3_ref` holder in
  `experiment_control.py`: a small `phase3_state()` resolver reads
  `phase3_ref["p3"].published_state()` and is passed to `BehaviorView` as a
  `phase3_state` provider callable. In-tab readers (the Phase 3 tab's own
  widgets) can read `self._state` directly on the Tk poll.

P0 only establishes this contract and the seam; it adds no statistics, no
widgets, and does not change the CSV format or any firmware command.

## Alternatives considered

- **Readers reach into the live `_rows` directly.** Rejected: `_rows` is mutated
  by the actuation thread, so a Tk-thread reader would race it (torn reads, list
  mutating during iteration). The single-writer invariant (ADR-0043) would be
  broken by ad-hoc cross-thread reads.
- **Re-parse the on-disk session CSV.** Rejected: adds file-I/O latency and
  couples every reader to the file format and write cadence; the data is already
  in memory. The CSV stays the durable record, not the live UI source.
- **Guard `_rows` with a lock shared across threads/tabs.** Rejected as
  unnecessary: an immutable snapshot swapped by atomic reference gives the same
  safety with less machinery and matches the pattern already used for the
  Tk→thread gate snapshot (ADR-0043).

## Consequences

- Multiple independent readers (behavior panel, trial ribbon, sequence view) can
  consume Phase 3 state without touching the actuation thread or the mutable
  table, enabling parallel follow-up work against one frozen interface.
- The snapshot is rebuilt (a few hundred `Phase3RowView`s) on each table change,
  not per frame — cheap, and only when something actually changed.
- Snapshots are point-in-time copies: a reader that caches one sees a stale view
  until it re-reads `published_state()`. Readers must poll (they already do, on
  the Tk cadence).
- If a future reader needs fields not yet in `Phase3StateSnapshot`, the frozen
  types must be extended (and their field-alignment tests updated) rather than
  reaching past the contract.

## Evidence / links

- `host/phase3_log.py` — `Phase3RowView`, `Phase3StateSnapshot`, `Phase3Log.snapshot`.
- `host/phase3_controller.py` — `self._state`, `published_state`, `_publish_state`,
  and the arm/change/lick/gate-close publish points.
- `host/experiment_control.py` — the `phase3_state()` resolver over `phase3_ref`,
  passed to `BehaviorView`.
- `host/test_phase3_log.py` — `TestSnapshot` (mirrors the plan, decoupled freeze,
  `current_index`/`armed`, row-view field alignment).
- Builds on ADR-0043 (Phase 3 actuation thread + atomic gate snapshot), ADR-0046
  (host-owned Phase 3), ADR-0047 (session data table).
