# ADR 0145: NWB as the primary derived/sharing format, via an offline preprocessing pipeline

- **Status:** Proposed
- **Date:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** the analysis-side derived layer (the ADR-0143 exporters →
  `export_nwb.py` / `export_parquet.py`, moving to the analysis repo per FW-0048);
  a future `preprocess_session` pipeline + its tests; the golden fixture (FW-0050)
  as the regression anchor. No change to the live rig write path.
- **Supersedes / Superseded by:** refines the NWB role of
  [ADR-0143](0143-two-tier-data-model-offline-interchange.md) (Parquet's
  convenience role is unchanged).

## Context

ADR-0143 established the two-tier model: the crash-safe text bundle stays the
source of truth on the rig hot path (Tier 1), and an offline derived layer (Tier 2)
produces typed interchange files. It framed **Parquet** (pooling/MATLAB) and
**NWB** (archival) as *proof-of-concept* peers. Since then the lab has decided that
**NWB is the format it will adopt going forward** for community standards, sharing
(DANDI), and eventually unifying behaviour with the Neuropixels recordings.

The lab also anticipates a **preprocessing step**: something that takes a raw
session bundle and produces a per-session NWB (and, as a convenience, Parquet),
optionally applying additional preprocessing (filtering, derived features, spike
alignment when ephys lands). That is Tier 2 growing from two hand-run exporters
into a real, tested pipeline.

Two nearby pieces shape how this must be built:

- The **analysis repo split** (FW-0048): the pipeline lives on the analysis side,
  importing only the on-disk bundle contract (`config/format.json` + version
  guards; ADR-0142), never the rig core.
- The **golden fixture** (FW-0050, landing with the split): a committed, rig-writer-
  regenerated bundle that replaces the temporary core-parity test as the drift
  tripwire. It pins the contract to a committed *artifact* and is the primary
  consumer-under-test for the exporters — and therefore for the pipeline.

## Decision

1. **Adopt NWB as the PRIMARY derived/sharing format.** The offline derived product
   of record is a per-session `.nwb`: a standard trials table + behavioural time
   series (position, cue/lick events), anchored at the session `t0` (ADR-0124) so
   Neuropixels data can later share one clock. **Parquet remains a convenience
   derivative** (Python/MATLAB pooling), regenerable from the same pass — not the
   primary sharing artifact.

2. **Introduce an offline preprocessing pipeline** (analysis repo):
   `preprocess_session(bundle) -> session.nwb (+ optional .parquet)`. It is
   **read-only over the bundle and fully regenerable** (re-run to refresh; never
   hand-edit outputs), with a **declarative registry of OPTIONAL preprocessing
   steps**, each deterministic given `(bundle, step_config)`. A batch driver runs it
   over the recordings roots.

3. **Emit a text provenance sidecar.** Every run stamps the applied steps + their
   parameters + the code/tool version + the bundle `format_version` into a text
   JSON sidecar next to the outputs (mirroring the exporters' `data_dictionary.json`
   emit-side contract test), so the record is value-comparable and reproducible for
   sharing — not buried only inside the `.nwb`.

4. **Key idempotency on INPUTS, not outputs.** A "skip-if-fresh" cache hashes the
   input bundle content + the step config, never the output bytes: NWB/Parquet embed
   library versions, compression, and (NWB) a creation timestamp, so output bytes
   vary run-to-run.

5. **Anchor tests on the golden fixture (FW-0050).** Regression tests round-trip the
   committed fixture through the pipeline and assert derived *values* + text-JSON
   equality of the dictionary/provenance sidecars; they **never byte-compare**
   `.nwb`/`.parquet`. The fixture must carry every stream a step reads (so adding a
   step that reads a new bundle field means extending the rig-side generator,
   regenerating the fixture, and copying it over — the same cross-repo handshake as
   a `format_version` bump). The lightweight synthetic-bundle builder is kept only
   for edge cases (legacy/no-`format.json`).

This ADR records the direction; the implementation is deferred (FW-0051) until
after the repo split and, for the ephys parts, until Neuropixels recording begins.

## Alternatives considered

- **Keep NWB as an archival-only POC (ADR-0143 scope).** Rejected: the lab's
  decision to standardize on NWB for sharing means it needs first-class, tested
  production, not a hand-run POC.
- **Make Parquet the primary product.** Rejected: Parquet is a general table format
  with no neuroscience schema, no ephys unification, and no sharing/archive story;
  it stays the convenience pooling layer.
- **Per-step bespoke scripts instead of a pipeline + registry.** Rejected: no
  uniform provenance, and optional steps become hard to test deterministically. A
  declarative registry gives one place to enumerate + test steps.
- **Hash outputs for idempotency.** Rejected: NWB/Parquet bytes are
  nondeterministic; hash the inputs instead.
- **Build the pipeline in the rig repo.** Rejected: it is analysis-side work with
  heavy deps (`pynwb`/`pyarrow`); it belongs in the analysis repo (FW-0048),
  reading only the bundle contract.

## Consequences

- **A clear derived-format target** for the lab: NWB for sharing/archival/ephys,
  Parquet for cheap pooling, both regenerable, the text bundle still the source of
  truth.
- **Cross-repo coupling to manage:** the golden fixture + `format_version` handshake
  (rig authoritative; analysis copies). Each new pipeline step that reads a new
  bundle field requires extending the rig-side golden generator + regenerating and
  copying the fixture.
- **Reproducibility is at the value level, not byte level:** shared `.nwb`s will
  differ byte-to-byte across runs/library versions; the provenance sidecar + the
  round-trip tests are how reproducibility is demonstrated.
- **Deferred work:** the pipeline, the DANDI upload path, and the ephys electrical-
  series/probe-geometry mapping land in FW-0051, gated on the split and the ephys
  pipeline.
- **Revisit if:** the community moves off NWB, or MATLAB/DANDI tooling assumptions
  change.

## Evidence / links

- Builds on ADR-0143 (two-tier model; refines its NWB role), ADR-0144 (units/dtype
  feed NWB `unit` fields + typed Parquet), ADR-0142 (bundle-as-contract + version
  guards), ADR-0124 (session `t0` anchor).
- Golden-fixture design + determinism/cross-repo rules: the analysis-repo-separation
  plan (Phase 2, "Golden fixture + exporter integration"; FW-0050).
- Related future work: FW-0048 (analysis repo carve-out), FW-0050 (golden fixture),
  FW-0051 (the pipeline + DANDI/ephys productionization this ADR directs).
- POC exporters this productionizes: `host/tmaze/analysis/export_nwb.py`,
  `export_parquet.py`, `bundle_data_dictionary.py`.
- External: Neurodata Without Borders (`pynwb` / `matnwb`) and the DANDI archive.
