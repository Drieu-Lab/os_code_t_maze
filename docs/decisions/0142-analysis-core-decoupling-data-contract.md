# ADR 0142: Decouple the analysis package from the rig core via a bundle data contract

- **Status:** Accepted
- **Date:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/analysis/` (new `phase3_format.py`; `bundle_reader.py`,
  `trajectories.py`, `choice_signals.py`, `rolling_metrics.py`,
  `intervention_response.py`, `__init__.py`), `host/tmaze/core/phase3/bundle_format.py`
  (new), `host/tmaze/core/phase3/phase3_log.py` (`PROTO_VERSION`),
  `host/tmaze/core/session/session_config.py` (`format.json`),
  `host/tmaze/ui/session_controller.py`, `host/tests/test_analysis_phase3_format.py`
  (new), `host/tests/test_session_config.py`
- **Supersedes / Superseded by:** —

## Context

The offline analysis code under `host/tmaze/analysis/` (ADR-0112) is growing fast
and on a different cadence from the rig/task-control code, which is stabilizing.
We want to version the two independently and, later, move the analysis layer to
its own git repo (see the "eventual split" note under Consequences).

The blocker was that the analysis package imported the live rig core in several
places, so it could not stand alone:

- `phase3_task` outcome tokens (`CORRECT` / `INCORRECT` / `ABANDONED`)
- `trial_sequence` cue tones (`HIGH` / `LOW`)
- `maze_state` (the `ZoneMap` point-in-polygon zone classifier + zone names)
- `session_notes` (the `summary/notes_*.jsonl` parser + the `auto:` tag vocabulary)
- `tmaze.paths.phase3_recordings_roots` (where bundles live)

Inspecting a real bundle showed the *data* those imports stand for is already
written per session: `config/zones.json` (zone names + polygons + a `_schema`
string + a `version`), `config/rig_scale.json` (cm/px), the trial CSV cells
(outcome/cue/arm values) with a `# ... proto=10` header, and
`config/config_snapshot.json` (`schema_version` + per-file SHA-256). So what the
analysis imports is really the on-disk *format* (token spellings + a little pure
geometry), which the analysis layer can legitimately own a copy of - if we also
make the bundle explicitly self-describing and guard against version drift.

## Decision

Introduce a single **data-contract boundary** so nothing under `tmaze.analysis`
imports `tmaze.core` / `tmaze.paths`:

1. **`tmaze.analysis.phase3_format`** (new, stdlib + optional numpy only) owns the
   analysis-side copy of the contract: the outcome/cue/arm tokens and zone-name
   vocabulary; a vendored `ZoneMap` (`point_in_polygon` / `project_scalar` copied
   verbatim from `maze_state`, so `classify_point` matches the rig bit-for-bit); a
   `parse_notes_jsonl` mirroring `session_notes.parse_jsonl(...).entries()`; an
   env-based `phase3_recordings_roots()`; and version guards.
2. **Repoint** `bundle_reader`, `trajectories`, `choice_signals`,
   `rolling_metrics`, `intervention_response` at `phase3_format`. `LEFT_LABEL` /
   `RIGHT_LABEL` now live in `phase3_format` (single source) and are re-exported by
   `bundle_reader` so its public surface is unchanged.
3. **Harden the bundle** with a machine-readable `config/format.json`: a new pure
   core `tmaze.core.phase3.bundle_format.format_manifest()` assembles it FROM the
   live cores (single source of truth) - `format_version`, `proto`
   (`phase3_log.PROTO_VERSION`, lifted from the `proto=10` literal), `zones_version`,
   the `outcome_labels` / `cue_labels` / `arm_labels` enumerations, `zone_names`,
   and a trial-CSV `columns` dictionary. `write_config_snapshot` gains a
   `format_manifest` param and writes it verbatim to `config/format.json` (kept
   SEPARATE from the file-hash `config_snapshot.json`); the session controller
   passes `bundle_format.format_manifest()` at Start.
4. **Version guards** (`phase3_format.assert_supported`, wired into
   `bundle_reader.load_bundle`): a NEWER `format.json` `format_version` or a newer
   `zones.json` `version` raises `UnsupportedBundleFormat` (fail loudly, don't
   silently misread). A MISSING `format.json` is a legacy bundle and is allowed -
   compatibility is inferred from `zones.json` `version` + the CSV `proto=` stamp,
   so historical sessions stay readable with no backfill.
5. **Temporary drift guard** (`test_analysis_phase3_format.TestCoreParity`): while
   both layers share this repo, assert the vendored tokens / `classify_point` /
   notes parser / manifest enumerations still equal `tmaze.core`. Deleted at the
   split, when the bundle contract becomes the sole coupling.

## Alternatives considered

- **Keep importing `tmaze.core` from analysis.** Rejected: it's the exact coupling
  that blocks independent versioning and the eventual repo move.
- **PEP 420 namespace package** to keep the `tmaze.analysis` import path across two
  repos. Rejected (for the eventual split): `tmaze` is a regular package
  (`host/tmaze/__init__.py`), so this is fragile and would churn the rig packaging;
  a top-level rename (`tmaze_analysis`) is cleaner.
- **Fold the format contract into `config_snapshot.json`.** Rejected: that file is
  the file-integrity manifest (hashes); mixing the data-format contract in muddies
  both. A separate `format.json` versions on its own.
- **Hard-require `format.json`.** Rejected: it would strand every already-recorded
  bundle. Inferring from `zones.version` + `proto` keeps history readable.

## Consequences

- The analysis package no longer imports `tmaze.core` / `tmaze.paths`
  (`grep -rE "tmaze\.(core|paths)" host/tmaze/analysis` is empty); it can be
  versioned independently and lifted to its own repo with a mechanical move.
- New bundles are fully self-describing (`config/format.json`); offline readers
  (Python, MATLAB, future tools) can learn the format from the bundle itself.
- The coupling is now strictly one-way and confirmed: no rig `core/io/ui/tools`
  module imports `tmaze.analysis`.
- Cost: a small vendored copy of the tokens + geometry + notes parser now exists in
  two places. The parity test guards drift until the split; after the split the
  bundle's `format_version` / `proto` / `zones.version` guards do.

**Eventual repo split (pre-decided, deferred - see FW-0048).** When this proves
out, carve the analysis layer into its own repo: rename the import root to a
top-level `tmaze_analysis`; a fresh copy (no git-history filtering required); the
human creates/authorizes the remote; the analysis repo becomes contract-only (delete
`TestCoreParity`), relying on the `format.json` / `proto` / `zones.version` guards;
the analysis notebooks + their `PROGRAMS.md` rows and the heavy
`pandas`/`numpy`/`statsmodels` deps move with it.

## Evidence / links

- Inspected bundle `602_20260827_154811`: `config/zones.json` (`version` + `_schema`
  + `EDITABLE_ZONES` names), `task/phase3_*.csv` header (`proto=10`, tokens as
  values), `config/config_snapshot.json` (`schema_version` + SHA-256 manifest).
- Tests: `host/tests/test_analysis_phase3_format.py` (21-case parity + version-guard
  + recordings-roots suite) and the extended `test_session_config.py` pass; the full
  analysis + `test_phase3_log` / `test_session_notes` suites (154 tests) and
  `test_architecture` (core purity) stay green.
- Builds on ADR-0112 (analysis layer), ADR-0083 (package layout / core purity),
  ADR-0074 (notes JSONL), ADR-0088/0113 (recordings roots), ADR-0072 (config
  snapshot), ADR-0124/0047 (bundle anchor / proto stamp).
