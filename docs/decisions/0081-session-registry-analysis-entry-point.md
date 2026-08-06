# ADR 0081: Session registry as the analysis entry point (full idempotent rebuild; wide + tidy tag outputs; generated/git-ignored)

- **Status:** Accepted
- **Date:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_registry.py` (new pure core), `host/build_session_registry.py` (new thin runner), `host/test_session_registry.py`, `docs/records-schema.md` (new), `docs/glossary.md`, `PROGRAMS.md`
- **Supersedes / Superseded by:** —

## Context

The coordinated-records upgrade made each session bundle self-describing
(canonical id ADR-0075, daily table ADR-0076, `animal_state` ADR-0077, sticky
`session_meta` ADR-0079, manual water ADR-0078, notes/tags ADR-0074, summary
ADR-0071). But answering a *cross-session* question - "how did 600 trend this
week, and which sessions had a non-stem-dissuasion intervention?" - still meant
opening dozens of folders by hand. The payoff of "coordinated records" is only
realised when those per-bundle facts are joined into one queryable table.

Two subtleties shape the design: reward water is "accounted several ways" (auto
task reward, rare in-task manual dispenses, post-session supplemental, and the
restriction span), and the note-tag vocabulary is **user-editable**, so any
"one column per tag" layout would change shape whenever the operator adds a tag.

## Decision

Add a **session registry** as the analysis entry point: a pure core
`session_registry.py` that folds every bundle under the recordings root into
`session_registry.csv` (one row per session) + a tidy `session_registry_tags.csv`,
driven by a thin `build_session_registry.py` runner. Key choices:

- **Full idempotent rebuild.** Re-scan all bundles each run (never append), so
  re-running after another session - or twice by accident - never double-counts.
- **Prefer the bundle's frozen state over re-joining by date.** Read husbandry
  scalars straight from `animal_state.json`; fall back to a `(animal, date)` join
  against `daily_log.csv` only for older bundles that lack it (and always for the
  post-session `supp_water`). This retires the fragile date half of the key for
  new sessions and is robust to later edits of the daily table.
- **Four water channels as separate, provenance-labelled columns, never summed:**
  `task_reward_ml`, `manual_reward_ml`, `supp_water`, `water_on`/`water_off`, so
  the several-ways accounting reconciles in one row.
- **Stable tag output.** The tidy long `session_registry_tags.csv`
  (`session_id, tag, count`) is the churn-proof source of truth; the wide
  `tag_<name>` columns in `session_registry.csv` are a convenience projection of
  the *current* vocabulary at build time (so a vocabulary edit changes only the
  convenience columns, not the tidy truth).
- **Behaviour is not recomputed.** Accuracy / per-cue / side bias come verbatim
  from the summary's `cued_task` block (ADR-0052) and excursions/compliance from
  its `locomotion` block (ADR-0032, mapped per ADR-0080). The registry only
  selects, joins, labels, and serialises.
- **Degrades gracefully.** A missing `animal_state.json`, an unmatched daily row,
  or an absent daily table leaves those columns blank rather than failing; the
  folder name still yields `session_id`/`animal`/`date`.
- **Generated + git-ignored + versioned.** Outputs are a generated analysis
  artifact aggregating the git-ignored bundles, so they are written under the
  recordings root (`host/data/`, already git-ignored) - regenerate, don't version,
  mirroring the resources repo's `planning/outputs/`. Both CSVs carry a leading
  `# schema_version=1` comment line (the `daily_log.csv` convention), and a new
  one-page `docs/records-schema.md` documents every coordinated-records artifact's
  columns/provenance/version as the single reference for a future analyst.

## Alternatives considered

- **Append to the registry as sessions finish.** Rejected: append makes the
  output depend on run history and risks double-counting; a full rebuild is simple
  and deterministic for the modest bundle counts here.
- **One wide column per tag only.** Rejected: the vocabulary is user-editable, so
  wide-only makes the schema non-stationary across rebuilds. The tidy file is the
  stable form; wide columns remain as a convenience derived from it.
- **Sum water into one number.** Rejected: the four channels mean different
  things; summing hides the reconciliation the records upgrade is trying to
  enable. Separate, labelled columns keep them auditable.
- **Re-join husbandry by `(animal, date)` for every session.** Rejected: that is
  the fragile key this upgrade retires; the bundle's frozen `animal_state.json` is
  authoritative and edit-proof, with the date join kept only as a fallback.
- **Version the registry in git.** Rejected: it is reproducible from the bundles
  and the bundles themselves are ignored; versioning it would churn and could go
  stale against the source.

## Consequences

- One `python build_session_registry.py` produces the queryable table (+ tidy
  tags) an analyst or the WS7 training-log view reads; WS7 consumes
  `session_registry.csv` via a symmetric sibling-path lookup.
- New artifacts documented once in `docs/records-schema.md`; new glossary terms
  (session registry, idempotent rebuild, tidy/long format) added per the docs rule.
- Outputs are git-ignored under `host/data/`; if the recordings root is relocated
  outside an ignored tree, add an ignore rule for the two CSVs.
- The registry is only as good as the bundles: older bundles predating ADRs
  0077/0079 fall back to the daily-table join or blanks, which is why the fallback
  and graceful-degradation paths are tested.

## Evidence / links

- Coordinated-records upgrade plan (WS4). Consumes ADR-0071/0073/0078 (summary +
  water), ADR-0077 (`animal_state`), ADR-0079 (`session_meta`), ADR-0074
  (notes/tags), ADR-0076 (daily table), ADR-0080 (excursion/compliance mapping),
  via the ADR-0075 sibling-path resolver.
- Reference: `docs/records-schema.md`.
- Tests: `host/test_session_registry.py` (join prefers bundle state; daily-table
  fallback; graceful-degradation blanks + non-bundle skipping; four water
  channels; wide + tidy tag tallies). Smoke-run against the live recordings root
  (0 sessions -> valid empty CSVs). Full run: `python -m unittest test_session_registry`.
