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
core. This split is the backbone of the whole codebase — the pure cores
([`host/protocol.py`](host/protocol.py), [`host/maze_state.py`](host/maze_state.py),
[`host/task_events.py`](host/task_events.py), [`host/rules_engine.py`](host/rules_engine.py))
are small and readable; the shells ([`host/experiment_control.py`](host/experiment_control.py)
and the `*_view.py` tabs) only wire them to hardware. The firmware mirrors it:
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
Prefer explicit, obvious code over general, clever code. Do not add an
abstraction (a base class, a factory, a config layer) until there are two or
three real callers that need it — premature indirection is comprehension debt
because every hop is something a reader must hold in their head. Match the flat,
direct style of the pure cores.

### 7. One canonical way to do each thing
New code uses the shared utilities rather than re-inventing them: parse firmware
lines through [`host/protocol.py`](host/protocol.py) (never a hand-rolled
`line.split(",")`), talk to the broker through [`host/rig_io.py`](host/rig_io.py),
guard optional deps through [`host/gui_deps.py`](host/gui_deps.py). Two ways to do
one thing forces every reader to learn the codebase twice.

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

### Run everything from `host/`
The Python tools import sibling modules (`from rig_io import RigClient`) and use
paths relative to the working directory, so they are **not** a package — always
`cd host` first (inside the `drieu_t_maze` conda env). Running from the repo root
will break imports. See [`PROGRAMS.md`](PROGRAMS.md).

### A learning path for new coders
Read in this order — each is a small, pure, tested module, so you can understand
the system without any hardware:

1. [`host/protocol.py`](host/protocol.py) + [`host/test_protocol.py`](host/test_protocol.py) — the serial line format.
2. [`host/maze_state.py`](host/maze_state.py) + [`host/test_maze_state.py`](host/test_maze_state.py) — position → discrete maze state.
3. [`host/task_events.py`](host/task_events.py) + [`host/test_task_events.py`](host/test_task_events.py) — maze state → task vocabulary.
4. [`host/rules_engine.py`](host/rules_engine.py) + [`host/test_rules_engine.py`](host/test_rules_engine.py) / [`host/test_well_reward.py`](host/test_well_reward.py) — when a reward fires.

Only then open the GUI shells (`camera_view.py`, `tracker.py`, `tracker_view.py`) —
they are large and mix Tk + threading + OpenCV.

### Legacy files — don't copy their style
[`host/maze_gui.py`](host/maze_gui.py) and
[`host/tools/test_sensors_licks.py`](host/tools/test_sensors_licks.py) predate the
conventions above (no docstrings, untyped, hand-rolled line parsing). They still
work, but they are **not** the pattern to imitate; when you touch them,
migrate them toward the current style rather than extending the old one.

## Running the tests

From `host/`, in the `drieu_t_maze` env:

```
run_tests.bat            REM Windows: discovers and runs every host test
python -m unittest discover -p "test_*.py"   REM cross-platform equivalent
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
