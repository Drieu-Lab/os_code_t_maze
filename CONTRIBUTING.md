# Contributing — writing code that stays understandable

This guide exists to fight **comprehension debt**: the compounding gap between
the volume of code in this repo (much of it written fast, often with an AI
agent) and what the humans in the lab actually understand about it. On a
behavioral rig, code that no one understands is code no one can trust with an
animal's session — so the goal of every convention below is to keep human
understanding attached to the code as it grows.

This is the "how we write code here" companion to [`AGENTS.md`](AGENTS.md)
(which is the maintenance contract for AI agents) and
[`PROGRAMS.md`](PROGRAMS.md) (the runnable-program registry). Read those too.

## Who this is for

The lab has coders of very different experience levels. You do **not** need to
be a strong programmer to work here safely — you need to follow the learning
path below and keep your changes small and explained. If you are reviewing or
merging AI-generated code, the rules in `AGENTS.md`
("AI-assisted change rules") apply to you.

## The nine guidelines

### 1. Pure core + thin shell
Put decision logic in a **pure core**: stdlib-only, no Tkinter / OpenCV /
serial, and headless-testable. Put I/O and UI in a **thin shell** that calls the
core. This split is the backbone of the whole codebase, and the folder layout
now mirrors it: the pure cores live under
[`host/tmaze/core/`](host/tmaze/core) ([`protocol`](host/tmaze/core/protocol.py),
[`maze_state`](host/tmaze/core/maze_state.py),
[`task_events`](host/tmaze/core/task_events.py),
[`rules_engine`](host/tmaze/core/rules_engine.py)) and are small and readable;
the shells live in [`host/tmaze/ui/`](host/tmaze/ui)
([`experiment_control`](host/tmaze/ui/experiment_control.py) and the `*_view`
tabs), [`host/tmaze/io/`](host/tmaze/io), and
[`host/tmaze/vision/`](host/tmaze/vision), and only wire the cores to hardware
(ADR-0083). The firmware mirrors it:
`task_core.h` is pure, `cue-guided-task.ino` is the shell. New logic goes in a
core, not in a widget's callback.

### 2. Keep cores small; a growing shell is a smell
Comprehension debt concentrates in the big files — the GUI tabs are already
800–930 lines. When a shell file grows because you put logic in it, that's the
signal to extract the logic into a tested core, not to add another 200 lines to
the widget. Prefer many small, single-purpose modules over a few large ones.

### 3. Tests are the comprehension contract
The fastest way for the next person to understand a module is to read its tests.
Every change to a pure core ships with a test, and tests are named as the
behavior they pin down (e.g. `test_reward_rearms_after_cooldown`) so the test
file reads as a spec. A passing, human-readable test is also proof that *you*
understood the behavior you asked for. Run the suite before you commit (see
[`PROGRAMS.md`](PROGRAMS.md) and [`host/run_tests.bat`](host/run_tests.bat)).

### 4. Comments explain intent and invariants, not mechanics
Follow the essay-style docstrings in the newer cores: say what a function's
inputs/outputs/invariants are and *why* it exists. **Do not** write narration
comments that restate the code (`# increment counter`, `# import numpy`) — they
add volume without adding understanding, which is pure debt, and AI tends to
generate them by the dozen. Delete them when you see them.

### 5. Every magic number points to a reason
A bare threshold, timing, or reward volume is a mystery to the next reader and a
scientific liability. A numeric constant is either a **named constant** (the pin
map and tunables live in `firmware/common/rig_config.h`) or it carries a
`# see docs/decisions/NNNN` pointing at the ADR that justifies its value. If the
value is a deliberate choice, it needs an ADR (see `AGENTS.md`).

### 6. Boring over clever
Prefer explicit, obvious code over general, clever code. The abstraction to
defer is **speculative generality** — a base class, a factory, a config layer
added *before* it's needed. Don't add one until there are two or three real
callers that need it; premature indirection is comprehension debt because every
hop is something a reader must hold in their head. Match the flat, direct style
of the pure cores.

This is *not* a rule against naming things. Extracting a chunk of logic into a
named function or a tested pure core, so a concept has one clear home, is
encouraged at any caller count — that's guidelines #1 and #2, and it *reduces*
what a reader must hold in their head. The line: extract to give an existing
concept a name (anytime); don't add an indirection layer to serve callers that
don't exist yet (wait for the callers).

### 7. One canonical way to do each thing
New code uses the shared utilities rather than re-inventing them: parse firmware
lines through [`tmaze.core.protocol`](host/tmaze/core/protocol.py) (never a
hand-rolled `line.split(",")`), talk to the broker through
[`tmaze.io.rig_io`](host/tmaze/io/rig_io.py), guard optional deps through
[`tmaze.ui.gui_deps`](host/tmaze/ui/gui_deps.py), and resolve on-disk paths
through [`tmaze.paths`](host/tmaze/paths.py) rather than hand-rolled `..` hops.
Two ways to do one thing forces every reader to learn the codebase twice.

### 8. Shared vocabulary, consistent naming
Much of the difficulty here is the *domain*, not Python. Use the task's real
vocabulary — **Free Roam**, **well**, **stem**, **return arm**, **loop**,
**credential / re-arm**, **shaping phase** — verbatim in identifiers, docstrings,
and commit messages, matching [`docs/how-tracking-and-rules-work.md`](docs/how-tracking-and-rules-work.md).
A term learned once shouldn't have to be re-inferred under three different names.

### 9. Commits (and ADRs) narrate *why*, not just *what*
The diff already shows what changed; your commit message should say why. Record
non-obvious decisions as ADRs and deferred ideas in `docs/future-work.md` (both
per `AGENTS.md`). These breadcrumbs are what let a future human reconstruct
intent without re-reading everything — the direct antidote to debt.

## Practical onboarding

### It's an installable package — `pip install -e .` once
The PC tools are an installable package **`tmaze`** (source under `host/tmaze/`,
layered `core / io / vision / ui / tools`; see
[ADR-0083](docs/decisions/0083-host-tools-become-tmaze-package.md)). Install it
editable once per machine (including each rig PC after a pull), inside the
`drieu_t_maze` env:

```
pip install -e .        # from the repo root
```

Then `import tmaze` and `python -m tmaze.<layer>.<name>` work from any directory —
no more `cd host` / sibling imports. Imports are package-qualified
(`from tmaze.core import protocol`); paths resolve via
[`tmaze.paths`](host/tmaze/paths.py), never hand-rolled `os.path.dirname(__file__)
+ "/.."` hops. See [`PROGRAMS.md`](PROGRAMS.md) for the exact launch commands.

**Placement rule (where a new module goes).** A *shell* layer
(`ui` / `vision` / `io`) if it imports Tk / OpenCV / serial; else `core/` (plus
the right cluster — `reward` / `phase3` / `session` / `records`) if it is pure and
headless-testable; else `tools/` if it is a standalone runnable. Cores must not
import `io` / `vision` / `ui`; the boundary is enforced by
[`host/tests/test_architecture.py`](host/tests/test_architecture.py).

### A learning path for new coders
Read in this order — each is a small, pure, tested module, so you can understand
the system without any hardware:

1. [`tmaze/core/protocol.py`](host/tmaze/core/protocol.py) + [`tests/test_protocol.py`](host/tests/test_protocol.py) — the serial line format.
2. [`tmaze/core/maze_state.py`](host/tmaze/core/maze_state.py) + [`tests/test_maze_state.py`](host/tests/test_maze_state.py) — position → discrete maze state.
3. [`tmaze/core/task_events.py`](host/tmaze/core/task_events.py) + [`tests/test_task_events.py`](host/tests/test_task_events.py) — maze state → task vocabulary.
4. [`tmaze/core/rules_engine.py`](host/tmaze/core/rules_engine.py) + [`tests/test_rules_engine.py`](host/tests/test_rules_engine.py) / [`tests/test_well_reward.py`](host/tests/test_well_reward.py) — when a reward fires.

Only then open the shells (`tmaze/vision/tracker.py`, `tmaze/ui/camera_view.py`,
`tmaze/ui/tracker_view.py`) — they are large and mix Tk + threading + OpenCV.

### Legacy files — don't copy their style
[`host/tmaze/ui/maze_gui.py`](host/tmaze/ui/maze_gui.py) and
[`host/tools/test_sensors_licks.py`](host/tools/test_sensors_licks.py) predate the
conventions above (no docstrings, untyped, hand-rolled line parsing). They still
work, but they are **not** the pattern to imitate; when you touch them,
migrate them toward the current style rather than extending the old one.

## Running the tests

From `host/`, in the `drieu_t_maze` env (the package must be installed —
`pip install -e .`):

```
run_tests.bat            REM Windows: discovers and runs every host test
python -m unittest discover -s tests -t .   REM cross-platform equivalent
```

Firmware logic has its own host-compiled tests — see
[`firmware/cue-guided-task/test/README.md`](firmware/cue-guided-task/test/README.md).

## Where change history lives

This repo has **no `CHANGELOG.md`** (by choice). Git history records the
what/when/who, ADRs in [`docs/decisions/`](docs/decisions/) record the *why* of
non-obvious decisions, and [`PROGRAMS.md`](PROGRAMS.md) always reflects the
current set of runnables — together those cover change history without a
hand-maintained log that would drift. (The sibling `os_code_t_maze_resources`
repo does keep a dated Change Log in its `AGENTS.md`; that's because it holds
reference material and generated artifacts where git diffs are less
self-explanatory. Different repo, different need.)

## Before you commit

- Keep the change to one purpose and small enough for a human to review in a
  sitting (see the AI-assisted change rules in `AGENTS.md`).
- Run the tests; add one for any pure-core change.
- Update `PROGRAMS.md` if you added/changed a runnable, and add an ADR for any
  non-obvious decision (both per `AGENTS.md`).
- Optionally run the formatter/linter (`ruff format . && ruff check .` from the
  repo root); it is advisory, not a gate.
