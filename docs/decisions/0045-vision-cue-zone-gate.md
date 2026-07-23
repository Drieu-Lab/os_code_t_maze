# ADR 0045: Host vision cue as a zone-membership gate (fast view + CueGate), with a redrawn return zone

- **Status:** Accepted
- **Date:** 2026-07-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/cue_gate.py` (new pure core) + `host/test_cue_gate.py`,
  `host/cue_controller.py` (new Cue tab / actuator) + `host/experiment_control.py`
  (tab wiring), `host/tooltip_text.py` (`cue.*` tips), `host/maze_state_validate.py`
  (cue-onset measurement), `zones.json` (LEFT_RETURN / RIGHT_RETURN redraw — a
  human annotation step, not code)
- **Supersedes / Superseded by:** —

## Context

ADR-0044 gave the host the wire commands to drive the tone (`CUE_ON,<kind>` /
`CUE_OFF`, gated by `CUE,VISION`). This ADR is the HOST half: how the ON/OFF
decision is computed from the rat's tracked position, and how we measure that the
onset is spatially tight enough for the place x auditory-cue work (BRAINK99).

Constraints Celine fixed in planning:

- The cue is POSITIONAL: the tone is ON strictly while the rat is inside a spatial
  window and cut the instant it leaves either end; a committed-then-reversed rat
  gets a partial cue.
- CHOICE window = the central stem between the ENTRY (S0) and EXIT (S1) beams,
  entered heading up-stem. RETURN window = a segment at the single-per-side return
  sensor. Rather than add a new centerline + cm annotation, reuse the existing
  `LEFT_RETURN` / `RIGHT_RETURN` zones, redrawn so their length along the arm
  matches the central-stem zone (the human does this in the zone editor).
- CHOICE fails OPEN on low confidence (don't miss a real cue); RETURN fails SAFE
  (skip a return whose entry is unconfident — a missed cue beats a mis-placed one).
- Read the FAST (low-dwell) maze-state view (ADR-0042) so onset commits in ~1
  frame, and actuate off the Tk poll on a dedicated thread (ADR-0043).

## Decision

**Reuse the maze-state estimator's OUTPUT as the spatial window** — no projection,
cm, or overlay math. `host/cue_gate.py` is a stateful pure core (stdlib only, like
`maze_state.py` / `task_events.py`): `CueGate.step(zone, direction, confidence,
valid) -> CueDecision`. The CHOICE window is `zone == CENTRAL_STEM` (and, by
default, `direction == up_stem`); the RETURN window is `zone in {LEFT_RETURN,
RIGHT_RETURN}`. The ENTRY/EXIT beams already bound the drawn `CENTRAL_STEM`
polygon, so "between the beams" is exactly that zone; the return window is the
redrawn return zone.

**Fail-open vs fail-safe as an onset rule, holding through dropouts.** Invalid
frames HOLD the current tone and change no edges (a dropout must not chop a playing
cue nor look like a leave/re-enter). On valid frames: CHOICE keeps re-attempting
onset while in the window (a boundary dropout only delays it); RETURN takes a
ONE-SHOT decision when the rat enters the return zone — fire if confident, else
latch "skipped" for the whole visit. `min_confidence` is an extra onset floor on
top of the estimator's own validity threshold.

**Thin shell owns I/O, mirroring the Rules tab (ADR-0043).** `cue_controller.py`
subscribes to the pipeline's fast view, enqueues each bundle, and a single
`cue-actuation` thread is the sole caller of `CueGate.step` + `send`, turning
`started`/`stopped` edges into `CUE_ON,<kind>` / `CUE_OFF`. The Tk poll only
publishes an atomic gate snapshot (armed + firmware in Full task + tracking +
tunables). On the gate's rising edge it sends `CUE,VISION` (take the DAC); on the
falling edge `CUE_OFF` then `CUE,BEAM` (restore firmware autonomy).

**Redraw the return zones instead of adding a centerline.** `LEFT_RETURN` /
`RIGHT_RETURN` are redrawn to the central-stem length at the sensor. `zones.json`
is a per-rig annotation (not versioned); no code changes.

**Measure the onset offline.** `maze_state_validate.py --cue` replays the FAST
estimator + CueGate over a recorded track and aligns each onset to its
ground-truth beam (ENTRY / L,R RETURN), reporting the signed vision-decision
latency. The command/serial/firmware component is measured separately on the rig
via the firmware `CUE,ON` echo (ADR-0044). The self-test covers a synthetic lap
(choice cue matches ENTRY, return cue matches LRETURN).

## Alternatives considered

- **A dedicated return-stem centerline + distance-`d`-in-cm window** (the original
  plan). Rejected: it duplicates zone logic, needs new annotation + a cm scale on
  the return path, and a second projection code path. Reusing the redrawn zone
  gives the same window with the estimator we already trust.
- **A raw beam/proximity trigger.** Rejected in ADR-0044 (a beam is an event, not
  a state) and here (proximity is not the drawn window; the estimator's committed
  zone is the intended semantic).
- **Read the debounced display view.** Rejected: its `min_dwell` (~3 frames) adds
  ~2 frames of onset lag; Celine asked for the fast view. The self-test shows the
  fast choice cue firing ~50 ms earlier than the debounced zone entry.
- **Cut the tone on any confidence dropout (symmetric fail-safe).** Rejected for a
  playing tone: a 1-frame dropout would chop it. Fail-safe is applied only at the
  RETURN onset edge, where a mis-placed cue is the real harm.
- **Choice cue without the up-stem requirement.** Rejected as default: the rat
  re-runs the stem downward between laps and would re-trigger the choice tone.
  Exposed as a toggle for bring-up.

## Consequences

- The return zones' semantics NARROW: `LEFT_RETURN` / `RIGHT_RETURN` now denote
  only the first stem-length segment of the arm. `loop_completed` is unaffected
  (it keys off the CHOICE arms; `maze_state._commit`), and the `clean_loop`
  credential only needs a single visit edge through the matching return arm
  (`task_events`: `visited_since(ret, HUB)`), which the redrawn zone still trips —
  so behavioral logging/scoring is preserved. Return-zone DWELL time in the state
  log is no longer whole-arm.
- Two independent actuators now attach to the fast view (Rules in Free Roam, Cue in
  Full task). They are gated to disjoint firmware modes, so they never both drive
  the DAC; each still holds its own gate snapshot + thread.
- The cue path adds no cm calibration dependency: it is purely zone-membership, so
  it works as soon as zones are drawn.
- If future work moves the trial sequence to the host, the gate is unchanged (it
  emits `CHOICE`/`RETURN`; identity stays a firmware concern per ADR-0044).

## Evidence / links

- ADR-0044 (the wire commands this drives), ADR-0042 (fast view), ADR-0043
  (actuation thread pattern mirrored here), ADR-0025 (maze state / zones),
  ADR-0026 (`task_events`, `clean_loop`).
- `host/test_cue_gate.py` — fail-open / fail-safe / dropout / reset coverage.
- `maze_state_validate.py --selftest` — synthetic lap: choice onset +110 ms vs the
  ENTRY beam, return onset +60 ms vs LRETURN, 0 spurious onsets.
- `docs/future-work.md` FW-0014 / FW-0008 (the tasks this closes the host half of)
  and the sequence-to-host migration item.
