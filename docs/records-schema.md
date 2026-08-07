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
| `animals.json` | resources · `records/animal/` | hand-edited | 1 | 0075 |
| `daily_log.csv` | resources · `records/animal/` | `build_daily_log.py` (from the Entry sheet) | 1 (comment line) | 0076 |
| `animal_state.json` | code · per-bundle `config/` | `session_controller` at Start (core `animal_state.py`) | 1 | 0077 |
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

## `daily_log.csv` (v1)

Machine-readable daily husbandry table (source of truth); a leading
`# schema_version=1` comment line precedes the header. One row per `(animal, date)`:

`animal_id, date (YYYY-MM-DD), weight_g, baseline_g, pct_baseline, water_on,
water_off, water_ml, supp_water, notes`.

`water_on`/`water_off`/`supp_water` are free text today ("6pm", "2 min"); see
FW-0029 for hardening them into real timestamps.

## `animal_state.json` (v1)

Pre-session physiological/motivational state frozen into the bundle at Start
(one per bundle):

`schema_version, session_id, animal, date, source, pre_session_weight_g,
baseline_g, pct_baseline, last_water_on, last_water_off, hours_since_water,
measured_water_ml, note`.

`source` ∈ `daily_table` | `start_dialog` | `manual` records where the values came
from. `hours_since_water` is null until water times are real timestamps (FW-0029).

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
