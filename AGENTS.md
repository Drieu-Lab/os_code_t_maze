# Agent guidelines

Guidance for AI coding agents working in this repository.

## Orientation — read these, don't ask the user to point you at them

This file is loaded automatically at the start of every session, so you already
have it. You do **not** need the user to tell you to "review the material."
Instead, use the map below and read the specific doc the task calls for — read on
demand, don't bulk-read everything.

This project spans **two sibling folders**:

- **`os_code_t_maze/`** (this repo) — experiment code only: Arduino firmware and
  the PC-side Python control/monitoring tools. Start here:
  - [`README.md`](README.md) — architecture overview (broker + TCP clients).
  - [`PROGRAMS.md`](PROGRAMS.md) — every runnable program and its exact launch
    command (conda env `drieu_t_maze`).
  - [`firmware/PROTOCOL.md`](firmware/PROTOCOL.md) — serial/TCP message protocol.
  - [`docs/decisions/`](docs/decisions/) — ADRs explaining *why* key choices
    were made (read the index in [`docs/decisions/README.md`](docs/decisions/README.md)).
  - [`CONTRIBUTING.md`](CONTRIBUTING.md) — how code is written here (the
    "pure core + thin shell" pattern, the run-from-`host/` rule, the new-coder
    learning path), framed around minimizing comprehension debt.
  - `host/README.md`, `firmware/README.md` — component-level details.
- **`../os_code_t_maze_resources/`** (sibling repo) — planning, hardware
 references, lab notes, and mockups. Its own
 [`AGENTS.md`](../os_code_t_maze_resources/AGENTS.md) has the full file map and
 maintenance rules; start there for the rig manual/schematics, the Gantt/
 dependency planning, and the Obsidian notes. It also holds the project's
 *why* and governing constraints — read these when you need scientific or
 procedural context for a task:
 - `research_question/BRAINK99_Drieu.pdf` — the Drieu K99/R00 BRAIN grant. The
 rig implements its cue-guided, reward-directed task: rats learn to map two
 auditory cues to left/right choices for distinct rewards while the
 AC–VS–DS–HPC network is recorded (Neuropixels) to study wake/sleep neuronal
 reactivations.
 - `animal_protocol/RA23A232.pdf` — the JHU ACUC protocol (PI Janak) the
 experiments run under: Long-Evans rats, water restriction, viral
 injections, and survival surgery for probe implants.
 - `rig-hardware/neural_recording/` — neural-recording hardware references and
 vendor orders (probes, headstages).

To get both folders auto-loaded in one Cursor window, open
[`os_code_t_maze.code-workspace`](os_code_t_maze.code-workspace) (a multi-root
workspace) instead of opening this folder alone.

## AI-assisted change rules — keep code understandable

This repo grows fast, often via AI agents, in a lab with coders of differing
experience. The compounding risk is **comprehension debt**: code volume that
outruns what any human actually understands. Two rules keep that in check (the
code-level conventions live in [`CONTRIBUTING.md`](CONTRIBUTING.md)):

- **Human author of record.** Agent-generated code does not land until a named
  human can explain what it does and why. That human — not the agent — is the
  git author and owner of the change. If you are that agent, make the change
  explainable: small diffs, intent in the commit message, an ADR for any
  non-obvious decision. If a human cannot yet explain it, stop and explain it to
  them rather than merging.
- **Cap change size to human reviewability.** One purpose per commit/PR. If a
  change is too big for a human to review in one sitting, split it — land the
  pure core plus its tests separately from the GUI/firmware wiring that uses it.
  Prefer several small, reviewable changes over one large milestone drop.

## Keep `PROGRAMS.md` up to date

[`PROGRAMS.md`](PROGRAMS.md) is the canonical list of runnable programs, their
descriptions, and their launch commands (Anaconda env: `drieu_t_maze`).

Whenever you add, remove, rename, or move a runnable program — or change how an
existing one is launched (its command, arguments, or required directory) — you
**must** update `PROGRAMS.md` in the same change so it stays accurate. This
includes:

- New host tools or scripts (Python or batch).
- New firmware sketches.
- New docs/tooling scripts.
- Changes to an existing program's run command, flags, or defaults.

Match the existing table format, keep descriptions to one line, and show the
exact command (noting the directory it should be run from).

## Record non-obvious decisions as ADRs

[`docs/decisions/`](docs/decisions/) is the decision log: numbered, immutable
Architecture Decision Records that capture *why* a choice was made, not just
*what* changed.

Whenever you make a decision whose reasoning isn't self-evident from the diff,
add an ADR in the **same change**. This includes:

- Protocol changes (framing, message format, version bumps).
- Timing / threshold / volume constants whose value is a deliberate choice.
- Architectural decisions (process splits, resource ownership, transports).
- Anything where a future reader would reasonably ask "why this way?".

Copy [`docs/decisions/template.md`](docs/decisions/template.md) to the next
`NNNN-short-title.md`, fill in every section (context, decision, alternatives,
consequences, evidence/links), add a row to the index in
[`docs/decisions/README.md`](docs/decisions/README.md), and reference the ADR
from the code (`// see docs/decisions/NNNN`) and the commit/PR body
(`Refs ADR-NNNN`). Never rewrite an existing ADR's reasoning — supersede it with
a new one. See [`docs/decisions/README.md`](docs/decisions/README.md) for the
full workflow. Skip ADRs for obvious, mechanical, or cosmetic changes.

## Track future work

[`docs/future-work.md`](docs/future-work.md) is a mutable backlog of **candidate
code changes that were deliberately deferred** — distinct from ADRs (decisions
already made) and from the planning/Gantt in the `os_code_t_maze_resources` repo
(project timeline).

Whenever you defer a non-trivial idea — something a future reader would want to
pick up but that's out of scope for the current change — **add an entry** using
the template in that file (copy it, take the next `FW-NNNN` id, add an index
row). Capture enough context to reconstruct it: motivation, affected modules,
related ADRs, a sketch of the approach, and how to evaluate it. When an item is
implemented, record the reasoning in a new ADR and remove (or mark `Done`) its
backlog entry.
