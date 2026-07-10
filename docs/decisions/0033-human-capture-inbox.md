# ADR 0033: A human-owned bug/idea capture inbox, separate from the agent-curated backlog

- **Status:** Accepted
- **Date:** 2026-07-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `docs/notes/` (new: `README.md`, `Bugs.md`, `Ideas.md`),
  `AGENTS.md` (orientation pointer + "Triage the capture inbox" rules),
  `.gitignore` (ignore `docs/notes/.obsidian/`), `docs/README.md`,
  `.cursor/rules/conventions.mdc`
- **Supersedes / Superseded by:** — (complements ADR-0029 governance;
  relates to the `docs/future-work.md` backlog)

## Context

The repo had no low-friction place for a human to jot a bug or a half-formed idea
noticed while running or building the rig. The existing surfaces are all either
heavy or agent-oriented:

- `docs/future-work.md` — a deliberately **curated, templated** backlog
  (`FW-NNNN`, ~multi-line entries) that agents maintain; too much ceremony for a
  quick capture, and its tidiness is a feature we don't want to erode with raw
  notes.
- `docs/decisions/` — immutable ADRs for decisions *already made*.
- The Gantt in the sibling `os_code_t_maze_resources` repo — project timeline.

The experimenter wanted a personal, low-ceremony list (openable in Obsidian) for
bugs and future work, in addition to the agent-edited backlog. A natural question
was whether to move the existing Obsidian **research/lab-notes** vault out of the
resources repo into this one to host it.

## Decision

Add a small, human-owned **capture inbox** at `docs/notes/` in this (code) repo:
plain Markdown (`Bugs.md`, `Ideas.md`, a `README.md`) that can be opened as an
Obsidian vault. It is the raw-capture counterpart to `future-work.md`:

- **Ownership / maturity split.** `docs/notes/` is the human's raw, possibly
  noisy inbox; `future-work.md` stays the curated, vetted backlog. Different
  owner, different maturity — that boundary is what prevents overlap.
- **One-way triage flow.** Items flow *out* of the inbox: a real deferred code
  change is promoted into `future-work.md` (full `FW-NNNN` template); a fixed bug
  is referenced in its commit; noise is deleted. Agents **may** read the inbox
  and perform this triage (documented in `AGENTS.md`), but must not require it to
  be tidy or invent entries on the human's behalf.
- **Scope = code only.** The inbox holds bugs/ideas about *the code*, kept next
  to the code they concern. Scientific and lab notes stay in the resources repo's
  Obsidian vault.
- **Obsidian config is not versioned.** `docs/notes/.obsidian/` is git-ignored
  (per-machine workspace cruft); the `.md` notes are versioned.

We **did not** move the research/lab-notes Obsidian vault from
`os_code_t_maze_resources` into this repo (see alternatives).

## Alternatives considered

- **Move the whole Obsidian vault from the resources repo into this repo.**
  Rejected: the project is organized around a documented invariant — this repo is
  "experiment code only," the resources repo holds "planning, hardware
  references, lab notes." The vault is mostly research/lab material, so moving it
  would blur that boundary and force edits to both `AGENTS.md` files. Splitting by
  *subject* (code bugs/ideas here, research notes there) keeps the charter intact.
- **A single Markdown file (`docs/inbox.md`) instead of a folder/vault.**
  Reasonable and simpler, but a small folder gives a cleaner Bugs-vs-Ideas split
  and is directly Obsidian-openable, which the experimenter wanted.
- **GitHub Issues.** Good integration (searchable, linkable, `gh`-readable) but
  higher friction for quick jotting and requires being online; rejected as the
  primary capture surface for now (still available for anything worth formal
  tracking).
- **Excel sheet in the resources repo.** Familiar and offline, but a binary blob
  agents can't easily read/edit or link from code; rejected for a capture surface
  that should be diff-able and agent-triageable.
- **Add the notes to the resources repo's existing vault.** Rejected: bugs/ideas
  are about the code and belong next to it (and next to `future-work.md`, which
  they feed).

## Consequences

- There is now a cheap place to capture bugs/ideas without ceremony, and a
  defined path for them to graduate into the curated backlog — so `future-work.md`
  stays tidy while nothing gets lost.
- One more tracking surface exists; the guard against bloat is the strict
  ownership/maturity split and the one-way triage flow (documented in the inbox
  `README.md` and `AGENTS.md`). If the inbox and backlog start to overlap in
  practice, revisit.
- The "code-only" charter for this repo is preserved; research notes remain in
  the resources repo.
- Agents get an explicit, bounded license to prune this one list — a deviation
  from "never edit the human's notes," justified by the triage flow.

## Evidence / links

- Files: `docs/notes/README.md`, `docs/notes/Bugs.md`, `docs/notes/Ideas.md`;
  `AGENTS.md` ("Triage the capture inbox"); `.gitignore`
  (`docs/notes/.obsidian/`); `docs/README.md`; `.cursor/rules/conventions.mdc`.
- Related: ADR-0029 (comprehension-debt guardrails / governance), the
  `docs/future-work.md` backlog it complements.
