# ADR 0146: Split the offline analysis layer into its own repository

- **Status:** Accepted
- **Date:** 2026-08-30
- **Author / Agent:** Agent (Cursor) on behalf of Rushil (ravipjk)
- **Affects:** removal of `host/tmaze/analysis/`, `host/tests/test_analysis_*.py`,
  and `host/notebooks/` from this repo; new sibling repo `drieu_lab_analysis`
  (import root `tmaze_analysis`); new `host/tools/make_golden_bundle.py` +
  `host/tests/test_golden_bundle.py` + `host/tests/fixtures/`; `pyproject.toml`,
  `host/requirements.txt`, `PROGRAMS.md`, `os_code_t_maze.code-workspace`
- **Supersedes / Superseded by:** completes FW-0048; builds on ADR-0142

## Context

ADR-0142 decoupled the offline analysis package from the rig core (no
`tmaze.analysis` module imports `tmaze.core` / `tmaze.paths`; the coupling is the
on-disk bundle FORMAT via `config/format.json` + version guards) and PRE-DECIDED
that the analysis layer would eventually be carved into its own repository
(FW-0048). This ADR records executing that split.

The motivation is the one in ADR-0142/FW-0048: this repo (`os_code_t_maze`) is the
rig — firmware + the PC-side control/monitoring tools — kept on a lean env. The
analysis layer is a different kind of software (pandas/statsmodels/matplotlib, plus
optional pyarrow/pynwb), changes on a different cadence, and its heavy deps and
notebooks do not belong in the rig env. Keeping them together grew the rig's
dependency surface and comprehension load for no benefit to running experiments.

## Decision

Move the entire offline analysis layer out of this repo into a new,
separately-versioned repository **`drieu_lab_analysis`**
(`git@github.com:ravipjk/drieu_lab_analysis.git`), with the importable package
renamed `tmaze.analysis` → **`tmaze_analysis`** (a top-level distribution under a
`src/` layout, installed with `pip install -e .`). What moved: the 14 analysis
modules, their 11 tests, both analysis notebooks (`animal_progress`,
`grant_figures/proposal_figures`), and the seeded data-contract ADRs. Notebook
imports were repointed to `tmaze_analysis`; the grant notebook's two rig imports
(`maze_state`, `resources_root`) were repointed to the vendored `phase3_format`
and an env-based local resolver.

The rig repo keeps only the WRITER side of the contract:
`core/phase3/bundle_format.py` (assembles `config/format.json`).

**Cross-repo drift guard (replaces the temporary `TestCoreParity`).** ADR-0142's
same-repo parity test asserted the vendored mirror equalled `tmaze.core`; that
test can't exist once `tmaze.core` isn't importable. In its place, a shared GOLDEN
FIXTURE (FW-0050): `tools/make_golden_bundle.py` writes a tiny, complete session
bundle with the REAL rig write path (`Phase3Log.render_csv()` +
`bundle_format.format_manifest()`), fully hard-coded so regeneration is
byte-identical. It is committed here under `tests/fixtures/` and copied into the
analysis repo. Both sides test their half of the contract against it:

- **this repo:** `test_golden_bundle.py` regenerates the fixture and diffs it
  (line-ending-normalised) against the committed copy — a change to the on-disk
  format shows up as a fixture diff, the cue to bump `FORMAT_VERSION` and re-issue
  the fixture;
- **analysis repo:** the reader + exporter tests round-trip the same fixture with
  value-level assertions.

## Alternatives considered

- **Preserve git history via `git filter-repo` / subtree split.** Rejected for
  now: the analysis layer is young, the extra tooling/coordination outweighed the
  value, and the user chose a clean single-commit import. History still exists in
  this repo's log up to the split.
- **Keep the analysis package here but as an optional extra.** Rejected: it still
  bloats the rig env/tests and blurs the repo's purpose; ADR-0142/FW-0048 already
  decided on a separate repo.
- **A monorepo with two packages.** Rejected: separate version cadences and the
  desire to keep the rig lean favour two repos; the golden fixture gives the
  cross-repo contract test a monorepo would have given for free.
- **Namespace package (`tmaze.analysis` across two distributions, PEP 420).**
  Rejected: keeping the `tmaze` name split across repos invites import-shadowing
  confusion; a clean top-level `tmaze_analysis` is unambiguous.

## Consequences

- The rig repo is leaner: no pandas/statsmodels/pyarrow/pynwb, no analysis tests
  or notebooks. `PROGRAMS.md`/`requirements.txt`/`pyproject.toml` updated; the
  multi-root `.code-workspace` gains the new sibling so both open together.
- The contract is now enforced across a repo boundary by the golden fixture rather
  than a shared import. Cost: a deliberate format change is a two-step handshake —
  bump `FORMAT_VERSION`, regenerate `tools/make_golden_bundle.py`'s fixture, and
  copy it to the analysis repo. The two version guards + the fixture diff make a
  forgotten step fail loudly rather than silently misread.
- Main risk (named in ADR-0142): silent format drift once `TestCoreParity` is
  gone. Mitigated by the runtime version guards (declared changes) + the golden
  fixture (undeclared semantic changes).
- The analysis repo owns its own ADR numbering going forward; the data-contract
  ADRs were copied there (keeping their numbers) as reference, with this repo
  remaining their source of truth.

## Evidence / links

- New repo: `git@github.com:ravipjk/drieu_lab_analysis.git` (initial import
  commit `436dd53`).
- Builds on [ADR-0142](0142-analysis-core-decoupling-data-contract.md); completes
  FW-0048; the fixture is FW-0050.
- Rig-side artifacts: `host/tools/make_golden_bundle.py`,
  `host/tests/test_golden_bundle.py`, `host/tests/fixtures/600_20260101_120000/`.
