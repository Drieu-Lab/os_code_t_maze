# ADR 0163: Records consistency fixes — one % baseline precedence, formula-safe Excel cells, queue-based UI hand-off

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/core/records/dashboard.py` (`pct_baseline_of`),
  `host/tmaze/core/records/daily_log.py` (`write_rows`, new `_with_recomputed_pct`),
  `host/tmaze/core/records/animal_state.py` (`_pct`),
  `host/tmaze/ui/records_manager.py` (`_run_async` / new `_drain_async_queue`);
  `os_code_t_maze_resources/records/animal/build_daily_log.py` and
  `build_session_training_log.py` (new `_escape_text_cell`); `docs/records-schema.md`.
- **Supersedes / Superseded by:** —

## Context

Three small inconsistencies surfaced while hardening the records system (the
Phase 7 clean-up of the Records Manager hardening plan). Each is individually
minor but each is a "why is it done this way?" question a future reader would
ask, so they are recorded together.

1. **Two answers for `% baseline`.** `pct_baseline` in `daily_log.csv` is a
   *derived* value — `weight_g / baseline_g` — but it is also stored as its own
   column (so the Excel Entry sheet can show a live formula and the rig can read
   a number without recomputing). Three readers each chose their own precedence:
   `dashboard.pct_baseline_of` trusted the stored cell first and only computed as
   a fallback; `animal_state._pct` did the same; the GUI recomputed on save. If a
   weight were ever edited on one surface without the mirror being refreshed, the
   dashboard and a frozen `animal_state` could disagree — a silent welfare-number
   drift.

2. **A note that starts with `=` becomes a formula.** The two Excel builders
   write free-text cells (daily notes, session `procedures`/`notes`, the legacy
   supplemental text) straight to `cell.value`. openpyxl marks any string
   starting with `=` (and Excel treats `+`, `-`, `@` similarly) as a *formula*,
   so a note like `=2 doses` would open as a `#NAME?` error — and, more
   seriously, a crafted cell is the classic spreadsheet formula-injection vector.

3. **A worker thread touching Tk.** `records_manager._run_async` ran the build
   off the UI thread, then called `self.root.after(0, ...)` *from the worker* to
   report back. Tk is single-threaded and does not officially guarantee that
   calling into a widget (even `after`) from another thread is safe; it works in
   practice on CPython but is the kind of thing that fails intermittently.

## Decision

1. **One precedence: weight/baseline win.** Weight and baseline are the source of
   truth; `pct_baseline` is a mirror. Both readers now **recompute first** and
   fall back to the stored cell only when the row can't be computed (weight or
   baseline missing): `dashboard.pct_baseline_of` and `animal_state._pct`. And
   `daily_log.write_rows` **always rewrites** the mirror from the row's
   weight/baseline (new `_with_recomputed_pct`, formatted to 4 dp, blank when not
   computable), so the stored column can never drift from the truth on disk. The
   Entry sheet keeps its live `=IF(...)` formula — that is the one place the
   formula is deliberate.

2. **Escape formula-looking text.** Both builders gain a tiny
   `_escape_text_cell(cell)` helper: after a free-text value is assigned, if it
   starts with `=`, `+`, `-`, or `@`, the cell's `data_type` is forced back to
   `'s'` (string). openpyxl then writes the literal text and Excel displays it
   verbatim instead of evaluating it. Deliberate formulas (the % baseline cell)
   are written straight to `cell.value` and never routed through the helper.

3. **Queue hand-off.** The worker now `put`s its result on a `queue.Queue`
   (`self._async_queue`) and the **UI thread** drains it in `_drain_async_queue`,
   scheduled every `_ASYNC_POLL_MS = 100` ms with `root.after`. The worker never
   touches a widget. This is the documented-safe Tk cross-thread pattern.

## Alternatives considered

- **Keep stored-`pct` precedence / drop the stored column.** Trusting the mirror
  first is what allowed drift; dropping the column would cost the Entry-sheet live
  formula and force every rig read to recompute. Recompute-first-but-keep-the-
  mirror gives both without the drift.
- **Prefix free text with a leading apostrophe.** Excel's own text marker, but
  openpyxl stores the apostrophe as a literal character, so it would *show* in
  the cell. Forcing `data_type='s'` keeps the text exact (verified on a
  round-trip: a `=…` cell reads back as a string, a real `=IF(...)` stays a
  formula).
- **Leave `root.after(0, …)` called from the worker.** Works today, but relies on
  undocumented Tk thread behaviour; the queue costs a handful of lines and a
  100 ms poll (imperceptible) and removes the hazard.

## Consequences

- The dashboard, the frozen `animal_state`, and the on-disk `daily_log.csv` now
  agree on `% baseline` by construction. Existing rows whose stored value already
  matched weight/baseline are unchanged; a stale value is corrected on the next
  write. Rows that can't be computed still surface the stored value to the reader.
- Excel opens builder output without spurious `#NAME?` cells and without
  evaluating hostile text. No change to legitimate formulas.
- Background builds can no longer corrupt Tk state from the wrong thread; the
  100 ms poll is the only added cost.

## Evidence / links

- Recompute round-trip and mirror-rewrite covered by
  `test_dashboard.TestSignal1.test_recompute_overrides_a_stale_stored_pct` /
  `test_falls_back_to_stored_when_not_computable` and
  `test_daily_log.TestWriteRows.test_write_rewrites_pct_baseline_mirror`.
- openpyxl escape confirmed against a save/reload (`data_type='f'` for `=…` naive,
  `'s'` after the guard, `'f'` preserved for a real formula).
- Phase 7 of the Records Manager hardening plan; refines ADR-0076 (daily log) and
  ADR-0085 (write surfaces).
