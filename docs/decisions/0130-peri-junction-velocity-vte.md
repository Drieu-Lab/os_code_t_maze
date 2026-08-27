# ADR 0130: Peri-junction velocity as the VTE deliberation readout

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/analysis/trajectories.py`,
  `host/tests/test_analysis_trajectories.py`,
  `host/notebooks/grant_figures/proposal_figures.ipynb`
- **Supersedes / Superseded by:** — (builds on ADR-0128)

## Context

ADR-0128 gave the proposal a VTE (vicarious-trial-and-error) proxy: seconds of
DWELL in `CENTRAL_STEM` u `JUNCTION` on the outbound run. Watching the animals,
the operator noted a sharper signal: a rat usually accelerates out of the hub and
blasts through the mid-stem (~130 cm/s) straight into an arm, and only
occasionally decelerates or briefly stops near the junction. So the *speed profile
around the junction* is a more sensitive deliberation readout than raw dwell time
(a fast, confident pass and a slow, deliberative one can log similar dwell if the
geometry is the same). This ADR adds that velocity readout ALONGSIDE the dwell
panel, not replacing it.

Turning per-frame position into a junction-anchored speed signal forces a few
choices a future reader will question:

- **What is "speed", in real units?** The sidecar stores the Kalman-smoothed
  centroid in PIXELS (ADR-0014); a proposal figure wants cm/s.
- **How is each trial aligned so trials with different timing overlap?** A
  time axis does not line trials up at the choice point; a spatial axis does.
- **Where is the stem axis?** To measure "distance from the junction along the
  stem" we need that axis, but `host/zones.json` carries only zone polygons - its
  optional `stem_axis` field is absent on the rig maps.
- **What counts as a "pause"?** The pause-time scalar needs a speed threshold, and
  a bad value makes the metric all-or-nothing.

## Decision

Pin the velocity definitions in `tmaze.analysis.trajectories` (constants +
tested pure helpers), consumed by the notebook's new "Panel 5":

- **Speed in cm/s** = central-difference speed of the smoothed centroid
  (`np.gradient` over `host_unix_s`, so uneven frame spacing is handled) times the
  bundle's `cm_per_px` (from `config/rig_scale.json`, ~0.233; `load_scale`, with a
  documented `DEFAULT_CM_PER_PX` fallback), then a centred rolling mean over
  `DEFAULT_SMOOTH_N = 5` frames to tame centroid jitter without blurring a real
  slow-down. (`rig_scale.json` `homography` is still null, so this is a single
  linear scale, adequate for a speed magnitude; the same reason ADR-0128 skipped a
  cm axis for dwell.)
- **Junction-anchored SPATIAL profiles.** Add a signed distance
  `s_junction_cm` = the centroid projected onto the stem axis IN PIXEL space
  (exact; normalized space distorts x vs y differently since width != height), in
  cm, with 0 at the junction, negative down the stem toward the hub, positive into
  the arm. Plotting speed vs `s_junction_cm` lines every trial up at the junction
  (x = 0) regardless of timing.
- **Stem axis = HUB-centroid -> JUNCTION-centroid** (`stem_axis`), used because the
  zones file has no explicit `stem_axis`. The stem is near-vertical and the arms
  diverge in x, so the line joining those two polygon centroids IS the stem
  direction with the junction at its tip. `stem_axis` still PREFERS an explicit
  `ZoneMap.stem_base/stem_tip` if a future map carries one.
- **Three deliberation scalars** per trial (`junction_kinematics`): `min_speed_cms`
  (slowest smoothed speed while inside `JUNCTION`), `peak_stem_speed_cms` (fastest
  while inside `CENTRAL_STEM`), `slowing_index = 1 - min_junction/peak_stem` clipped
  to `[0, 1]` (0 = barrels through, ->1 = near-stop at the junction), and
  `pause_time_s` = seconds inside `JUNCTION` below `DEFAULT_PAUSE_SPEED_CMS`. Zone
  membership reuses the LIVE `maze_state.ZoneMap.classify_point` (offline == on-rig,
  same as ADR-0128); the pause integral is the same left-Riemann sum as the dwell.
- **Pause threshold `DEFAULT_PAUSE_SPEED_CMS = 10.0`.** Set from a read-only
  measurement on rat 602 blocks (8/25-26): peak stem speed ran ~130 cm/s
  (p10-p90 ~87-165) while the junction MINIMUM speed spanned ~1-60 cm/s
  (median ~13-19). 10 cm/s is ~8 % of the typical run speed - well below normal
  locomotion, so it flags a genuine near-stop / deliberation rather than an
  ordinary turn - and sits just under the median junction minimum, so it separates
  the trials that actually paused.
- **Profile window `-70..+10 cm` in `2.5 cm` bins** (`PROFILE_S_MIN_CM`,
  `PROFILE_S_MAX_CM`, `PROFILE_BIN_CM`): the same 602 check showed the outbound
  window spans roughly `-74 cm` (stem foot) to a few cm into the arm, and junction
  detection fired exactly once per trial (median 1, range 1-1), so a single
  junction window per run is safe.

`mean_profile` bins the pooled per-trial `(s_junction_cm, speed_cms)` samples into
a mean +/- SEM band for the across-trial curve; `kinematics_per_trial` mirrors the
existing `dwell_per_trial` (one row per trial, tracking-dropout trials surfaced via
`n_samples`).

## Alternatives considered

- **Keep only the dwell panel (ADR-0128)** - rejected per operator observation:
  dwell can't tell a fast confident pass from a slow deliberative one; the speed
  dip can. Dwell is kept as the simpler companion.
- **Temporal alignment (speed vs time-from-cue or -from-choice)** - rejected: run
  duration varies trial to trial, so a time axis smears the junction; the spatial
  axis pins every trial at x = 0.
- **A single scalar** - rejected in favour of reporting three (min speed, slowing
  index, pause time) so the clearest separator can be chosen from the plots; they
  are cheap and share one pass over the segment.
- **Heading-based zIdPhi** (the classic quantitative VTE score) - still deferred
  (as in ADR-0128): it needs a smoothed heading / angular-velocity estimate and
  careful normalisation. This speed-based readout was the operator's steer and is
  transparent; zIdPhi stays a possible follow-up.
- **Projecting in normalized coordinates** - rejected: x and y scale differently
  (width != height), so a normalized projection distorts the cm distance; projecting
  in pixels then scaling by `cm_per_px` is exact.
- **Reading a real pixel->cm homography** - not available (`homography` null); the
  linear `cm_per_px` is enough for a speed magnitude.

## Consequences

- The velocity definitions have one home (`trajectories.py`) with headless
  synthetic-motion tests (constant-velocity speed, projection sign, slowing index,
  pause time, profile binning), so the notebook can't drift from them and narrowing
  the junction window or swapping in zIdPhi later is a small, testable edit.
- Still pure pandas/numpy + `tmaze.core` (no Tk/OpenCV/matplotlib); plotting stays
  in the notebook. No rig/firmware/GUI change - read-only consumption of recordings.
- The stem axis is derived, not stored; if a future zones file gains an explicit
  `stem_axis`, `stem_axis()` uses it automatically.
- The pause threshold and profile range are tuned to the current rig scale and this
  animal's speeds; they are named constants, easy to retune if the camera height or
  scale changes.

## Evidence / links

- Code: `host/tmaze/analysis/trajectories.py` (`load_scale`, `stem_axis`,
  `add_kinematics`/`with_kinematics`, `peri_junction_profile`,
  `junction_kinematics`, `kinematics_per_trial`, `mean_profile`),
  `host/tests/test_analysis_trajectories.py` (26 tests pass),
  `host/notebooks/grant_figures/proposal_figures.ipynb` (Panel 5).
- Validation (rat 602, 8/25-26): peak stem speed median ~130 cm/s
  (p10-p90 ~87-165); junction min speed median ~13-19 cm/s (p10 ~1.3, min ~1.1);
  junction entries/trial median 1 (range 1-1); outbound window ~-74..+7 cm.
- Generated figure (rat 600): speed holds ~150 cm/s down the stem and dips to
  ~50-70 cm/s at the junction, the chance block running faster/noisier on the
  approach than the higher-performance block.
- Builds on ADR-0128 (outbound window + VTE dwell + time base), ADR-0015
  (rig scale / undistort), ADR-0014 (video sidecar), ADR-0112 (analysis layer),
  ADR-0083 (paths / layering).
