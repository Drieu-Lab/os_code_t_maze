# ADR 0069: Give Phase 3 lick top-ups + a reward pulse/volume control via the shared WellDispenser

- **Status:** Accepted
- **Date:** 2026-07-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/phase3_controller.py`, `host/phase3_log.py`,
  `host/reward_dispenser.py` (adds `WellDispenser.reset`), `host/tooltip_text.py`,
  tests (`test_phase3_log.py`, `test_reward_dispenser.py`)
- **Supersedes / Superseded by:** — (builds on ADR-0068; reward path per ADR-0054)

## Context

Host-owned Phase 3 (ADR-0046) dispensed exactly **one** reward pulse per rewarded
trial: on the scored-choice tick the controller fired a single `R<port>1,<ms>`
through the shared dispatcher (ADR-0054), with the pulse width fixed at the
`Phase3Config.reward_pulse_ms` default (500 ms) and no way to change it from the
tab. The reward-shaping phases (Phase 1/2a/2b, the `WellRewardWithExcursionRule`)
have long done more: a **prime** pulse plus a few **lick top-ups** at the well
(extra pulses the rat earns by licking after the prime), and their pulse width is
editable. Phase 3 lacked both. On a water-restricted animal the per-session
volume is what titrates the daily supplement, so the operator wants to (a) set the
reward pulse width, (b) see the calibrated volume, and (c) top up on collection
licks, exactly as in the shaping phases.

ADR-0068 had just extracted the shaping-phase dispensing into a standalone pure
core, `reward_dispenser.WellDispenser` (owns port/pulse, the OUTSIDE/AT_WELL visit
lifecycle, prime delivery, and the top-up counter/cap/cooldown + lick freshness
gates). That is exactly the mechanism Phase 3 was missing, so the seam to reuse
already existed.

## Decision

Wire the **same `WellDispenser`** into the Phase 3 controller and add the two
missing controls to the tab.

- **Composition.** On arm, the controller builds a `WellDispenser` with
  `prime_on="credential"` (the mode where the prime fires the instant a credit is
  granted, not on well arrival). Phase 3's `Phase3Task` stays the "why/when" — it
  already decides `reward` on the scored-choice tick, playing the role
  `RewardTrigger` plays for the shaping rule — so no `RewardTrigger` is used here;
  the dispenser is driven directly. This keeps the *timing* identical to before:
  the prime still fires out at the choice, the moment the choice is scored.
- **Flow.** On a rewarded choice the controller calls
  `dispenser.prime_at_credential(now)` and routes the returned `FireReward`
  through the shared dispatcher. Every frame it calls `visit_tick(...)` to advance
  the well-visit lifecycle (so a later lick knows the rat is camping at the well),
  and each center-well lick (already snooped and routed to the actuation thread)
  calls `on_lick(...)`, whose top-up `FireReward`s are dispatched too, up to
  `max_topups`.
- **UI.** A "Reward (central well)" box adds a **pulse (ms)** Spinbox and a **max
  top-ups (licks)** Spinbox, plus a live **calibrated-volume readout**
  (`reward_volume_ul`/`format_volume`, ADR-0019): `≈ V/pulse · up to K pulses/trial
  = total`. Both apply on the next arm (like the seed / reward-incorrect flag).
  The status line now also shows the running **session dispensed volume**.
- **Clock.** The dispenser's lick-freshness/top-up timing needs a monotonic
  timestamp on the same clock as the frames. The maze pipeline stamps
  `state.now == mono_ts` (`time.monotonic()`; there is a test for it) and Rules
  feeds licks `time.monotonic()`, so the lick snoop now carries `time.monotonic()`
  alongside the ISO string, and `visit_tick`/`prime_at_credential` use
  `task_frame.now`. Same convention as the Rules path.
- **`WellDispenser.reset()` (new).** Clears the per-visit state (visit lifecycle,
  pending credential prime, top-up counter/timing) without touching the configured
  knobs or the lifetime `fires` tally. The controller calls it on re-arm and on
  Skip/Repeat/Back/Pause (`_silence_and_rearm`) so a prime that never reached the
  well can't carry a stale top-up window into the next trial.
- **Data log.** `phase3_log` gains an additive per-trial `topups` count column
  (blank until the first top-up); the prime stays the `rewarded=1` / `reward_cmd`
  it always was. A top-up lick lands after the trial has advanced, so it is
  attributed to the trial the prime was logged against (`_reward_trial_index`),
  the same "reward lands, then licks arrive" pattern the existing lick attribution
  already handles.

Constants: `DEFAULT_MAX_TOPUPS = 3` (starts on, matching the shaping phases;
operator can set 0), `PHASE3_TOPUP_COOLDOWN_MS = 0`, `PHASE3_LICK_DEBOUNCE_MS =
150` (lean on the debounce + the dispatcher's global rate backstop to space fires).

## Alternatives considered

- **Add a bespoke top-up counter in the Phase 3 controller.** Rejected: it would
  re-derive, in a second place, the exact visit-lifecycle + cap + cooldown +
  freshness logic ADR-0068 just centralized — the duplication that ADR was written
  to avoid, and a scientific-consistency risk (Phase 3 top-ups behaving unlike the
  shaping phases').
- **Use `prime_on="well_arrival"` (fire the prime when the rat reaches the well).**
  Rejected: Phase 3 has always fired the correct-choice pulse *at the choice* as
  immediate feedback; `well_arrival` would move the pulse to well entry and change
  observed behaviour. `credential` preserves the current timing exactly.
- **Per-trial reward-volume column(s) in the CSV.** Deferred: the session total
  (status line) is what matters for water titration, and a `topups` count plus the
  logged pulse width already lets volume be reconstructed downstream. A dedicated
  `reward_ul` column can be added later if needed.
- **Config-driven `phase3.json` (FW-0024) to persist these knobs.** Deferred to a
  separate change: Phase 3's other knobs (seed, min-confidence, reward-incorrect)
  are also plain tab state applied on arm, so adding two more is consistent; a
  config file is a broader, separately-reviewable enhancement.

## Consequences

- Phase 3 reward now behaves like the shaping phases (prime + capped lick top-ups)
  through one shared, tested core; the operator can set the pulse width and see the
  calibrated per-trial and session volumes.
- One clear ownership split: `Phase3Task` = why/when to reward, `WellDispenser` =
  how to dispense, `RewardDispatcher` = rate backstop + volume record + wire send.
- Defaulting `max_topups` to 3 changes the delivered volume versus the old
  single-pulse behaviour; an operator who wants the old behaviour sets it to 0.
- The global rate backstop (ADR-0054) still caps fire rate, so top-ups faster than
  the backstop interval are recorded as suppressed (not counted) — intended.
- Follow-up: FW-0024 (`phase3.json`) to persist the reward + task knobs; a
  per-trial reward-volume column if downstream analysis wants it without
  recomputation.

## Evidence / links

- Builds on ADR-0068 (Trigger/Dispenser split) and ADR-0054 (shared dispatcher);
  volume calibration per ADR-0019; lick-port/index mapping per ADR-0022; reward at
  the central port per ADR-0007.
- `mono_ts == state.now` guaranteed by `test_maze_pipeline.test_state_now_is_mono_ts`.
- Tests: `test_reward_dispenser.test_reset_clears_visit_and_credential`,
  `test_phase3_log.test_topups_count_increments` /
  `test_reset_trial_clears_topups`; full host suite green (403 tests).
