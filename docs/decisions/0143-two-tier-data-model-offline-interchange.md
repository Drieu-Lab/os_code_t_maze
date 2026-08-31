# ADR 0143: Two-tier data model — crash-safe text capture + offline Parquet/NWB interchange

- **Status:** Proposed
- **Date:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** the session-bundle contract (`docs/design/data-format-review.md`);
  new offline exporters under `host/tmaze/analysis/` (`export_parquet.py`,
  `export_nwb.py`); `PROGRAMS.md`; `host/requirements.txt` (optional deps). No
  change to the live rig write path.
- **Supersedes / Superseded by:** —

## Context

The on-disk session bundle is now the *contract* between the rig (writer) and the
offline analysis (reader), which ADR-0142 decoupled and which will move to its own
repo (FW-0048). The bundle's raw capture is plain text (per-trial CSV, config/
notes/manifest JSON, serial CSV, video + sidecar), written live while an animal is
on the maze, atomically (temp file + rename), and self-describing since ADR-0142
(`config/format.json` + read-time version guards). See the full evaluation in
[`docs/design/data-format-review.md`](../design/data-format-review.md).

Two goals now pull the format in opposite directions:

- **Live capture** wants append-friendly, crash-proof, human-inspectable files.
  The rig writes during behaviour with animals present; losing a session is the
  worst outcome. Plain atomic text already satisfies this and must not regress.
- **Analysis, MATLAB interop, and data sharing** want *typed*, *compressed*,
  *standardized* files: cheap cross-session pooling, native typing in MATLAB
  (`parquetread`), and a way to sit behavioural data next to the Neuropixels
  recordings and share it (the community uses NWB + the DANDI archive).

Forcing one format to serve both either endangers sessions (a heavy/appending
binary on the hot path) or hobbles analysis (re-parsing wide `#`-header CSVs and
re-typing every column in every tool). The scorecard in the design review grades
live-capture/provenance **Strong** but Python+MATLAB interop and cross-session
pooling only **Fair** — the open weaknesses are all on the analysis/interop side.

## Decision

Adopt a **two-tier data model**:

1. **Tier 1 — live raw capture (unchanged).** Keep the atomic text bundle
   (CSV/JSON/JSONL + video) exactly as it is on the rig hot path. The ADR-0142
   `config/format.json` is the machine-readable interface. Improvements to Tier 1
   are additive only (see ADR-0144) and never add a heavy dependency or a
   non-append write to the hot path.

2. **Tier 2 — offline derived interchange (new).** Add an **offline** export step
   that reads a *finished* bundle and produces:
   - **Parquet** tidy tables (`trials.parquet`, `excursions.parquet`,
     `tracking.parquet`, `sessions.parquet`) — typed, compressed, keyed by
     `session_id`/`animal`/`trial` for trivial pooling across sessions and native
     reading in Python (`pandas.read_parquet`) and MATLAB (`parquetread`).
   - **NWB** (Neurodata Without Borders) — one `.nwb` per session with
     `session_start_time` from the manifest `t0`, a standard trials table, and
     behavioural time series (position, cues, licks). This is the archival /
     interchange target that will later unify behaviour with the Neuropixels
     recordings and support DANDI sharing.

The derived layer is **regenerable at any time from the raw bundle** (like
`session_registry.csv` today, ADR-0081), so the text bundle remains the single
source of truth and the two tiers never need to be kept in sync by hand.

The exporters live under `host/tmaze/analysis/` (not `tmaze.tools`) so they sit on
the *reader* side of the ADR-0142 boundary — they import the bundle contract
(`bundle_reader`, `trajectories`, `phase3_format`), never `tmaze.core` /
`tmaze.paths` — and therefore travel with the analysis repo at the FW-0048 split.
Their heavy dependencies (`pyarrow`, `pynwb`) are guarded at import (the pattern
`tmaze.ui.gui_deps` uses for optional deps) and listed only as *optional* extras,
so the lean rig environment never needs them.

This ADR ships the decision plus **read-only proof-of-concept** exporters; it does
not modify the live write path and does not install the optional deps into the rig
env.

## Alternatives considered

- **Keep-and-improve only (text everywhere).** Necessary but insufficient: it does
  nothing for cheap cross-session pooling, native MATLAB typing, or the
  behaviour+ephys/DANDI story. Adopted for Tier 1 (additively), not as the whole
  answer.
- **Put a columnar/hierarchical format on the live path (Parquet/HDF5 as raw
  capture).** Rejected: appends/concurrency on a live binary risk partial-file
  loss on a crash and are far less inspectable/recoverable than per-stream atomic
  text. Live-write safety is paramount.
- **Single NWB file as the primary record.** Rejected as the *raw* format: `pynwb`
  is a heavy dependency to put on the rig, and NWB's model wants a deliberate
  mapping and one timing convention — good for a *derived* archival copy, wrong for
  the hot path.
- **HDF5 as the interchange layer instead of Parquet+NWB.** Rejected: for pooling,
  Parquet is cheaper and more directly typed in both Python and MATLAB; for the
  neuroscience/archival role, NWB (HDF5 underneath) adds the standard schema HDF5
  alone lacks. Plain HDF5 would sit awkwardly between the two.

## Consequences

- **Live-write safety is untouched;** analysis and interop gain typed, poolable,
  standard files without any hot-path risk.
- **No history is stranded:** the exporters are pure readers and rely on ADR-0142's
  legacy inference, so any recorded bundle (including archive-drive sessions,
  ADR-0113) can be converted.
- **Two representations exist,** but the derived one is regenerable, so drift is a
  non-issue (re-export to refresh).
- **Two optional dependencies** (`pyarrow`, `pynwb`) enter the *analysis* extras
  only; the rig env stays lean (comprehension-debt cost is bounded and opt-in).
- **Follow-ups:** productionize the exporters and add the Neuropixels stream + a
  DANDI path when ephys recording begins (FW-0051); enrich the column dictionary
  with units/dtype so the exporters can type and annotate columns from the
  contract rather than a local table (ADR-0144); add a golden fixture bundle so the
  contract (and the exporters) are testable across the repo split (FW-0050).
- **Revisit if:** MATLAB drops cheap Parquet support, or the lab standardizes on a
  different interchange format for ephys.

## Alternatives to the two POC formats

Parquet and NWB are complementary, not competing: Parquet is the cheap
pooling/typing layer for day-to-day analysis; NWB is the standardized archival
layer that unifies behaviour with ephys and enables sharing. Shipping both as POCs
lets the lab feel each before committing either to production.

## Evidence / links

- Design review + scorecard: [`docs/design/data-format-review.md`](../design/data-format-review.md).
- Inspected bundle `601_20260828_180049` (~12.15 GB; video dominates footprint).
- Builds on ADR-0142 (analysis decoupling + `config/format.json`), ADR-0124/0047
  (session anchor / proto stamp + atomic CSV), ADR-0049 (bundle tree), ADR-0072
  (config snapshot + hashes), ADR-0081 (regenerable cross-session index as
  precedent for a derived layer), ADR-0112 (analysis layer), ADR-0113 (archive
  roots), ADR-0083 (package layout / core purity).
- Related future work: FW-0048 (analysis repo carve-out), FW-0049 (relative
  manifest paths), FW-0050 (golden fixture + migration tooling), FW-0051 (NWB
  productionization + DANDI/ephys), FW-0052 (video footprint policy).
