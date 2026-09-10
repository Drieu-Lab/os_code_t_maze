# ADR 0153: Anchor the board ROI and pose on the persistent red/green LED fiducials

- **Status:** Accepted
- **Date:** 2026-09-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/sync/led_map.py`, `host/tmaze/vision/led_detector.py`,
  `host/tmaze/tools/sync_fit.py`, `host/led_map.json` schema (v1→v2),
  `docs/design/cross-stream-sync-design.md` §3.4, `docs/mapping-session-runbook.md`
- **Supersedes / Superseded by:** Refines the ROI/registration part of ADR-0152's
  predecessors on sync (design-doc Slice 2/3); does not supersede an existing ADR.

## Context

The board-LED video↔Arduino sync (design doc §3) reads the rig's indicator LEDs
out of the recorded video and fits `cam_timestamp = offset + rate·firmware_ms`.
Two problems remained after the offline tool landed (design-doc Slice 3):

1. **A hand-drawn search rectangle.** The detector searched a `board_rect` seeded
   by the mapping run and stored in `led_map.json`. Building a map on a session
   that still has a rat in it required passing `--board-rect` by hand (the
   runbook hard-coded `800 470 1120 800`), because a max-minus-median over the
   whole frame lights up wherever the rat moved and floods the blob list. The
   same flooding happens when an operator fires channels **by hand** during a
   mapping session — the hand's motion is a moving bright blob, not just the LED.
2. **No board pose.** Registration matched the *blinking* LED constellation, but
   a row of four identical channel LEDs is ambiguous under a one-pitch shift, and
   a channel LED that never fired this session had no anchor at all.

The rig's electronics board carries a constellation of **persistently-lit red and
green LEDs** (power/status indicators) that are on whenever the board is active.
Crucially they are present in **every past recording** (the retroreflective border
markers the lab has since started adding are not — they were placed ad-hoc, in
non-repeatable positions, and appear in no recorded session), they are soldered
in fixed places, and their colour breaks the left/right symmetry of an otherwise
identical LED row.

## Decision

**Make the persistent red/green LEDs the geometric anchor of the whole pipeline.**

- **Detect fiducials by persistence + colour.** `led_detector.persistence_images`
  takes the low percentile (`100·(1−fid_min_frac)`, default 10th) over the sampled
  frames of the per-pixel colour contrast `R−max(G,B)` (red) and `G−max(R,B)`
  (green). A steady LED scores high in every frame; a dot lit only briefly (a
  hand or the rat) is dark most of the time and scores low; a neutral/white
  retroreflective marker has `R≈G≈B` and scores ≈0. `detect_fiducials` finds these
  as local maxima and **excludes any that coincide with a blinking blob** — a
  channel LED that merely *rests lit* (a beam/lick LED is on ~95 % of the time)
  looks persistent but also blinks, so it is a channel LED, not a fiducial.
- **Auto-ROI = the fiducials' convex hull.** `led_map.board_poly_around` grows the
  hull outward by `hull_margin_px` (default **30 px**, up from the old rect margin
  of 24; one channel LED on the real board sits ~17 px outside the fiducial
  border, so a smaller margin dropped it). The blinking LEDs are searched inside
  that polygon; anything outside — hand, rat, reflection — is ignored. The
  polygon's bounding box is the fast crop. All the geometry (`convex_hull`,
  `point_in_poly`, `board_poly_around`) is **pure stdlib** so it stays in the core
  (ADR-0083 / `test_architecture`).
- **Board pose from a colour-constrained fit.** `register` takes optional
  per-point colour labels; correspondences may only pair same-colour points,
  which both speeds the search and breaks the row ambiguity. A v2 map stores the
  fiducial constellation + colours; each session registers its fiducials against
  them to recover the board pose (rotation + shift + a little scale).
- **Hybrid LED location.** With a pose, every mapped LED is placed by
  `hybrid_blobs`: a detected blinking blob when one is present, else the
  pose-transformed stored position, so an LED that never fired this session is
  still traced.
- **`led_map.json` schema v1→v2** adds a top-level `fiducials` list and
  `reference.board_poly`; `from_dict` still loads v1 maps (they fall back to
  registering the blinking constellation).
- **Markers are a physical QA reference, not a detected input.** The pipeline
  keys on the LEDs' colour, so neutral markers are ignored. `--map` (and
  `--overlay` on a normal run) writes a debug PNG — the ROI polygon + fiducials +
  located LEDs drawn on a still frame — so the operator can eyeball that the ROI
  lands inside the physical marker corners.

## Alternatives considered

- **Anchor on the retroreflective markers (detect them).** Rejected: they appear
  in no past recording (we must process historical sessions), and their current
  placement is ad-hoc and non-repeatable, so a marker-based geometry would not be
  reproducible if a marker were replaced. Only the corner markers are repeatable;
  a considered marker scheme is deferred to FW-0059. Markers stay a manual QA aid.
- **Keep the hand-drawn `board_rect`.** Rejected: it needs a human measurement per
  rig move, floods on a rat/hand session, and gives no pose (so no fallback for a
  silent LED and no way to break the row ambiguity).
- **A rotated-rectangle ROI (or reuse the zone editor).** Rejected: the convex
  hull of the fiducials is tighter and needs no hand editing; the still-frame
  zone editor would reintroduce a manual step for no gain while the LEDs already
  define the region.
- **Strict per-pixel min instead of a low percentile.** Rejected: a single dark
  frame from compression noise would reject a real fiducial; the 10th percentile
  is robust to brief occlusions while still rejecting a transient.

## Consequences

- A map can now be built from *any* session — no rat-free requirement, no
  `--board-rect` by hand — because the ROI excludes off-board motion; the runbook
  is rewritten around this. Historical sessions are processable (the LEDs are in
  them).
- The blink detector is unchanged and still finds exactly the 11 blinking LEDs on
  the (now wider) board clip, because a steady LED produces no difference-image
  signal — the fiducials are invisible to it, and vice-versa.
- The board clip fixture was re-cut **wider** (full-frame x 815–1025, y 560–744)
  to include the persistent LEDs. A fresh CRF-22 re-encode at a new crop shifts
  luma centroids sub-pixel (~1 px), so the clip test tolerances were loosened
  from 0.6 to ~1.3 px — still far under the ~12 px LED spacing.
- Follow-ups: a repeatable retroreflective-marker placement scheme (FW-0059);
  the ENTRY/EXIT beam LEDs are still not visible on the board (design §3.7, open);
  manifest/format registration of the `sync/` stream is the next slice.

## Evidence / links

- Code: `led_map.py` (schema v2 + `convex_hull`/`point_in_poly`/`board_poly_around`),
  `led_detector.py` (`persistence_images`, `detect_fiducials`, colour-labelled
  `register`, `hybrid_blobs`, `draw_overlay`), `sync_fit.py` (fiducial pose +
  hybrid + `--overlay`).
- Tests: `test_led_detector.py` (fiducial detection, rest-lit exclusion, colour
  registration, hull, hybrid, overlay), `test_led_detector_clip.py` (real clip:
  11 blinking LEDs + fiducial hull contains them), `test_sync_fit.py` (v2 map
  build + overlay, v2 hybrid run, v1 fallback).
- Design: `docs/design/cross-stream-sync-design.md` §3.4 (registration) and its
  fiducial-slice note; backlog FW-0045 (this feature), FW-0059 (marker scheme).
