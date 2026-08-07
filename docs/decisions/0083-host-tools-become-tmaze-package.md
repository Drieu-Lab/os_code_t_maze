# ADR 0083: Restructure the flat host/ tools into an installable `tmaze` package

- **Status:** Accepted
- **Date:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** all of `host/` (staged migration into `host/tmaze/{core,io,vision,ui,tools}`), `pyproject.toml` (new packaging), the launch commands in `host/start_rig.bat` / `host/run_tests.bat` / `PROGRAMS.md`, and the "run from `host/` / not a package" guidance in `CONTRIBUTING.md`, `host/README.md`, `AGENTS.md`, and `.cursor/rules/conventions.mdc`. Reverses those guidance docs; supersedes no prior ADR.
- **Supersedes / Superseded by:** —

## Context

The PC-side tools began as a handful of sibling scripts in `host/` that import
each other flat (`import protocol`, `from rig_io import RigClient`) and find
their data files relative to the working directory. The whole scheme rests on
one rule, written into `CONTRIBUTING.md`, `AGENTS.md`, and the
`conventions.mdc` Cursor rule: **"they are not a package — always `cd host`
first."** Flat imports resolve only because the current directory is first on
Python's import search path.

That worked at ~20 files. It no longer scales: `host/` now holds **98 `.py`
files** (62 source + 36 tests) plus 8 JSON configs — ~110 files in one
directory — and it grew by ~27 `.py` files in the two weeks this was under
discussion. A single flat folder of that size defeats navigation, hides the
architecture, and (in a lab whose stated first enemy is *comprehension debt*)
makes it hard for a human to hold the system in their head. The irony is that
the code already *has* a clear architecture — the "pure core + thin shell"
split (CONTRIBUTING.md #1) — it just isn't reflected in the file layout.

The revised CONTRIBUTING.md #6 sharpened the relevant principle: organizing
code into clear *homes* (a naming act) is encouraged at any caller count; what
must be deferred is *speculative generality* (indirection built for callers that
don't exist). Grouping files into folders is the former, not the latter.

## Decision

Turn `host/` into an installable package **`tmaze`** whose subpackages mirror
the pure-core/thin-shell architecture, and reverse the "not a package" rule.

- **Layout:** `host/tmaze/{core, io, vision, ui, tools}`. `core/` (pure,
  stdlib-only, headless-tested) sub-groups the large clusters: `reward/`,
  `phase3/`, `session/`, `records/`.
- **Imports** become package-qualified (`from tmaze.core import protocol`);
  **launch** becomes `python -m tmaze.<layer>.<module>`.
- **Editable install** (`pip install -e .`, driven by new packaging metadata in
  `pyproject.toml`) makes `import tmaze` resolve from any directory, replacing
  the "always `cd host`" rule. Run once per machine (incl. each rig PC on pull).
- **Minimal machinery (per #6):** bare `__init__.py` markers (a one-line
  signpost only, no re-export hubs); `python -m` over a console-script/factory
  layer; one boring path helper (`tmaze/paths.py`) instead of a settings
  framework; flat, direct imports inside the package.
- **Path resolution by landmark, not by counting `..`.** Data bundled with the
  package is addressed via `importlib.resources`; same-repo files (the firmware
  header) and the sibling `os_code_t_maze_resources` repo are found by searching
  upward for a landmark (`.git` / `pyproject.toml`) — reusing and de-fragilizing
  the existing `animals.resources_root()` resolver (ADR-0075) rather than
  assuming a fixed folder depth.
- **Staged, not big-bang.** The migration lands as a sequence of small,
  independently verifiable commits (packaging; `core/` in two parts; `io/`;
  `vision/`; `ui/` + entry points; docs), each leaving the app runnable and
  verified (the headless test suite for `core/`, a rig smoke test for the
  hardware/UI layers) before the next. This honors the AGENTS.md
  "cap change size to human reviewability" rule; a single mechanical rename of
  every file would violate it and is mostly unverifiable outside the rig.

**Placement rule (the anti-drift contract):** a new module goes to a *shell*
layer (`ui`/`vision`/`io`) if it imports Tkinter/OpenCV/serial, else to `core/`
+ the right cluster if it is pure and headless-testable, else `tools/` if it is
a standalone runnable; paths are resolved via `tmaze.paths`, never hand-rolled.
An **architecture boundary test** in `tests/` enforces the core purity and the
no-upward-imports invariant; the placement rule is added to the auto-loaded
`conventions.mdc`/`AGENTS.md` so every future agent sees it.

## Alternatives considered

- **Keep the flat layout, add a docs-only module index.** Lightest and
  zero-risk, but it only documents the sprawl; the 62 source files stay in one
  folder. Rejected as insufficient given the growth rate — though the index idea
  survives as the package's `__init__` signposts.
- **Group the source into folders without a package (sys.path shim).** Would let
  flat imports keep working from subfolders via a path hack. Rejected: a
  `sys.path` manipulation is exactly the clever indirection #6 warns against, and
  it is more fragile and surprising than a standard editable install.
- **One big-bang commit that moves everything at once.** Producible in a single
  session and could pass the headless tests, but ~two-thirds of the surface (the
  `ui`/`io` layers) can only be verified on the rig, so it would be a large,
  mostly-unverified diff that no one can review in a sitting — against the
  reviewability rule. Rejected in favor of staging.
- **Console-script entry points (`[project.scripts]`).** Rejected for now as
  speculative machinery; `python -m tmaze...` needs no extra layer. Add scripts
  only if a concrete need appears.
- **Recompute `../..` hops at each new depth / pin heavy deps in `[project]`.**
  Rejected: hop-counting is the fragility this migration is trying to remove
  (use landmark search), and pinning optional GUI deps (pypylon/opencv/av) would
  fight the graceful-degradation guards; deps stay managed by the conda env.

## Consequences

- **Navigation + architecture become legible:** folders mirror the pure/shell
  split; `core/` is the obvious "read this to understand the system" entry point.
  The layout is enforced (boundary test) and self-documenting (signposts + this
  ADR), which resists re-flattening by future authors.
- **One-time cost, repo-wide:** every import and launch command changes, and
  each machine (incl. rig PCs) must run `pip install -e .` once after pulling.
  During the staged migration the repo is in a valid *mixed state* — moved
  layers import via `tmaze...`, not-yet-moved files stay flat and run from
  `host/` — so nothing breaks between stages.
- **Path handling gets more robust** (landmark search + a single `tmaze.paths`
  home), retiring scattered depth-dependent `__file__` arithmetic.
- **We would revisit** if the editable-install step proves too heavy for the lab
  workflow, or if a non-editable/wheel install is ever needed (the `.git`
  landmark wouldn't exist there — bundled data already uses `importlib.resources`
  to avoid that).

## Evidence / links

- Stage 0 (this commit): `host/tmaze/` skeleton + `pyproject.toml` packaging.
  Verified: `pip install -e .` succeeds; `import tmaze` resolves from the repo
  root (not `host/`); the flat modules stay isolated; the test suite is
  unchanged at **522 passed, 17 skipped** (`python -m unittest discover` from
  `host/`, system Python 3.13 without OpenCV, so the tracker tests skip).
- Stage 1a (`Refs ADR-0083`): moved the pure-core primitives + the `reward/`
  cluster into `host/tmaze/core/` (9 top-level: `protocol`, `maze_state`,
  `task_events`, `rules_engine`, `cue_gate`, `line_router`, `serial_liveness`,
  `behavior_stats`, `rig_config`; `reward/`: `reward_volume`, `reward_dispenser`,
  `reward_dispatcher` - the dispatcher is core-safe because it takes a *send
  callback*, not a broker). Added `tmaze/paths.py` (landmark `repo_root()` +
  `package_data()`); re-anchored `rig_config`'s firmware-header path through it.
  Added the `test_architecture.py` boundary test (AST-parses `tmaze/core`,
  fails on any Tk/OpenCV/numpy/serial or `tmaze.io|vision|ui` import). Imports
  were rewritten to `from tmaze.core... import` across 43 files by a one-off,
  reviewed-and-deleted script. Test files stay flat in `host/` for now (they get
  the import rewrite) and relocate to `host/tests/` in one clean step at the end,
  so `run_tests.bat` discovery keeps working throughout. Verified: `compileall`
  clean; every `tmaze.core` submodule imports; `rig_config` header resolves and
  exists; **523 passed, 17 skipped** (522 baseline + the new boundary test).
- Stage 1b (`Refs ADR-0083`): moved the remaining pure cores into three
  clusters under `host/tmaze/core/`. `phase3/`: `trial_sequence`,
  `phase3_config`, `phase3_task`, `phase3_log`, `phase3_stats`, `phase3_ribbon`.
  `session/`: `session_serial`, `session_summary`, `session_config`,
  `session_log`, `session_meta`, `session_notes`, `session_registry`,
  `session_timer`. `records/`: `animals`, `daily_log`, `animal_state`,
  `note_tags`. The two `*_controller` shells (Tk) and the `build_session_registry`
  tool stayed flat (they get only the import rewrite). Re-anchored
  `animals.resources_root()` - the sibling-repo resolver (ADR-0075) - through
  `tmaze.paths.repo_root()`, retiring its `..`-hop. Imports rewritten across 32
  files by the same one-off, deleted script. Verified: no stale flat imports;
  `compileall` clean; all 34 `tmaze.core` submodules import; `resources_root()`
  resolves to the real sibling repo (`animals.json` present); **523 passed, 17
  skipped** (unchanged). After 1b, everything left flat in `host/` is a shell
  (`ui`/`io`/`vision`) or a `tool` - the pure core is fully packaged.
- Stage 2 (`Refs ADR-0083`): moved the io layer into `host/tmaze/io/`:
  `rig_broker`, `rig_io`, `rig_logger`, `rig_process`, `vision_actuator`. The
  broker and logger became package entry points - launch changed from
  `python rig_broker.py` to `python -m tmaze.io.rig_broker` (and likewise the
  logger) in `start_rig.bat`; the still-flat GUIs keep running as `.py` from
  `host/`. Re-anchored the logger's default output to `host/data` via
  `tmaze.paths.repo_root()`. Updated the "Quit rig" fallback matcher
  (`rig_process.RIG_SCRIPTS`) to match the broker/logger *module* command lines;
  the primary `rigsid=`-based match is unchanged. Added a `tmaze`-import
  pre-flight to `start_rig.bat` and updated the affected `PROGRAMS.md` rows.
  This was the first stage needing the rig: I installed the package into the
  `drieu_t_maze` conda env (`pip install -e .`, the one-time per-machine step)
  and ran a real hardware smoke test - broker opened COM3, served TCP 5555, read
  the Due's session clock; logger connected and wrote a wall-clock-stamped
  `STATUS` line to `host/data/`. Verified: no stale flat imports; `compileall`
  clean; all 5 io modules import and both `-m` entry points respond to `--help`;
  `experiment_control` imports cleanly; **523 passed, 0 skipped** in the rig env
  (unchanged from the pre-stage rig-env baseline).
- Stage 3 (`Refs ADR-0083`): moved the vision layer into `host/tmaze/vision/`:
  `tracker`, `track_worker`, `maze_pipeline`, `calibration`. No path anchoring
  needed - `tracker`/`calibration` take their model/ROI/calibration paths from
  callers (the `host/ratmodels/` + `host/roi_polygon.json` defaults live in the
  still-flat `tracker_view`/`blob_tracker_prototype`, so they move with the ui/
  tools stages). Kept the stage single-purpose (vision only): the standalone
  tools were left flat because they still depend on not-yet-moved layers
  (`blob_tracker_prototype` -> `camera_view` (ui); `maze_state_validate` ->
  `test_maze_state` (flat test)); they get only the import rewrite and move once
  those land. `gui_deps`/`zone_overlay` stay flat too (ui-shared) - none of the
  4 vision cores import them. Imports rewritten across 11 files. Verified in the
  rig env: no stale flat imports; `compileall` clean; all 4 vision modules import
  with real cv2 4.13.0; **523 passed, 0 skipped** (the tracker/pipeline tests run
  unskipped with cv2 present). Camera smoke: grabbed 6 live Basler frames
  (1280x1024 BGR) and ran them through the moved `tracker.process()` reference-
  diff path - returned a valid `TrackResult` (cv2 mask computed).
- Stage 4a (`Refs ADR-0083`): **config-path anchoring** - split out of Stage 4 as
  its own purpose (per the reviewability rule). Added `tmaze.paths.host_dir()`
  (= `repo_root()/"host"`, landmark-based) and rebased every default that pointed
  at a `host/*.json` / `host/data` / the sibling-repo calibration onto it, so the
  paths survive a module living inside the package. This **repaired a latent bug
  introduced in Stages 1a/1b**: `maze_state.ZONES_PATH` (read directly by
  `experiment_control`) and `session_config`'s `host_dir` default were computed
  `__file__`-relative, so after those modules moved they resolved *inside* the
  package (`host/tmaze/core/zones.json`) instead of `host/zones.json` - the GUI
  would have silently failed to load the zone map. String constants (built at
  import), so no crash and the headless tests - which pass explicit paths - didn't
  catch it; found by auditing path defaults before the ui move. Touched (moved
  cores) `maze_state`, `session_config`; (still-flat, anchored in place before
  their Stage 4b move) `behavior_view`, `rules_view`, `tracker_view` (incl.
  `MODEL_DIR=ratmodels`), `maze_state_view`, `phase3_controller`,
  `session_controller`, `camera_view` (calibration now via `resources_root()`,
  not `../..`), `blob_tracker_prototype`, `build_session_registry`. Verified in
  the rig env: every anchored constant resolves to the real `host/<file>` (all 8
  configs + the sibling calibration present); all edited flat modules import with
  real cv2/pypylon; **523 passed, 0 skipped** (unchanged).
- Stage 4b (`Refs ADR-0083`): moved the **ui shells + standalone tools** - the
  last flat source - into the package. 20 modules to `host/tmaze/ui/`
  (`experiment_control`, `camera_view`, `rig_view`, `tracker_view`, `rules_view`,
  `maze_state_view`, `phase3_controller`, `session_controller`, `task_view`,
  `behavior_view`, `trial_ribbon_view`, `trial_control_bar`, `preview`,
  `tooltips`, `tooltip_text`, `still_frame_editor`, `zone_overlay`, `gui_deps`,
  `lick_tuner`, `maze_gui`) and 3 to `host/tmaze/tools/` (`blob_tracker_prototype`,
  `build_session_registry`, `maze_state_validate`). `gui_deps`/`zone_overlay` are
  ui-only post-migration, so they went to `ui/`. **Launch** for the three GUI
  entry points changed to `python -m tmaze.ui.{experiment_control,lick_tuner,
  maze_gui}` and the tools to `python -m tmaze.tools.<name>` (`start_rig.bat`,
  `PROGRAMS.md`); the "Quit rig" fallback matcher (`rig_process.RIG_SCRIPTS`) now
  matches those module command lines. Added a `tmaze`-import pre-flight to
  `run_tests.bat`. Imports rewritten across 19 files (18 moved modules that
  cross-import + the one Tk wiring test) by a one-off, reviewed-and-deleted
  script; the tools and `maze_gui`/`session_controller`/`tooltip_text` needed no
  rewrite (they import only `core`/`io`). Verified in the rig env: no stale flat
  imports; `compileall` clean over the whole tree; importing
  `tmaze.ui.experiment_control` (which transitively pulls in every ui view) and
  the 3 tool modules all succeed with real cv2/pypylon; **523 passed, 0 skipped**.
  A live GUI launch smoke (operator ran `start_rig.bat`) confirmed the windows
  open and "Quit rig" closes them. After 4b, `host/` root holds only the `test_*`
  suite, the config JSON, `data/`, `ratmodels/`, the two `.bat` launchers, and
  `tools/test_sensors_licks.py` (a pre-existing standalone bring-up script).
- Deferred to Stage 5 (docs pass): reconcile the remaining stale
  `host/<name>.py` library paths in `PROGRAMS.md` for the core/vision modules
  moved in Stages 1a/1b/3; relocate the `test_*` suite to `host/tests/` and, with
  it, resolve `tmaze.tools.maze_state_validate`'s `from test_maze_state import
  make_zone_map` (a tool importing a test fixture - move the fixture or inline it).
- Stage 5a (`Refs ADR-0083`): relocated the whole `test_*` suite (37 modules)
  into a `host/tests/` package (added `tests/__init__.py`), leaving `host/` root
  free of source. Discovery is now `python -m unittest discover -s tests -t .`
  (updated `run_tests.bat`). Resolved the one bad dependency direction the move
  exposed: `tmaze.tools.maze_state_validate` had imported the `make_zone_map`
  fixture *from* `test_maze_state` (a tool depending on a test). Moved the
  fixture (and its tiny `rect` helper) into the tool - its natural home, since
  `--selftest` needs a synthetic map - and pointed `test_maze_state` /
  `test_maze_pipeline` at it, so the direction is now test -> tool -> core. Tests
  import the code under test via the installed package path, so their new folder
  location doesn't affect resolution. Verified: `make_zone_map` tests + the tool
  `--selftest` pass from the flat layout before the move; **523 passed, 0 skipped**
  from the new `tests/` layout after. (Note: the full sweep initially *appeared*
  to hang on the one Tk GUI wiring test, `test_session_phase3_wiring`. The
  operator identified the real cause: with a real Tk display and no daily-table
  row for rat 600, `_start_session()` popped the operator-facing pre-session
  weight modal (`simpledialog.askfloat`) once per test method (~5 dialogs) and
  blocked waiting for input. Fixed in Stage 5c by stubbing that one prompt in the
  headless test - see below.)
- Stage 5b (`Refs ADR-0083`): the docs pass that finishes the migration. Reversed
  the "not a package / `cd host` / sibling imports" guidance everywhere it lived
  and replaced it with the package model (`pip install -e .` once per machine;
  launch `python -m tmaze.<layer>.<name>`; the placement rule; resolve paths via
  `tmaze.paths`): `.cursor/rules/conventions.mdc`, `CONTRIBUTING.md`,
  `host/README.md`, `AGENTS.md`, `pyproject.toml`, and `docs/maze_state_validation.md`.
  Reconciled `PROGRAMS.md`: the setup/intro sections, the 22 per-test commands
  (`python -m unittest tests.test_<name>`), the `run_tests.bat` row
  (`discover -s tests -t .`), and the 17 stale `host/<name>.py` library-table paths
  for the core/vision modules moved in Stages 1a/1b/3 (now
  `host/tmaze/<layer>/...`). The immutable earlier ADRs (0023/0024/0025/0057/0058)
  keep their historical "run from host/" wording - this ADR supersedes that
  guidance rather than rewriting them. **Migration complete:** `host/` root now
  holds only the config JSON, `data/`, `ratmodels/`, the two `.bat` launchers,
  `tools/`, and the `tmaze/` package + `tests/` suite.
- Stage 5c (`Refs ADR-0083`): fixed the suite stall the operator diagnosed after
  5a (see the 5a note). `SessionControlView._ask_pre_session_weight` legitimately
  pops a modal `simpledialog.askfloat` in real use when the daily husbandry table
  has no row for the animal; the headless `test_session_phase3_wiring` drives a
  real Tk root, so `_start_session()` hit that modal and blocked. Added an
  injectable `ask_weight` provider to the `SessionControlView` constructor (takes
  the animal id, returns grams or `None`): it defaults to the operator-facing
  modal, and `_ask_pre_session_weight` calls the provider when one is given. The
  test injects `ask_weight=lambda _a: None` (the "operator skipped" path -> a
  minimal animal-state record it doesn't exercise), so it - and any future headless
  driver of Start - can never present a dialog. Verified: the wiring test runs
  unattended in ~1.8 s across repeated runs; the full suite stays **523 passed**.
  (This supersedes the earlier, incorrect "window focus grab / flaky" reading of
  the stall.) No production behavior change - the app constructs the panel without
  `ask_weight`, so operators still get the modal.
- Related conventions: CONTRIBUTING.md #1 (pure core + thin shell), #6
  (organize into homes; defer speculative generality), #7 (one canonical way);
  AGENTS.md "cap change size to human reviewability"; ADR-0075 (the sibling-path
  resolver being reused for landmark-based resolution).
