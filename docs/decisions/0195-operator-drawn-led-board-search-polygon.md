# ADR 0195: Confine LED detection to an operator-drawn board search polygon

- **Status:** Accepted
- **Date:** 2026-09-22
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/vision/led_detector.py` (`polygon_mask`, `mask_extremes`,
  `accumulate_extremes`), `host/tmaze/core/sync/led_map.py` (schema v2→v3,
  `board_search_poly`, `led_board_search_path`), `host/tmaze/tools/sync_fit.py`
  (`--board-poly`, precedence, baking), `host/tmaze/ui/tracker_view.py`
  (`Define LED board region…` → `host/led_board_search.json`), `PROGRAMS.md`,
  `docs/mapping-session-runbook.md`
- **Supersedes / Superseded by:** — (refines the self-calibrating ROI of ADR-0153)

## Context

The cross-stream sync (`sync_fit`, FW-0045, ADR-0153) finds the board's indicator
LEDs in the recorded video and binds them to serial channels. To keep off-board
motion (a rat on the maze, the operator's hand while firing) from flooding the
blob list, the detector is **self-calibrating**: it detects the persistent
red/green *fiducial* LEDs and searches inside their convex hull (ADR-0153).

On the rig's first real mapping bundle (`000_20260922_163830`) that safeguard was
not enough. Two things went wrong at once:

- **A high noise floor.** The whole-frame dry-run reported `noise_floor = 95`
  (the H.264 floor is normally ~25) and ~271 candidate blobs. A running-max
  projection showed why: a large green trail through the maze stem plus orange/red
  trails on the left — the operator's arm sweeping across the frame while firing
  sensors. That motion, spread across the whole frame, both lifts the floor and
  packs the blob list, and the left lick LED `L0` lost the NMS/threshold
  competition and never bound.
- **Spurious fiducials corrupting the hull.** The fiducial hull's bounding box
  reached `x≈573` — far left of the board (which sits at `x≳843`). Several of the
  14 detected "fiducials" were false persistent-LED hits in the noisy maze
  region, so the auto-hull *itself* stretched into the maze rather than confining
  the search to the board.

The fiducial-hull ROI only filters *blinking blobs*; it does not lower the noise
floor computed over the crop, and it depends on the fiducials being detected
correctly — which failed here. A rectangle can't fix it either: the board sits at
a diagonal next to the maze opening the rat crosses, so any rectangle tight enough
to exclude the maze corner also clips the board or its drift margin. The camera is
fixed but the board can shift on the cart between sessions, so the search region
must stay generous around the board while still excluding the maze.

## Decision

Add an **operator-drawn board search polygon** the detector is confined to,
overriding the auto-hull.

- **Geometry (vision).** `led_detector.polygon_mask()` builds an inside-polygon
  boolean mask (vectorised ray-casting, no OpenCV); `mask_extremes()` blanks every
  pixel *outside* the polygon in the accumulated max/min and the median samples, so
  the difference images, the persistence (fiducial) images, **and** the noise floor
  see only what is inside. `accumulate_extremes()` takes an optional crop-relative
  `poly` and applies the mask after the single video read.
- **Storage (map).** `led_map.json` gains an optional `board_search_poly`
  (full-frame px), schema **v2→v3** (v1/v2 still load). It is baked in on `--map`
  and reused automatically by every later fit; its bounding box is the stored
  `board_rect` (the fast read window), and the polygon masks inside it.
- **Shell (`sync_fit`).** `--board-poly <file>` reads a normalized `polygon_norm`
  JSON (the same schema as `roi_polygon.json`) and denormalizes to px. Precedence:
  an explicit `--board-poly` wins; else the polygon baked into the map is reused;
  else, only while *building* a map, the default `host/led_board_search.json` is
  picked up. A polygon overrides `--board-rect`. `clock_fit_<id>.json` records the
  `board_search_poly` used, for provenance.
- **UI (draw it once).** The Tracking tab gains a **"Define LED board region…"**
  button that reuses the existing still-frame polygon editor (`_RoiEditor`) and
  saves `{"polygon_norm": …}` to `host/led_board_search.json` — separate from the
  tracker ROI (`roi_polygon.json`) and from the task zone map.

Draw the polygon to enclose the **whole board** — all S/L/SOL channel LEDs and the
green/red pose fiducials, with margin for cart drift — but not the maze the rat
crosses.

## Alternatives considered

- **Rely on the fiducial-hull ROI alone (status quo, ADR-0153).** Rejected: it
  only prunes blinking blobs, not the noise floor, and it broke here because
  spurious off-board fiducials stretched the hull into the maze.
- **A tighter `--board-rect`.** Rejected by the operator: the board is diagonal
  beside the maze opening, so a rectangle that excludes the rat's path also shrinks
  the region the board may drift within and still be found. A polygon keeps generous
  margin everywhere except the maze corner.
- **A new `LED_board_search_zone` in the task zone map.** Rejected: the zone
  vocabulary is fixed to task regions, and `maze_state`/`cue_gate` scan every zone
  to classify the rat, so a stray region risks being reported as the rat's location
  and perturbing cue/scoring logic. A dedicated vision/sync file avoids the coupling.
- **Overload the tracker ROI (`roi_polygon.json`).** Rejected: that is the
  tracker's mask; the sync search region is a different artifact and must not clobber
  it.

## Consequences

- The map build (and every later fit) ignores the maze the rat crosses: the noise
  floor reflects only the board region, and off-board motion can neither spawn blobs
  nor corrupt the fiducial hull. This is what lets `L0` bind.
- The polygon is drawn once in the GUI and baked into the map; routine fits need no
  flag. A specific run can still override with `--board-poly`, and the map is rebuilt
  if the board is relocated.
- Normalized `polygon_norm` (0..1) transfers across resolutions and the
  undistort-at-source pipeline (ADR-0015), exactly like the tracker ROI/zones — it is
  denormalized against the bundle's sidecar frame size, so it needs that frame size to
  resolve (clear error otherwise).
- Schema v3 is additive and backward-compatible; older maps load with
  `board_search_poly = None` and fall back to the rectangle/hull.
- If the drawn polygon is *too* tight and the board later drifts partly outside it,
  fiducials/LEDs outside are unsearched and the fit degrades — the same failure a too-
  tight rectangle would have, mitigated by drawing generously.

## Evidence / links

- Mapping bundle `000_20260922_163830`; whole-frame dry-run `noise_floor=95`,
  ~271 blobs, `L0` unbound; `board_poly` bbox reaching `x≈573` (spurious left
  fiducials). Scratch analysis + the running-max annotation figures live outside
  the repos at `t-maze_recordings/_led_fiducial/` (`map_max_rect.png`,
  `map_max_board.png`).
- Refines ADR-0153 (self-calibrating fiducial-hull ROI) and the mapping-session
  runbook (`docs/mapping-session-runbook.md`).
- Tests: `tests/test_led_detector.py` (`TestBoardSearchMask`, `board_search_poly`
  round-trip, v2-loads) and `tests/test_sync_fit.py` (`TestBoardSearchPoly`).
