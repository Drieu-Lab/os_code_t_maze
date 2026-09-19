# ADR 0177: Phase 3 keeps the planned matrix in memory until a session exists (no host/data orphans)

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/ui/phase3_controller.py` (`_arm_session`, `_publish_status`, retired `DATA_DIR`), `host/tests/test_session_phase3_wiring.py`
- **Supersedes / Superseded by:** —

## Context

ADR-0060 has the operator ARM Phase 3 before pressing Start Session, so the cue
gate is already open (and the task already running) before a session folder
exists. On that first arm, `_arm_session` bound the trial CSV to a fallback
directory - `host/data/` (the module-level `DATA_DIR`) - and wrote the planned
matrix there immediately; ADR-0066's Start rebind then re-pointed the path into
the session's `task/` folder and rewrote the whole table.

That fallback write is an ORPHAN: a `phase3_<animal>_<stamp>.csv` under
`host/data/` that belongs to no bundle, is never referenced by a manifest, and
lingers after the run. The same happens on a re-arm between sessions (gate
toggled for the next rat) - another loose file outside any bundle. It also
muddied the operator's mental model: a file appeared on disk while "recording"
had not actually started, so an armed-but-not-started run looked recorded.

The planned matrix and every outcome already live in memory on the `Phase3Log`
object; the on-disk file is only a rendering of it, and the Start rebind renders
the WHOLE table anyway. So the fallback write buys nothing and costs an orphan.

## Decision

Phase 3 writes its trial + excursion CSVs ONLY into a live session's `task/`
folder. When armed WITHOUT a session it keeps the plan in memory.

- In `_arm_session`, bind and write the log/excursion paths only when
  `snap.session_out_dir` is set. Otherwise leave `_log_path` (and hence
  `_excursion_path`) `None`. `_write_log` / `_write_excursion_log` already no-op
  when the path is `None`, so every intermediate write while armed-without-session
  is a silent no-op and the plan/outcomes accumulate in memory.
- The Start rebind (`_rebind_session_output`, ADR-0066) is unchanged: on Start it
  binds the real `task/` path and does the FIRST write, which renders the whole
  in-memory table - so any trials run between arm and Start are preserved.
- The status line says **`NOT RECORDING - start a session`** while armed with the
  gate open and no bound file (`_log_path is None`), so an armed-but-not-started
  run is never mistaken for one being recorded.
- The `DATA_DIR` module constant (the retired fallback) is removed from
  `phase3_controller`; nothing else in the module used it, and the session
  controller keeps its own unrelated `DATA_DIR` (the `rig_logger` pointer).

## Alternatives considered

- **Keep writing to `host/data/` on a session-less arm** — the status quo; leaves
  an orphan per arm and shows a file on disk before recording started.
- **Write to a temp dir and move on Start** — the rebind already renders the whole
  table on the first in-session write, so an intermediate temp file is pure
  overhead for no durability gain (nothing consumes it before Start).
- **Keep `DATA_DIR` for standalone (session-less) Phase 3 runs** — no real
  workflow arms Phase 3 without the session controller except tests; the cluster-4
  decision (2026-09-13) is that Phase 3 writes nothing to disk until a session
  exists, so the standalone-file case is dropped deliberately.

## Consequences

- No `host/data/` orphans from arming; the only trial/excursion files are inside a
  bundle's `task/` folder, which is what the manifest and every offline reader
  expect.
- A session that is armed but never Started records nothing - correct (there was no
  session), and now visible via the status line rather than a silent stray file.
- Behaviour once a session Starts is byte-for-byte unchanged: the rebind binds and
  writes exactly as before, including any pre-Start trials.
- Sets up Increment 4a (full verbatim serial capture into the bundle), which
  likewise routes all session data through the session's folder.

## Evidence / links

- Roadmap Increment 4b (`sensor_polarity_hand_check` plan): "no orphan files".
- `docs/decisions/0060` (arm-before-Start), `0066` (Start rebind), `0049`
  (bundle layout).
- Tests: `host/tests/test_session_phase3_wiring.py`
  (`test_armed_without_session_writes_nothing_and_says_not_recording`,
  plus the arm-before-Start / rebind / second-session tests updated to the
  in-memory contract).
