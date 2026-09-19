# ADR 0185: Distance via an anchor + dead-band (stop the jitter creep)

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of RJ
- **Affects:** `host/tmaze/core/behavior_stats.py` (`_accumulate_distance`,
  `BehaviorSnapshot.distance_method`, `DEFAULT_DEADBAND_CM`),
  `host/tmaze/core/session/session_summary.py` (the `Distance:` line);
  test `test_behavior_stats`; closes bug `#260904_DistanceCreep`
- **Supersedes / Superseded by:** builds on ADR-0032

## Context

The live Phase 2a "distance run" readout (and the session summary that mirrors it)
kept **climbing while the animal sat still** — logged as `#260904_DistanceCreep`.

`behavior_stats._accumulate_distance` summed `hypot` between *every* consecutive
smoothed-centroid pair, adding the segment whenever it was under the 200 px jump
cap. A resting rat's centroid is not static: the tracker jitters and the animal
makes tiny in-place wiggles, each a sub-cap hop. Summing consecutive pairs chains
all of that noise into fake distance, so the metre count grew with time-on-task
rather than with real locomotion — contaminating both the live readout and the
summary's `Distance` / `m/min` figures.

## Decision

Compute distance from an **anchor + dead-band** instead of consecutive-pair
summing (`DEFAULT_DEADBAND_CM = 1.0`, a constructor knob `deadband_cm`):

- Keep `_last_pos` (the previous tick) purely for the existing per-tick jump-cap
  and discontinuity handling.
- Add `_anchor` — the last **counted** position. On each usable, non-teleport
  tick, measure the displacement anchor→current in cm (via the same canonical
  `scale.distance_cm`). If it is **≥ `deadband_cm`**, add that straight-line
  segment and advance the anchor to the current point; if it is **< `deadband_cm`**,
  add nothing and **leave the anchor put**. So jitter within 1 cm of a resting
  spot accumulates exactly zero no matter how many ticks pass, while real movement
  past the band is counted (with ≤ 1 cm quantization — a deliberate, conservative
  undercount, matching the module's existing "undercount over fabricate" stance).
- A confidence gap or an over-cap single-tick jump is a discontinuity: drop/reset
  **both** `_last_pos` and `_anchor`, so a re-acquisition a distance away never
  adds the straight-line jump.

Record provenance: `BehaviorSnapshot.distance_method`
(`"anchor_deadband_1cm"`) flows verbatim into the summary JSON's `locomotion`
block and is annotated on the text `Distance:` line, so a reader knows the
dead-band was applied (a pre-0185 bundle lacks the key — it used the old
consecutive-pair method).

## Alternatives considered

- **Per-segment minimum (drop each consecutive pair below a threshold).** Rejected:
  jitter above the per-pair threshold still adds, and a slow genuine crawl of
  sub-threshold steps is dropped entirely. The anchor holds a fixed reference so
  in-place jitter is zeroed *and* a real crawl still counts once it crosses the band.
- **Sum over a coarser time step (decimate frames).** Rejected: ties the metric to
  the poll rate and still chains jitter within each step; the dead-band is
  displacement-based and rate-independent.
- **Speed-gated (ignore frames below a speed threshold).** Rejected: needs a
  reliable per-frame speed and a second tuned threshold; a displacement dead-band
  is simpler and directly answers "did it actually move?".
- **Bump the session-summary `schema_version`.** Rejected: `distance_method` lands
  inside the verbatim `locomotion` readout (additive), not as a new top-level
  summary field; readers that ignore it are unaffected.
- **Fix the offline analysis distance too.** N/A: the analysis repo computes no
  total travelled distance (only per-frame speed + signed junction distance), so
  there is no offline code path with this bug.

## Consequences

- A resting animal now contributes 0 distance; the live readout and the summary
  `Distance`/`m/min` reflect real locomotion. Real movement is counted with ≤ 1 cm
  quantization (negligible against session-scale distances).
- Sessions recorded before this change used the old chaining method and are not
  comparable at fine scale; `distance_method` (present only from v0185 bundles)
  makes the boundary explicit. This is a live-metric change, not a stored-format
  one — no `format_version` bump.
- The dead-band is a knob (`deadband_cm`); 1 cm is well above the rat centroid's
  resting jitter and small against locomotion. Revisit if a future camera scale or
  tracker noise floor makes 1 cm too coarse or too fine.

## Evidence / links

- Bug `#260904_DistanceCreep` (removed from `docs/notes/Bugs.md` in this change).
- `test_behavior_stats`: resting jitter → 0; clear >1 cm steps summed exactly; a
  sub-band slow crawl counted (band-quantized, never zero, never over the true
  path); the existing jump-cap and confidence-gap tests unchanged.
- Builds on ADR-0032 (the live behavior readout / distance measure).
