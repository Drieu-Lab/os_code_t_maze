# ADR 0133: Junction min-speed spatial band + VTE-vs-accuracy quartile panel + per-rat speed heatmaps (with rig-image overlay)

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/analysis/trajectories.py`,
  `host/tests/test_analysis_trajectories.py`,
  `host/notebooks/grant_figures/proposal_figures.ipynb`
- **Supersedes / Superseded by:** supersedes the junction min-speed / pause REGION
  of ADR-0130 (zone-membership -> spatial band); otherwise builds on ADR-0130/0128.

## Context

Two changes to the peri-junction VTE (vicarious-trial-and-error) readout from
ADR-0130, both driven by looking at the generated speed profile.

1. **Where the junction slow-down actually is.** ADR-0130 measured
   `min_speed_cms` (and `pause_time_s`) over frames whose centroid is
   point-in-polygon inside the `JUNCTION` zone. But the mean speed profile shows the
   deceleration begins UP-STEM and the minimum sits at, or a few cm before, the
   junction point (`s_junction_cm = 0`) - before the centroid crosses into the
   `JUNCTION` polygon (whose stem-side border sits well below 0, ~-18 cm on the 601
   map). So a zone-membership minimum can miss the deepest, most deliberative part of
   the dip. The operator asked for the region to be "10 cm before and 5 cm after the
   junction" instead.

2. **Does deliberation predict accuracy?** The Panel 5 scalars were only compared
   between the two hand-picked overlay blocks. The natural scientific question is
   whether, across ALL trials, more deliberation goes with better choices. That calls
   for pooling every trial and relating a per-trial VTE measure to per-trial
   correctness.

## Decision

### A. Junction min-speed / pause measured over a stem-axis BAND

`junction_kinematics` now takes the min-speed / pause region from a spatial band
along the stem axis, using the existing per-frame `s_junction_cm`:

- A frame is in-band when `-band_before_cm <= s_junction_cm <= band_after_cm`,
  anchored at the junction POINT `s = 0` (the profile's `x = 0`), with
  `DEFAULT_JUNCTION_BEFORE_CM = 10.0`, `DEFAULT_JUNCTION_AFTER_CM = 5.0`
  (parameters `band_before_cm` / `band_after_cm`, threaded through
  `kinematics_per_trial`).
- `min_speed_cms` = slowest smoothed speed in the band (NaN only if no tracked frame
  lands in the band, or `s_junction_cm` is absent because the stem axis was missing).
- `pause_time_s` = seconds in the band below `pause_speed_cms` (same left-Riemann
  integral as before, just band-gated instead of zone-gated).
- `peak_stem_speed_cms` is UNCHANGED - still the fastest speed while inside the live
  `CENTRAL_STEM` zone - so `slowing_index = 1 - min_band/peak_stem` follows the new
  minimum automatically.

Anchoring at `s = 0` (not the zone's stem-side border) is deliberate: a band of
`[border-10, border+5]` would sit entirely up-stem (~`[-28, -13]` cm) and MISS the
near-0 minimum; the `[-10, +5]` band around the point brackets the actual dip. The
Panel 5 profile now shades this band so the figure shows exactly where the scalar is
measured.

### B. Panel 6 - choice accuracy by VTE quartile

New pooled analysis (pure helpers in `trajectories.py`, plotting in the notebook):

- Pool EVERY completed trial across the included sessions (per-session sidecar / zone
  map / scale), compute the four per-trial measures over the ADR-0128 outbound window,
  and honour `EXCLUDE_INTERVENTION` (drop de-bias / de-alternation trials).
- Split trials into QUARTILES by each measure with a RANK-based cut
  (`pd.qcut` on the tie-broken rank) so a measure with heavy ties - e.g. the many
  `pause_time_s == 0` trials - still divides into equal-sized groups instead of
  collapsing. `accuracy_by_quartile` returns per-quartile `n`, `n_correct`,
  `accuracy`, and a WILSON 95% CI (`wilson_interval`), which behaves at the small
  per-quartile counts and near-0/1 accuracies where a Wald interval would run out of
  `[0, 1]`.
- Compare Q1 vs Q4 accuracy with a TWO-PROPORTION z-test (`two_proportion_z`), NOT
  the Mann-Whitney U used for the continuous Panel-5 scalars: accuracy is a proportion
  of binary correct/incorrect trials, so a proportions test is the right, interpretable
  choice (both are SciPy-free, sharing the same normal tail).
- Show BOTH a raw-pooled row and a WITHIN-SESSION z-scored row (`zscore_by_group`):
  pooling raw across days conflates within-session deliberation with across-session
  learning (an early low-accuracy day may also differ in VTE), and the z-scored row
  removes that between-session offset.
- DROP POLICY is per measure: each measure's panel uses every trial where that measure
  is defined (N annotated per panel). Two "no-dip" NaN imputations keep pass-throughs
  in the low-VTE quartile instead of dropping them: `slowing_index` NaN -> 0 (no
  slowing) and `min_speed_cms` NaN -> that trial's `peak_stem_speed_cms` (ran as fast
  as the stem, no dip). For `min_speed_cms` a LOW value is more VTE, so its Q1 is the
  high-deliberation end; the panel marks the more-VTE quartile.

### C. Panel 7 - per-rat occupancy & mean-speed heatmaps

A 2D companion to the Panel-5 profile that answers "is there a PREFERRED zone where
each rat slows down?" without collapsing the arena onto the stem axis:

- `speed_occupancy_grid` (pure) bins pooled per-frame normalized positions into an
  `occupancy` count grid and a `mean_speed` grid over the unit square, masking a bin's
  mean speed when it holds fewer than `min_count` finite-speed frames (a couple of stray
  frames can't paint a fake slow spot).
- The notebook pools every frame in a CONFIGURABLE per-trial window (like the Panel-3
  trajectories): `HEATMAP_START_EVENT -> HEATMAP_END_EVENT` + pads, default trial start ->
  the first well lick (`lick_time_iso`), with `EXCLUDE_INTERVENTION` honoured, and draws
  one row per rat: a mean-speed map (cool = slow, the deceleration view) and an occupancy
  map (log counts, context). The same window feeds BOTH maps.
- No-lick trials (incorrect / unrewarded) store `lick_time_iso` as an EMPTY STRING (not
  null), so they are coalesced onto `HEATMAP_END_FALLBACK_EVENT` (default `trial_end_iso`)
  via a non-blank test, not a null test - otherwise they'd parse to NaT, be dropped, and
  bias the maps toward rewarded trials (the exact bug caught during review; ~half the
  trials on the 602 window).
- The mean-speed grid is NOT smoothed (a Gaussian would bleed the masked NaNs across the
  sparse path edges); occupancy may be smoothed for readability when SciPy is present, else
  shown raw. Occupancy is kept as a preference reference: with the window running to the
  well, the reward spout naturally shows as a slow, high-occupancy hot-spot; the
  deliberation signal is the stem slow-down approaching the junction.

### D. Panel 8 - the same maps overlaid on the rig image

A companion per-rat figure lays each map TRANSLUCENTLY (`HEATMAP_OVERLAY_ALPHA`) over that
rat's overhead background frame (`<session>_background.png`, the same image Panel 3 draws
on) in pixel coordinates, so the hot-spots sit on the real maze. The background is the
rat's most recent CONTRIBUTING session; pooled frames span several sessions, so the overlay
assumes the maze didn't move between them (same approximation as the zone outlines). One
PNG per rat (`heatmap_overlay_<animal>.png`); a missing background falls back to a blank
canvas. The pooled-frame loader is cached so Panels 7 and 8 don't reload the sessions twice.

### E. Panel 9 - low- vs high-accuracy block heatmaps

The Panel-7 maps restricted to the two blocks the figure already contrasts (the selected
rat's chance `low` and higher-performance `high` blocks from `select_blocks`), as a 2x2
grid (rows = the two blocks, columns = mean speed / occupancy) so the slow-down pattern
can be compared between a chance and a higher-accuracy run:

- Reuses the same per-trial `HEATMAP_*` window and the shared `_hm_windows` / `_pool_frames`
  helpers (the empty-string-safe fallback from C), just fed each block's `.trials` slice.
- A single ~`BLOCK`-trial block has far fewer frames than the pooled per-rat maps, so it
  uses coarser `BLOCK_HEATMAP_BINS` / `BLOCK_HEATMAP_MIN_COUNT` knobs to avoid a mostly-
  blank grid. Both blocks share one colour scale per map (speed vmin/vmax, occupancy vmax)
  so they are directly comparable.
- Drawn in the normalized frame with zone outlines (like Panel 7), not on the rig image,
  because the sparse per-block grid reads more clearly without the background.

## Alternatives considered

- **Anchor the band at the junction zone's stem-side border** - rejected: it lands
  up-stem and misses the near-0 minimum (the whole point of the change).
- **Keep zone-membership for min speed** - rejected per the profile evidence that the
  dip precedes the polygon border.
- **Also move `peak_stem_speed_cms` onto a band** - not needed: peak is the run's
  reference speed, well-defined by the stem zone; only the MINIMUM needed relocating.
- **Split by all four measures jointly** - impossible for a 1-D quartile; instead one
  panel per measure (small multiples).
- **Mann-Whitney U on the 0/1 correctness** - works but a two-proportion test is the
  natural, interpretable comparison of two accuracies; Wilson CIs communicate the
  per-quartile uncertainty directly.
- **Drop trials undefined for ANY measure (global N)** - rejected in favour of
  per-measure drops so each measure keeps its full sample.
- **Raw pooling only** - kept as one row but paired with the within-session z-scored
  row so the across-day learning confound is visible/controlled.
- **Plain occupancy heatmap for the slowing question** - rejected as the primary map:
  occupancy conflates slowing with waiting; a mean-speed map reads deceleration directly.
  Occupancy is kept as a context panel.

## Consequences

- The band region and the quartile/stat helpers live in `trajectories.py` with
  headless tests (a synthetic run whose minimum is up-stem proves the band captures a
  dip the old zone test would miss; Wilson/two-proportion/zscore/quartile helpers each
  tested), so the notebook can't drift and the band edges are easy to retune via the
  named constants / notebook knobs (`JUNCTION_BEFORE_CM` / `JUNCTION_AFTER_CM`).
- Panel 5's `min_speed_cms` / `slowing_index` / `pause_time_s` VALUES change (they now
  reflect the pre-border slow-down); this is the intended correction, recorded here as
  superseding the ADR-0130 region. Profiles/dwell are unchanged.
- Panel 6 is additive and heavier (loads every session's sidecar once); still pure
  pandas/numpy + `tmaze.core`, read-only.
- Panels 7-8 are additive and load each rat's sessions once (cached, shared between them);
  the binning is a single pure helper (`speed_occupancy_grid`) with tests, so the notebook
  stays a thin shell. The configurable window means the maps now include the reward-well
  leg by default (well shows as a slow hot-spot) - intended, per the operator's ask for a
  trial-start->first-lick extent.

## Evidence / links

- Code: `host/tmaze/analysis/trajectories.py` (`DEFAULT_JUNCTION_BEFORE_CM` /
  `DEFAULT_JUNCTION_AFTER_CM`, band-based `junction_kinematics` /
  `kinematics_per_trial`, `wilson_interval`, `two_proportion_z`, `zscore_by_group`,
  `accuracy_by_quartile`, `speed_occupancy_grid`),
  `host/tests/test_analysis_trajectories.py` (44 tests pass),
  `host/notebooks/grant_figures/proposal_figures.ipynb` (Panel 5 band shading +
  Panel 6 + Panel 7 heatmaps).
- Generated figures (rat 601, 8/25-26): the profile's blue "higher" curve bottoms out
  at `s ~= -4 cm` (inside the `[-10, +5]` band, before `s = 0`), confirming the band
  choice; Panel 6 pooled 205 trials over 2 sessions with no significant Q1-vs-Q4
  accuracy split on this short window (all p > 0.37) - reported honestly with N + p.
- Supersedes the min-speed / pause REGION of ADR-0130; builds on ADR-0130 (velocity
  readout), ADR-0128 (outbound window + time base + block selection), ADR-0112
  (analysis layer), ADR-0083 (paths / layering).
