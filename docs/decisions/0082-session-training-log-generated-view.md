# ADR 0082: The per-session training log is a generated view of the session registry (+ a thin annotation layer)

- **Status:** Accepted
- **Date:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `os_code_t_maze_resources/records/animal/build_session_training_log.py` (new builder), `.../session_training_log.xlsx` (new generated workbook), `os_code_t_maze_resources/AGENTS.md`, `os_code_t_maze_resources/README.md`
- **Supersedes / Superseded by:** —

## Context

The lab kept a hand-typed per-session `training_log.xlsx` (one sheet per rat,
one row per session: date, weight, trainer, stage, trials/correct, notes, and
a few hand-added columns such as "Total excursions" and "Common procedures
across days"). Every number in it was transcribed by hand at the rig, so it
drifted from its builder and duplicated facts the rig now captures itself.

After the coordinated-records upgrade the rig already knows most of that row:
`animal_state.json` (ADR-0077) has the weight/percent-baseline, `session_meta.json`
(ADR-0079) has trainer/stage/reward type, the summary (ADR-0071) has the
behaviour + water, and `build_session_registry.py` (ADR-0081) folds all of it
into one `session_registry.csv`. What the rig *can't* know is the small
qualitative layer - "what did we do across days", free-text observations.

This mirrors the daily-husbandry decision (ADR-0076): make the machine-readable
table the source of truth and let the workbook be a **view** over it, while
leaving the legacy hand-kept workbook untouched.

## Decision

Add a new **generated** per-session workbook `session_training_log.xlsx`, built
by a new `build_session_training_log.py` in the resources repo, as a view over
the session registry plus a thin human annotation layer. Key choices:

- **Registry is the single upstream source.** The builder reads
  `session_registry.csv` only - not the individual bundles. Because ADR-0081
  already folded `session_meta` (trainer/stage/reward) and the daily-table
  `supp_water` into the registry, re-reading `session_meta.json` / `daily_log.csv`
  here would just duplicate that join. One source keeps the view honest.
- **Two sheet kinds, same no-clobber pattern as `build_daily_log.py`.** An
  editable **"Annotations"** sheet keyed by `session_id` holds the two fields the
  rig can't derive (*Common procedures across days*, free-text *Notes*); it is
  preserved across rebuilds (read back, carried forward, new sessions appended).
  The **"View <id>"** sheets are regenerated read-only per-rat tables: every
  registry column joined with your annotations by `session_id`.
- **Seed pre-rig history read-only.** The legacy `training_log.xlsx` is read
  (never written) by header name so any hand-added columns are picked up, and its
  rows are shown - tinted, `Source = history` - beneath the rig rows so no
  history is lost. Re-read every rebuild (idempotent), since the legacy file is
  frozen.
- **Cross-repo read is the mirror of ADR-0075.** The registry lives in the code
  repo (git-ignored under `host/data/`); this resources-repo builder resolves it
  via the `OS_CODE_T_MAZE` env override else the default sibling folder - the
  reverse of how `animals.resources_root()` finds this repo. A missing registry
  degrades to a clear "run `build_session_registry.py` first" message, not a
  silent empty table.
- **Leave the originals untouched.** Neither `training_log.xlsx` nor its builder
  is modified; the new files sit alongside them (as agreed for the water log too).

## Alternatives considered

- **Refactor `training_log.xlsx` in place.** Rejected: the user asked to preserve
  the working logs; an in-place rewrite would risk the hand-typed history and
  couple the source-of-truth migration to a live file.
- **Re-join `session_meta.json` + `daily_log.csv` here (as the plan first
  sketched).** Rejected as duplication: ADR-0081 already performs that join into
  the registry. Reading the registry alone is one source and less code to keep in
  sync; the trade-off is a dependency on the registry being regenerated first,
  which the builder makes explicit.
- **A formula workbook (like the legacy log's auto columns).** Rejected: the
  values come from a CSV, not from adjacent cells, so a generated static view is
  simpler and can't silently mis-total.
- **Keep hand-counting the qualitative columns in the legacy file.** Rejected:
  that is the transcription burden the upgrade removes; the annotation sheet keeps
  only the genuinely human fields.

## Consequences

- After a session: `python build_session_registry.py` (code repo) then
  `python records/animal/build_session_training_log.py` (resources repo)
  refreshes the per-rat view; the operator types only the qualitative annotation.
- The view is only as current as the registry - stale until the registry is
  rebuilt. The builder's hard error on a missing registry makes the ordering
  obvious.
- `session_training_log.xlsx` is a generated artifact; like `daily_log.xlsx` it is
  committed for convenience but is fully reproducible from the registry + legacy
  seed.
- New runner documented in the resources `AGENTS.md`/`README.md` file maps and
  regen commands (the resources-repo convention; `PROGRAMS.md` stays code-repo
  only, as with `build_daily_log.py`) per the maintenance rules.

## Evidence / links

- Coordinated-records upgrade plan (WS7). Consumes ADR-0081 (registry),
  ADR-0079 (`session_meta`), ADR-0076 (daily table / supp water), ADR-0077
  (`animal_state`), ADR-0080 (excursion/compliance mapping) - all already folded
  into the registry it reads. Cross-repo resolver mirrors ADR-0075.
- Smoke-run against the live registry (0 rig sessions -> Annotations header +
  per-rat Views seeded with 19 legacy history rows); a fake-registry round-trip
  verified the annotation no-clobber (hand-typed *Common procedures* / *Notes*
  survive a rebuild and merge into the generated rig row).
