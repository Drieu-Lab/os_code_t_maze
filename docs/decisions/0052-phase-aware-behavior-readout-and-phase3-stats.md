# ADR 0052: Phase-aware behavior readout with a pure Phase 3 stats core

- **Status:** Accepted
- **Date:** 2026-07-23
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** new `host/phase3_stats.py` (pure core) + `host/test_phase3_stats.py`;
  `host/behavior_view.py` (phase-aware sub-panels); `host/tooltip_text.py`
  (`behavior.phase3.*` keys); `PROGRAMS.md`
- **Supersedes / Superseded by:** builds on ADR-0032 (Phase 2a behavior readout),
  ADR-0050 (published Phase 3 state snapshot), ADR-0046/0047 (host-owned Phase 3
  + session data table); —

## Context

The Rig view tab's bottom-strip behavior panel (ADR-0032) reports the Phase 2a
shaping measures live, folding the shared maze-state pipeline's task-event stream
through the pure `behavior_stats.py`. But once a session moves to the host-owned
Phase 3 cued task (ADR-0046), that panel is the wrong readout: Phase 2a
compliance / L-R / distance say nothing about whether the rat is learning the
cue->side map, and Phase 3's own tab shows only a one-line status plus the trial
ribbon (ADR-0051), not aggregate accuracy.

P0 (ADR-0050) already built the seam: the Phase 3 controller publishes an
immutable `Phase3StateSnapshot` of the whole session table via atomic reference,
and `BehaviorView` is handed a zero-arg `phase3_state` provider that returns the
latest snapshot (or None). P0 deliberately added no statistics and no widgets.
This ADR is the P1 consumer: turn that snapshot into live Phase 3 training
measures and show them in the existing panel when a Phase 3 session is running.

## Decision

**1. A pure stats core `phase3_stats.py`.** `compute(snapshot, *, window_n=20)
-> Phase3Stats` (a frozen dataclass) reduces a published `Phase3StateSnapshot` to
the training measures, mirroring how `behavior_stats.py` serves Phase 2a. It is
stdlib-only, headless-testable, imports no Tk/serial, and derives everything from
the frozen snapshot alone (a "completed" trial is one the snapshot already scored
- `outcome` set). Measures: trials completed, cumulative + rolling-window choice
accuracy, per-choice-cue (HIGH vs LOW) accuracy, side bias (chosen LEFT vs
RIGHT), reward rate, win-stay/lose-shift, and mean cue-onset->choice reaction
time. Because trials resolve in order, the completed trials are a contiguous
prefix, so the rolling window is just their tail.

Two definition choices are deliberate:

- **Win-stay / lose-shift are keyed on the trial OUTCOME, not the reward.**
  `win_stay` = P(same arm chosen next | this trial CORRECT); `lose_shift` =
  P(different arm next | this trial INCORRECT). The reward-incorrect shaping aid
  (ADR-0046) rewards incorrect choices too, so a reward-based definition would
  collapse (few/no "lose" trials) exactly when shaping is on; the outcome-based
  definition stays interpretable and directly flags a rat falling back on a naive
  strategy instead of following the cue.
- **Reaction time is the host `choice_cue_on_iso -> choice_time_iso` interval.**
  These are host wall-clock actuation-decision stamps carrying serial latency
  (ADR-0047), so it is an approximate cue-onset->choice latency, not a
  hardware-precise reaction time; documented as such.

**2. `behavior_view.py` becomes phase-aware via two switched sub-panels.** The one
LabelFrame now holds a Phase 2a box (default) and a Phase 3 stats box. Each poll
(the existing `_schedule_poll` cadence) it reads the `phase3_state` provider; when
that returns a snapshot with `armed=True` it packs the Phase 3 box (retitling to
"Phase 3 cued task - live stats") and fills it from `phase3_stats.compute(...)`,
otherwise it shows the unchanged Phase 2a readout. The panel switch only re-packs
on an actual phase change, not every poll. The rolling-window spinbox is shared
(it sizes both readouts' windows). Reading the provider is cheap and thread-safe -
the snapshot is immutable and published by atomic reference (ADR-0050) - so the
panel needs no lock and touches none of the actuation thread's live state.

`window_n` defaults to 20 (vs Phase 2a's 10): a Phase 3 session is a few hundred
trials, so ~a block's worth smooths per-trial noise while still showing
within-session learning.

## Alternatives considered

- **Put the stats logic in the view's poll callback.** Rejected: it violates the
  pure-core/thin-shell split (CONTRIBUTING §1) and would be untestable without Tk.
  A pure core is headlessly tested and reusable (e.g. offline analysis).
- **A separate always-visible Phase 3 stats tab/panel.** Rejected: the
  experimenter runs the session from the Rig view tab and the bottom strip is the
  established home for the live readout (ADR-0032); the two phases are mutually
  exclusive per session, so switching one panel avoids a second dead panel.
- **Reward-based win-stay/lose-shift.** Rejected for the shaping-aid collapse
  above; the outcome-based definition is invariant to `reward_incorrect`.
- **Re-derive stats from the on-disk session CSV.** Rejected for the same reasons
  ADR-0050 rejected it for readers: file-I/O latency and coupling to the file
  format; the immutable in-memory snapshot is the live source.

## Consequences

- The experimenter gets a live, glanceable "is the rat learning the cue" panel
  (accuracy trend + per-cue split + bias + reward rate + strategy + RT) on the tab
  they already run sessions from, with no reward-actuation risk (pure observer).
- The Phase 3 controller, the Phase 3 tab widgets, `phase3_log.py`, and the
  published `Phase3StateSnapshot` contract are untouched: this is a pure consumer
  of the P0 seam, so P0 and P1 stayed independently reviewable.
- The panel keeps folding Phase 2a stats in the background while Phase 3 is shown
  (the local estimator keeps running); harmless, and the Phase 2a readout is
  intact when a session disarms.
- Stats are point-in-time over whatever snapshot the provider returned; the panel
  re-reads each poll, so it is at most one poll stale (matches ADR-0050).
- If future measures need snapshot fields not yet published, the frozen types
  (ADR-0050) must be extended rather than reaching past the contract.

## Evidence / links

- Code: `host/phase3_stats.py` (`Phase3Stats`, `compute`), `host/behavior_view.py`
  (`_phase3_snapshot`, `_show_phase`, `_refresh_phase3`), `host/tooltip_text.py`
  (`behavior.phase3.*`).
- Tests: `host/test_phase3_stats.py` - trials-completed counting, cumulative +
  rolling-window accuracy, per-cue split (cross-checked against the plan), side
  bias, reward rate, outcome-based win-stay/lose-shift, and reaction time, over
  deterministic snapshots.
- Builds on ADR-0050 (the published snapshot + `phase3_state` provider), ADR-0032
  (the Phase 2a readout this parallels), ADR-0046/0047 (host-owned Phase 3 + the
  session table the snapshot freezes).
