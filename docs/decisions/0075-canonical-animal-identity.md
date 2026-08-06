# ADR 0075: Canonical animal identity for joining recordings to husbandry data

- **Status:** Accepted
- **Date:** 2026-08-05
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/animals.py` (new pure core + resources resolver), `host/test_animals.py`, `host/session_controller.py` (Session Start), `os_code_t_maze_resources/records/animal/animals.json` (new canonical list)
- **Supersedes / Superseded by:** —

## Context

A session bundle is keyed by `<animal>_<stamp>` and is meant to be joined, later,
to per-animal husbandry and training records by **animal id + date**. Until now
that id was whatever free text the operator typed into the Task tab, passed
through `protocol.sanitize_animal_id` (a slugger). That slug is a *weak key*:
"Rat 600", "600 ", and a typo like "60O" (letter O) each produce a *different*
folder name, and a mistyped id silently mints an orphan bundle that no downstream
join will ever match - and no one notices at capture time.

This is the first workstream of the coordinated-records upgrade, whose whole point
is to make recordings self-describing and analysis-ready. A reliable animal key is
the precondition for every later join (daily husbandry table, session registry),
so identity needs a single source of truth rather than ad-hoc slugs.

Constraints:

- The rig PC always has **both** repos checked out (`os_code_t_maze` +
  `os_code_t_maze_resources`), and all other animal metadata already lives in the
  resources repo (`records/animal/`). Identity belongs there too.
- The rig must **never** be hard-blocked from starting a session. A validation
  failure (unknown id, or the resources repo temporarily absent/renamed) can warn
  but must not stop a recording.

## Decision

Add one canonical list -
[`records/animal/animals.json`](../../../os_code_t_maze_resources/records/animal/animals.json)
in the resources repo - as the single source of truth for animal identity: an
ordered list of `{id, aliases}` (ids `600/601/602`, `schema_version` 1). It is
read by both the rig and (later) the records builders.

Add a small pure core `host/animals.py` that:

- resolves the sibling resources repo path once (`resources_root()`, honouring an
  `OS_CODE_T_MAZE_RESOURCES` env override) - the shared resolver the later
  workstreams reuse;
- loads the list best-effort (`load_registry` - a missing/malformed file yields an
  empty, `available == False` registry, so the rig runs without the resources
  repo);
- normalizes an entered id to its canonical form via **loose matching** -
  lower-case alphanumerics only, so "Rat 600" -> "600".

Wire it into Session Start in `session_controller.py`: the entered id is
normalized to the canonical id used for the `session_id` and manifest. If it isn't
recognized (or is blank), fall back to the old sanitized slug **and** append a
short **non-blocking** warning to the session status line (reusing the existing
`notes` seam), so the operator catches a typo at capture time. The rig still
starts.

## Alternatives considered

- **Keep identity in the code repo (for rig-standalone use).** Rejected: the rig
  PC always has both repos, all other animal metadata is in resources, and keeping
  two homes for identity invites drift. A code-side resolver reads the resources
  copy instead.
- **Hard-block Start on an unknown id.** Rejected: a behavioral rig must keep
  running; a validation hiccup (or a genuinely new animal) must never cost a
  session. A non-blocking warning catches the common typo without that risk.
- **A popup confirm on an unknown id.** Rejected for now as heavier than needed;
  the status-line warning reuses an existing seam and stays out of the operator's
  way. Can be revisited if orphan bundles still slip through.
- **Exact-match validation only.** Rejected: operators naturally type "Rat 600";
  loose matching accepts the obvious variants while still catching real typos.

## Consequences

- Recognized ids now yield a stable, canonical bundle key, so every later join
  (WS3 daily table, WS4 registry) has a dependable animal key.
- A new animal must be added to `animals.json` to be recognized; until then Start
  warns and falls back to the slug (no data lost). This is the intended nudge to
  keep the list current.
- `host/animals.py` also becomes the home of the resources-path resolver reused by
  the rest of the upgrade.
- `sanitize_animal_id` remains as the fallback, so behaviour is unchanged when the
  registry is unavailable.

## Evidence / links

- Coordinated-records upgrade plan (WS1).
- Builds on the `session_id`/manifest correlation index (docs/decisions/0048,
  0049) and the non-blocking status-line seam already used for the "arm Phase 3
  first" / notes-count messages in `session_controller._start_session`.
- Tests: `host/test_animals.py` (loose normalization, aliases, `available`,
  tolerant load).
