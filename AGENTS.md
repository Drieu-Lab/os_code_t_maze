# Agent guidelines

Guidance for AI coding agents working in this repository.

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
