# ADR 0110: Record per-trial reward magnitude (pulse widths) and sum water per width

- **Status:** Accepted
- **Date:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_log.py`,
  `host/tmaze/core/phase3/phase3_stats.py`,
  `host/tmaze/core/session/session_summary.py`;
  `host/tmaze/ui/phase3_controller.py`; tests `host/tests/test_phase3_log.py`,
  `host/tests/test_phase3_stats.py`, `host/tests/test_session_summary.py`
- **Supersedes / Superseded by:** — (refines the water estimate of ADR-0073;
  keeps the task/operator disjointness of ADR-0084; uses ADR-0091's per-width
  volume curve)

## Context

The Phase 3 trial CSV records WHETHER a trial was rewarded (`rewarded`) and HOW
MANY lick top-up pulses it earned (`topups`), but not the pulse WIDTH delivered.
The end-of-session water estimate (`session_summary.estimated_water`, ADR-0073)
therefore multiplied the total pulse count by the ONE `reward_pulse_ms` config
value. Two problems:

1. If the operator changes the pulse width mid-session (a common shaping tweak),
   every earlier pulse is retroactively re-priced at the final width - the total is
   simply wrong.
2. Dispensed volume is NON-linear in pulse width (ADR-0091's measured curve), so
   even summing "pulse-milliseconds" and converting once would be inaccurate; the
   volume must be looked up per width.

The width is known at the instant each pulse fires (the dispenser's `FireReward.ms`
that the controller already sends to the firmware), so it can be recorded per trial
at no extra cost.

## Decision

Record the raw pulse width per trial, additively, and price water per width:

- `phase3_log.py`: `reward_fired(index, iso, pulse_ms=None)` and
  `topup_fired(index, pulse_ms=None)` now also store the width - a `prime_ms` cell
  and a running `topup_ms_total` sum - appended as the last two CSV columns
  (`prime_ms`, `topup_ms_total`). Raw ms only; NO calibration in the pure log.
  `pulse_ms` is optional so a caller that omits it (or old data) leaves the cells
  blank.
- `phase3_stats.py`: a calibration-FREE per-width tally
  `reward_pulses_by_ms: Dict[str,int]` over completed trials - one prime pulse at
  `prime_ms`, plus `topups` pulses whose per-pulse width is reconstructed from
  `topup_ms_total / topups` (top-ups within a trial share the run's width, so the
  division is exact). Keyed by width string so a mid-run change is representable.
  Empty for a pre-magnitude session.
- `session_summary.estimated_water`: when the tally is present, sum
  `reward_volume_ul(width) * count` per width (exact under a mid-run width change)
  and expose a `by_ms` breakdown (`pulse_ms` then lists the widths); otherwise fall
  back to the pre-0110 single-configured-width path. `SCHEMA_VERSION` 5 -> 6.
- `phase3_controller.py`: pass the fired `FireReward.ms` into `reward_fired` /
  `topup_fired`.

`estimated_water` (task) and `manual_water` (operator) stay DISJOINT and additive
(ADR-0084) - this change only makes the task side exact; it does not move any pulse
between the two channels.

## Alternatives considered

- **Store one summed pulse-ms per trial and convert once.** Rejected: volume is
  non-linear in width (ADR-0091), so a single conversion of summed ms is wrong when
  widths differ. Tallying by width and converting each is exact.
- **Keep pricing everything at the final `reward_pulse_ms`.** Rejected: that is the
  bug - a mid-run width change re-prices history.
- **Store the computed volume (uL) per trial in the log.** Rejected: the pure log
  must stay calibration-free (the volume curve is data that changes; ADR-0091). The
  log keeps raw ms; volume is computed downstream where the curve lives.
- **New columns first in the schema.** Rejected: appended LAST so existing
  offline readers and column letters are unaffected (the additive rule).

## Consequences

- Summing per-trial delivered volume now reconciles with `estimated_water`, and a
  session that changed pulse width mid-run reports the correct total.
- Two new CSV columns + one new `Phase3Stats` field + a richer `estimated_water`
  JSON block (with `by_ms`); all additive. `SCHEMA_VERSION` 6 lets an offline reader
  branch. Old bundles (no width columns) transparently use the fallback path.
- The per-pulse top-up width is reconstructed by division, which assumes top-ups
  within ONE trial share a width - true today (the width only changes between arms /
  operator edits, not within a well visit). If per-pulse widths ever vary within a
  trial, store them explicitly and supersede this.

## Evidence / links

- Core: `phase3_log.reward_fired` / `topup_fired` (+ `prime_ms` / `topup_ms_total`
  columns); `phase3_stats._reward_pulses_by_ms` (+ the `reward_pulses_by_ms` field);
  `session_summary.estimated_water` (per-width branch + `_as_float`), `SCHEMA_VERSION`
  bump.
- Wiring: `phase3_controller` reward + top-up fires now pass `fire.ms`.
- Tests: `test_phase3_log` (prime/top-up width recording + reset + blank
  back-compat), `test_phase3_stats` (`TestRewardPulsesByMs`: prime tally, top-up
  width reconstruction, mid-run change, empty fallback), `test_session_summary`
  (`TestEstimatedWaterPerWidth`: exact per-width sum + `by_ms`; the schema bump).
- Related: ADR-0073 (the water estimate), ADR-0091 (per-width volume curve),
  ADR-0084 (task vs operator water disjointness), ADR-0069 (prime + top-ups).
