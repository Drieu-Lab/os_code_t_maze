# ADR 0036: Session timer in the behavior readout - wall-clock span, separate from active time

- **Status:** Accepted
- **Date:** 2026-07-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/session_timer.py` (pure core) + `host/test_session_timer.py`;
  `host/behavior_view.py` (Start/Stop wiring, session label); `host/tooltip_text.py`
  (`behavior.session`)
- **Supersedes / Superseded by:** builds on [ADR-0032](0032-live-behavior-readout.md)
  (the live behavior readout); —

## Context

The experimenter asked for a session timer in the behavior readout - a start
time, a stop time, and a running duration (`docs/notes/Bugs.md`
`#260710_SessionTimer`). The behavior panel (ADR-0032) already has a manual
**Start/Stop** button and a **Reset**, so the natural control surface already
exists; only a clock was missing.

The panel already tracks `behavior_stats.active_s` - but that is a different
quantity. `active_s` is *active* time: it sums the dt between poll ticks only
while running and measuring, drops over-long gaps, and is paused via `mark_gap()`,
because its job is to denominate the per-minute rates (trials/min, m/min). It is
deliberately not the wall-clock length of the session, and it would be the wrong
number to show an experimenter asking "how long has this session been going?"

## Decision

Add a small pure core `host/session_timer.py` (`SessionTimer` +
`SessionTimerSnapshot`), stdlib-only and headless-testable like the other cores,
and drive it from the panel's existing Start/Stop button.

- **Duration is wall-clock SPAN, including pauses.** `elapsed_s = now - start_ts`
  while running, else `stop_ts - start_ts`. If the experimenter stops and
  restarts, the original `start_ts` is kept and the span keeps running from the
  first Start (the paused stretch is included). This is the complement of
  `active_s`, not a duplicate of it.
- **Anchors:** the first Start since a reset sets `start_ts`; each Stop sets
  `stop_ts`. Redundant start/stop calls are no-ops.
- **`now` is injected** (epoch seconds) so the core is fully testable without the
  wall clock; the view passes `time.time()`.
- **Reset semantics match the rest of the panel:** the Reset button and a
  firmware `MODE` switch (a new session) clear the timer, exactly as they reset
  the behavior tallies.
- The view formats it as `Session: started HH:MM:SS  -  H:MM:SS` while running and
  `Session: HH:MM:SS - HH:MM:SS  (H:MM:SS)` once stopped.

## Alternatives considered

- **Reuse `active_s` as the duration.** Rejected: it excludes pauses and drops
  gaps by design (it exists to denominate rates), so it would under-report the
  session length and confuse the two concepts. Keeping them separate lets each
  stay correct for its purpose.
- **Add the timer state to `BehaviorStats`.** Rejected: the timer is driven by
  button actions (start/stop), not the per-tick `update()`, and its span-includes-
  pauses semantics are intentionally opposite to `active_s`. A separate one-purpose
  core keeps both cores simple and independently testable.
- **Sum only running intervals (exclude pauses).** Rejected: that is closer to
  `active_s` again; the experimenter wanted a plain session clock (start -> now),
  and a span that visibly includes a pause is the less surprising reading.

## Consequences

- The experimenter gets a glanceable session clock next to the behavior measures,
  on the tab they already run sessions from.
- Two time concepts now coexist deliberately: session span (this timer) and
  active time (`active_s`, rate denominator). The tooltip calls out the difference.
- If a pause-excluding "running time" display is ever wanted too, it can be added
  from `active_s` without touching this core.

## Evidence / links

- Code: `host/session_timer.py` (`SessionTimer`, `SessionTimerSnapshot`),
  `host/behavior_view.py` (Start/Stop -> start/stop, `_session_text`, `_clock`,
  `_duration`), `host/tooltip_text.py` (`behavior.session`).
- Tests: `host/test_session_timer.py` - running elapsed grows with stop_ts None,
  start/stop span, restart keeps original start and latest stop updates, double
  start / stop-without-start no-ops, reset clears, pre-start empty state.
- Builds on ADR-0032 (live behavior readout). Resolves `#260710_SessionTimer`.
