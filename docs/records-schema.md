# Records schema reference

The single place to look up **what every coordinated-records artifact contains**,
where it lives, what writes it, and its current `schema_version`. These artifacts
were introduced by the coordinated-records upgrade (ADR-0075–0081) to make each
Phase 3 session self-describing and analysis-ready. Terms like [session
registry](glossary.md#session-registry), [idempotent
rebuild](glossary.md#idempotent-rebuild), [tidy format](glossary.md#tidy-long-format),
and [intervention tag](glossary.md#intervention-tag) are defined in the glossary.

Rates and fractions are stored **0..1** (a reader converts to % for humans);
blank means "not captured for this session". Water is surfaced as **four separate
provenance-labelled channels**, never summed (see the registry below).

## Artifact map

| Artifact | Repo / location | Written by | schema_version | ADR |
| --- | --- | --- | --- | --- |
| `animals.json` | resources · `records/animal/` | Records Manager GUI (Roster) or hand-edited | 1 | 0075 |
| `daily_log.csv` | resources · `records/animal/` | Records Manager GUI (Daily) or `build_daily_log.py` (from the Entry sheet) | 2 (comment line) | 0076, 0100 |
| `session_annotations.csv` | resources · `records/animal/` | Records Manager GUI (Sessions) or `build_session_training_log.py --import-annotations` | 1 (comment line) | 0082 / 0085 |
| `records_changes.csv` | resources · `records/animal/` | appended by every records write path (Records Manager saves; builders' `--export` / `--import-annotations`) | 1 (comment line) | 0155 |
| `animal_state.json` | code · per-bundle `config/` | `session_controller` at Start (core `animal_state.py`) | 2 | 0077, 0101 |
| `session_meta.json` | code · per-bundle `config/` | `session_controller` at Start (core `session_meta.py`) | 1 | 0079 |
| `session_registry.csv` | code · recordings root (git-ignored) | `build_session_registry.py` (core `session_registry.py`) | 1 (comment line) | 0081 |
| `session_registry_tags.csv` | code · recordings root (git-ignored) | `build_session_registry.py` | 1 (comment line) | 0081 |

Related per-bundle streams these join (not re-defined here): the session manifest
`session_<id>.json` (ADR-0048), the behavioural summary `summary/session_summary_<id>.json`
(ADR-0071/0073/0078), and the notes log `summary/notes_<id>.jsonl` (ADR-0074).

## `animals.json` (v1)

Canonical animal identity — the join key between recordings and husbandry.

- `schema_version` — layout version.
- `animals[]` — `{id, aliases[]}`: the canonical id plus any loose spellings that
  normalize to it (ADR-0075).

## `daily_log.csv` (v2)

Machine-readable daily husbandry table (source of truth); a leading
`# schema_version=2` comment line precedes the header. One row per `(animal, date)`:

`animal_id, date (YYYY-MM-DD), weight_g, baseline_g, pct_baseline, water_on,
water_off, water_ml, supp_water, notes, water_regime`.

`water_on`/`water_off`/`supp_water` are free text today ("6pm", "2 min"); see
FW-0029 for hardening them into real timestamps.

`water_regime` (schema v2, ADR-0100) marks the day's regime: blank or
`restriction` (the default, on water restriction) vs `ad_lib` (off-restriction,
e.g. a weekend of free water). Appended last so a v1 reader ignores it and a v1
file (no column) loads as `""` == restriction. An `ad_lib` day is excluded from
the dashboard's water-maintenance trace and never raises a not-weighed alarm, but
still plots on the % baseline trend; the within-day on/off boundary reuses the
`water_on`/`water_off` cells.

A save merges by `(animal_id, date)` (an [upsert](glossary.md#upsert-keyed-merge),
never a blind overwrite) and refuses to run if the file is present but
unreadable, so a save can't clobber existing rows (ADR-0085; bug
`#260810_RecordsIntegrity`).

## `session_annotations.csv` (v1)

Per-session qualitative annotations — the two hand-added fields the rig can't
know (source of truth; ADR-0082/0085). A leading `# schema_version=1` comment
line precedes the header. One row per `session_id`:

`session_id, animal, date, procedures, notes`.

`animal`/`date` are denormalised copies (handy when reading the CSV alone);
`procedures`/`notes` are the free-text fields the operator types in the Records
Manager's Sessions tab. A save merges by `session_id`
([upsert](glossary.md#upsert-keyed-merge)) and, for a session not in the
registry, preserves the row's existing `animal`/`date` rather than blanking them.

## `records_changes.csv` (v1)

The append-only [change log](glossary.md#change-log-records) for the three
records files above (ADR-0155). A leading `# schema_version=1` comment line,
then one line per changed **field**:

`ts, operator, source, file, key, field, old, new`.

`ts` is local wall-clock time to the second (ISO 8601, one stamp per save);
`operator` is the person chosen in the Records Manager's Operator dropdown (or
a builder's `--operator`); `source` is the tool (`records_manager`,
`build_daily_log --export`, ...); `file` the records file's basename; `key` the
row identity as one string (`600|2026-09-10` for the daily log, the
`session_id` for annotations, the animal id for the roster). A row that appears
or vanishes gets a `(row)` marker line (`new=added` / `old=removed`) plus one
line per non-blank field. Never rewritten or sorted; readers drop a partial
trailing line. It describes the records, it is not one: if it disagrees with a
records file, the file wins. Core: `tmaze.core.records.records_changelog`.

**Git history.** The four records files (`daily_log.csv`,
`session_annotations.csv`, `animals.json`, `records_changes.csv`) are tracked in
the resources repo. The Records Manager's **Commit records** button
(`tmaze.core.records.records_git`) stages exactly those four, refuses if anything
else is already staged, builds the message from the change-log entries not yet
in `HEAD` (the log is append-only, so "not yet committed" is just the tail past
the committed copy - no clock comparison), and commits with the machine's git
identity. It never pushes (FW-0064). The toolbar shows "N records edits not yet
committed".

## `animal_state.json` (v2)

Pre-session physiological/motivational state frozen into the bundle at Start
(one per bundle):

`schema_version, session_id, animal, date, source, pre_session_weight_g,
baseline_g, pct_baseline, last_water_on, last_water_off, hours_since_water,
measured_water_ml, note`.

`source` ∈ `daily_table` | `start_dialog` | `manual` records where the values came
from. `last_water_on` / `last_water_off` are today's daily-row cells verbatim.
`hours_since_water` (schema v2, ADR-0101) is now DERIVED when the daily table is
available: the gap from the most recent *parseable* water-off (typically the
previous evening's — the resolver skips the current row's own evening off-time,
which is in the future for a morning session) to the session start time. It stays
null when no daily row prefills the state or the water-off cells are free text
("Till Monday evening"); those irregular cases are left for the operator, matching
ADR-0097's "code surfaces drift, data fixes stay the human's".

## `session_meta.json` (v1)

Sticky human bookkeeping frozen into the bundle at Start (one per bundle):

`schema_version, session_id, trainer, stage, stage_source, reward_type`.

`stage_source` ∈ `auto` (derived from the active phase) | `override` (operator
typed a sub-stage), so a reader can tell a derived stage from a typed one
(ADR-0079).

## `session_registry.csv` (v1)

The cross-session index — one row per bundle, a full [idempotent
rebuild](glossary.md#idempotent-rebuild) of everything under the recordings root
(ADR-0081). Leading `# schema_version=1` comment line, then these columns:

- **Identity / timing:** `session_id, animal, date, start_iso, duration_s`.
- **Husbandry** (preferred from the bundle's `animal_state.json`, else a
  `(animal, date)` join against `daily_log.csv`): `husbandry_source`
  (`daily_table`/`start_dialog`/`manual`/`daily_table_join`/`none`), `weight_g`,
  `baseline_g`, `pct_baseline`, `hours_since_water`.
- **Water — four channels:** `task_reward_ml` (auto rewards, from the summary's
  `estimated_water`), `manual_reward_ml` (operator GUI-button reward dispenses,
  from the summary's `manual_water`; ADR-0084, supersedes 0078), `supp_water`
  (post-session top-up, daily table), `water_on`/`water_off` (restriction span).
  The task and operator reward channels are disjoint, so `task_reward_ml +
  manual_reward_ml` is a correct reward total; the others are separate figures.
- **Cued task** (verbatim from the summary's `cued_task`/`phase3_stats` block,
  ADR-0052): `trials_total, completed, correct, incorrect, accuracy_cum,
  high_accuracy, low_accuracy, side_bias_left, side_bias_right, reward_rate,
  seed, high_tone_hz, low_tone_hz, high_tone_is_left`.
- **Excursions / compliance** (from the summary's `locomotion`/`behavior_stats`
  block; ADR-0080 mapping): `total_excursions` (= `total_choices`),
  `stem_choices`, `nonstem_choices`, `stem_compliance` (= `compliance_cum`).
- **Session metadata** (from `session_meta.json`): `trainer, stage, stage_source,
  reward_type`.
- **Provenance:** `bundle` (absolute path to the session folder).
- **Wide tag columns:** one `tag_<name>` per tag in the *current* `note_tags.json`
  vocabulary (0 when the session has none) — a convenience projection of the tidy
  file below; the column set follows the vocabulary and so is not fixed.

## `session_registry_tags.csv` (v1)

The [tidy](glossary.md#tidy-long-format), churn-proof form of the tag tallies —
stable across vocabulary edits. Leading `# schema_version=1` comment line, then:

`session_id, tag, count` — one row per `(session, tag)`, `count` summing that
tag's presses in the session (a counter tag reads as its running total; ADR-0074).
