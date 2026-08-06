# ADR 0076: Daily husbandry table (daily_log.csv) is the source of truth; the workbook is an entry surface + generated views

- **Status:** Accepted
- **Date:** 2026-08-05
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `os_code_t_maze_resources/records/animal/daily_log.csv` (new), `.../build_daily_log.py` (new), `.../daily_log.xlsx` (new, generated); the rig will read `daily_log.csv` (WS2 prefill, WS4 join). Legacy `water_restriction_log.xlsx` + `build_water_restriction_log.py` unchanged.
- **Supersedes / Superseded by:** —

## Context

Daily welfare/water-restriction data (weight, % baseline, water on/off, notes)
lives only in `water_restriction_log.xlsx` - a per-rat grid of weekly blocks built
for a human to read/fill, mirroring the paper form. It is a *presentation* format:
formulas, merged cells, one sheet per rat. Nothing machine-readable joins it to a
session recording except a fragile "free-text animal id + calendar date" match.

The coordinated-records upgrade needs a husbandry table the **rig and future
analyses can read directly** so each session bundle can freeze its own pre-session
state (WS2) and the session registry can join husbandry columns (WS4). But the lab
is comfortable entering data in Excel, and the existing workbook holds real
hand-entered history that must not be lost or disturbed.

This is a cross-cutting decision (it defines the recordings<->husbandry join), so
its rationale is recorded here in the code repo even though the files live in the
resources repo (which has no ADR log - only its `AGENTS.md` Change Log, which
references this ADR).

## Decision

Make a diffable CSV the source of truth and keep Excel as a validated entry
surface, all as **new** files created *alongside* the untouched legacy log:

1. **`records/animal/daily_log.csv` is the source of truth**, keyed
   `(animal_id, date)`: `weight_g, baseline_g, pct_baseline, water_on, water_off,
   water_ml, supp_water, notes`. CSV (not SQLite) so it stays diffable/inspectable
   in git and both the builder and the rig read it with stdlib `csv` - no new
   dependency, and no opaque binary blob in git for a handful of rows/day.

2. **Format version = a leading `# schema_version=N` comment line** (currently
   `1`), not a repeated column. Readers skip lines starting with `#`. Chosen over a
   per-row `schema_version` column because the version is a property of the file,
   not of each row; a comment line keeps every data row pure `(animal_id, date, …)`
   while still letting a future reader branch on the layout (matches the manifest's
   `SCHEMA_VERSION` intent in [host/session_log.py](../../host/session_log.py)).

3. **Entry UX = Option D: a flat Excel Entry sheet mapped 1:1 to the CSV**, in a
   **new** workbook `daily_log.xlsx` built by a **new** `build_daily_log.py`. The
   Entry sheet is one row per `(animal_id, date)`, one column per CSV field
   (including free-text `notes`), with an animal-id dropdown sourced from
   `animals.json` (ADR-0075), a live `pct_baseline` formula, red-flag-below-90%
   formatting, and non-negative numeric validation. It is the **human input
   surface**. `build_daily_log.py --export` reads that sheet and (re)writes
   `daily_log.csv` (recomputing `pct_baseline` from weight/baseline rather than
   trusting the formula cache). The per-rat **View** sheets are **generated,
   read-only** weekly-block mirrors of the paper form, rebuilt from the CSV and
   never hand-edited (a refresh must not clobber the Entry sheet).

4. **The legacy `water_restriction_log.xlsx` + its builder are left untouched** as
   the historical working log; `build_daily_log.py` only ever *reads* it (read-only)
   once to **seed** `daily_log.csv` with existing history (it recovered ~30 rows of
   real data across the three rats).

Scope: WS3 covers only the *daily* (per animal, per day) husbandry log.
`training_log.xlsx` is per-session, handled separately as a generated view of the
session registry (WS7 / a later ADR), not as a view of `daily_log.csv`.

## Alternatives considered

- **SQLite as the store.** Rejected: opaque in git (the resources repo versions
  data like the xlsx), needs a viewer, and is overkill for a few rows/day. CSV is
  diffable and stdlib-readable on both sides.
- **Edit the raw CSV directly (no workbook).** Rejected: loses the validation and
  affordances (dropdowns, auto %, red flags) the lab relies on, and invites
  malformed rows.
- **Parse/extend the legacy weekly-block grid as the machine format.** Rejected:
  the merged-cell, formula-driven block layout is brittle to parse and mixes
  presentation with data; better to generate that view *from* clean data.
- **Modify the legacy workbook in place.** Rejected: it holds hand-entered history
  and is the lab's current working log; the new system is additive and
  non-destructive (the file-preservation rule of this upgrade).
- **A `schema_version` column on every row.** Rejected in favour of the comment
  line (see Decision 2).

## Consequences

- The rig and analyses read one clean, versioned table; the recordings<->husbandry
  join stops depending on parsing a presentation workbook.
- Two-step entry cadence for the human: edit the Entry sheet in Excel, then run
  `build_daily_log.py --export` to publish `daily_log.csv` (and refresh Views).
  This is the cost of keeping Excel comfort while producing machine-readable data;
  it is a deliberate, documented step, not an automatic save hook.
- The CSV is the merge point: if both the CSV and the Entry sheet are edited
  independently, `--export` (Entry -> CSV) wins. Documented in the builder.
- `pct_baseline` is stored (derived) in the CSV for easy reading, but always
  recomputed from `weight_g/baseline_g` on export, so it can't drift.
- WS2/WS4 (code repo) will add a small stdlib `csv` reader (with tests) that skips
  the `#` line and reads the header; they degrade gracefully if the table is absent.

## Evidence / links

- Coordinated-records upgrade plan (WS3).
- Legacy builder: `os_code_t_maze_resources/records/animal/build_water_restriction_log.py`.
- Canonical id list it reads: ADR-0075 (`animals.json`).
- Resources `AGENTS.md` Change Log entry (2026-08-05) references this ADR.
