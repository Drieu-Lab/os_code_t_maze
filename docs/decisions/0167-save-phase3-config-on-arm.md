# ADR 0167: Persist the live Phase 3 config to phase3.json on arm (save-on-arm)

- **Status:** Accepted
- **Date:** 2026-09-17
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/phase3_controller.py` (new `persist_live_config`,
  `_collect_live_config` extracted from `_save_settings`),
  `host/tmaze/ui/session_controller.py` (call it in the Start flow, before the
  config snapshot), `host/tests/`.
- **Refs:** review finding **P2** (provenance); builds on ADR-0070 (host-owned
  `phase3.json`), ADR-0072/0073 (per-session `config/` snapshot), ADR-0060 (arm
  before Start).

## Context

The Phase 3 tab's reward/cue/intervention knobs are live Tk variables. The
running session reads them every actuation snapshot (`_publish_snapshot` builds
`_Phase3Snapshot` from the live vars each Tk poll), so a value typed into the tab
takes effect on the **next arm** whether or not the operator pressed **Save
settings**.

The per-session reproducibility snapshot (`config/phase3.json`, ADR-0072),
however, is a **verbatim copy of the on-disk `host/phase3.json`** - i.e. only what
was last *Saved*. So an operator who nudges the reward pulse (or flips an
intervention generator) and arms WITHOUT clicking Save runs the rat at the new
value but archives the **old** one. The bundle then misrepresents what actually
shaped the data - a silent provenance hole. The summary's `_task_params` read the
same stale copy and carried an explicit "not reflected unless Saved" caveat.

## Decision

Add **save-on-arm**: in the Start flow, just before the `config/` snapshot is
written, the Session controller asks the Phase 3 controller to persist its live
GUI-editable config to `host/phase3.json`. The snapshot's verbatim copy then
reflects exactly what the armed session will run with.

- `Phase3Control.persist_live_config()` folds the same operator-editable fields as
  the Save button (reward + cue + the debias-generator / intervention-block
  carve-out of `sequence`; the scientific schedule params, `seed`, and `_schema`
  are preserved), validates, and writes - but **silently** (no dialogs) and
  **best-effort**: an invalid edit or a write error writes nothing and returns a
  short note the Start flow logs as a session note, so it can never break Start.
- The shared field-folding logic is extracted into `_collect_live_config()` so Save
  and save-on-arm stay in lockstep (one source of truth for "the editable slice").
- Ordering is safe because ADR-0060 already requires Phase 3 to be armed *before*
  Start, and the persist call sits immediately before `_write_config_snapshot` on
  the same (Tk) thread that owns the tab vars - so no cross-thread read and no race
  with the actuation thread (which reads the snapshot, never `self._config`).

## Alternatives considered

- **Write the live resolved config straight into the snapshot** (bypass the disk
  file) - rejected: the operator explicitly wanted `host/phase3.json` itself to
  carry the last-used values (so the next launch starts from them), and reusing the
  existing verbatim-copy path keeps one archiving mechanism.
- **Persist on the arm checkbox rising edge** instead of at Start - rejected: Start
  is the tightest point before the snapshot, and the checkbox can flip well before
  Start (values may still change in between); pinning at Start matches the snapshot.
- **Force the operator to Save before arming** (block Start otherwise) - rejected:
  adds friction to a live-tuning workflow and still wouldn't capture a post-Save
  nudge; automatic persistence is both safer and quieter.
- **Fold Phase 3 config into `runtime_params.json`** - rejected: it already has a
  first-class file; duplicating it into runtime params would create two sources.

## Consequences

- The archived `config/phase3.json` (and the summary's params block) now match the
  values the session **armed** with, closing P2 for the GUI-editable fields. The
  stale caveats in `_task_params` / the snapshot docstring are updated.
- `host/phase3.json` is rewritten on every Start (pretty-printed round-trip;
  `_schema` and hand-edited sequence params preserved), so it always reflects the
  last-used settings - a convenience for the next launch.
- Residual, accepted limitations (noted in code): a field changed AFTER Start is
  applied live but not re-persisted (same live-tuning caveat the data-file header
  carries); and if the collected config fails validation the snapshot keeps the
  last valid on-disk file (a logged note) - strictly no worse than before P2.

## Evidence / links

- Gap: `session_config.write_config_snapshot` copies `host/phase3.json` verbatim;
  `phase3_controller._publish_snapshot` builds the actuation snapshot from live Tk
  vars - the two could disagree.
- Tests: `host/tests/test_session_phase3_wiring.py` (Start archives a live,
  un-Saved value into `config/phase3.json`) and the `persist_live_config` unit
  behaviour.
