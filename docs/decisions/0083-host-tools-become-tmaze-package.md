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
- Subsequent stages carry `Refs ADR-0083` and record their own smoke tests.
- Related conventions: CONTRIBUTING.md #1 (pure core + thin shell), #6
  (organize into homes; defer speculative generality), #7 (one canonical way);
  AGENTS.md "cap change size to human reviewability"; ADR-0075 (the sibling-path
  resolver being reused for landmark-based resolution).
