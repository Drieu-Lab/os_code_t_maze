# ADR 0186: Per-animal cue->side mapping (`high_tone_is_left` in `animals.json`)

- **Status:** Accepted
- **Date:** 2026-09-20
- **Author / Agent:** Agent (Cursor) on behalf of RJ
- **Affects:** `host/tmaze/core/records/animals.py` (schema v2->v3 + tri-state
  `high_tone_is_left`, `AnimalRegistry.high_tone_is_left`, `coerce_high_tone`,
  normalize/validate/note), `host/tmaze/ui/records_manager.py` (Roster editor +
  column), `host/tmaze/ui/phase3_controller.py` (`_resolve_cue_mapping`,
  `resolved_cue_mapping`, cached registry, snapshot fields, status token),
  `host/tmaze/core/phase3/phase3_log.py` + `phase3_log_reader.py`
  (`high_tone_is_left_source` header token + snapshot field),
  `host/tmaze/ui/session_controller.py` (manifest provenance `cue_mapping` +
  summary tones), `host/tmaze/core/session/session_summary.py` (tone line);
  `os_code_t_maze_resources/records/animal/animals.json` (v3 data);
  the golden fixture; tests `test_animals` / `test_session_phase3_wiring` /
  `test_session_summary`
- **Supersedes / Superseded by:** builds on ADR-0075/0161 (animals.json v1/v2),
  ADR-0070 (host-owned phase3.json), ADR-0180 (manifest provenance),
  ADR-0182 (phase3 header tokens); Increment 6d

## Context

The cue->side mapping - which tone (HIGH / LOW) means which arm (LEFT / RIGHT) -
was a single rig-wide constant, `sequence.high_tone_is_left` in `phase3.json`
(ADR-0070). That is fine while every rat in the cohort shares one mapping (they do
today), but it has two weaknesses:

- **It is not per-animal.** A future cohort that deliberately counterbalances the
  mapping across rats (to dissociate tone from side) cannot be expressed; the
  operator would have to hand-edit `phase3.json` between animals and hope they
  never forget.
- **The bundle does not self-certify it per animal.** The mapping is recorded in
  the trial-CSV header as a value, but nothing says *whose* convention it is or
  where it was resolved from - so a mis-set `phase3.json` for one animal is
  invisible after the fact.

`animals.json` is already the single source of truth for animal *identity*
(ADR-0075), carrying aliases (v1) and `date_of_birth` (v2, ADR-0161). It is the
natural home for a per-animal task parameter that must travel with the animal.

## Decision

Add an **optional, tri-state** `high_tone_is_left` to each `animals.json` entry
(**schema v2 -> v3**) and have the rig resolve it per animal at arm, recording the
result and its source into the bundle.

**Schema (`animals.py`).** `high_tone_is_left` is `true` / `false` / `null`:
`true` = HIGH means LEFT, `false` = HIGH means RIGHT, `null` (or missing) =
**unset**. `null` is deliberately NOT the same as `false` - unset means "fall back
to `phase3.json`". `AnimalRegistry.high_tone_is_left(id)` returns the tri-state (a
real bool only when set). `coerce_high_tone` normalises a cell (bool stays,
`null`/`""` -> `None`, anything else is passed through so `validate_records` can
reject it - a hand-edit typo like the string `"true"` is a hard error, never
silently dropped). An older v1/v2 file loads unchanged (field absent = unset); a
write stamps v3 and refreshes the `_schema` note.

**Resolution (`phase3_controller`).** `_resolve_cue_mapping()` returns
`(value, source, overrides_config)`: `animals.json` wins when it records a value
for the current animal, else `phase3.json`'s default; `source` is
`"animals.json"` or `"config"`; `overrides_config` is True when a per-animal value
*disagreed* with `phase3.json`. The registry is loaded once and cached
(best-effort; an absent resources repo yields an empty registry, so the rig still
runs - ADR-0075). `_current_trial_config()` applies the resolved value to the
sequence, so the schedule is built with it. The resolution is carried to the
actuation thread on the snapshot (`cue_mapping_source` / `cue_mapping_overrides`).

**Recording (three places, one resolved value).**
- **Trial-CSV header** gains a `high_tone_is_left_source=` token next to
  `high_tone_is_left=` (tolerant `#`-comment key, read back by
  `phase3_log_reader`); `Phase3StateSnapshot.high_tone_is_left_source` carries it.
- **Manifest provenance** (ADR-0180) gains a `cue_mapping: {high_tone_is_left,
  source}` block, filled at Start from `phase3.resolved_cue_mapping()` - the single
  top-level provenance home.
- **Session summary** tone line appends `[per-animal: animals.json]` when the
  source was a per-animal override; the JSON `tones` block carries the source key.

**Operator visibility (`use-and-show`).** When `animals.json` sets the mapping the
Phase 3 status line shows `cue:H->L(animals.json)`, with a trailing `!` when it
*disagreed* with `phase3.json`, so an override is never silent.

**Editing (`Records Manager`).** The Roster tab gains a `cue (high tone)` column
and a tri-state dropdown (`(default)` / `high -> left` / `high -> right`) that maps
to `null` / `true` / `false`; Save validates and writes v3.

**No `format_version` bump.** The additions are additive and tolerant: a
`#`-comment header key (the ADR-0181 precedent for tolerant header additions
without a bump), a manifest-metadata provenance key (ADR-0180 territory, not the
on-disk task-data contract), and a summary annotation. The golden fixture is
regenerated (source token + `cue_mapping` stub) and re-copied to the analysis
mirror, which folds unknown header tokens tolerantly and does not guard the
manifest `schema_version`.

## Alternatives considered

- **Keep it `phase3.json`-only.** Rejected: can't express a counterbalanced cohort
  and the operator must hand-edit between animals; the whole point is a value that
  travels with the animal.
- **A two-state boolean (no `null`).** Rejected: then every animal must declare a
  side and there is no "use the rig default", so upgrading the file would force a
  decision for animals whose mapping is genuinely the shared default; the tri-state
  keeps `phase3.json` as the fallback.
- **Silent override on mismatch.** Rejected: an operator who left a stale
  `phase3.json` should *see* that `animals.json` is steering the run; the status
  `!` flag + the recorded source make it auditable.
- **Refuse to arm on mismatch.** Rejected: too rigid - `animals.json` is
  authoritative by design, so a disagreement is resolved, not an error; blocking
  Start would fight the very feature.
- **Record the source only in the trial header (not the manifest).** Rejected: the
  user chose a single top-level provenance home; a copied bundle then carries the
  mapping provenance in one predictable place.
- **Bump `format_version`.** Rejected: the header/manifest/summary additions are
  additive + tolerant (ADR-0181 precedent); readers that ignore the new tokens are
  unaffected.

## Consequences

- A bundle now self-certifies, per animal, which cue->side convention it ran under
  and where that came from (`animals.json` vs `phase3.json`). A counterbalanced
  cohort is expressible by setting the field per rat.
- For this cohort all three rats are set `high_tone_is_left: true` in
  `animals.json`, matching `phase3.json`, so `source` reads `animals.json` with no
  behavioural change - but the mapping is now pinned to the animal, not the rig.
- `null` vs `false` matters: an unset animal falls back to `phase3.json`. Code that
  reads the registry must treat `None` as "unset", never as `False`.
- Old bundles lack the header token / provenance key / summary annotation; readers
  default the source to `config`, which is the correct historical interpretation.

## Evidence / links

- `test_animals`: tri-state lookup, `None`-vs-`False`, normalize coercion,
  validate rejects a non-bool, v3 round-trip + older-file load.
- `test_session_phase3_wiring`: `animals.json` overrides `phase3.json` and the
  source lands in the log header + snapshot; an unset animal falls back to config
  (and `None` is not read as `False`); the resolved mapping lands in the manifest
  provenance.
- `test_session_summary`: the tone line flags a per-animal override.
- `test_golden_bundle`: the regenerated fixture (source token + `cue_mapping`
  stub) round-trips byte-for-byte; the analysis suite reads the re-copied fixture
  unchanged.
- Builds on ADR-0075/0161/0070/0180/0182/0181.
