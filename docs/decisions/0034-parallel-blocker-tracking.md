# ADR 0034: Track orange-taped blockers in parallel and subtract their footprint

- **Status:** Accepted (footprint-persistence sub-decision superseded by ADR-0035)
- **Date:** 2026-07-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (RatModel, TrackerParams, RatTracker.process + blocker API), `host/tracker_view.py` (Blocker controls), `host/test_tracker_blocker.py`, `docs/how-tracking-and-rules-work.md`
- **Supersedes / Superseded by:** Footprint-persistence choice superseded by ADR-0035 (footprint is now per-frame)

## Context

Experimenters sometimes drop a physical blocker into the maze to close a path.
The tracker builds its foreground mask by subtracting a stored empty-rig
reference (ADR-0014); a blocker placed *after* that reference was grabbed is not
in the background, so it shows up as a persistent foreground blob. Blockers are
dark like the rat, so the reported bug (`#260710_BlockerBlobs`) was two-fold: the
extra blob competes with the rat, and when the rat gets close their contours
merge (the morphology close step bridges them), so `RETR_EXTERNAL` yields a
single blob whose centroid is pulled onto the blocker - "the tracking sticks to
the blocker."

Operating constraints from the bench:

- The blocker is **repositioned to arbitrary positions/orientations during a
  session**, so a model must generalize across placements - its silhouette
  (size/shape) changes with perspective, but its colour does not.
- To reposition it the operator must **bring a hand into the frame**, which
  pollutes any continuous-video registration window.
- The operator added **orange tape to the blocker's borders** (some black
  surface still visible), giving a colour the black rat never has.

## Decision

Track the blocker as a second, parallel entity, identified by its **orange tape**
(a position-invariant cue), and remove it from the rat's view by subtracting its
**pixel footprint** from the mask before the rat is chosen.

- **Learned orange range, not a preset.** Registration is **on-demand still
  captures**: the operator places the blocker, withdraws their hand, and captures
  a sample; the dominant foreground blob is sampled (`_pick_registration_blob`
  bootstraps selection without needing a working gate yet) and its *colourful*
  pixels (S,V above the seed floors, which isolates tape from the black body) are
  accumulated. `RatModel.recompute()` derives robust HSV bounds `orange_lo/hi`
  (hue ~[p2, p98] of tape pixels; S,V low-percentile floors), stored on the model
  and persisted in the `.npz`. So the gate adapts to the tape + room lighting.
- **Footprint from the orange convex hull.** Each frame,
  `_blocker_footprint_from_orange()` thresholds the learned orange range, and
  fills the convex hull of the in-range pixels (dilated by
  `blocker_footprint_dilate`) to cover the whole blocker, interior included. The
  rat's detections are recomputed from `mask AND NOT footprint`.
- **Reuses the appearance machinery.** The blocker is a `RatModel` (its colour +
  size/shape are still learned; only the orange range is used for the footprint).
  A blocker model's acquisition params are provenance-only and are **not**
  re-applied on load, so loading a blocker never disturbs the rat model's
  restored operating point (cf. ADR-0027).
- **Constants** live in `TrackerParams` (`blocker_h_lo/h_hi/s_min/v_min` seed +
  floor, `blocker_orange_min_px`, `blocker_footprint_dilate`). `TrackResult`
  gains `blocker_detected`/`blocker_centroid` (+ three sidecar columns) and the
  overlay draws an orange `BLOCKER` marker.

## Alternatives considered

- **Background patching / re-grab with the blocker in place** — robust for a
  *static* blocker, but the blocker here moves during the session and you cannot
  re-grab a clean full background mid-session with the rat present. Rejected as
  the primary mechanism (still the fallback if no tape is used).
- **Exclusion zones (mask-out polygons)** — simple, but the rat goes invisible
  inside them, and the blocker moves so the zones would need constant redrawing.
- **Appearance-model claim of a whole blob (colour hist + size)** — a black
  blocker is a weak match against a black rat, and, critically, when they merge
  into one contour a blob-level claim cannot split them; footprint subtraction
  from the *orange pixels* can. An earlier draft gated footprint refresh by a
  learned size band to detect merges, but with multi-position registration the
  size band is too broad to reliably distinguish "blocker looks bigger here" from
  "blocker + rat merged." The orange-hull footprint sidesteps this entirely.
- **Fixed/preset orange HSV gate** — brittle across tapes and lighting; learning
  the range from the tape removes the guesswork (chosen).

## Consequences

- Solves the reported symptom: because only the *orange* pixels define the
  footprint and the rat has none, a rat touching the blocker is split at the
  blocker's pixels and keeps its own centroid. The footprint follows a moved
  blocker (refreshed whenever enough tape is visible).
- Requires the orange tape and an empty-rig background (no blocker, no rat) at
  grab time. If the tape is fully occluded the last footprint is reused (may go
  briefly stale). While a hand is in frame repositioning, tracking can be noisy -
  reposition between trials.
- Single blocker for now (one model slot). Multi-blocker and an appearance-based
  fallback when the tape is occluded are natural follow-ups.

## Evidence / links

- `host/test_tracker_blocker.py` — synthetic frames prove the learned range
  brackets the tape hue, survives save/load, the blocker is excluded, and the rat
  survives a merge; full suite green (`run_tests.bat`).
- Bug: `docs/notes/Bugs.md` `#260710_BlockerBlobs` (removed on this change).
- Builds on ADR-0014 (markerless tracking), ADR-0015 (undistort at source),
  ADR-0020 (saved background), ADR-0027 (model carries its acquisition params).
