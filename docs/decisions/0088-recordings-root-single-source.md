# ADR 0088: Single-source recordings root; scan input decoupled from registry output

- **Status:** Accepted
- **Date:** 2026-08-09
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/paths.py` (`camera_output_dir`, `phase3_recordings_root`); `host/tmaze/ui/camera_view.py` (`DEFAULT_OUTPUT_DIR`); `host/tmaze/ui/session_controller.py` (`DEFAULT_RECORDINGS_ROOT`); `host/tmaze/tools/build_session_registry.py` (`DEFAULT_SCAN_ROOT`, `REGISTRY_OUTPUT_DIR`, `--root`/`--out-dir`); `host/tmaze/ui/records_manager.py` (recordings-root pref + picker)
- **Supersedes / Superseded by:** — (builds on ADR-0049, 0081, 0083)

## Context

The Records Manager's "Generate files" found **zero** sessions even though ~40
Phase 3 sessions existed on disk. Root cause: two disagreeing notions of "the
recordings root".

- The rig writes every session bundle under `camera_view.DEFAULT_OUTPUT_DIR`
  (`C:\Users\Celine\Documents\t-maze_recordings`) + `\phase3_recordings`
  (`experiment_control` composes it; ADR-0049).
- But `build_session_registry.py` defaulted its scan root to
  `host/data/phase3_recordings` - a *different*, empty folder - and the GUI called
  `build_session_registry.main([])` with no `--root`, so it scanned nothing.

A second, easy-to-miss constraint: the registry *output* (`session_registry.csv`)
is read from a fixed place by two consumers - the Records Manager dashboard
(`records_manager.registry_path`) and the sibling resources builder
`build_session_training_log.py` (`default_registry_path()` = code-repo
`host/data/phase3_recordings/session_registry.csv`). The old tool coupled input
and output (`out_dir = args.out_dir or args.root`), so naively pointing `--root`
at the rig folder would *also* move the CSV away from where those consumers look.

## Decision

Make the recordings root a single source of truth, and decouple *where bundles
are scanned* (input) from *where the registry CSV is written* (output).

- **One recordings root, in `tmaze.paths`.** Add `camera_output_dir()` (the rig
  recordings parent; `TMAZE_RECORDINGS_DIR` env override, else the machine
  default) and `phase3_recordings_root()` (`<camera_output_dir>/phase3_recordings`).
  `camera_view.DEFAULT_OUTPUT_DIR` and `session_controller.DEFAULT_RECORDINGS_ROOT`
  now source from these, so the rig and the tools can't drift. `paths.py` stays
  pure stdlib, so cores/tools import it freely.
- **Scan input vs. registry output are separate in the tool.**
  `build_session_registry.py` gains `DEFAULT_SCAN_ROOT` (=`phase3_recordings_root()`,
  the `--root` default) and `REGISTRY_OUTPUT_DIR` (=`host/data/phase3_recordings`,
  git-ignored, the `--out-dir` default). `--out-dir` no longer falls back to
  `--root`. So the tool scans the rig's bundles but writes the CSV where the
  consumers already read it.
- **Operator-pickable, persisted recordings folder.** The Records Manager shows
  the current recordings folder with a "Browse..." button and passes it as
  `--root`. The choice persists in `host/records_manager.json` (a machine path, so
  git-ignored). Scan-root precedence: **GUI pref -> `TMAZE_RECORDINGS_DIR` env ->
  machine default**.

## Alternatives considered

- **Just change `build_session_registry`'s default root to the rig folder.**
  Rejected alone: because output was coupled to root, it would relocate
  `session_registry.csv` away from the GUI and the resources builder, breaking
  both. Decoupling input/output is the actual fix.
- **Import `camera_view.DEFAULT_OUTPUT_DIR` into the tool.** Rejected: `camera_view`
  is a heavy UI module (Tk/OpenCV/pypylon); a headless CLI tool must not pull it
  in. Putting the value in pure `tmaze.paths` lets both sides share it cheaply.
- **Hardcode the path a third time in the GUI.** Rejected: that is exactly the
  duplicated-source-of-truth bug we're fixing.
- **Make the recordings root fully config-file-driven for the rig too.** Deferred:
  the env override + GUI pref cover the immediate need; a rig-wide config is a
  larger change than this bug warrants.

## Consequences

- "Generate files" (and a bare `python -m tmaze.tools.build_session_registry`)
  now scans the rig's recordings folder and finds the real sessions, while the CSV
  still lands in `host/data/phase3_recordings` for the dashboard and the resources
  workbook builder.
- The recordings location is no longer machine-locked: set `TMAZE_RECORDINGS_DIR`
  (handy for tests/other machines) or pick a folder in the GUI.
- The legacy flat `host/{camera_view,build_session_registry,session_controller,
  experiment_control,session_registry}.py` copies (pre-package, unused by
  `python -m tmaze...`) still carry the old hardcoded constants; they're out of
  scope here and left for a separate cleanup.

## Evidence / links

- Fix: `host/tmaze/paths.py`, `host/tmaze/tools/build_session_registry.py`,
  `host/tmaze/ui/records_manager.py`, `host/tmaze/ui/camera_view.py`,
  `host/tmaze/ui/session_controller.py`.
- Test: `host/tests/test_build_session_registry.py` (scan finds a bundle; CSV is
  written to `--out-dir`, independent of `--root`).
- Builds on ADR-0049 (per-session recording tree), ADR-0081 (session registry),
  ADR-0083 (tmaze package + landmark path resolution).
