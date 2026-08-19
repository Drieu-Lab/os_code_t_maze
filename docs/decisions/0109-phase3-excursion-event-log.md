# ADR 0109: Log every Phase 3 outbound excursion as a timestamped event

- **Status:** Accepted
- **Date:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/tmaze/core/phase3/excursion_log.py`;
  `host/tmaze/ui/phase3_controller.py`, `host/tmaze/ui/session_controller.py`;
  test `host/tests/test_excursion_log.py`; `docs/glossary.md`
- **Supersedes / Superseded by:** — (extends the ADR-0055 choice-commit edge and
  the ADR-0049/0066 session-file model; complements ADR-0032/0095 non-stem counts)

## Context

The Phase 3 trial CSV (`phase3_log.py`, ADR-0047) has one row per *trial* - a
scored stem trip. But the animal also makes outbound trips that never become
trials: it darts up a side arm without going up the stem (a "non-stem" / denied
excursion; ADR-0099 makes such a trip inert - no cue, score, reward, or advance).
Those trips carry real behavioral signal - impulsive shortcut pulls, a
warming-up or fatiguing animal, the shape of compliance over a session - yet the
only trace they leave today is an end-of-session aggregate (`behavior_stats`
non-stem counts, ADR-0095). There is no way to reconstruct WHEN each denied trip
happened, or to draw a stem-vs-non-stem compliance-over-time curve, from the saved
bundle.

`task_events.choice_committed` (ADR-0055) already fires exactly once per outbound
trip, any route, carrying the committed side; `outbound_used_stem` on the same
frame says whether that trip used the stem. So the event we need is already
computed - it is simply discarded for non-stem trips.

## Decision

Add a new pure core `excursion_log.py` (an `ExcursionRow` + an append-only
`ExcursionLog` with `render_csv`, mirroring `phase3_log.py`'s style) and have
`phase3_controller` append ONE row per `choice_committed` edge, on the actuation
thread, into a `phase3_<id>_excursions.csv` written beside the trial CSV in the
session `task/` folder (atomic temp-file + `os.replace`, like `_write_log`).

Row schema (all additive, fixed order): `event_index` (1-based), `iso`, `mono`,
`type` (`up-stem`|`non-stem`), `side` (LEFT/RIGHT), `outcome`
(`scored`|`denied`), `linked_trial` (the 1-based trial index for a stem trip;
blank for a denied non-stem trip - the join key back to the trial CSV),
`sched_state`, `bias` (the closed-loop context at commit), and `cue_dwell_s` (the
relative cue-zone dwell of a scored stem trip; ADR-0108). `type`/`outcome` are
derived from `outbound_used_stem`.

`session_controller` registers it as a `phase3_excursions` manifest stream, reusing
the exact `path_within_session` guard the `phase3_task` stream uses (ADR-0066) so a
pre-rebind path (a previous session or the `host/data` fallback) is never recorded.
The excursion file is re-pointed in lockstep with the trial CSV on the ADR-0060
Start rebind and on an ADR-0058 Restart.

**Reconciliation caveat (recorded here on purpose):** these events come from
`phase3_controller`'s OWN `TaskEvents` instance (the fast actuation-thread view
that also drives cues/reward). The `behavior_stats` non-stem aggregates come from a
SEPARATE `TaskEvents` folded on the ~100 ms Tk poll (the debounced view). The two
are close but NOT bit-exact (a trip can be split differently across the two
cadences). The excursion log is defined as the AUTHORITATIVE per-excursion source;
`behavior_stats` remains the live at-a-glance tally. We deliberately did not unify
them in this change (that would mean re-plumbing `behavior_stats` onto the
actuation thread - out of scope, and the live panel wants the debounced view).

## Alternatives considered

- **Reuse `behavior_stats`' `TaskEvents` (one shared instance).** Rejected for this
  change: the two consumers run on different threads at different cadences by
  design (ADR-0042/0043); sharing one instance across threads needs a lock and
  re-plumbing. Captured as the reconciliation caveat instead; a future unification
  can supersede this.
- **Add columns to the trial CSV instead of a new file.** Rejected: the trial CSV
  is one row per *trial*; a denied non-stem trip has no trial row to attach to.
  Forcing it in would break the "one row per trial" contract every offline reader
  relies on.
- **Only keep the end-of-session non-stem tally (status quo).** Rejected: it can't
  answer "when" or "compliance over time", which is the whole point.
- **Emit a JSON-lines event stream.** Rejected for consistency: the rest of the
  Phase 3 record is header-commented CSV; a second format raises the reader's cost
  for no gain here.

## Consequences

- Every outbound trip is now individually recoverable with a timestamp, side,
  stem/non-stem type, and (for trials) a trial link + cue dwell - enabling a
  stem-vs-non-stem compliance-over-time curve by binning `iso`.
- One extra small CSV per session, written on the same atomic path as the trial
  CSV; a crash can't half-write it. Additive: no existing file or reader changes;
  the manifest gains one free-form stream key (no `SCHEMA_VERSION` bump - `streams`
  is an open dict).
- The controller keeps a little more per-trip state (`_choice_cue_on_iso`) to stamp
  the dwell; cleared on each (re)arm / silence-and-rearm so a stale value can't leak
  across trips.
- **Revisit when:** the two `TaskEvents` views are unified, or a hardware sync
  fiducial (FW-0018) lets the excursion `iso`/`mono` be placed on the Open-Ephys
  timeline for cross-stream alignment.

## Evidence / links

- Core: `host/tmaze/core/phase3/excursion_log.py`; tests
  `host/tests/test_excursion_log.py` (indexing, side normalization, scored vs
  denied shape, CSV rendering with blanks + rounded floats).
- Wiring: `phase3_controller._append_excursion` / `_write_excursion_log` /
  `current_excursion_path` (append on `choice_committed`); the `_arm_session` /
  `_rebind_session_output` path bookkeeping; `session_controller._resolve_paths`
  (`phase3_excursions` stream) + `_phase3_excursion_path`.
- Related: ADR-0055 (choice_committed edge), ADR-0095/0032 (non-stem counts),
  ADR-0099 (non-stem trips are inert), ADR-0108 (cue-zone dwell), ADR-0049/0066
  (session file model + within-session guard), ADR-0042/0043 (fast vs Tk views).
