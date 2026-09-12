# ADR 0161: Record each animal's date of birth in `animals.json` (schema v2)

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/records/animals.py` (`ANIMALS_SCHEMA_VERSION`
  2, `DOB_FIELD`, `AnimalRegistry.date_of_birth` / `age_days`,
  `normalize_records`, `validate_records(today=)`, `_read_meta`),
  `host/tmaze/core/records/records_audit.py` (`animals_missing_dob`,
  `missing_date_of_birth` section), `host/tmaze/ui/records_manager.py`
  (Roster tab), `docs/records-schema.md`
- **Supersedes / Superseded by:** — (refines ADR-0075)

## Context

`animals.json` (ADR-0075) is the single source of truth for animal identity:
`{id, aliases}` per rat. It carries nothing else about the animal. The one
biographical fact every downstream reader eventually wants is **age** - the
grant's analyses compare learning across animals, the ACUC protocol
(RA23A232) sets age/weight expectations, and the welfare dashboard's
baseline-weight logic has no idea whether a rat is still growing. Today the
birth dates live only on the animal-facility cage cards and in the paper
protocol records; nothing in either repo can compute an age for a session
or a daily row, and nothing flags that the fact is missing.

The 2026-09-11 records review listed this under the identity file's gaps
and deferred it to the schema phase of the hardening plan (ADR-0159's
consequences name it).

## Decision

**One optional field, `date_of_birth`, per animal - `YYYY-MM-DD` or blank -
and age is always derived from it, never stored.**

- `animals.json` goes to **schema v2**: each entry is
  `{id, aliases, date_of_birth}`. Blank (`""`) means "not yet recorded" and
  is a legal state, so the roster can be saved before every date has been
  looked up. A v1 file (no field) still loads; a missing field reads as
  blank.
- `validate_records` rejects a non-blank value that isn't a real ISO date
  or lies in the future (a `today` parameter keeps the check testable).
  Nothing else is validated - a birth date decades ago is a data-entry
  question for the audit/human, not a write refusal.
- `AnimalRegistry.date_of_birth(id_or_alias)` returns the stored string;
  `age_days(id_or_alias, on_date)` does the one piece of arithmetic
  (negative when the date precedes birth, so a bad entry is visible rather
  than clamped). Age is **never written** to any file - it is a function of
  the birth date and the row's date, and storing it would create a second
  copy to drift.
- A write **stamps the current `schema_version`** and refreshes the stock
  `_schema` note (recognised verbatim) to the v2 wording; a hand-edited
  note is preserved. Before this, `write_records` copied the old version
  number forward, which would have produced a "v1" file containing v2
  fields.
- The **records audit** gains `missing_date_of_birth` - one finding per
  canonical id with no date - with the fix hint pointing at the Roster tab.
  It is skipped when `animals.json` is unavailable.
- The **Roster tab** gains a "date of birth" entry (checked at *Add* so a
  typo is caught before several staged edits are refused together at Save),
  a `date of birth` column, and a derived `age today` column
  (`N d (W.w wk)`). The change log records `date_of_birth` as its own field
  per animal (ADR-0155).
- The live `animals.json` is **not** edited by this change: it remains a v1
  file until the next Roster save, and the audit now lists all three rats as
  missing a birth date - the human's to-do, filled from the facility records.

## Alternatives considered

- **Put the birth date in `daily_log.csv` or `animal_state.json`.**
  Rejected: it is a per-animal constant, and both of those are per-day /
  per-session; identity data belongs in the identity file.
- **Store `age_weeks` alongside.** Rejected: a derived value in a file is a
  second copy that goes stale; one date plus arithmetic is the only
  non-drifting form.
- **Make the field required.** Rejected: it would block saving the roster
  (and adding a new rat at arrival) until the date is at hand; the audit
  section provides the pressure without the blockade.
- **Add sex / strain / vendor now.** Deferred: the protocol fixes strain and
  vendor for these cohorts, and no reader asks for them yet; adding fields
  that nothing reads is comprehension debt. Recorded as FW-0065 rather than
  done here.

## Consequences

- `records_check` reports 3 more findings until the birth dates are entered
  (one Roster edit each).
- `animals.load_records` entries now always carry a `date_of_birth` key;
  the builders in the resources repo read only `id`, so they are unaffected
  (they gain the field naturally when Phase 6 makes them import `tmaze`).
- A later change can show age on the Sessions tab metrics block and the
  dashboard status strip (`age_days` against the row's date); this ADR only
  lands the field and its integrity (FW-0065).

## Evidence / links

- ADR-0075 (identity file), ADR-0155 (change log), ADR-0159 (the
  deferral), ADR-0160 (the companion Phase 5 schema change).
- `records_check` on 2026-09-11 after this change: `missing_date_of_birth`
  = 600, 601, 602.
- Records Manager hardening plan, Phase 5c/d.
