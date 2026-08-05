# ADR 0071: Write a per-session behavioral summary on Session Stop

- **Status:** Accepted
- **Date:** 2026-07-31
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_summary.py` (new pure core) + `host/test_session_summary.py`; `host/session_controller.py` (`_stop_session` hook, `summary/` folder, manifest pointers); `host/behavior_view.py` (`behavior_summary()` + a session-scoped Phase 3 locomotor accumulator); `host/experiment_control.py` (provider wiring)
- **Supersedes / Superseded by:** Extended by ADR-0073 (summary layout -> schema v2: operator notes, folded task params, estimated water)

## Context

A session (ADR-0048/0049) already writes a manifest, the camera video, a filtered
serial file, and the per-trial Phase 3 CSV (ADR-0047), but **no ready-made
behavioral summary**. Everything needed is on disk, yet to answer "what did the
animal do this session?" a human has to reprocess the CSV. The live behavior
panel already computes exactly the wanted measures - the Phase 3 cued-task stats
(`phase3_stats.compute` over the published table, ADR-0052) and the Phase 2a
locomotor/compliance stats (`behavior_stats.BehaviorSnapshot`, ADR-0032) - but
they are display-only and vanish when the window closes.

Two of the wanted numbers - **stem-compliance and the non-stem "denied" count** -
cannot come from the Phase 3 snapshot at all: a row is only ever scored for a
genuine up-the-stem cued trial, so the many non-stem excursions the animals make
in Phase 3 are structurally absent from the table (and distance needs live
position, also absent). Those measures only exist in the live `task_events` +
position stream that `behavior_stats` folds. So a summary that includes them has
to read a live accumulator, not just the snapshot.

## Decision

On **Session Stop**, build and persist a per-session behavioral summary from the
two existing pure stats cores - recomputing nothing - and write it into the
session folder next to the manifest.

- **New pure core `session_summary.py`.** `build_summary(...)` folds a
  `Phase3Stats` (cued task), a `BehaviorSnapshot` (locomotor/compliance), and
  session metadata into a `SessionSummary` that renders to (a) machine-readable
  JSON for reanalysis and (b) a compact human-readable text block. It only
  selects/labels/serialises the two cores' fields, so they stay the single source
  of truth. Stdlib-only, headless-tested (`test_session_summary.py`).
- **Two files, in a new `summary/` subfolder.**
  `summary/session_summary_<session_id>.{json,txt}`, written atomically
  (temp + `os.replace`) by `session_controller._write_session_summary()` just
  before the final manifest write, which then records `summary_json` /
  `summary_text` stream pointers.
- **Locomotor source: a session-scoped accumulator in the behavior panel.**
  `BehaviorView` gains a second `BehaviorStats` (`_p3_stats`) driven automatically
  whenever a Phase 3 session is armed (tracking on + zones), independent of the
  panel's own Start button, and **reset on each Phase 3 arm** so its counts cover
  the session. `behavior_view.behavior_summary()` exposes its snapshot; the
  Session controller reads it through an injected provider (the same closure
  pattern as `phase3_state` / `session_timing`, ADR-0050/0060). Cued stats come
  from `phase3.published_state()` at Stop.
- **Best-effort.** The whole build/write is guarded so a summary failure can never
  break a clean Stop.

## Alternatives considered

- **Compute everything from the Phase 3 snapshot only.** Rejected: the snapshot
  has no non-stem excursions and no position, so stem-compliance, the non-stem
  count, and distance are impossible from it - exactly the measures the operator
  most wanted for Phase 3.
- **Embed the stats directly in the manifest JSON (bump its schema).** Rejected:
  the manifest is deliberately a lean *correlation index* (paths + clocks,
  ADR-0048); a stats blob bloats it and couples two concerns. A separate summary
  file, pointed to from the manifest, keeps each artifact single-purpose.
- **JSON only / text only.** Rejected: the request was both a broad human read
  ("what did the animal do") and reanalysis-ready data, so both forms are written.

## Consequences

- The end-of-session artifact set now includes a summary a human can read at a
  glance and a machine can reparse; the two stats cores remain the only place a
  measure is defined.
- Because both inputs are measured **"since Phase 3 arm"** (the published table
  since arm; the locomotor accumulator reset on the arm edge), the summary equals
  the session in the normal arm-then-Start-then-Stop workflow, but **not** if the
  operator ran trials before Start or spanned several Start/Stop cycles on one
  arm. The summary records this via its `scope` field / header line. Exact
  Start/Stop-window scoping (filtering CSV rows by `t0`/`t_end`) is deferred to
  FW-0028.
- The behavior panel now folds the shared `task_events` stream during a Phase 3
  session even without its local Start pressed; this is the session-scoped
  accumulator ADR-0052's phase-aware readout anticipated, and it makes the
  locomotor group available live for a future Phase 3 sub-panel too.

## Evidence / links

- New pure core + tests: `host/session_summary.py`, `host/test_session_summary.py`
  (`python -m unittest test_session_summary`).
- Reused cores: `host/phase3_stats.py` (ADR-0052), `host/behavior_stats.py`
  (ADR-0032). Session plumbing: `host/session_controller.py`,
  `host/session_log.py` (ADR-0048/0049), provider pattern (ADR-0050/0060).
- Deferred follow-up: FW-0028 (exact Start/Stop-window scoping).
