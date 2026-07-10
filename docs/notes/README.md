# Capture inbox (bugs & ideas)

A **human-owned, low-friction scratchpad** for things you notice while running or
building the rig: bugs to fix and ideas for future work. Jot it here and move on
— no template, no ceremony.

Open this folder (`docs/notes/`) as an **Obsidian vault** if you like; the files
are plain Markdown, so they work in any editor too.

## What goes where

- [`Bugs.md`](Bugs.md) — something is broken or wrong.
- [`Ideas.md`](Ideas.md) — something to add or improve later.

One bullet per item is enough. Rough is fine — this is capture, not
documentation.

Give each item a short **handle** so you can reference it from a commit or a
`future-work.md` entry: `#YYMMDD_ShortName`, date-first (e.g.
`#260710_DoubleReward`). The date prefix is the creation date (no separate date
needed) and keeps handles unique without you having to remember old ones; once a
handle is referenced elsewhere, don't rename it. In Obsidian these are clickable
tags (a purely numeric tag like `#260710` wouldn't register — the name suffix is
what makes it a real tag).

## How this differs from the other lists

| This inbox | `docs/future-work.md` | `docs/decisions/` (ADRs) |
|------------|-----------------------|--------------------------|
| Raw, human-owned capture | Curated, vetted backlog | Decisions already made |
| Anything, half-formed | Concrete deferred code changes, templated | Immutable, numbered |
| Low friction, may be noisy | Kept tidy | Append-only |

The project timeline / Gantt lives in the sibling `os_code_t_maze_resources`
repo; scientific and lab notes live in that repo's Obsidian vault. This inbox is
only for **code** bugs and ideas, next to the code they concern.

## Triage (keeps this list short)

Items are meant to flow *out* of here:

- A real deferred **code change** → an agent (or you) promotes it into
  [`docs/future-work.md`](../future-work.md) with the full `FW-NNNN` template,
  then removes it here.
- A **bug** → fix it and reference the fix in the commit, then remove it here.
- **Noise / stale** → just delete it.

Agents may read this inbox and perform that triage (see the maintenance rules in
[`AGENTS.md`](../../AGENTS.md) and ADR-0033). It's fine for this file to be messy
between triage passes.
