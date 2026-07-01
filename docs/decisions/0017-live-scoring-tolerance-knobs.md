# ADR 0017: Expose appearance-model tolerances as live, scoring-time knobs

- **Status:** Accepted
- **Date:** 2026-06-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (`TrackerParams`, `RatModel.form_score`/`area_band`,
  `RatTracker._associate`), `host/tracker_view.py` ("Model tolerances" section)
- **Supersedes / Superseded by:** — (extends ADR-0014)

## Context

The markerless tracker (ADR-0014) scores each candidate blob against a learned
`RatModel` (H-S colour histogram + a Gaussian over `[log(area), log(elongation),
solidity]`) and accepts the best if it clears `accept_score`. During real-rat
tuning the operator needs to widen or tighten how forgiving the match is: a rat
changes silhouette area a lot (rearing, grooming, stretching), contorts into
non-elliptical shapes, and an experimenter's hand/arm is a same-ish-size but
wrong-coloured intruder that must be rejected.

The tolerances that govern this were either hard-coded (`w_dist/w_hist/w_form`
weights) or baked into derived values at **recompute time**: the size band
`area_min/area_max` was computed as `exp(muA ± area_gate_k·sigmaA)` inside
`RatModel.recompute()`, which only runs at registration finalize and during
enrichment. A slider for `area_gate_k` would therefore do nothing mid-track
until the model was re-registered — confusing and useless for live tuning.

## Decision

Add four tolerance knobs and read every one **live at scoring time** inside
`_associate()`, so dragging a slider takes effect on the next frame with no
`recompute()`:

- **`area_gate_k`** (size tolerance) — the hard size band is now computed per
  frame via `RatModel.area_band(k)` from the stored `mu`/`cov`, not from the
  baked `area_min/area_max`.
- **`appearance_bias`** (appearance-vs-position) — a single `[0,1]` knob giving
  appearance's share of the score; distance gets the remainder (and only when a
  prediction exists). The colour:shape split inside "appearance" keeps the
  `w_hist:w_form` ratio. The default `0.55` reproduces the old fixed weights
  exactly (`w_dist=0.45, w_hist=0.35, w_form=0.20`), so behaviour is unchanged
  until the operator moves it. `w_dist` is kept as a (legacy) field but is
  superseded by `appearance_bias` for the position/appearance split.
- **`form_tol`** (shape softness) — multiplies the Mahalanobis denominator in
  `RatModel.form_score(x, tol)` (`exp(-0.5·d²/(FORM_DOF·tol))`); `>1` is more
  tolerant of off-shape blobs. Default `1.0` = unchanged.
- **`hist_min`** (colour hard gate) — reject any candidate whose histogram
  correlation is below this, even if size/shape match. Default `0.0` = off.

In the GUI these live in a collapsed-by-default "Model tolerances" section
(`tracker_view._TOL_SLIDERS` + `_add_collapsible`) so the main Tuning panel stays
uncluttered and the section adds no height unless opened.

## Alternatives considered

- **Recompute-coupled knobs (bake into `recompute()`)** — rejected: a slider
  change would not take effect until the next registration/enrichment, which
  makes live tuning against a moving rat feel broken. The cost of computing the
  band live is two `exp()` calls per blob per frame — negligible.
- **Three raw weight sliders (`w_dist/w_hist/w_form`)** — rejected for the main
  knob: relative, renormalized weights are unintuitive to tune. A single
  appearance-vs-position balance (with a fixed colour:shape ratio) captures the
  realistic need ("trust appearance over last position when re-acquiring") with
  one control. The raw ratio is still available in code if finer control is
  needed later.
- **Colour as soft-only** — rejected: a soft colour term can still be outvoted
  by a strong size/shape match, so a same-size wrong-coloured hand could win.
  An optional hard `hist_min` gate gives a clean reject without forcing colour
  to dominate the soft score.

## Consequences

- Tolerances respond immediately while tuning; no re-registration needed.
- Defaults are behaviour-preserving (`appearance_bias=0.55`, `form_tol=1.0`,
  `hist_min=0.0`, `area_gate_k=3.0`), so existing saved models/sessions behave
  as before until knobs are touched.
- `RatModel.recompute()` still sets `area_min/area_max` for display/fallback, but
  gating no longer depends on them; `area_band()` is the source of truth.
- `hist_min` is a hard gate: set too high it can reject the rat under lighting
  changes. It is off by default and meant as a deliberate intruder-rejection
  tool.
- Knobs are not persisted with the `RatModel` (they are session tuning, not
  model data); if per-animal tolerance presets become desirable, that is a
  future-work item.

## Evidence / links

- `host/tracker.py`: `TrackerParams.appearance_bias/form_tol/hist_min`,
  `RatModel.area_band()`, `RatModel.form_score(tol=...)`, and the live weighting
  + gating in `_associate()`.
- `host/tracker_view.py`: `_TOL_SLIDERS`, `_add_collapsible()`.
- Headless checks (2026-06-30): defaults reproduce `0.45/0.35/0.20`; smaller `k`
  tightens the band live; higher `form_tol` raises the score of an off-shape
  blob (0.51 -> 0.80); a blue (wrong-colour) blob tracked with `hist_min=0`
  but was rejected after setting `hist_min=0.6` mid-track with no
  re-registration.
- Extends ADR-0014 (markerless position tracking).
