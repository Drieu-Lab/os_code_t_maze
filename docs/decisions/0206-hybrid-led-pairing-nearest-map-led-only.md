# ADR 0206: A detected LED blob is only paired with the map LED it is nearest to

- **Status:** Accepted
- **Date:** 2026-09-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/vision/led_detector.py` (`hybrid_blobs`), `host/tests/test_led_detector.py`
- **Supersedes / Superseded by:** — (refines ADR-0153's hybrid rule and ADR-0196's closest-first pairing)

## Context

With a map, the offline LED sync locates each map LED by the **hybrid rule**
(ADR-0153): the stored position is carried into this session through the board
pose, and if a detected blinking blob lies within `tol_px` (4 px with the
current map: `nms_radius` 3 + 1) the LED is traced at that blob instead;
otherwise it is traced at its stored spot. ADR-0196 made the pairing
closest-pair-first, because the beam rows' LEDs sit only ~4 px apart.

The whole-archive dry run (`_sync_dryrun_all_20260929_152425`) showed the ENTRY
row's second LED, `S1_b`, unbound or silent in 10 of the 23 sessions since the
board was re-seated on 09-22, while `S1_a` and every other beam LED bound.
Re-running detection and mapping every located LED back into reference
pixels (scratch `eval_s1b_pos.py`) showed why:

- In the failing sessions `S1_b` was traced at ~(1024.1, 730.8), 3.4 px from its
  map spot (1024.97, 734.11). That point is a **second detection of `S2_b`**
  (map (1025.98, 729.02), 2.6 px away), which already had its own, closer blob.
  Closest-first gave `S2_b` its own blob, and left the duplicate free for the
  next-nearest map LED - `S1_b` - which took it and was traced off its LED.
- The map position is right: on `602_20260929_124256`, where a 14.5 px board
  shift kept any blob from being paired, `S1_b` was traced at its
  pose-transformed map spot and bound to `S1` at precision 0.97.
- The unmapped blob that bound to `S1` in those sessions (~(1018, 725)) is
  not `S1_b`: it goes LIT while S1 is active (beam LEDs go dark) at
  precision 0.68-0.78 - spill from a neighbour, not a channel LED.

## Decision

In `hybrid_blobs`, a detected blob may be paired only with the expected LED it
is **nearest** to (within `tol_px`, closest pair first as before). A blob whose
nearest map LED already has a closer blob is a duplicate; it is left over and
reported as an unmapped blob (`blob_N`), and the next-nearest LED is traced at
its own stored spot. `led_map.json` is unchanged.

## Alternatives considered

- **Move `S1_b` in `led_map.json`** (the first plan). The map spot is correct
  (above); moving it would make it wrong for every session where it binds.
- **Shrink `tol_px` to 3 px.** It would stop this snap (3.4 px) but not a
  duplicate at 2.9 px, and several real LEDs are detected 2.8-2.9 px from their
  map spots (e.g. `S1_a`, `S0_b`), so it would drop genuine pairings too.
- **Also trace the stored spot whenever a paired blob is more than ~2 px away.**
  Two traces per LED id complicates binding and the report for no gain once
  duplicates can't be handed on.
- **Rebuild the map from a recent session.** It would re-localise every LED
  for the same board; the fault is in the pairing, which would repeat.

## Consequences

- `S1_b` binds in the sessions where it failed; nothing else changes. The
  duplicate now shows as an unmapped blob bound to `S2`, which feeds no new
  fit edges (the fit already takes one edge per channel and firmware time).
- Before 09-22 (board ~59 px and 0.9° away, board-pose fit error ~1.8 px -
  half the row spacing) `S1_b` fails for a different reason: its traced spot
  lands between rows. No single map position serves both board placements; `S1`
  is still fitted from `S1_a` there. Left as is.
- **Revisit when:** LEDs are packed closer than `tol_px` in both directions (the
  nearest-LED test then decides between two real LEDs), or the map is rebuilt.

## Evidence / links

Comparison against the saved dry run (scratch `eval_nearest_rule.py`, detection
+ fit re-run on the bundles, nothing written into them):

| session | before | after | fit edges | residual MAD |
| --- | --- | --- | --- | --- |
| `602_20260925_131452` | `S1_b` unbound | `S1_b` → `S1=1` | 940 → 948 | 11.70 → 11.70 ms |
| `600_20260925_145917` | `S1_b` unbound | `S1_b` → `S1=1` | 1222 → 1224 | 11.53 → 11.55 ms |
| `602_20260923_151520` | `S1_b` matched | no LED changed | 904 → 904 | 11.12 → 11.12 ms |
| `601_20260929_135930` | `S1_b` matched | no LED changed | 1204 → 1204 | 11.43 → 11.43 ms |
| `600_20260820_131447` (pre-09-22) | `S1_b` unbound | no LED changed | 1568 → 1568 | 15.24 → 15.24 ms |

- Unit test: `test_hybrid_blobs_a_second_detection_of_a_neighbour_is_not_handed_on`
  (the real coordinates of `602_20260925_131452`).
- Related: ADR-0153 (hybrid rule), ADR-0196 (closest-first; the map build),
  ADR-0202 (map check), FW-0045.
