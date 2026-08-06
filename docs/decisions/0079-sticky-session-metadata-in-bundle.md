# ADR 0079: Capture sticky per-session metadata (trainer / stage / reward type) in the bundle

- **Status:** Accepted
- **Date:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_meta.py` (new pure core), `host/test_session_meta.py`, `host/session_meta.json` (sticky-defaults seed), `host/session_controller.py` (Session-panel fields + `config/session_meta.json` write), `PROGRAMS.md`
- **Supersedes / Superseded by:** —

## Context

A session bundle now captures the machine side well (task params, pre-session
animal state, streams) but the few **human** facts the paper training log records
- who ran the session (**trainer**), what training **stage** it was, and what
**reward type** was used - live only in the operator's head or the spreadsheet,
re-typed per session. The coordinated-records upgrade wants each bundle to carry
these too, and the later generated training-log view (WS7) + registry (WS4) to
read them straight from the bundle rather than by hand.

These fields are mostly low-entropy: the trainer and reward type rarely change
day-to-day (**sticky**), and the stage is just the phase the rig is running
(**derivable**). Re-typing them every session is friction that invites blanks and
typos.

## Decision

Capture them at the source in the Session panel and let the rig remember them
(variant A3 of the plan), split into a pure core + a thin Tk shell like the other
records work:

- **Pure core `host/session_meta.py`.** A `SessionMeta` dataclass (schema v1,
  `{schema_version, session_id, trainer, stage, stage_source, reward_type}`) that
  renders/parses JSON with no file I/O or Tk, plus a `SessionMetaDefaults`
  dataclass with tolerant `load_defaults` / atomic `save_defaults` (the
  `note_tags.py` pattern, ADR-0074). `build()` decides the stage: the operator's
  override when set (`stage_source="override"`), else the auto-derived phase
  (`stage_source="auto"`), so a reader can always tell a typed stage from a
  derived one.
- **Thin shell wiring in `session_controller`.** A single compact row on the
  Session panel: remembered `Trainer` + `Reward` entries, a read-only auto-derived
  `Stage` label, and an optional sub-stage override combobox
  (Habituation/Shaping/Reversal/Probe, free-text allowed). At Start it writes
  `config/session_meta.json` into the bundle as its **own** manifest stream (key
  `session_meta`, atomic temp + `os.replace`), exactly like `animal_state.json`
  (ADR-0077), and persists trainer + reward type to `host/session_meta.json` for
  next time.

Stage auto-derivation is deliberately simple: the Session panel drives the Phase 3
task and Start already requires it armed (ADR-0060), so a wired Phase 3 controller
means stage "Phase 3"; headless/standalone use leaves it blank for the override.

The sticky defaults store **only** trainer + reward type. The stage is
**not** persisted - it is re-derived every session - so a stale override can never
silently mislabel a later run.

Kept as its own stream (not merged into `animal_state.json`) per WS2's division of
labour: `animal_state.json` holds physiological/husbandry scalars, `session_meta`
holds the human bookkeeping. Both are session-authored streams, deliberately not
routed through `config_snapshot.json` (the copied-host-config index, ADR-0072).

## Alternatives considered

- **Re-type the fields into a Start dialog each session.** Rejected: that is the
  friction we are removing; a modal every Start also slows the common case where
  nothing changed.
- **Persist the stage override too (fully sticky).** Rejected: a remembered
  override would quietly mislabel later sessions once the sub-stage changed; the
  small cost of re-selecting a rare override is worth the safety.
- **Fold trainer/stage/reward into `animal_state.json`.** Rejected: it mixes
  human bookkeeping with physiological scalars and muddies both streams' meaning;
  separate streams keep each self-describing (mirrors ADR-0074/0077).
- **Route through `config_snapshot.json`.** Rejected for the same reason as
  ADR-0077: that index is specifically the copied host config files, not
  session-authored data.
- **A "Meta..." popup like Notes.** Rejected: unlike free-text notes these are a
  few set-and-forget fields; inline keeps them visible so a wrong trainer/stage is
  caught at a glance. Kept to one grid row so the full-height Rig view barely grows.

## Consequences

- Every new bundle carries trainer/stage/reward type; WS4 (registry) and WS7
  (generated training-log view) read them straight from the bundle.
- The operator sets trainer + reward type once and the rig remembers them; only a
  rare sub-stage needs a per-session pick.
- New host config file `host/session_meta.json` (versioned like `note_tags.json`),
  seeded empty; a new `session_meta` manifest stream key (the manifest
  `schema_version` is unchanged - it is an open `streams` entry, as with
  `animal_state`/`notes_events`).
- Reader caveat: `stage_source` distinguishes an auto stage from a typed one, so
  downstream analysis is not misled by an operator override.

## Evidence / links

- Coordinated-records upgrade plan (WS6); feeds WS4 (registry) and WS7 (generated
  training-log view).
- Precedent: ADR-0074 (`note_tags.py` host-config persistence + own stream),
  ADR-0077 (`animal_state.json` as its own bundle stream), ADR-0072
  (`config_snapshot` is the copied-config index, deliberately separate),
  ADR-0060 (Session button requires Phase 3 armed - the basis for the auto stage).
- Tests: `host/test_session_meta.py` (build/override, JSON round-trip, defaults
  load/save tolerance). Full suite: `python -m unittest test_session_meta`.
