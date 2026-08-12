# ADR 0101: Derive hours-since-water from parsed daily-table water times

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/animal_state.py` (clock-time parsing +
  `hours_since_water` derivation, `animal_state.json` schema v1 -> v2),
  `host/tmaze/ui/session_controller.py` (resolve the prior water-off + pass the
  session-start time at Session Start), `docs/records-schema.md`. Implements
  FW-0029. Reads through to `session_registry.csv` (already surfaces the field).
- **Supersedes / Superseded by:** — (a follow-up to
  [ADR-0076](0076-daily-husbandry-table-source-of-truth.md) /
  [ADR-0077](0077-pre-session-animal-state-in-bundle.md))

## Context

Time-since-water is a direct measure of an animal's motivational state for a
water-rewarded task, and a valuable per-session covariate. The pre-session
`animal_state.json` ([ADR-0077](0077-pre-session-animal-state-in-bundle.md))
already reserves a `hours_since_water` field, but it was **always null**: the
daily table records water on/off as free text ("6pm", "5:30PM", "Till Monday
evening"), so `last_water_on` / `last_water_off` were kept verbatim and never
turned into a number.

The real `daily_log.csv` shows two things that shape the design:

1. The thirst-relevant event is usually the **previous evening's** water-off. A
   row's `water_on`/`water_off` (e.g. `5:45PM`->`6:15PM`) is that date's *evening*
   supplemental-water window; the rat then does the task the next day. So for a
   morning session on date D, the last water-off is from **D-1**, not D (whose
   evening off-time hasn't happened yet).
2. The column is **messy**: legitimate irregular entries ("Till Monday evening",
   "6:30PM till Sun"), ml notes mis-filed in `water_on` ("8.46ml rig; No suppl"),
   and mixed casing/format. Any parser has to degrade gracefully, not fight this.

## Decision

Keep `water_on` / `water_off` as **tolerant free text** in the daily table (no
hard schema, no forced format) and parse them **best-effort** to derive the
number, rather than restricting what the lab can type.

Add pure helpers to `animal_state`:

- `parse_clock_time(text)` — a time-of-day from the lab's shorthand ("6pm",
  "5:30PM", "17:00", "11:30AM") using whole-string `strptime`, so a sentence or
  an ml note parses to `None` (we never pull a time out of prose).
- `water_datetime(date, time_text)` — combine a row's date with a parsed time.
- `hours_since_water(last_off, session_start)` — the gap in hours (2 dp), `None`
  if either is missing or the gap is **negative** (an off-time after the session
  is an inconsistent pairing we decline to report).
- `resolve_last_water_off(rows, session_start)` — the most recent parseable
  water-off **at or before** the session start, scanning the animal's daily rows;
  blank/free-text cells are skipped and a same-day future evening off-time is
  excluded. This is what implements "usually the previous evening".

`from_daily_row` gains optional `session_start` and `last_water_off` params and
fills `hours_since_water` when both are supplied; with neither it behaves exactly
as before (null). `last_water_on` / `last_water_off` stay the row's verbatim cells
for human reference. `animal_state.json` schema bumps **v1 -> v2** to signal the
field may now be a real number.

At Session Start, `session_controller._write_animal_state` takes the session start
as `datetime.now()` (Start runs then), resolves the prior water-off from the
loaded daily table, and passes both in. The value then flows unchanged into
`session_registry.csv`, which already reads `hours_since_water` from the bundle
([ADR-0081](0081-session-registry-analysis-entry-point.md)).

## Alternatives considered

- **Hard-validate the Entry-sheet time (reject un-parseable), as FW-0029's
  acceptance text suggested** — rejected: the real log has legitimate irregular
  entries ("Till Monday 11:30AM", weekend ad-lib spans). A hard schema would
  fight the lab; a best-effort parse that nulls out is honest and low-friction.
- **Use the current row's own `water_off`** — wrong for the common morning
  session (that off-time is the *upcoming* evening). The prior-row resolver fixes
  this while still using today's row when the session is after its off-time.
- **A full ISO datetime column in the CSV** — most precise but most typing, and it
  would break the reversible Excel round-trip's simple text cells; deferred. The
  HH:MM-against-row-date convention gives most of the value now.
- **Rewrite the messy historical `water_on` cells as part of this change** —
  declined: data fixes stay the operator's (ADR-0097). The parser just leaves
  those rows' `hours_since_water` null so the gap is visible, not silently wrong.
- **Overwrite `last_water_off` with the resolved prior-day datetime** — kept the
  verbatim today cell instead (less surprising for a human reading the file); the
  derived number lives only in `hours_since_water`.

## Consequences

- New bundles started with a daily-table row and a clean prior water-off time get
  a real `hours_since_water` in `animal_state.json` and the registry — a usable
  motivational covariate for analysis.
- Backward compatible: older v1 bundles still read (`from_dict` tolerates the
  bump); rows with free-text water cells simply keep a null. No migration.
- The session-start time is `datetime.now()` at Start (accurate to seconds),
  not a separately recorded stamp; good enough for an hours-scale covariate. If we
  later need sub-minute precision or to cross-check against a rig "water off at
  Start" event, that's a follow-up.
- We would revisit toward a real datetime column (or the rig stamping its own
  water-off) if the free-text convention proves too lossy in practice.

## Evidence / links

- Backlog item: `docs/future-work.md` FW-0029 (now Done).
- Related: [ADR-0076](0076-daily-husbandry-table-source-of-truth.md),
  [ADR-0077](0077-pre-session-animal-state-in-bundle.md),
  [ADR-0081](0081-session-registry-analysis-entry-point.md) (registry surfaces
  the field), [ADR-0097](0097-records-integrity-guardrails.md) (data fixes stay
  the operator's), [ADR-0100](0100-ad-lib-water-regime.md) (sibling husbandry
  schema step).
- Tests: `host/tests/test_animal_state.py` (`TestWaterTimeParsing`,
  `TestHoursSinceWater`, `TestResolveLastWaterOff`, `TestFromDailyRowHours`).
