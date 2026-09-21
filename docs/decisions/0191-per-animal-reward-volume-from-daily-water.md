# ADR 0191: Size the Phase 3 reward per animal from its daily water target

- **Status:** Accepted
- **Date:** 2026-09-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/records/animals.py` (schema v3 -> v4:
  `daily_water_ml`, `reward_ul`), `host/tmaze/core/phase3/session_cap.py`
  (`reward_ul_for_target`, `resolve_reward_ul`, `water_target_summary`),
  `host/tmaze/core/reward/reward_volume.py` (`pulse_ms_for_volume`,
  `round_pulse_ms`, `PULSE_MS_STEP`), `host/tmaze/core/phase3/phase3_log.py`
  (`reward_pulse_source=` / `daily_water_ml=` header line),
  `host/tmaze/core/session/session_summary.py` (v8 `water_target` block),
  `host/tmaze/ui/phase3_controller.py` (`_resolve_reward_pulse`,
  `resolved_reward_policy`, `water_target`, readout + banner),
  `host/tmaze/ui/session_controller.py` (manifest `provenance.reward_policy`,
  summary block), `host/tmaze/ui/records_manager.py` (Roster columns/fields),
  `host/phase3.json` (`reward.pulse_ms` 470 -> 365 fallback),
  `../os_code_t_maze_resources/records/animal/animals.json` (v4; 8 / 9 / 6 mL);
  tests `test_animals`, `test_session_cap`, `test_reward_volume`,
  `test_session_phase3_wiring`, `test_session_summary`
- **Supersedes / Superseded by:** extends ADR-0019 / ADR-0091 (calibrated
  volume), ADR-0069 (prime + top-ups), ADR-0186 (per-animal overrides in
  `animals.json`); paired with ADR-0190 (session cap)

## Context

ADR-0190 caps a session at 120 scored trials (or 45 min). A cap alone does not
say how much water a session is worth; that is the reward volume, which until
now was one number for every rat (`reward.pulse_ms`, 465-470 ms ~ 90 uL of
10 % sucrose on the centre port). The three rats differ in what they drink in a
day - about 7-8 mL (600), 8-9 mL (601) and 5-6 mL (602) - so the same volume
means a different share of the day's water for each, and at 120 trials x 90 uL
a chance-level rat (60 correct) would still earn 5.4 mL: most of a day for 602,
about two-thirds for 600.

The operator's requirement, stated for this change: lower the per-trial volume
(to about 70 uL), and make it **animal-specific** so that 50 % of 120 trials
dispenses about **50 % of that animal's daily water** - a perfect session earns
the whole day, and the rest is given as a supplement in the home cage later.
Both a derived (from daily mL) and an explicit (uL) per-animal setting were
asked for. The operator then asked, for now, for **one constant 70 uL reward
for every rat** so the per-trial reward signal is the same magnitude across the
cohort, while still seeing "earned X % of the daily target, supplement Y mL" at
Stop - which is why the explicit volume takes priority over the derived one and
the daily target is kept as the readout's denominator.

## Decision

1. **`animals.json` schema v4** adds two optional per-animal numbers, each a
   positive number or `null` (unset):
   - `daily_water_ml` - the animal's daily water target (mL);
   - `reward_ul` - an explicit per-correct-trial volume (uL).
   The live file sets `daily_water_ml` to 8 / 9 / 6 for 600 / 601 / 602 and
   `reward_ul` to 70 for all three. `AnimalRegistry.daily_water_ml()` / `reward_ul()`
   read them; `normalize_records` coerces, `validate_records` refuses a
   non-number or a value <= 0 before a write (a blank is unset, never zero).
   The Records Manager Roster gains `daily water (mL)` and `reward (uL)`
   columns and entry fields, validated at Add, and both fields are in the
   change-log diff.
2. **The reward rule** (`session_cap.reward_ul_for_target`):

       uL per correct trial = daily_water_ml x 1000 x water_at_chance_fraction
                              / (max_trials x CHANCE_ACCURACY)

   with `CHANCE_ACCURACY = 0.5` fixed in code (an alternating or side-biased rat
   is right half the time) and `water_at_chance_fraction` (0.5) and
   `max_trials` (120) from the `session_cap` section. For the cohort this
   would give 66.7 uL (600), 75 uL (601), 50 uL (602). A rat at chance earns
   half its day; a perfect rat earns exactly all of it; `supplement = target -
   earned` is zero on a perfect day, not a fixed top-up. This rule is what the
   rig uses when the animal has no explicit `reward_ul` (item 3).
3. **Priority** (`session_cap.resolve_reward_ul`): `reward_ul` (explicit) ->
   `daily_water_ml` (derived, item 2) -> the tab's Pulse field / `phase3.json`
   (`source = "config"`). The explicit volume wins because it is the operator
   pinning the reward magnitude - today 70 uL for every rat, so the reward
   signal is the same size across the cohort while cue-following is being
   shaped; the derived rule stays available by blanking `reward_ul`. Whichever
   branch sizes the reward, `daily_water_ml` remains the water *target* the
   Stop summary measures earned water and the supplement against (with 70 uL
   x 120 trials = 8.4 mL, a perfect day earns 105 / 93 / 140 % of 8 / 9 / 6 mL;
   a chance day 53 / 47 / 70 %). The Pulse field therefore only matters for an
   animal with neither number set; the tab's volume readout says so when it is
   being overridden (`[per-animal: reward_ul 70 uL in animals.json -> 365 ms;
   the Pulse field is ignored]`), and the status line shows
   `reward:365ms(animals.json)`.
4. **Volume -> pulse width** goes backwards through the same calibration curve
   the forward estimate uses (`reward_volume.pulse_ms_for_volume`, the inverse
   of the piecewise-linear `reward_volume_ul` over `reward_calibration.csv`,
   same `(port, liquid)` fallback chain), then rounds to `PULSE_MS_STEP = 5` ms
   (`round_pulse_ms`, floored at one step so a derived width is never 0 ms).
   Resolved on the Tk poll like the cue mapping (ADR-0186) and applied on arm:
   365 ms (70 uL) for all three rats today; the derived rule would give
   350 / 390 / 275 ms for 600 / 601 / 602. A volume the curve cannot invert
   falls back to the field.
5. **`phase3.json` `reward.pulse_ms` 470 -> 365** (~70 uL) as the fallback for
   an animal with no per-animal number - the operator's requested default,
   folded in here in place of their uncommitted 465 -> 470 edit.
6. **Provenance.** The trial-CSV header gains
   `# reward_pulse_source=<config | animals.json:daily_water_ml |
   animals.json:reward_ul> daily_water_ml=<mL or blank>` (additive; no
   `format_version` bump); the manifest `provenance.reward_policy` records
   `{pulse_ms, source, daily_water_ml, reward_ul}`; the summary (v8) gains a
   `water_target` block `{daily_water_ml, earned_ml, fraction, supplement_ml}`
   and the text line `Water target: earned 5.50 mL = 69% of 8.0 mL target,
   supplement 2.50 mL`, and the ADR-0190 cap note carries the same sentence so
   the supplement can go straight into the daily log at Stop.

## Alternatives considered

- **One lower global volume (70 uL) only, via `phase3.json`.** This is
  effectively today's setting, but done through `phase3.json` alone the summary
  could not say what share of each rat's day was earned or how much supplement
  is owed; keeping the target per animal gives the readout a denominator and
  makes the constant-volume choice explicit and per-animal in the records.
- **Derived volume taking priority over the explicit one** (the first draft of
  this ADR). Rejected once the operator asked for one constant magnitude across
  rats: with the derived rule on top, pinning 70 uL would have meant blanking
  each rat's daily target and losing the readout. With the explicit volume on
  top, both intents are expressible: fill `reward_ul` to pin, blank it to
  derive.
- **Explicit per-animal `reward_ul` only, no target.** Hides the intent: the
  operator would recompute uL by hand whenever the cap or a rat's intake
  changes, and nothing could report the shortfall.
- **Store the target in `daily_log.csv` (per day) rather than `animals.json`.**
  Intake varies day to day, but the *target* is a stable per-animal setting
  (like the cue mapping), and the rig reads `animals.json` at Session Start
  already (ADR-0075/0186); a per-day override can follow if needed (FW-0071).
- **Use the measured `estimated_water` since Session Start for `earned`.** The
  summary's task-water figure is scoped "since Phase 3 arm" (FW-0028); the
  `water_target` block uses the same run tally (`_reward_ul`) so the two
  figures agree, and the arm-before-Start trials are already flagged
  (`pre_start_trials`).
- **A finer pulse step (1 ms).** The calibration is measured at 5-10 ms
  spacing and the firmware pulse is in ms; 5 ms keeps a derived width a round,
  re-typeable number (350, not 351.4) at < 1.5 % volume error.
- **Adjust the volume mid-session as accuracy unfolds.** Rejected: the volume
  is part of the task's fixed contingency; a moving reward confounds learning.

## Consequences

- With the explicit 70 uL pinned, "50 % of trials = 50 % of the day" holds
  only approximately (53 / 47 / 70 % for 600 / 601 / 602); the Stop-time
  summary and the cap note print the actual fraction and the supplement owed
  (floored at 0 mL, so a rat that out-earns its target reads "supplement
  0.00 mL"). Blanking `reward_ul` restores the exact rule.
- On a time-capped session (rat 602 near 90 trials) even a perfect run earns
  less than the day, by design - the clock is the backstop, not the target.
- Changing a rat's explicit volume - or, when it is blank, its daily target,
  the cap's `max_trials`, or `water_at_chance_fraction` - changes its pulse
  width on the next arm; the header / manifest / summary record which values
  produced it.
- `reward.pulse_ms` in `phase3.json` is now only a fallback; the tab still
  shows it and says when it is overridden.
- `animals.json` v3 files still load (fields unset); the builders in the
  resources repo read only `id` / `aliases` / `date_of_birth` and are
  unaffected. The Roster save upgrades the file to v4.
- Pre-v4 bundles have no `reward_pulse_source=` token; readers treat absence
  as `config`.

## Evidence / links

- Requirement + daily intakes: operator, 2026-09-21 planning conversation
  (8 / 9 / 6 mL for 600 / 601 / 602).
- `host/reward_calibration.csv` (center, 10 % sucrose): the inverse yields
  365 ms for 70 uL and 350 / 390 / 275 ms for 66.7 / 75 / 50 uL
  (`tests/test_reward_volume.py::TestRealCalibration`).
- Code: `session_cap.reward_ul_for_target` / `resolve_reward_ul` /
  `water_target_summary`; `reward_volume.pulse_ms_for_volume` /
  `round_pulse_ms`; `Phase3ControlView._resolve_reward_pulse`;
  `AnimalRegistry.daily_water_ml` / `reward_ul`.
- Tests: `tests/test_animals.py::TestWaterBudget`,
  `tests/test_session_cap.py::TestRewardRule / TestResolve / TestWaterTarget`,
  `tests/test_reward_volume.py::TestInverse`,
  `tests/test_session_phase3_wiring.py` (explicit uL wins and the target is
  still recorded; daily target derives the reward when the uL is blank; config
  last resort; cap note carries the target),
  `tests/test_session_summary.py::TestSessionCapAndWaterTarget`.
- Related: ADR-0190 (cap), ADR-0019 / 0091 (calibrated volume), ADR-0069
  (prime + top-ups), ADR-0075 / 0161 / 0186 (`animals.json` schema history),
  ADR-0155 (records change log), FW-0028 (summary scope).
