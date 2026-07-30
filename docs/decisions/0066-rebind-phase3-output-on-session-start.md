# ADR 0066: Re-point the Phase 3 data file at the session on Start, and refuse foreign paths in the manifest

- **Status:** Accepted
- **Date:** 2026-07-29
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/phase3_controller.py`, `host/session_controller.py`,
  `host/session_log.py`, `host/test_session_log.py`,
  `host/test_session_phase3_wiring.py`
- **Supersedes / Superseded by:** —

## Context

Two earlier decisions collided and quietly broke the per-session data tree.

[ADR-0049](0049-per-session-recording-folder-tree.md) says everything a session
produces lives under `<root>/<session_id>/`, with the Phase 3 task CSV in that
folder's `task/` subfolder and the manifest recording where each stream landed.
[ADR-0060](0060-session-button-owns-phase3-session.md) then made the operator arm
Phase 3 *before* pressing Start Session — Start refuses to run otherwise, so a
recording can never begin without the task running.

But Phase 3 binds its data-file path (`_log_path`) in `_arm_session`, and
`_arm_session` runs only on the cue gate's **rising edge** or an operator Restart.
Under ADR-0060 the gate is already open by the time the session mints its folder,
so no rising edge fires and nothing re-points the path. `set_session_context`
stored the new `session_id` and `task/` folder but left `_log_path` alone, and
`SessionControlView._resolve_paths` then recorded whatever `current_log_path()`
returned, with no check that it belonged to this session.

Two things went wrong as a result, both observed on the rig:

- **The first session after the app starts** got a manifest pointing into
  `host/data/` (the manual-arm fallback), with its own `task/` empty.
- **Every later session** got a manifest pointing into the *previous* session's
  `task/` folder. Nothing cleared the session context on Stop, so when the
  operator toggled tracking between animals the gate closed and reopened, and that
  rising edge minted a data file inside the already-closed session's folder —
  which the next session's manifest then recorded.

Session `600_20260727_134317` is the recorded instance: its manifest's
`phase3_task` names
`000_20260727_132752\task\phase3_000_20260727_132752.csv`, its own `task/` folder
is empty, and that `000` file's last write is 13:45:27 — two minutes after session
600 started (13:43:17) and while it was still recording (stopped 13:46:32). No
data was lost, but the folder tree is wrong and the manifest points at another
animal's session.

## Decision

Two changes, one per side of the seam.

**1. Phase 3 re-points an already-open data file on Start.**
`set_session_context` now publishes the new context to the actuation thread and
enqueues a `("cmd", "rebind")` item on the existing ADR-0058 command channel. The
actuation thread — the sole owner of the log and its path — runs
`_rebind_session_output`, which sets `_log_path` to
`<out_dir>/phase3_<session_id>.csv`, updates the header's correlation id, resets
the restart counter, and writes immediately.

Nothing is moved and no task state is touched: the trial number, sequence,
tallies and ribbon all survive, and because `_write_log` renders the *whole* table
on every write, the session's file is complete from its first write — including
any trials the rat ran between arming and Start. The publish-then-enqueue order
matters: the Tk poll is otherwise up to `POLL_INTERVAL_MS` (100 ms) behind, and
the thread would rebind using the previous session's snapshot.

**2. The manifest refuses a path from outside the session.**
A new pure predicate `session_log.path_within_session(path, session_dir)`
(case- and separator-insensitive `os.path` string logic, no filesystem access)
gates `_resolve_paths`: a Phase 3 path outside this session's tree is dropped
rather than recorded. This also makes the rebind's asynchrony harmless — if the
500 ms resolve poll wins the race, the manifest stays silent for one tick instead
of recording a stale path.

**Plus: Stop releases the context.** `_stop_session` calls
`set_session_context()` with no arguments after the final manifest write, so a
re-arm between animals falls back to `host/data/` instead of writing into a
closed session's folder. Phase 3 still stays armed and running, as ADR-0060
requires.

## Alternatives considered

- **Re-arm Phase 3 on Start** (run a full `_arm_session`) — gives the cleanest
  semantics, a session whose file always starts at trial 1 aligned with video
  `t0`. Rejected because it discards any trials run between arming and Start and
  resets the ribbon/tallies mid-run: the rat would silently repeat trials it had
  already done, which is a behavioural change to fix a bookkeeping bug.
- **Rebind *and* move the pre-Start file** into the session folder with
  `os.replace`. Rejected as unnecessary: the rebound file already contains the
  whole table, so the move would only delete a redundant copy, at the cost of
  filesystem work on the actuation thread (and a cross-drive move is not atomic).
  The pre-Start file stays in `host/data/` as a harmless partial snapshot.
- **Derive the path lazily on every write** from the current snapshot, removing
  the edge-triggered binding entirely. Structurally the strongest option, but it
  makes a file's location implicit, changes how Restart's `_r<n>` tagging works,
  and is a much larger change than the bug warrants.
- **Guard the manifest only** (change 2 alone). Rejected as a half fix: the
  manifest would stop lying, but the session's `task/` folder would still be
  empty and the data would still land in the wrong tree. Kept as the backstop.

## Consequences

- Each session's task data now lives in, and is referenced from, its own folder,
  whatever order the operator armed and started in.
- A pre-Start `host/data/phase3_<animal>_<stamp>.csv` is left behind whenever the
  gate opened before Start (the common case). It is a complete, readable partial
  snapshot of the same table, not a fragment, but it *is* clutter — worth a
  periodic sweep of `host/data/`.
- Within a session the first run keeps the plain `phase3_<session_id>.csv` name
  and a Restart appends `_r1`, exactly as
  [ADR-0058](0058-rig-view-trial-control-buttons.md) intends. Restarts made
  before Start no longer show up in the name, since that run's file lives
  elsewhere.
- Still unfixed, and pre-existing: a gate close/reopen *during* a session re-runs
  `_arm_session` with the session context, which regenerates the plan at trial 1
  and overwrites the session's file. Deferred to `docs/future-work.md` FW-0026.
- `test_session_phase3_wiring.py` is the first suite here to drive the Tk shells
  rather than a pure core, because the bug lives in the wiring between two
  shells. It skips itself when no display is available.

## Evidence / links

- Symptom on the rig, 2026-07-27:
  `<recordings>/phase3_recordings/600_20260727_134317/session_600_20260727_134317.json`
  records `"phase3_task": "...\\000_20260727_132752\\task\\phase3_000_20260727_132752.csv"`,
  and `600_20260727_134317/task/` is empty. File mtimes place the `000` file's
  last write (13:45:27) inside session 600's span (13:43:17–13:46:32).
- Mechanism confirmed by a headless instrumented trace of both real controllers
  (stub pipeline/camera, synthetic `RatState` frames) before any fix: every frame
  after Start reported `rising_edge: false`, `set_session_context` left
  `log_path` outside the new `out_dir`, and `_resolve_paths` recorded it with
  `belongs_to_this_session: false`. After the fix the same trace reports
  `belongs_to_this_session: true` throughout, and the between-sessions re-arm
  falls back to `host/data/`.
- That trace is preserved as `host/test_session_phase3_wiring.py`. Its four cases
  all fail with `_rebind_session_output` and `path_within_session` neutered, and
  pass with them in place.
- Related: [ADR-0049](0049-per-session-recording-folder-tree.md) (the folder
  tree), [ADR-0060](0060-session-button-owns-phase3-session.md) (arm before
  Start), [ADR-0047](0047-phase3-return-schedules-and-session-log.md) (the data
  file), [ADR-0058](0058-rig-view-trial-control-buttons.md) (the command
  channel), [ADR-0043](0043-rules-actuation-thread.md) (why the rebind runs on
  the actuation thread).
- Captured in `docs/notes/Bugs.md` as `#260729_SessionTaskFolder`.
