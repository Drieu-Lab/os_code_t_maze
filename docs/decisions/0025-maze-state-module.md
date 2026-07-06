# ADR 0025: Discrete maze-state module (zones + direction + loops) over the tracker

- **Status:** Accepted
- **Date:** 2026-07-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/maze_state.py` (new pure core), `host/maze_state_view.py`
  (new "Maze state" tab + zone editor), `host/zones.json` (new, per-rig,
  gitignored), `host/test_maze_state.py` (new tests),
  `host/maze_state_validate.py` (new beam-validation harness),
  `docs/maze_state_validation.md` (results note),
  `host/experiment_control.py` (tab wiring), `host/tooltip_text.py`, `.gitignore`,
  `PROGRAMS.md`
- **Supersedes / Superseded by:** — (builds on
  [ADR-0023](0023-position-based-rules-engine.md) rules engine,
  [ADR-0018](0018-landmark-annotations.md) landmarks,
  [ADR-0015](0015-undistort-at-source.md) undistort-at-source,
  [ADR-0014](0014-markerless-position-tracking.md) tracker,
  [ADR-0024](0024-shared-host-protocol-parser.md) protocol parser)

## Context

The tracker (ADR-0014) gives the rat's centroid in full-frame px, and the rules
engine (ADR-0023) already consumes it for proximity-to-landmark rules. But the
training-shaping logic and a behavior-statistics module both need something the
raw centroid does not give directly: a **discrete, per-tick maze-state stream** -
which zone the rat is in, the transition edges, and (critically) *direction*.

The forcing observation is **state observability at the beams**. The rig's
body-beam sensors are non-directional: a single break at, say, the central-stem
beam cannot tell whether the rat is progressing UP the stem toward the choice
point or turning back DOWN toward the reward well. The firmware task FSM can only
disambiguate by waiting to see which beam breaks *next*. Continuous position
resolves this immediately - the centroid's motion along the stem is the
direction. A useful reduction must exploit that, not just report "which region
is the centroid in right now."

This reduced representation is a **coupling interface**: separate, future chats
own (a) the host-side task-rule shaping logic and (b) the behavior-statistics
module. Both consume this contract; neither is built here. So the contract itself
- the `RatState` shape and the `zones.json` schema - is the deliverable to get
right, and the producer must be independently testable.

## Decision

**A pure core (`host/maze_state.py`) + a thin shell (`host/maze_state_view.py`),
mirroring `tracker.py` / `rules_engine.py`.** The core has no Tk/OpenCV/numpy: it
takes a duck-typed `TrackResult` (fields via `getattr`, like
`RulesEngine._make_frame`) and a `ZoneMap`, and returns an immutable `RatState`.
Point-in-polygon is ray-casting and direction is a dot-product projection - plain
arithmetic, so the core is headlessly unit-tested over synthetic point sequences
(`host/test_maze_state.py`). All OpenCV/Pillow/Tk lives in the shell, with deps
pulled from `gui_deps` so importing the module never breaks the GUI.

**Zone vocabulary + `RatState` contract.** The maze is a figure-8 /
continuous-alternation loop, so a dedicated `HUB` (the central reward zone where
the well sits and the central stem + both return arms converge) anchors the
vocabulary: `HUB` (substates `in_well` / `outside_well`), `CENTRAL_STEM`,
`LEFT_CHOICE`, `RIGHT_CHOICE`, `LEFT_RETURN`, `RIGHT_RETURN`, `JUNCTION`,
`UNKNOWN`. Per tick, `RatState` carries: `zone` +
`substate`; `entered` / `exited` edges; optional `direction` (`up_stem` /
`down_stem`) + `stem_progress` (0..1); `loop_completed` + `loop_side`;
`confidence`; a `valid` flag; and a bounded zone `history` with a
`visited_since(target, since_zone)` query (e.g. "was in a choice arm since last
leaving the stem"). When the tracker is COASTING/LOST or below `min_confidence`,
the estimator emits `zone=UNKNOWN`, `valid=False`, and **never a false
transition** - it holds its committed state internally so a return to TRACKING
resumes cleanly.

**Zone map as normalized polygons (`host/zones.json`).** Zones are stored as
normalized (0..1) polygons like the ROI (`roi_polygon.json`) and landmarks
(`landmarks.json`), so they survive preview/proc/full-frame scaling and the
undistort-at-source pipeline (ADR-0015); the centroid is normalized by
`frame_size` before classification. The schema adds `well_norm` (an optional
polygon, drawn inside the HUB, driving its `in_well` substate) and `stem_axis`
(`base` = HUB/well end, `tip` = junction end) driving direction + progress. A
multi-region still-frame polygon editor in the shell (modeled on
`tracker_view._RoiEditor` / `_LandmarkEditor`) writes it.

**Topology + direction + loops.** The module knows the figure-8 adjacency
(`HUB<->CENTRAL_STEM<->JUNCTION<->{choice}<->{return}<->HUB`). Direction comes
from projecting the centroid onto the stem axis: increasing progress =
`up_stem`, decreasing = `down_stem`, with a deadband so jitter does not assign a
direction. A completed **HUB -> stem -> junction -> choice-arm -> return -> HUB
lap** is detected by tracking which choice arm was visited since the animal last
left the HUB, and firing `loop_completed`/`loop_side` on HUB re-entry. Boundary
robustness is a
**min-dwell hysteresis**: a new candidate zone must persist `min_dwell_ticks`
before it commits, so border jitter does not flicker the state; gaps between
polygons stick to the current zone.

**Validation against the beams.** `host/maze_state_validate.py` replays a
recorded track CSV through the estimator and aligns the `entered`-zone
transitions to the firmware `S,<i>` rising edges (parsed via `protocol`,
ADR-0024) on the shared host wall-clock, reporting per-beam match rate + signed
latency + candidate false transitions. A `--selftest` exercises it end-to-end on
a synthetic loop (5/5 matched); real paired-session numbers are pending a
recording (see `docs/maze_state_validation.md`).

**A separate "Maze state" tab.** The estimator is a *consumer* of the tracker,
like the rules engine (ADR-0023), not a tracking tuner - so it gets its own tab
(injected with the shared tracker + camera, read-only, no actuation), leaving the
dense Tracking tab alone and giving the state/direction/loop readout + the
state-timeline log room to breathe.

## Alternatives considered

- **Point + radius "zones" (reuse landmarks + a cm scale)** — rejected: choice/
  return arms are extended, non-circular regions; polygons classify them
  correctly, and using polygons keeps the core free of any `Scale`/cm dependency
  (unlike the rules engine, this module needs no physical distances).
- **`in_well` via the center-well landmark + a cm radius** — rejected for the
  same reason: an optional `well_norm` polygon in `zones.json` is self-contained
  and keeps the core cm-free.
- **Fold the reward hub into the lower `CENTRAL_STEM`** (no dedicated `HUB`
  zone) — rejected: the maze is a figure-8 where the central stem and both
  return arms converge at a distinct reward hub, and reward is delivered there
  (ADR-0007). A dedicated `HUB` makes "arrived at the reward zone" and lap
  completion explicit (a lap closes on HUB re-entry) instead of conflating them
  with traversing the stem, and it matches the physical topology the zone map is
  drawn against. Bonus: the HUB has no body beam, so "entered HUB" is a
  transition only vision produces - a concrete case of the beam-ambiguity
  resolution this ADR is about.
- **Fold the editor into the Tracking tab** — rejected: the Tracking tab is
  already dense, and a consumer-of-the-tracker belongs beside the Rules tab, not
  in the tracker-tuning surface (ADR-0023 precedent).
- **Direction from heading (body-axis orientation)** — rejected/deferred: heading
  is FW-0002 (needs real video to disambiguate head/tail) and is orientation, not
  travel direction. Projecting motion onto the stem axis answers the actual
  up-vs-back question directly and needs no new tracker output.
- **No hysteresis (classify each frame independently)** — rejected: border jitter
  would flicker the zone and emit spurious transitions; the min-dwell trades a
  small, measurable latency (quantified by the harness) for stability.
- **Put the state stream in the tracker core** — rejected: the tracker is about
  detecting the blob; maze semantics (zones/topology) are a separate concern, and
  a separate pure module keeps both testable and lets the rules/stats consumers
  depend on just this contract.

## Consequences

- A clean, headlessly-tested foundation (`test_maze_state.py`, 26 cases) the
  future rules-shaping primitives (`InState`/`EnteredState`) and the stats module
  build on, without either being built here.
- Direction + loop detection give the beam-ambiguity resolution the motivation
  called for; the beams remain the ground truth the harness scores against.
- One more per-rig gitignored artifact (`host/zones.json`), tied to the camera
  geometry like `roi_polygon.json` / `landmarks.json`: re-draw if the camera
  moves or the calibration changes.
- The min-dwell adds a small, predictable transition latency (~`min_dwell_ticks`
  x frame period); the harness surfaces it so it can be tuned to the real frame
  rate.
- Revisit if: consumers need finer sub-zones or per-zone dwell stats (extend the
  schema), the direction model needs a true stem centerline rather than a single
  axis segment (curved stems), or heading (FW-0002) lands and should enrich the
  state.

## Evidence / links

- Code: `host/maze_state.py` (`ZoneMap`/`Zone`, `point_in_polygon`,
  `project_scalar`, `MazeStateParams`, `ZoneVisit`, `RatState.visited_since`,
  `MazeStateEstimator.update`, `state_timeline_header`/`state_row`),
  `host/maze_state_view.py` (`MazeStateControlView` + `_ZoneEditor`),
  `host/experiment_control.py` (Maze state tab wiring).
- Tests: `host/test_maze_state.py` (26 cases; run
  `python -m unittest test_maze_state` from `host/`).
- Validation: `host/maze_state_validate.py` (`--selftest` passes 5/5) +
  `docs/maze_state_validation.md`.
- Related: ADR-0023 (rules engine consumer pattern this mirrors), ADR-0018
  (landmarks + the `S,i`/`L,i` index meanings), ADR-0015 (undistort-at-source -
  zones/centroid share the corrected px space), ADR-0014 (tracker), ADR-0024
  (protocol parser used by the harness), FW-0004 (the position-rules backlog this
  extends).
