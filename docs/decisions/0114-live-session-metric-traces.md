# ADR 0114: Live within-session metric trace panel

- **Status:** Accepted
- **Date:** 2026-08-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/tmaze/core/phase3/phase3_traces.py` +
  `host/tests/test_phase3_traces.py`, new `host/tmaze/ui/phase3_traces_view.py`,
  `host/tmaze/ui/experiment_control.py` (new "Metrics" tab + lifted
  `phase3_state` seam), `PROGRAMS.md`, `docs/glossary.md`
- **Supersedes / Superseded by:** —

## Context

The live Phase 3 GUI shows behaviour as *scalars* (the bottom-strip behavior
readout folds the published snapshot to accuracy / bias / reward rate / win-stay
via `phase3_stats`, ADR-0052) and as discrete per-trial cells (the trial ribbon).
Neither shows how a measure MOVES over a session. The operator specifically
wanted to watch a rat drift toward disengagement in real time - the offline
longitudinal analysis (ADR-0112) already treats the rolling inter-trial interval
as its engagement proxy, but that only exists post-hoc in the notebook.

The data needed is already published every poll on the immutable
`Phase3StateSnapshot` (ADR-0050): each completed row carries `trial_start_iso` /
`trial_end_iso` (the ITI stamps; trial start = first hub->stem departure, trial
end = `loop_closed` back at the hub, per `phase3_task`), outcome, choice, cue,
and the closed-loop context. So a live trace panel needs no new logging - only a
reduction from the snapshot to per-trial time series and a plot surface.

## Decision

Add a live trace panel as a pure core + thin shell (ADR-0083), on its own
top-level GUI tab.

1. **Pure core `phase3_traces.build_traces(snapshot, window_n, iti_window_n,
   min_den)`** emits
   one point per COMPLETED trial on a wall-clock x-axis (seconds since the
   earliest trial-start stamp) plus rolling series, reusing the settled
   definitions from `phase3_stats` (the ISO parser, the median helper, the arm /
   intervention labels, the ADR-0093 own-pair adjacency rule) so the live traces
   and the text readout never disagree. Series: rolling **median ITI** (the
   disengagement signal), rolling accuracy + per-cue accuracy, strategy rates
   (side bias, behavioural alternation = switch rate, win-stay, lose-shift,
   cue-follow), the signed **directional-error bias** (`dir_bias`, the
   session-level twin of the debias controller's signal, ADR-0090) and the
   scheduler's per-trial cue-aware alternation rate; plus contiguous
   INTERVENTION spans for shading. Stdlib only, headlessly tested.

2. **ITI is the DEAD TIME `trial_start[N] - trial_end[N-1]`**, not the
   start-to-start gap the offline `rolling_metrics` uses. Dead time is the
   interval the rat spends NOT initiating the next trial (loitering / resting at
   the hub), which is the engagement signal; the start-to-start gap conflates it
   with trial duration. Negative gaps (a clock anomaly) are dropped. Because ITI
   spans tens of seconds, the ~ms host-clock actuation latency that made short
   intervals unreliable (the "Choice RT" removal, ADR-0094) is negligible here.

3. **Default disengaged line = 45 s**, held as `phase3_traces.
   DEFAULT_DISENGAGED_GAP_S` so it stays in step with
   `analysis/mode_label.ModeThresholds.disengaged_gap_s`; it is a plotted
   threshold the view draws, not used to compute anything.

4. **Thin Tk shell `phase3_traces_view.py`** renders stacked matplotlib subplots
   (ITI / accuracy / strategy / bias) via `FigureCanvasTkAgg` (guarded import
   with a text fallback, as in `records_manager`, ADR-0087), pull-mode
   self-polling the published snapshot gated by `progress_signature` (like the
   ribbon). Configurable: the disengaged line, the rolling `window_n`, a SEPARATE
   `iti_window_n` for the ITI trace (blank = follow `window_n`, so the engagement
   signal can be smoothed over a shorter span and react faster than the rate
   traces), per-subplot show/hide toggles, and a "last N minutes" x-window; only
   `window_n`, `iti_window_n`, and the line feed the core, the rest are pure view
   state (a control change forces a redraw the change-gate would otherwise miss).

5. **Its own top-level "Metrics" tab**, not the Rig-view bottom strip (already
   full: ribbon + behavior readout + control bar + Session panel) - the stacked
   subplots need vertical room. The existing `phase3_state` snapshot getter was
   lifted from inside the behavior block to the top of `main()` so the tab factory
   and the behavior readout share one seam.

## Alternatives considered

- **Reuse the offline `analysis/rolling_metrics` for the live panel.** Rejected:
  it is a pandas/numpy pipeline over a loaded CSV bundle - forbidden in the pure
  core (`test_architecture`) and the wrong input shape (it reads a tidy frame, not
  the live snapshot). The live core mirrors its *definitions* in stdlib instead.
- **Start-to-start ITI (the offline proxy).** Rejected for the live engagement
  read (see decision 2); dead time is the cleaner disengagement signal.
- **Put the panel in the Rig-view bottom strip / as a behavior sub-tab.** The
  strip has no vertical room; a tabbed "Behavior" group would mean moving the
  existing behavior readout (a larger refactor of the ADR-0053 layout). A new tab
  is the smallest change that gives the plots room; the ribbon stays on Rig view
  for at-a-glance state.
- **Recompute + append a history in the view.** Rejected: rebuilding from the
  snapshot each redraw is O(trials) and trivially cheap for a few-hundred-trial
  session, and keeps the view stateless (no drift from the authoritative table).

## Consequences

- No new logging or protocol change; the panel is a read-only consumer of the
  ADR-0050 snapshot, so it can never perturb a run.
- matplotlib becomes a (soft) dependency of the live GUI for this tab only,
  guarded so its absence just shows an "install matplotlib" note.
- Excursion-based engagement (trips/min, non-stem fraction) is deferred: the
  excursion log (ADR-0109) is written to CSV but not published on the snapshot, so
  a live version needs new publishing - captured as FW-0042.
- `reward_rate` is intentionally omitted from the trace set (it pins near 100 %
  under the reward-incorrect shaping aid, ADR-0094).

## Evidence / links

- `host/tests/test_phase3_traces.py` (13 tests): time axis, dead-time ITI +
  threshold crossing, negative-gap drop, side-bias / switch / win-stay limits,
  min_den gating, undefined-without-errors `dir_bias`, and INTERVENTION spans.
  Full host suite green (833 passed / 17 skipped) incl. `test_architecture`.
- Builds on ADR-0050 (published snapshot), ADR-0052 (`phase3_stats`), ADR-0093
  (own-pair adjacency), ADR-0090 (`dir_bias`), ADR-0087 (matplotlib-in-Tk),
  ADR-0083 (package layout / pure-core), ADR-0112 (offline traces / the ITI
  engagement proxy this mirrors live).
