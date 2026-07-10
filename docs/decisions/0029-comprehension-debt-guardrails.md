# ADR 0029: Fight comprehension debt with advisory guardrails, not enforcement

- **Status:** Accepted
- **Date:** 2026-07-08
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `CONTRIBUTING.md` (new), `AGENTS.md` (AI-assisted change rules +
  CONTRIBUTING pointer), `pyproject.toml` (new, ruff), `host/run_tests.bat`
  (new), `environment.yml` (new), `host/requirements.txt` (pinned floors),
  `.cursor/rules/*.mdc` (new), `os_code_t_maze.code-workspace` (editor hint)
- **Supersedes / Superseded by:** —

## Context

The codebase grows in large, AI-generated milestone drops (see the git history:
single commits add the camera, tracking, rules engine, and shaping phases at
once) and is maintained by lab members of very different programming experience.
The compounding risk is **comprehension debt**: code volume outrunning what any
human actually understands. On a behavioral rig that is a scientific and
animal-welfare liability, not just a maintenance one.

The project already had strong *decision* logging (ADRs, `docs/future-work.md`)
but no written *coding* guide, no environment lock, no one-command test run, and
no style tooling; conventions lived only in the newer code (the "pure core +
thin shell" split, run-from-`host/`, parse-through-`protocol.py`). New or novice
coders had to infer them, and could just as easily copy the older, pre-convention
GUI scripts.

The obvious industrial fix — CI gates, enforced pre-commit hooks, strict
lint/type checking — was explicitly considered and rejected for this setting
(see the chat that produced this ADR). In a small lab with rotating,
mixed-experience contributors and no dedicated maintainer, hard gates mostly
produce blocked students and bypassed hooks, not better code.

## Decision

Add a layer of **advisory, non-blocking** guardrails whose sole purpose is to
keep human understanding attached to the code:

- **`CONTRIBUTING.md`** — the "how we write code here" guide, framed around
  minimizing comprehension debt: nine conventions (pure core + thin shell, small
  cores, tests-as-contract, intent-only docstrings / no narration comments,
  magic-numbers-cite-an-ADR, boring-over-clever, one canonical way, shared
  vocabulary, why-not-what commits) plus the run-from-`host/` rule and a
  new-coder reading path through the pure cores.
- **Two process rules in `AGENTS.md`**: (1) a *human author of record* must be
  able to explain any merged AI-generated change and is the git author; (2) cap
  each change to what a human can review in one sitting (split pure core + tests
  from GUI wiring) rather than landing milestone drops.
- **Reproducibility**: `environment.yml` codifies the `drieu_t_maze` env (named
  everywhere but previously defined nowhere); `requirements.txt` gets version
  floors. Neither is an exact lock — the note tells you to `conda env export` on
  the rig for that.
- **One-command tests**: `host/run_tests.bat` (unittest discovery), documented in
  `PROGRAMS.md`, so the suite is discoverable instead of per-module tribal
  knowledge.
- **Style tooling as opt-in**: a lenient `ruff` config in `pyproject.toml`
  (bugs + imports + light style; `E501`/`E741` ignored) run by hand, plus a
  workspace format-on-save hint that defaults to off. No CI, no hook.
- **Cursor rules** (`.cursor/rules/conventions.mdc`) mirror the conventions as
  file-scoped nudges for agents, pointing back to `CONTRIBUTING.md`/`AGENTS.md`
  as canonical rather than restating them; no `hooks.json` (that would be
  enforcement).

No `CHANGELOG.md` is added to this repo: git + ADRs + `PROGRAMS.md` already cover
what/why/current-state.

## Alternatives considered

- **CI gating + enforced pre-commit hooks + strict lint/mypy.** Rejected for this
  lab: blocks mixed-experience contributors and invites `--no-verify` bypasses;
  the cost/benefit favors advisory tooling here. Revisit if the team grows or a
  maintainer owns CI.
- **Put the conventions only in `AGENTS.md`.** Rejected: `AGENTS.md` is the
  agent maintenance contract; humans need a human-addressed guide, and the
  code-level conventions are long enough to warrant their own file.
- **Pin exact versions now.** Deferred: exact pins must come from the rig PC
  (this dev machine has no conda env); floors + a "lock on the rig" instruction
  are the honest interim.
- **A `CHANGELOG.md` mirroring the resources repo.** Rejected: it would duplicate
  git/ADRs and drift; the resources repo keeps one only because it holds binary
  reference material where diffs are opaque.

## Consequences

- New coders get an explicit reading path and a "don't copy the legacy files"
  signal; the two coding eras can converge as files are touched, without a wall
  of lint errors.
- Nothing here blocks a commit, so discipline still depends on people following
  the guide and on the human-author-of-record rule during review — accepted
  trade-off for this lab.
- The env/dep files are floors, not a lock; true reproducibility needs a
  `conda env export` captured on the rig (a good future addition).
- If the project outgrows the honor system, this ADR is the point to revisit and
  supersede (e.g. add non-blocking CI first).

## Evidence / links

- Files: `CONTRIBUTING.md`, `AGENTS.md` (AI-assisted change rules), `pyproject.toml`,
  `host/run_tests.bat`, `environment.yml`, `host/requirements.txt`,
  `.cursor/rules/conventions.mdc`.
- `run_tests.bat` verified: `python -m unittest discover -p "test_*.py"` runs the
  host suite (96 tests; tracker/model tests skip without OpenCV).
- Related: ADR-0004 (the `drieu_t_maze` env + external conda activation),
  ADR-0024 (single host parser — an instance of "one canonical way").
