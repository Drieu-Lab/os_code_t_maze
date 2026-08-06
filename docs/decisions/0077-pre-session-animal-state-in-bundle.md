# ADR 0077: Capture pre-session physiological/motivational state in the session bundle

- **Status:** Accepted
- **Date:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/animal_state.py` (new pure core), `host/daily_log.py` (new pure reader), `host/test_animal_state.py`, `host/test_daily_log.py`, `host/session_controller.py` (Session Start writes `config/animal_state.json`)
- **Supersedes / Superseded by:** —

## Context

A session bundle is internally consistent (everything keyed by `session_id`) but
its *motivational context* - the animal's weight, % of free-feeding baseline, and
when water was last available - lives only in the husbandry spreadsheet, joined to
the recording by the fragile "animal id + date" key (ADR-0075/0076). To analyse a
session later (or to feed a learning sandbox) you have to go find the right
spreadsheet row and hope the id/date match. If the husbandry file is later edited,
the number you'd reconstruct can even change.

For a water-rewarded task these scalars are not incidental: motivation scales with
water restriction, so weight/% baseline/time-since-water are covariates of
behaviour. The coordinated-records upgrade wants every bundle to stand on its own.

## Decision

At Session Start, freeze the pre-session state into the bundle as
`config/animal_state.json`, its **own** first-class manifest stream (key
`animal_state`), following the `session_notes` precedent (ADR-0074): a pure
dataclass core (`host/animal_state.py`) that renders/parses JSON with no file I/O
or Tk, and a thin write in `session_controller._start_session` (atomic temp +
`os.replace`, registered via `SessionManifest.set_stream`).

Schema (`animal_state.py`, `schema_version` 1):
`{schema_version, session_id, animal, date, source, pre_session_weight_g,
baseline_g, pct_baseline, last_water_on, last_water_off, hours_since_water,
measured_water_ml, note}`. `schema_version` + `session_id` make the file
self-describing on its own, matching the manifest's `SCHEMA_VERSION` pattern.

Prefill order (`source` records which happened):
1. `daily_table` - today's row from the resources `daily_log.csv` (ADR-0076), read
   by a new pure `host/daily_log.py` (stdlib `csv`, via the ADR-0075 sibling-path
   resolver, best-effort).
2. `start_dialog` - if today's row is absent, prompt for the pre-session weight
   (baseline recovered from the animal's most recent daily row so % baseline still
   computes).
3. `manual` - if the operator skips, write a minimal record so the bundle still
   carries the stream uniformly.

Division of labour: `animal_state.json` holds the structured husbandry **scalars**;
timestamped observations/interventions stay in the notes log (ADR-0074). It is
**not** routed through `config_snapshot.json` (ADR-0072), which indexes the
*copied host `*.json` files* - this is session-authored data, so it is its own
stream, keeping the two indexes clean.

## Alternatives considered

- **Route it through `config_snapshot.json`.** Rejected: that index is
  specifically the copied host config files; mixing session-authored husbandry data
  into it muddies both. Its own notes-style stream is cleaner and matches ADR-0074.
- **Leave state in the spreadsheet and join by date at analysis time.** Rejected:
  that is exactly the fragile key this upgrade is retiring; a later edit of the
  husbandry file would silently change a "past" session's reconstructed state.
- **Hard-require a weight at Start (modal, blocking).** Rejected: the rig must keep
  running; the dialog is skippable (source=manual) and a bundle always gets the
  stream regardless.
- **Store water on/off as real ISO timestamps + compute `hours_since_water` now.**
  Rejected for this change: the daily table records water times as free text
  ("6pm", "Till Monday evening"), so they are kept verbatim and
  `hours_since_water` is left null rather than invented. Hardening those into real
  timestamps is deferred (FW-0029).

## Consequences

- Every new bundle carries its pre-session state, so analysis (and a later sandbox
  loader) reads it straight from the bundle - the recordings<->husbandry join is
  captured once, at capture time, and is robust to later husbandry edits.
- The code repo now reads the resources daily table (`host/daily_log.py`), reusing
  the ADR-0075 resolver; it degrades gracefully (no table -> Start dialog -> manual).
- Water timing remains coarse (free-text pass-through, null hours); FW-0029 tracks
  hardening it.
- Manifest `schema_version` is unchanged (still 2): `animal_state` is a new *stream
  key* in the open `streams` map, not a manifest layout change - the same way the
  `notes_events` stream was added under ADR-0074.

## Evidence / links

- Coordinated-records upgrade plan (WS2).
- Precedent: ADR-0074 (`session_notes` as an in-bundle stream), ADR-0072
  (`config_snapshot` is the copied-config index, deliberately separate).
- Source of prefill: ADR-0076 (`daily_log.csv`), keyed by the ADR-0075 canonical id.
- Deferred: FW-0029 (real water timestamps + `hours_since_water`).
- Tests: `host/test_animal_state.py`, `host/test_daily_log.py`. Verified end-to-end
  against the real `daily_log.csv` (resolver + prefill produce a valid state).
