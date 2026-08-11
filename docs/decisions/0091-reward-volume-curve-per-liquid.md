# ADR 0091: Measured reward-volume curves per liquid, in a data file driven by the Reward field

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/reward/reward_calibration.py` (new), `host/tmaze/core/reward/reward_volume.py`, `host/tmaze/core/reward/reward_dispatcher.py`, `host/tmaze/tools/reward_calibrate.py` (new), `host/tmaze/ui/session_controller.py`, `host/tmaze/ui/experiment_control.py`, `host/reward_calibration.csv` (new data file), `host/tests/test_reward_calibration.py` + `test_reward_volume.py` (new), `host/tests/test_reward_dispatcher.py`
- **Supersedes / Superseded by:** Supersedes the single-point, in-code-constant calibration decision of [ADR-0019](0019-reward-volume-and-pulse-duration-logging.md) (the rest of 0019 - logging pulse duration on the wire, host-side volume tracking - still stands)

## Context

The host maps each reward pulse to a dispensed volume so the GUI can total a
session's reward (to titrate the daily water-restriction top-up; animals are on a
water-restriction protocol, `os_code_t_maze_resources/animal_protocol/RA23A232.pdf`).

[ADR-0019](0019-reward-volume-and-pulse-duration-logging.md) put that calibration
in code as a single point, `REWARD_UL_CALIBRATION = {300: 60.0}`, and assumed
volume is proportional to pulse width (an origin-anchored line). Its own comment
flagged the reality: reward delivery is "roughly - but not exactly - linear."
Three things make the single point insufficient:

- **One point can't show the curve's shape.** Valve opening/closing dynamics and
  line priming make the low end non-proportional; only measured points at several
  widths reveal it.
- **Viscosity matters.** The rig runs two reward liquids - **water** and **10%
  sucrose** - and the more viscous sucrose is expected to dispense a different
  volume at the same pulse width. A single water-anchored line mis-estimates
  sucrose, which is the *default* reward liquid in use.
- **Density makes mass a poor proxy.** Measuring by weight (mass -> volume)
  depends on the liquid's density, so it would need per-liquid density constants;
  a direct volume read does not.

## Decision

Measure the curve directly, per liquid, store it in a data file, and pick the
active liquid from the GUI field that already names it.

- **Direct-volume, many-pulses method.** For each pulse width, fire N identical
  pulses into the (empty) well, aspirate all the dispensed liquid with a
  graduated syringe, and read the total volume; `uL_per_pulse = measured_uL / N`.
  Volume (not mass) so it is density-independent for any reward liquid; N pulses
  so a readable few mL accumulates (at ~60 uL/300 ms, N=50 -> ~3 mL).
- **Per-(port, liquid) data file.** `host/reward_calibration.csv` (beside the
  package like `rules.json` / `rig_scale.json`; versioned + reproducible) holds
  one row per `(port, liquid, pulse_ms)` with the per-pulse `volume_ul` plus
  provenance (`n_pulses, measured_ul, date, operator, notes`), behind a leading
  `# schema_version=1` line (mirrors the records CSVs). A pure core
  `reward_calibration.py` owns the liquid vocabulary (`KNOWN_LIQUIDS` +
  `normalize_liquid`, so "10% sucrose"/"10% sucros"/"sucrose" all key to
  `sucrose_10pct`), the per-pulse math, and the CSV read/write/upsert.
- **Port + liquid-aware lookup with a graceful fallback.**
  `reward_volume.reward_volume_ul(dur_ms, port="center", liquid=DEFAULT_REWARD_LIQUID)`
  loads the file (cached; `reload_calibration()` to refresh) and interpolates over
  the matching curve. `DEFAULT_REWARD_LIQUID = "sucrose_10pct"` (the current
  reward liquid). Fallback chain: requested `(port, liquid)` -> `(port, water)` ->
  the in-code central bench point (`{300: 60.0}`), so an un-measured liquid/port
  or a missing file degrades sensibly instead of crashing. With one point a curve
  is still treated as proportional (0019's behaviour), so nothing regresses until
  real points are added.
- **The shared dispatcher carries the active liquid.**
  `RewardDispatcher` gains a settable `active_liquid` (default
  `DEFAULT_REWARD_LIQUID`) and scores each fire against it. There is one shared
  dispatcher across both task controllers (ADR-0054), so this is rig-global.
- **The Session panel's existing "Reward" field drives it.** That field
  (`reward_type`, ADR-0079) already names the liquid and is sticky between
  sessions, so it is the natural "what's in the line" source. It becomes an
  editable combobox pre-filled with the known liquids (still free-typeable for
  logging), and on edit / on Start its value is normalized and pushed to
  `dispatcher.set_active_liquid`. The free text is still logged verbatim in
  `session_meta.json`; normalization only affects the curve key.
- **Guided tool.** `python -m tmaze.tools.reward_calibrate` fires the sweep at a
  chosen port/liquid through the broker (same `R<port><sol>,<ms>` command the GUI
  uses, port + reward-solenoid index read from `rig_config`), prompts for each
  aspirated volume, and upserts the points into the CSV.

## Alternatives considered

- **Just add rows to the in-code dict (ADR-0019 shape).** Rejected: re-measuring
  would keep editing code, and the dict had no liquid dimension.
- **Measure by mass balance.** Rejected: mass -> volume depends on density, so it
  needs a per-liquid density constant; the reward liquid isn't always water. A
  graduated-syringe read is direct and density-independent (operator's call).
- **A separate GUI control to pick the active liquid.** Rejected as redundant:
  the Session panel already has a "Reward" field naming the liquid; reusing it
  (normalized, fallback-safe) avoids a second source of truth to keep in sync.
- **Fit a smooth/non-linear curve.** Deferred: piecewise-linear over measured
  points is enough for a per-session total; a fit can come later if the residuals
  warrant it (see FW-0032 area).
- **Bundle the CSV inside the package.** Chose beside-the-package `host/` instead,
  matching the other per-rig runtime configs (`rules.json`, `rig_scale.json`) so
  re-measuring never touches package code.

## Consequences

- Reward totals become data-backed and liquid-correct once the sweeps are run;
  until then behaviour is unchanged (the seed row is the original water
  `300 ms -> 60 uL`, and the fallback chain reproduces 0019's proportional line).
- Volume accounting now depends on the "Reward" field being set correctly. It is
  free text, so a typo could mis-key a curve; mitigated by `normalize_liquid`
  (incl. the "10% sucros" typo), the combobox, and the always-safe fallback
  (never crashes, worst case uses the water/in-code curve).
- Only the central spout is characterized today; the file format is already
  per-port, so left/right slot in later with no code change (see FW-0032).
- No firmware/protocol change; the calibration tool reuses the existing manual
  solenoid command and the broker.

## Evidence / links

- Supersedes the single-point calibration of [ADR-0019](0019-reward-volume-and-pulse-duration-logging.md);
  shared dispatcher seam [ADR-0054](0054-unified-reward-dispatcher.md); sticky
  Session-panel metadata / the "Reward" field [ADR-0079](0079-sticky-session-metadata-in-bundle.md);
  host reads the firmware pin map [ADR-0021](0021-host-reads-rig-config-pin-map.md).
- Deferred per-port characterization: `docs/future-work.md` FW-0032.
- Tests: `host/tests/test_reward_calibration.py` (math, `normalize_liquid` incl.
  the typo, CSV round-trip/upsert), `test_reward_volume.py` (proportional /
  interpolation / per-liquid / fallback chain / missing file), and the
  `active_liquid` cases in `test_reward_dispatcher.py`; full suite green (627 tests).
- Originating idea: `docs/notes/Ideas.md` `#260810_SolenoidVolumeCurve` (triaged
  out by this change).
