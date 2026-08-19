# ADR 0111: Record operator interventions as first-class auto notes on the timeline

- **Status:** Accepted
- **Date:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/session/session_notes.py`,
  `host/tmaze/ui/session_controller.py`, `host/tmaze/ui/phase3_controller.py`;
  tests `host/tests/test_session_notes.py`
- **Supersedes / Superseded by:** — (reuses the note machinery of ADR-0074;
  keeps the task/operator water disjointness of ADR-0084)

## Context

A Phase 3 session records the trial outcomes, but the operator's live
*interventions* leave no timestamped trace: a manual reward boost (the
"Solenoid test" reward-spout button), a debias or de-alternation block the
operator forces on or off, and the Back / Skip / Repeat trial-control overrides.
These are exactly the events an analyst later needs to explain a kink in the
learning curve ("why did side bias jump here? - because a debias block started")
or to reconcile water intake ("three manual boosts at these times"). Today they
are either invisible or only inferable indirectly (e.g. `manual_water` totals the
boost VOLUME but not WHEN each happened or which trial it landed on).

Two wrinkles shape the design:

1. The rig already has a per-session, timestamped, editable operator **note log**
   (`session_notes.py`, persisted to `summary/notes_<id>.jsonl`, folded into the
   summary; ADR-0074). Interventions belong on that same host timeline
   (ADR-0048), so a separate stream would duplicate machinery for no gain - but a
   machine event must stay distinguishable from a human's free-text note.
2. The emit points live on two DIFFERENT threads. The manual-reward boost is
   handled on the Tk thread (`session_controller.note_operator_reward`). The
   trial-control overrides fire on Phase 3's **actuation thread**
   (`phase3_controller._process_command`), which must never touch Tk or the note
   log directly (ADR-0043).

## Decision

Reuse the note log, tag machine events, and cross the thread boundary with a
queue:

- **`session_notes.py`** gains a `source` field on `NoteEntry` (default
  `"operator"`; auto events use `"auto"`) and a reserved `auto:` tag namespace
  with named constants (`AUTO_MANUAL_REWARD`, `AUTO_DEBIAS_START/END`,
  `AUTO_ALT_START/END`, `AUTO_REPEAT`, `AUTO_SKIP`, `AUTO_BACK`). A machine event
  carries BOTH `source="auto"` and an `auto:` tag, so either channel
  distinguishes it from a human note (`NoteEntry.is_auto()` / `is_auto_tag()`).
  `from_dict` defaults `source` to `"operator"`, so every pre-0111 record loads as
  a human note (additive, back-compatible). `NoteLog.add_auto(tag, text, ...)` is
  the convenience emit path.
- **Manual reward (Tk seam):** `session_controller.note_operator_reward(ms)` also
  appends an `auto:manual_reward` note (`"manual reward <ms> ms"`). This is
  purely additive to the existing `manual_water` volume accounting (ADR-0084) - it
  adds a timestamp + trial link, it does not move any water between the task and
  operator channels.
- **Trial-control overrides (actuation-thread seam):** Phase 3 exposes a
  thread-safe sink `set_intervention_sink(cb)`. In `_process_command`, after
  applying a debias/de-alternation edge or a Back / Skip / Repeat, it builds an
  event dict (`tag`, `text`, `iso`, `mono`, `trial_index`) and calls the sink.
  The Session controller supplies a sink that only `queue.put`s (enqueue-only, no
  Tk), and DRAINS the queue on its existing Tk-thread session poll
  (`_resolve_paths`), folding each event into the note log via `add_auto`. The
  event's own `iso`/`mono`/`trial_index` (captured on the actuation thread at the
  moment) are preserved, so the note lands at the right time and trial even though
  it is written a poll-tick later.

## Alternatives considered

- **A separate intervention stream / CSV.** Rejected: duplicates the note log's
  atomic-write, reload-on-crash, and summary-fold machinery (ADR-0074) for no
  analytical gain. Interventions and human notes want the same timeline; the
  `source`/tag split keeps them distinguishable within one stream.
- **Call the note log directly from the actuation thread.** Rejected: violates the
  single-Tk-thread rule (ADR-0043) - the note log + its file write are Tk-thread
  owned. A queue drained on the Tk poll is the same pattern the rest of the tab
  uses to cross that boundary.
- **A new recurring Tk poll in `session_controller` just to drain.** Rejected: the
  controller already reschedules `_resolve_paths` every tick while a session is
  active; draining there adds no new lifecycle to start/stop/cancel.
- **Infer interventions offline from the trial CSV** (e.g. a repeated side ==
  debias). Rejected: lossy and ambiguous - a forced block and a coincidental run
  look identical; and manual boosts / Skips leave no reliable CSV signature.

## Consequences

- Manual boosts, debias/de-alternation overrides, and Back/Skip/Repeat are now
  recoverable per session with timestamps + trial linkage, and are
  machine-distinguishable from human notes (filter on `source == "auto"` or the
  `auto:` tag prefix). Reconstructing an intervention timeline needs only the
  existing `notes_<id>.jsonl`.
- The sink is enqueue-only and best-effort on both ends (a missing sink on a
  manual arm / standalone tab, or a raising sink, is swallowed), so recording a
  note can never disturb the task on the actuation thread.
- Auto notes appear in the summary's note list + tag tally alongside human notes;
  a reader wanting only human notes filters them out by `source`. If the volume of
  auto notes ever crowds the human view, a future change can split the summary
  rendering by `source` without touching this seam.
- The `source` field is additive; old bundles load unchanged (they read as
  `operator`). No `SCHEMA_VERSION` bump is needed (the notes list is a passthrough
  of the entry dicts).

## Evidence / links

- Core: `session_notes` `source` field + `is_auto()` / `is_auto_tag()` +
  `add_auto()` + the reserved `AUTO_*` tag constants; `from_dict` back-compat
  default.
- Wiring: `session_controller` intervention queue (`_on_intervention_event`
  producer, `_drain_interventions` consumer on `_resolve_paths`), `_add_auto_note`
  helper, and the `auto:manual_reward` emit in `note_operator_reward`;
  `phase3_controller.set_intervention_sink` + `_emit_intervention` from
  `_process_command`.
- Tests: `test_session_notes` (`TestAutoSource`: default operator, `add_auto`
  sets source+tag, `is_auto_tag` helper, source round-trip, legacy back-compat,
  auto-tag-alone).
- Related: ADR-0074 (the note log), ADR-0048 (one host timeline), ADR-0043
  (single Tk thread / actuation-thread seams), ADR-0084 (task vs operator water
  disjointness), ADR-0058 (the trial-control overrides).
