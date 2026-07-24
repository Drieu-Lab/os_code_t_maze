# ADR 0053: Regroup the experiment-control GUI into three workflow tabs (Operate / Setup / Task), retiring the Cue tab

- **Status:** Proposed
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/experiment_control.py` (the `ttk.Notebook` tab registry + Rig-view bottom strip), `host/tracker_view.py`, `host/maze_state_view.py`, `host/rules_view.py`, `host/cue_controller.py` (retired), `host/phase3_controller.py`, `host/behavior_view.py`, `host/session_controller.py`; the shared skeletons in `host/preview.py` and a new shared vision-actuator base
- **Supersedes / Superseded by:** —

## Context

The experiment-control GUI has grown one tab at a time as the rig matured, and
`host/experiment_control.py` now registers **six** top-level notebook tabs
(`experiment_control.py` around lines 415-451):

1. **Rig view** — the maze schematic (`RigView`) beside the camera
   (`CameraView`), plus a bottom strip carrying the firmware/mode/solenoid/
   reward/lick controls and (right column) the live behavior readout
   (`BehaviorView`) and the `SessionControlView` panel (ADR-0048/0049).
2. **Tracking** — `tracker_view.py` (tracker tuning + ROI/landmark still-frame
   editors + live preview).
3. **Rules** — `rules_view.py` (position-based rules engine, scale calibration,
   rules editor; ADR-0023/0031).
4. **Maze state** — `maze_state_view.py` (zone editor + discrete state readout;
   ADR-0025).
5. **Cue** — `cue_controller.py` (host-driven vision-gated audio cue; ADR-0044/
   0045).
6. **Phase 3** — `phase3_controller.py` (host-owned cued task; ADR-0046-0052).

This is becoming cumbersome to navigate, and a closer review surfaced real
duplication across the tabs, not just visual clutter:

- **Task group (Cue vs Phase 3).** The two share a near-identical cue-gate UI
  block — "Choice cue (central stem, up-stem)", "require up-stem direction",
  "Return cue (return arm, fail-safe)", "Min confidence" — see
  `cue_controller.py` (279-303) and `phase3_controller.py` (591-621). They also
  share the ADR-0043 actuation skeleton (fast-view subscription, gate snapshot,
  dedicated actuation thread, DAC handshake) with `rules_view.py`. Functionally
  **Cue is Phase 3 without scoring, reward, or a trial sequence**.
- **Setup group (Tracking + Maze state).** Both host modal still-frame editors
  that re-copy the same scaffolding (scale to `EDITOR_MAX_W`, `Toplevel`,
  `grab_set`, `_close`) — `_RoiEditor`/`_LandmarkEditor` and `_ZoneEditor`
  (already captured as FW-0006) — plus near-identical live-preview loops and
  config-file I/O.
- **Operate group.** The serial fan-out in `on_line`
  (`experiment_control.py` 515-532) parses each line once for the bottom-strip
  tallies and then hands the raw line to every module to parse again; MODE-switch
  resets are split across several modules; and animal-ID state is duplicated
  (Phase 3 tab entry + `SessionControlView`, each with its own `_sanitize`).

The duplication and the tab count are two facets of one problem: the GUI is
organized by *implementation module*, not by *what the operator is doing*.

## Decision

**Regroup the six tabs into three, organized by workflow, and retire the Cue
tab.**

Target top-level tabs:

- **Operate** — the run-time cockpit: Rig view (schematic + camera), the
  firmware/mode/solenoid/reward/lick controls, the live behavior readout, and
  the Session start/stop panel. (Essentially today's Rig-view tab, kept whole.)
- **Setup** — calibration/definition, done before a run: Tracking and Maze state,
  nested (e.g. an inner notebook or two panes) so the two still-frame-editing
  workflows live together.
- **Task** — the host-owned task controller: Phase 3 (with Rules available for
  the position-based reward-shaping engine). A selector chooses the active task
  controller; the shared cue-gate controls appear once.

**Retire the Cue tab.** Cue == Phase 3 minus scoring/reward/sequence, and the
user wants Phase 3 only; the standalone Cue actuator is removed and its
still-useful pieces (the vision cue gate) remain reachable through Phase 3.

**Commit to refactor A now; treat B/C as Phase-3-internal cleanups.** Alongside
the regroup:

- **A (committed).** Extract a **shared vision-actuator skeleton** for the
  Rules engine and Phase 3 controllers — the ADR-0043 pattern (fast-view
  subscribe, atomic gate snapshot, dedicated actuation thread, DAC handshake)
  that is currently copied three ways. This is the one cross-cutting dedup this
  ADR commits to, because removing Cue leaves exactly two real callers, meeting
  the CONTRIBUTING "2-3 real callers before abstracting" bar.
- **B/C (Phase-3-internal).** The cue-gate UI block and the DAC handshake become
  internal details of the Phase 3 controller once Cue is gone; no separate
  abstraction is introduced for them.

The deeper reward-core and event-layer dedup found during the review (one reward
seam + rate backstop; a shared choice-commit edge in the task-event layer) is
**not** bundled here — it is recorded as deferred follow-up work (FW-0019,
FW-0020) so this change stays reviewable in one sitting.

## Alternatives considered

- **Keep the flat six tabs.** Rejected — the tab count and the by-module
  organization are precisely the complexity the user asked to streamline; doing
  nothing lets it keep growing.
- **Collapse everything into a single mega-tab.** Rejected — loses the natural
  Operate/Setup/Task workflow boundary and would make one enormous, hard-to-
  review pane; nesting within three groups keeps each screen scoped.
- **Retire Cue by folding a "no-score" mode into Phase 3.** Rejected — the user
  explicitly wants Phase 3 only and no cue-only mode, so a compatibility mode
  would be dead weight.
- **Bundle the reward-core / event-layer dedup (D/E) into this change.**
  Rejected for now — it would push the diff past one-sitting reviewability and
  couples a behavioral seam (reward gating) to a UI reorganization; deferred to
  FW-0019/FW-0020.

## Consequences

- Fewer, workflow-aligned top-level tabs; the operator sees Operate / Setup /
  Task instead of six module tabs.
- The migration touches many modules, so it is **staged as a sequence of
  small PRs** rather than one drop (see below). Each phase builds and its tests
  pass on its own.
- Retiring Cue deletes `cue_controller.py` and its tab registration; the vision
  cue gate (`cue_gate.py`, ADR-0044/0045) stays, now consumed only through
  Phase 3. Any ADR-0044/0045 references to the standalone Cue tab become
  historical.
- Refactor A creates a shared actuator base that Rules and Phase 3 both use;
  future task controllers inherit the thread/gate/handshake plumbing for free.
- Deferred items are tracked as FW-0019-FW-0023 (+ an extension to FW-0005), each
  cross-linking this ADR, so nothing found in the review is lost.
- Revisit if a genuinely cue-only run mode is needed again, or if Rules and
  Phase 3 diverge enough that the shared skeleton (A) stops paying for itself.

## Staged migration (sequence of PRs, not a task list)

Kept coarse on purpose — this is a decision record, not an implementation plan.
Each phase is executed later in its own fresh, scoped session with a short plan
verified against then-current code, one PR, and a human author of record.

1. **Remove the Cue tab** — delete `cue_controller.py` + its registration; keep
   `cue_gate.py`. Smallest, self-contained, highest-clarity win.
2. **Extract the shared vision-actuator skeleton (A)** — Rules + Phase 3 on one
   base.
3. **Merge Setup** — nest Tracking + Maze state under one Setup tab; extract the
   still-frame editor base (FW-0006).
4. **Task selector + shared Animal ID** — one Task tab with a controller
   selector; single animal-ID field (FW-0005).

## Evidence / links

- Current tab registry: `host/experiment_control.py` (~415-451); serial fan-out:
  same file (515-532); bottom-strip wiring: (547-588).
- Duplicated cue-gate UI: `host/cue_controller.py` (279-303) vs
  `host/phase3_controller.py` (591-621).
- Reward seams: `host/rules_engine.py` `_apply_global` (815-838, guarded backstop)
  vs `host/phase3_controller.py` `_send_cmd` (451, 522, direct `R<port>` fire).
- Shared preview helpers already extracted: `host/preview.py`.
- Duplicated `_sanitize`: `host/session_controller.py` (85-88), mirrored in
  `rig_logger`/`phase3_controller`.
- Per-tab screenshots from the GUI review that prompted this ADR.
- Follow-ups: FW-0019, FW-0020, FW-0021, FW-0022, FW-0023, and the extended
  FW-0005 in `docs/future-work.md`.
- Related: ADR-0023/0031 (Rules), ADR-0025 (Maze state), ADR-0043 (actuation
  thread), ADR-0044/0045 (vision cue), ADR-0046-0052 (host-owned Phase 3),
  ADR-0048/0049 (Session), FW-0006 (still-frame editor base).
