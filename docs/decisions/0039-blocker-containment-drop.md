# ADR 0039: Exclude the blocker by dropping its blob (one pass), not pixel-subtracting and re-detecting

- **Status:** Accepted
- **Date:** 2026-07-21
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (`RatTracker.process`, new `_footprint_overlap`, `TrackerParams.blocker_overlap_drop`), `host/test_tracker_blocker.py`, `docs/how-tracking-and-rules-work.md`
- **Supersedes / Superseded by:** Partially supersedes ADR-0034 (the "subtract the footprint pixels then re-detect" mechanism); the tape-based blocker location and per-frame footprint (ADR-0035) stand.

## Context

Investigating `#260720_TrackingDelays`, the reported symptom was that rat tracking
loses confidence more often (COASTING/LOST) and localizes later whenever the
orange-taped blocker is in view - and, tellingly, **even when the rat is far from
the blocker**. That last fact rules out the footprint erasing rat pixels as the
primary cause (that only bites when the rat is adjacent) and points at per-frame
CPU load: tracking runs inline on the camera grab thread (ADR-0011/0014), so a
slower `process()` delays the next `RetrieveResult`, overflows the camera buffer,
and drops frames at the source (the `src_drops` counter). Bigger gaps between
processed frames mean bigger inter-frame motion, which breaks the association gate
(`gate_radius`) and decays confidence.

The blocker path as built by ADR-0034/0035 did, on every frame the tape was
visible, a **second full detection + clustering pass**: after the normal
`_detections()` + `_cluster_detections()`, it recomputed
`cv2.bitwise_and(mask, ~footprint)` and re-ran `_detections()` **and**
`_cluster_detections()` on the result. With clustering enabled (`cluster_gap > 0`,
ADR-0037) that second `_cluster_detections()` is an O(n^2) bbox pass plus a
per-blob `cv2.calcHist` - a large, avoidable per-frame cost paid arena-wide
whenever the tape is merely present. The only case that actually needed
pixel-level work was a rat **fused** with the blocker into a single contour
(splitting it required subtracting pixels).

## Decision

Exclude the blocker by **dropping whole detections that lie inside its footprint**,
before clustering, in a single detect + single cluster pass:

- Compute the orange-tape footprint once (unchanged, per ADR-0035), then, during
  tracking, drop any first-pass detection whose filled area is at least
  `TrackerParams.blocker_overlap_drop` (default **0.6**) inside the footprint mask
  (`RatTracker._footprint_overlap`). Cluster the survivors once.
- Use an **overlap fraction**, not a centroid-in-footprint test: the footprint is
  a *dilated* convex hull (`blocker_footprint_dilate`), so a centroid test would
  wrongly discard a separate rat blob that merely grazes the halo.
- Do the drop **before** clustering so blocker fragments cannot be merged into a
  rat cluster (which would hide them from the test), and **skip** it while any
  registration is active (rat or blocker), so a (re)registered sample still sees
  every blob.

This removes the second `_detections()` and second `_cluster_detections()`
entirely; a tape-visible frame now costs one detect + one cluster.

## Alternatives considered

- **Keep the pixel-subtract but only in the fused case** (detect a single blob
  overlapping the footprint with substantial outside area, subtract just that
  blob's footprint, re-detect locally). Preserves rat/blocker de-merge at low
  cost, but adds a "is this fused?" branch and local re-detection logic. Deferred
  to future work, gated on evidence that the contact case actually matters (see
  `docs/future-work.md`).
- **GPU-accelerate the CV pipeline** to absorb the cost instead of removing it.
  Rejected as the first move: the installed OpenCV is not built with CUDA, the
  contour/cluster stages are not GPU-friendly, and cutting redundant work is
  cheaper and lower-risk. (GPU NVENC recording is tracked separately as a way to
  free CPU cores.)
- **Raise `min_area`/tune to reject the blocker** - fragile; the blocker's size
  overlaps the rat's and depends on placement.

## Consequences

- Tape-visible frames drop from two detect+cluster passes to one, cutting the
  per-frame load that was dropping source frames arena-wide - the dominant lever
  for `#260720`.
- **Trade-off accepted:** whole-detection dropping cannot split a rat *fused* with
  the blocker into one contour. That blob is mostly inside the footprint, so it is
  **dropped** (the track is not stolen onto the blocker) and the rat briefly
  COASTS through direct contact until they separate. This is judged acceptable
  because contact is transient, the prior pixel-subtract was itself lossy in the
  contact halo (it eroded/fragmented the rat), and the arena-wide frame-drop cost
  was the bigger problem. If frequent contact at a decision-relevant blocker
  position proves harmful, restore scoped de-merge (the deferred alternative).
- `blocker_overlap_drop` is a live tracking-dynamics knob (restored on model load
  via `MODEL_REAPPLY_FIELDS`); it does not change the measured blob's stored
  statistics, so it is not a model-shaping field.

## Evidence / links

- `host/test_tracker_blocker.py`: `test_blocker_excluded_from_rat`,
  `test_rat_tracked_near_but_separate_blocker`, and the documented
  `test_fused_rat_blocker_not_stolen`; full suite green (163 tests) via
  `run_tests.bat`.
- New `track: <avg>/<peak> ms` readout in the Camera tab status line
  (`host/camera_view.py`) measures the per-frame tracking time this reduces.
- Bug: `docs/notes/Bugs.md` `#260720_TrackingDelays`. Related: ADR-0034, 0035,
  0037, 0011, 0014.
