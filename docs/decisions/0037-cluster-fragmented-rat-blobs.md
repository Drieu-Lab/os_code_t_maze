# ADR 0037: Track the rat as a cluster of blobs, not a single contour

- **Status:** Accepted
- **Date:** 2026-07-15
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (`TrackerParams.cluster_gap`,
  `cluster_hist_min`, `reg_proximity_mult`; `Detection.members`;
  `RatModel.in_support_fraction`; `_cluster_detections`, `_merge_cluster`,
  `_bbox_gap`, `_pick_registration_sample`; `process()` wiring),
  `host/tracker_view.py` (Cluster gap / Cluster color gate sliders),
  `host/tooltip_text.py`, `docs/how-tracking-and-rules-work.md`
- **Supersedes / Superseded by:** —

## Context

The markerless tracker (ADR-0014) detects the rat by background subtraction +
thresholding and then finds **one** contiguous contour to score against the
appearance model (a pooled H-S colour histogram + a size/shape Gaussian). On the
real rig the rat rarely presents as a single clean blob: as its pose and the
lighting change, the foreground mask **fragments** into several disconnected
pieces — a big torso blob (mixed black/white fur, only the parts above the diff
threshold), a smaller head blob, and a long, lighter tail that often detaches
entirely. These fragments merge and split frame to frame.

A single-contour tracker copes badly with this. Each fragment on its own is the
wrong size and shape for the whole-animal Gaussian and carries only part of the
colour distribution, so the size gate and colour gate reject or flip between
pieces, the tracked point jumps, and the track is lost. Simply loosening the
gates to admit fragments also admits noise and intruders (e.g. an orange-taped
blocker, ADR-0034).

Two further constraints shaped the design:

- **A naive per-fragment colour test fails.** Requiring each fragment to *match*
  the rat's full colour histogram (`hist_score`) would reject legitimate body
  parts — the lighter tail's distribution is nothing like the torso's.
- **Registration has no prior.** When first building a model there is no colour
  distribution yet to test membership against, so registration must bootstrap the
  colour support from scratch while still producing a size band that matches how
  the rat will actually be measured at tracking time.

## Decision

Treat the rat as a **cluster of nearby blobs** assembled before scoring, with a
two-level appearance check and a *decoupled* registration sampler.

1. **Spatial clustering (`cluster_gap`, proc px).** After detection, blobs whose
   bounding boxes are within `cluster_gap` of each other (nearest-edge distance,
   `_bbox_gap`) are unioned (union-find) into one aggregated `Detection`
   (`_cluster_detections` / `_merge_cluster`): area = sum of member areas,
   centroid + elongation from the filled union mask, solidity = union-area /
   convex-hull-area, `contour` = union hull, and `members` = the fragment contours
   (kept so the colour histogram covers the true rat pixels, not the hull's gaps).
   `cluster_gap = 0` is a no-op — one blob per candidate, fully backward
   compatible. Kept in **proc pixels** (not cm) for now; see FW-0002 discussion.

2. **Lenient colour-*membership* merge gate (`cluster_hist_min`).** Only once a
   model exists and `cluster_hist_min > 0`, a fragment may join a cluster only if
   at least that fraction of its pixels fall in H-S bins the model has **ever**
   observed (`RatModel.in_support_fraction` over pooled `hist_counts > 0`). This
   is membership, not a distribution match: any real body part (tail/head/torso)
   whose colours are a subset of the rat's palette passes, while a
   differently-coloured speck (blocker tape, reflection) scores ~0 and stays out.
   The whole-animal *distribution* is still checked at the group level by the
   existing `hist_score` during association.

3. **Decoupled registration (`_pick_registration_sample`, `reg_proximity_mult`).**
   Registration runs the clusterer with the colour gate **off** (there is no
   committed support yet). It then learns the two halves of the model from
   different footprints:
   - **Colour support** from a *proximity union*: the dominant gap-cluster plus
     every other in-ROI cluster within `reg_proximity_mult * cluster_gap` of it.
     Detached-but-nearby parts (the tail) thus enter the palette and are not
     rejected later at tracking time.
   - **Size/shape Gaussian** from the **dominant gap-cluster only** — the same
     unit `cluster_gap` assembles at tracking — so the learned area band matches
     what is actually measured while tracking. A proximity-union area would
     over-state the rat and cause size-gate rejections of the (smaller) assembled
     cluster.

   `cluster_gap` is treated as a model-shaping field (a saved model is only valid
   under the gap it was built with); `reg_proximity_mult` is provenance-only (it
   only matters while building a model). `cluster_hist_min` is a live scoring knob
   (reapplied on load). Both new tuning knobs are exposed as GUI sliders.

## Alternatives considered

- **Per-fragment `hist_score` gate** — reject fragments that don't match the full
  colour distribution. Rejected: kills legitimate parts (the lighter tail never
  matches the torso), which is the whole failure mode we're fixing.
- **Dominant-cluster-only registration** (learn colour from the dominant blob
  too) — Rejected: biases the histogram toward the torso's black/white palette and
  drops the tail's colour, so the tail is later rejected. This is exactly why
  colour learning was decoupled to the proximity union.
- **All-in-ROI proximity for the size band** (learn the Gaussian from the union
  too) — Rejected: over-states the rat's area; the assembled cluster at tracking
  (gap-limited) is smaller and falls outside the band.
- **Mask-level bridging** (morphological close / large dilation to fuse
  fragments) — Rejected: a blunt, global instrument that also fuses the rat to
  nearby objects and distorts area/shape; clustering keeps fragments' true pixels
  and is gated per candidate.
- **Track-guided aggregation** (grab everything near the predicted point) —
  Rejected as the primary mechanism: it can't bootstrap registration and would
  absorb intruders sitting near the rat; proximity + colour membership is safer.
  (The Kalman gate still operates downstream on the assembled candidate.)
- **A richer colour model** (per-part sub-models / a multimodal GMM instead of one
  pooled histogram) — Deferred, not rejected: heavier and only warranted if the
  single pooled histogram + membership test proves insufficient. Captured as
  future work; the intended longer-term answer is a learned pose estimator
  (DeepLabCut-Live, FW-0010) rather than hand-built part models.

## Consequences

- The tracker survives pose/lighting-driven fragmentation: nearby pieces are
  reassembled into one whole-animal candidate before the size/colour gates run, so
  the tracked point stays on the rat instead of flipping between fragments.
- Defaults are unchanged (`cluster_gap = 0`, `cluster_hist_min = 0`): existing
  models and behaviour are untouched until a user opts in via the sliders.
- The colour-membership gate keeps the blocker (and other wrong-coloured objects)
  out of the rat cluster even when it sits within `cluster_gap`, complementing the
  orange-footprint subtraction (ADR-0034/0035).
- New coupling to watch: a model's stored stats are only valid under the
  `cluster_gap` it was acquired with (hence `cluster_gap` is model-shaping and
  saved with the model); changing the gap should prompt re-registration, like
  changing `proc_max_w` (ADR-0027).
- Cost: an extra union-find + per-fragment histogram pass per frame (bounded by
  blob count) and a hull/moment computation per merged cluster — negligible at rig
  frame rates and resolutions.
- Revisit if the pooled-histogram membership test proves too permissive/strict in
  practice, or once a learned pose estimator (FW-0010) supersedes blob assembly.

## Evidence / links

- Code: `host/tracker.py` (`_cluster_detections`, `_merge_cluster`, `_bbox_gap`,
  `_pick_registration_sample`, `RatModel.in_support_fraction`), wired in
  `process()`.
- Tests: `host/test_tracker_cluster.py` (spatial merge / gap threshold /
  `cluster_gap = 0` no-op; colour-gate accepts same-colour fragments and rejects a
  wrong-coloured blob; decoupled registration learns the tail's colour while the
  size band tracks the dominant cluster). `host/test_tracker_model.py` covers the
  new fields in the save/load round-trip.
- Related: ADR-0014 (markerless tracking), ADR-0017 (live scoring knobs),
  ADR-0027 (embedded acquisition params), ADR-0034/0035 (blocker tracking +
  per-frame footprint). Future work: FW-0002 (orientation, incl. tail-colour
  disambiguation), FW-0010 (DeepLabCut-Live), FW-0011 (richer colour model).
