# ADR 0074: Timestamped, editable note entries with incremental persistence and user-defined tags

- **Status:** Accepted
- **Date:** 2026-08-05
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/session_notes.py` + `host/test_session_notes.py`; new `host/note_tags.py` + `host/note_tags.json` + `host/test_note_tags.py`; `host/session_summary.py` (schema v2 -> v3: `notes` string -> entry list; text tally) + `host/test_session_summary.py`; `host/session_controller.py` (note log + JSONL persistence, `notes_events` manifest stream, reworked notes popup, quick-stamp hotkey); `docs/glossary.md`
- **Supersedes / Superseded by:** Supersedes ADR-0073 (the notes half of it: the `notes` data model, its UI, and its persistence). The folded task params + estimated water from ADR-0073 are unchanged.

## Context

ADR-0073 gave the session summary an operator `notes` field: a single free-text
string, held in `session_controller.self._notes`, edited in a "Notes..." popup,
and folded into the summary only when Session Stop ran. Three problems showed up
in use:

- **A blob loses the timeline.** The rig deliberately aligns behavior, video, and
  neural data on one host clock (ADR-0048). An observation like "dissuaded a
  non-stem shortcut" is far more useful as an event stamped with *when* it
  happened (wall clock + the monotonic clock the vision/rules streams use + the
  Phase 3 trial in progress) than as a line in an undated paragraph. A blob
  can't be queried or cross-referenced against the other streams.
- **Notes could be lost.** They lived only in memory until Stop wrote the
  summary. An abnormal end - an app crash, or the documented Due serial wedge
  (`#260727_SerialLockout`) - lost the whole session's notes. Two smaller
  footguns made it worse: closing the popup without "Save" silently discarded
  typed text, and `_start_session` wiped `self._notes`, so anything jotted while
  setting up (before Start) vanished when the session began.
- **No cross-session vocabulary.** Recurring interventions (non-stem dissuasion,
  lift-backs) had to be re-typed each time and were phrased inconsistently, so
  they couldn't be compared across sessions later.

## Decision

Replace the single notes string with an ordered list of **timestamped, editable
note entries**, persist them incrementally, register the file as a manifest
stream, and add a **user-defined tag vocabulary**. Summary JSON goes to schema
**v3**. New logic lands in pure, headless-testable cores; only Tk stays in the
`session_controller` shell, matching the "pure core + thin shell" rule.

- **Note entry data model - `host/session_notes.py` (pure core).** A `NoteEntry`
  carries `id`, `iso`, `mono`, `trial_index`, `text`, and (for a tag press) `tag`
  + `count`, plus `edited_iso`. A `NoteLog` owns the ordered list with
  `add`/`edit`/`delete`, stable monotonic ids, and `render_jsonl()` /
  `parse_jsonl()`. `edit` preserves the original `iso`/`mono`/`trial_index` and
  only sets `edited_iso`. The core does no file I/O and imports no Tk (it
  renders/parses strings; the shell writes them), mirroring `phase3_log`
  (renders CSV) + the controller (writes it).
- **Incremental persistence = atomic full rewrite.** The shell writes
  `render_jsonl()` to `summary/notes_<session_id>.jsonl` (temp file +
  `os.replace`) on every add/edit/delete. We chose a whole-file atomic rewrite
  over an append-only op-log because entries can be edited/deleted: rewriting the
  few-line file each change is the simplest way to keep the on-disk record
  consistent, and `parse_jsonl` still skips a blank/corrupt trailing line so a
  file half-written when the app died loads its intact entries. Stop re-reads the
  file and folds *that* into the summary, so notes survive an unclean end.
- **Manifest stream.** The notes file is registered via
  `set_stream("notes_events", path)` on Start (guarded by `path_within_session`,
  ADR-0066), so it is discoverable alongside `serial_sensors` / `camera_video` /
  `phase3_task` and is recorded even if the session never reaches a clean Stop.
- **Footgun fixes.** `_start_session` no longer wipes the log - it *binds* it to
  the session file and flushes pre-Start entries into it; closing the popup with
  non-empty compose text autosaves it as an entry; Stop resets the log for the
  next session (its notes are already on disk + in its summary).
- **User-defined tags - `host/note_tags.py` (pure core) + `host/note_tags.json`.**
  A `TagVocabulary` (`{name, counter}` entries) with `add`/`rename`/`delete`,
  case-insensitive de-duplication, and JSON load/save, persisted next to the
  other `host/*.json` config so it is reused and editable across sessions. The
  popup surfaces tags as one-click buttons (a press appends a stamped entry
  labeled with that tag; a `counter` tag records a `+1` event) plus a "Manage
  tags..." add/rename/delete dialog. Tag-labeled entries are ordinary entries -
  editable/deletable like any other - so tags are queryable across sessions.
- **Summary schema v3.** `SessionSummary.notes` becomes a `list[dict]`; JSON
  emits the entry list verbatim; the text `Notes` section renders a timestamped
  list (`HH:MM:SS  trial N  #tag  text (edited)`) with a leading per-tag tally
  line. The tally aggregates the given entries (not a stats-core measure) - the
  same latitude the formatter already takes for `duration_s`.

## Alternatives considered

- **Append-only op-log persistence (add/edit/delete records reconciled on
  read).** Rejected: robust to partial writes but more complex to reconcile, and
  needless here - a session accrues only a handful of notes, so an atomic rewrite
  of the whole small file is trivially cheap and always internally consistent.
- **Keep the single free-text blob, just persist it on each keystroke.**
  Rejected: solves data loss but not the timeline/queryability goal (ADR-0048)
  that motivated the change.
- **Hardcode a fixed intervention-tag set.** Rejected per the request: the
  experimenter's useful tags aren't known up front and evolve; a user-editable
  vocabulary persisted to `note_tags.json` lets them curate it over time.
- **Counter tag stores a single running total on one entry.** Rejected in favour
  of one stamped `+1` entry per press, so every press keeps its own place on the
  shared timeline; the summary sums them per tag.

## Consequences

- The session's notes are now machine-parseable events on the shared timeline,
  survive a crash, and share a curated vocabulary across sessions - so later
  analysis can, e.g., compare sessions with vs without a "non-stem dissuasion"
  tag. JSON gains `schema_version: 3`; an offline reader must treat `notes` as a
  list of entry objects, not a string (a v2 reader keyed on a string breaks -
  branch on `schema_version`).
- A new `notes_events` stream appears in the manifest and a new
  `summary/notes_<id>.jsonl` file in each session folder.
- The tag vocabulary is global to the host (not per-animal); renaming a tag does
  not rewrite entries already stamped with the old name (past entries keep their
  label, which is the honest record of what was applied at the time).
- The persisted `.jsonl` is the source of truth on Stop; an entry edited/deleted
  after it was written is reflected because Stop re-reads the file.

## Evidence / links

- Cores + tests: `host/session_notes.py`, `host/test_session_notes.py`;
  `host/note_tags.py`, `host/test_note_tags.py`; `host/session_summary.py`,
  `host/test_session_summary.py`
  (`python -m unittest test_session_notes test_note_tags test_session_summary`).
- Shell wiring: `host/session_controller.py` (note log, atomic JSONL write,
  `notes_events` stream, popup, `<Control-Shift-N>` quick-stamp).
- Supersedes the notes half of ADR-0073; one-timeline rationale is ADR-0048;
  manifest path rule is ADR-0066; trial index comes from the Phase 3 snapshot
  (ADR-0050).
