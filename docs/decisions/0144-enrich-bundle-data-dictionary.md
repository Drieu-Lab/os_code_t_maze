# ADR 0144: Enrich the bundle data dictionary with units and data types

- **Status:** Proposed
- **Date:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/phase3/bundle_format.py` (the `format.json`
  `columns` dictionary), a generated `docs/data-dictionary.md`, and the analysis
  readers/exporters that would consume the richer metadata. No live-write-path
  change is made *by this ADR* (it is a proposal); the proof-of-concept exporters
  carry a local units/dtype table in the meantime.
- **Supersedes / Superseded by:** —

## Context

ADR-0142 made the bundle self-describing: `config/format.json` now enumerates the
outcome/cue/arm tokens, the zone names, the protocol version, and a **one-line
meaning per trial-CSV column** (`bundle_format._COLUMN_DOC`). That is a big step —
a reader can learn *what* a column means from the bundle itself.

Two gaps remain for a reader that must turn the bundle into typed, tidy tables
(the Tier-2 Parquet/NWB exporters of ADR-0143, and MATLAB users):

- **No units.** `bias` is a signed rate, `prime_ms` is milliseconds, `cue_dwell_s`
  is seconds, `*_iso` are host wall-clock ISO strings, `lick_fw_ms` is a firmware
  clock in ms — but the contract records none of this, so every reader hard-codes
  it (and MATLAB re-implements it). Units live only in prose in the CSV `#`-header
  notes and in ADRs.
- **No data types.** The analysis reader loads *every* column as text and then
  selectively coerces a few to numeric/datetime (`bundle_reader._COLUMN_DEFAULTS`
  + ad-hoc `pd.to_numeric`/`to_datetime`). An exporter that wants a typed table has
  to duplicate those coercion rules, which will drift once analysis is a separate
  repo (FW-0048).

The design review grades "one data dictionary (meaning + units + dtype)" only
**Fair** for exactly these reasons — see
[`docs/design/data-format-review.md`](../design/data-format-review.md).

## Decision

Extend the bundle's machine-readable data dictionary so each trial-CSV column
carries **meaning + units + dtype** (not just meaning), and generate a
human-readable dictionary doc from the same single source.

Concretely (proposal for a later, additive change — see "Consequences" on why it
is deferred):

1. **Enrich `bundle_format`.** Replace the one-line `_COLUMN_DOC[str] -> str` with
   a per-column record `{meaning, unit, dtype}` (e.g. `bias` →
   `{meaning: "...", unit: "rate", dtype: "float"}`; `trial_start_iso` →
   `{unit: "iso8601_local", dtype: "datetime"}`; `lick_fw_ms` →
   `{unit: "ms_firmware_clock", dtype: "int"}`). `format_manifest()` emits the
   richer `columns` map. A missing entry still degrades to
   `{meaning: "", unit: "", dtype: "str"}` so adding a column never breaks the
   contract.
2. **Bump `format_version`** (1 → 2) because the `columns` value shape changes from
   a string to an object. Readers already guard on `format_version` (ADR-0142), so
   an older reader against a newer bundle fails loudly rather than misreading; the
   analysis mirror's `FORMAT_VERSION` is bumped in lockstep.
3. **Generate `docs/data-dictionary.md`** from `bundle_format` (a small
   `python -m tmaze.tools.build_data_dictionary`-style rebuild, idempotent), so the
   human-facing dictionary can never drift from what the rig writes — the same
   single-source-of-truth pattern used for the records schema (ADR-0081/0085).
4. **Consume it on read.** `bundle_reader` and the ADR-0143 exporters type/annotate
   columns from `format.json` instead of hard-coding coercions, so units flow into
   Parquet column metadata and NWB `TimeSeries.unit` automatically.

Until this lands, the ADR-0143 **proof-of-concept exporters carry a local
units/dtype table** (clearly labelled) so they can demonstrate typed output
without touching the rig write path.

## Alternatives considered

- **Leave the dictionary meaning-only.** Rejected: it pushes units/dtype knowledge
  into every reader and every tool (Python, MATLAB, NWB), which is exactly the
  drift ADR-0142 set out to prevent — the units are part of the on-disk *format*.
- **Put units/dtype in a separate hand-written doc only.** Rejected: a hand-kept
  doc drifts from `phase3_log.COLUMNS`. Generating it from `bundle_format` keeps
  one source of truth.
- **Add units/dtype without bumping `format_version`.** Rejected: changing the
  `columns` value shape is a breaking change for a strict reader; the version guard
  exists precisely to make this safe and loud.
- **Full schema language (JSON Schema / Frictionless / Arrow schema file).**
  Deferred: heavier machinery than the contract needs today; the `{meaning, unit,
  dtype}` record covers the concrete need. Revisit if streams beyond the trial CSV
  (sidecar, serial, excursions) also need formal typed schemas.

## Consequences

- **Readers and exporters become simpler and drift-proof:** typing and units come
  from the bundle, so Parquet gets typed columns + unit metadata and NWB gets
  correct `unit` fields for free.
- **A human-readable `docs/data-dictionary.md`** gives the mixed-background lab one
  place to look up what every column means and its units.
- **Cost:** a `format_version` bump and a coordinated update of the analysis
  mirror; the enrichment touches the manifest the rig writes at Session Start.
  Because this task must not modify the live write path, **implementation is
  deferred** to a follow-up change; this ADR records the decision and the POC
  exporters demonstrate the value with a local table.
- **Revisit if:** additional streams need formal schemas (then consider a schema
  language), or if a stream's units become session-dependent (unlikely).

## Evidence / links

- Current one-line dictionary: `host/tmaze/core/phase3/bundle_format.py`
  (`_COLUMN_DOC`, `format_manifest`), mirrored/guarded in
  `host/tmaze/analysis/phase3_format.py` (`FORMAT_VERSION`, `assert_supported`).
- Ad-hoc typing on read today: `host/tmaze/analysis/bundle_reader.py`
  (`_COLUMN_DEFAULTS`, `pd.to_numeric`/`pd.to_datetime`).
- Design review + scorecard: [`docs/design/data-format-review.md`](../design/data-format-review.md).
- Builds on ADR-0142 (self-describing bundle + version guards) and ADR-0143
  (two-tier exporters that consume the dictionary); precedent for generated,
  single-source docs: ADR-0081/0085 (records schema).
