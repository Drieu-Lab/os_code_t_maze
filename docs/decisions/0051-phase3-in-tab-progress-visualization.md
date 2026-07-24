# ADR 0051: In-tab Phase 3 progress visualization (trial ribbon + planned-trial list)

- **Status:** Accepted
- **Date:** 2026-07-23
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** new pure core `host/phase3_ribbon.py` (+ `host/test_phase3_ribbon.py`),
  `host/phase3_controller.py` (two read-only widgets + a poll-driven refresh),
  `host/tooltip_text.py`, `PROGRAMS.md`
- **Supersedes / Superseded by:** builds on ADR-0050 (published `Phase3StateSnapshot`),
  ADR-0047 (the session table + per-arm return cues), ADR-0046 (host-owned Phase 3),
  ADR-0043 (the actuation thread + Tk-poll cadence); —

## Context

The host-owned Phase 3 tab (ADR-0046/0047) ran a whole cued session but showed
the experimenter only a one-line status string (`ARMED seed:… trial:i/N
correct:…`). During a session the human at the rig cannot see *what the plan is*
(which arm the current cue means, what the next few trials will be) or *what the
rat just did* (the last few choices and whether they were correct). That plan
already exists in memory: ADR-0050 (P0) made the controller publish an immutable
`phase3_log.Phase3StateSnapshot` (the whole planned N-row cue matrix plus the
live per-trial outcomes), readable off the Tk thread without racing the
actuation thread's live table. P0 deliberately added the seam but no widgets.

We want an at-a-glance, in-tab progress view. Two constraints shape it: it must
read only the published snapshot (never `_rows` or the actuation thread, per
ADR-0050), and it must refresh on the existing `_schedule_poll` Tk cadence
(~100 ms) without churning widgets or racing anything.

## Decision

Add the visualization as a **pure "what to show" core plus a thin "how to draw
it" shell**, matching the house pattern (CONTRIBUTING guideline 1).

- **`host/phase3_ribbon.py` (pure, Tk-free, tested).** `ribbon_rows(snapshot)`
  turns a `Phase3StateSnapshot` into the `RibbonRow`s for a small window: the
  current trial plus `RIBBON_BEFORE = 3` resolved trials behind and
  `RIBBON_AFTER = 3` upcoming ahead (a ≤7-trial window, **clamped** at the start,
  the end, and a completed session where `current_index == total`). Each row
  carries the choice cue, the arm it maps to (`cue_side(cue, high_tone_is_left)`
  — the display twin of `trial_sequence._is_high`, equal to the planned
  `correct_arm` by construction), the two planned return-arm cues, and, for
  resolved trials, the rat's chosen arm + outcome. The 3/3 window and the mapping
  are covered by `test_phase3_ribbon.py` over deterministic snapshots.
- **Two read-only widgets in the Phase 3 tab.** (a) A compact **trial ribbon** of
  fixed pre-made cells (updated in place; the helper clamps, so unused cells are
  `grid_remove`d) with the current trial highlighted. (b) A **scrollable list** of
  all N planned trials (a `Listbox` + scrollbar) with the current trial
  highlighted and `see()`-scrolled into view. Both are built in `_build_ui` and
  refreshed by `_refresh_progress()` on the existing Tk poll.
- **Change-gated refresh.** The poll computes a cheap signature
  `(seed, total, current_index, armed, resolved_count)` and rebuilds the widgets
  only when it changes, so a steady session doesn't churn Tk widgets every
  100 ms. A sentinel initial value forces the first draw (since `None` — the
  not-armed state — is itself a valid signature). `self._state` is read directly
  and `None`-guarded (nothing shown before the first arm).

Colours and the one-letter arm/cue abbreviations are cosmetic constants. The
published contract, `phase3_log.py`, the actuation thread, `behavior_view.py`,
and the CSV format are unchanged.

## Alternatives considered

- **Rebuild the whole ribbon + list every poll.** Simpler, but re-creating up to
  ~200 `Listbox` rows and 7 cells ten times a second churns widgets and flickers
  the selection/scroll for no benefit — the table changes only a few times per
  trial. The change-signature gate keeps redraws to real changes.
- **Draw the list directly from `_rows` (skip the snapshot).** Rejected: that is
  exactly the cross-thread race ADR-0050 removed. Readers consume the immutable
  snapshot only.
- **Put the ribbon window logic in the widget callback.** Rejected: the "which
  trials, which arm does the cue mean, resolved or not" decision is testable pure
  logic and belongs in a core (guideline 1/3), leaving the shell to styling. It
  also lets a future consumer (e.g. an overlay) reuse `ribbon_rows`.
- **Re-derive each list row's arm via `cue_side` too.** Rejected as redundant: the
  snapshot row already carries the authoritative `correct_arm`; the list uses it
  directly (the ribbon exercises `cue_side`, and the test pins the two equal).

## Consequences

- The experimenter sees the live plan and recent choices at a glance, without a
  second screen or re-reading the CSV; the ribbon reuses a tested pure helper.
- The widgets are strictly read-only consumers of the ADR-0050 snapshot, so they
  cannot perturb the task, the actuation thread, or timing.
- `RIBBON_BEFORE`/`RIBBON_AFTER` (3/3) and `SEQ_LIST_ROWS` are display choices; if
  the tab grows more panels they may need revisiting. The `Listbox` is plain
  (monospace text, one highlight colour) — deliberately boring; a richer per-cell
  widget list was not warranted for N≈200 rows.
- The refresh gate assumes `(current_index, resolved_count)` captures every
  display-relevant change; that holds because the shown fields (plan + chosen
  arm/outcome) only change when a trial advances or resolves.

## Evidence / links
- `host/phase3_ribbon.py` — `ribbon_rows`, `cue_side`, `RibbonRow`,
  `RIBBON_BEFORE`/`RIBBON_AFTER`; `host/test_phase3_ribbon.py` — window/clamp,
  mapping (both orientations, checked against `correct_arm`), resolved fields.
- `host/phase3_controller.py` — `_build_progress_ui`, `_refresh_progress`,
  `_rebuild_ribbon`/`_fill_ribbon_cell`, `_rebuild_sequence`/`_seq_line`.
- Builds on ADR-0050 (`Phase3StateSnapshot` + `published_state`), ADR-0047
  (session table + per-arm return cues), ADR-0046 (host-owned Phase 3), ADR-0043
  (actuation thread + Tk poll cadence).
