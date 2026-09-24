# ADR 0196: Build the LED map with a two-pass, event-triggered detector

- **Status:** Accepted
- **Date:** 2026-09-24 (proposed 2026-09-23)
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/tools/sync_fit.py` (the `--map` builder),
  `host/tmaze/core/sync/event_frames.py` + `board_layout.py` (new, pure),
  `host/tmaze/vision/led_event_map.py` (new), `host/tmaze/core/sync/led_map.py`
  (schema v4), `host/led_board_layout.json` (new config), and one per-session
  fix in `led_detector.hybrid_blobs`
- **Supersedes / Superseded by:** — (refines ADR-0153; complements ADR-0195, ADR-0199)

## Context

The offline clock sync (FW-0045, ADR-0153) finds each board indicator LED by
*unsupervised image statistics*: an LED is a "blob" only if it stands out in a
difference image (`max − median` or `median − min` over the clip). LEDs lit on
~90 %+ of frames are filed as persistently-lit **fiducials** (the pose anchors).
Binding a blob to a serial channel happens afterwards, by matching its lit/dark
spans against the serial event spans.

On the first real mapping bundle (`000_20260922_163830`) that left gaps:

- The ENTRY (`S0`) and EXIT (`S1`) beam LEDs rest lit and go dark only during a
  break (~2 % of frames). They passed the 90 % bar and were **filed as
  fiducials** (`red_5`, `red_6`, ~4 px from the LEDs).
- The left-lick LED (`L0`) flashes only briefly and never became a blob.
- **Every beam and lick sensor lights TWO LEDs**, one in each of two rows (the
  second row dimmer, ~1/3 of the bright row's swing; the rows are offset by about
  (+11.5, +3) px). Pass 1 found only some of the dim-row LEDs, and one it did find
  (`L2`) was named as if it were the bright one. The expected total is 33 channel
  LEDs: 12 beam + 6 lick + 15 reward. Pass 1 found 25.

Hardcoding the missing pixels was rejected (see Alternatives): the board is on a
movable cart, so any fixed pixel is stale as soon as the cart is nudged.

## Decision

The **`--map` builder** (which has the full serial log as ground truth) finds
the LEDs *supervised by the serial events*, in two passes.

1. **Pass 1 — bootstrap (the existing pipeline, unchanged).** Detect the easy
   blinking blobs, bind them, and fit the clock. On the test bundle that is 25
   LEDs and a solid fit (588 pairs, MAD 11.5 ms). Pass 1 needs none of the hard
   LEDs, so the fit can be trusted before we look for them.

2. **Pass 2 — event-triggered localisation, for ALL channels** (one more video
   read). The pass-1 fit places each channel's ACTIVE serial spans on the video's
   frame axis. The active state is the one the bundle's `sensor_polarity` says,
   ADR-0199. Per event, the builder averages up to 6 frames DURING it (skipping 1
   frame after the predicted onset) minus up to 8 frames just BEFORE it (skipping
   2). The per-channel average of these "during − before" images is the channel's
   **composite**: anything steady at that moment cancels, so its own LED(s) are
   what remain. The pure core (`event_frames`) turns this into per-frame weights;
   the vision layer (`led_event_map`) sums `weight × grey frame`.

   - **Quiet baseline.** A before-frame counts only if no channel of the same
     function (another beam for a beam, another lick for a lick) and no reward is
     active within ±1 frame, so a neighbour's glow cannot leak into the
     baseline. During-frames drop any frame where another channel of the same
     function is active.
   - **Board layout.** `host/led_board_layout.json` says how many LEDs each
     channel lights (`S`: 2, `L`: 2, `SOL`: 1; a per-channel override is
     possible). A channel's LEDs are the strongest local maxima of its smoothed
     |composite|, up to that count. Each must be ≥ 6 robust-noise units, ≥ 15 %
     of the channel's brightest, and ≥ 4 px from the others.
   - **Strong-first claim.** Channels are handled in order of their peak
     strength, and each claimed LED blocks 2 px around it (4 px for a reward
     LED, whose flash blooms wider) for the channels after it. A weak channel
     whose events co-fire a bright reward LED therefore cannot take that LED's
     pixel.
   - **Naming.** A channel's LEDs are named brightest first: `_a` = bright row,
     `_b` = dim row.
   - **Polarity** of a found LED is the sign of its composite: a darker LED during
     the event is `dark`, a brighter one `lit`. The prototype saw this sign flip
     on beams and licks only because the sync was then gating on the resting
     state. ADR-0199 fixed that, and with the right polarity the signs are right:
     beams `dark`, licks and rewards `lit`, matching the 7b hand measurement.

3. **Reconciliation.**
   - A pass-1 LED within 3 px of a pass-2 LED of the same channel is the same LED
     (pass 1's centroid is pulled by bloom; the next LED along a row is ~4 px
     away). It lends its measured lag and is noted `pass 1+2`.
   - A pass-1 binding that pass 2 put somewhere else is **kept but flagged**, not
     silently dropped.
   - Unbound pass-1 blobs are kept unless they sit on a channel LED.
   - **Fiducials within 5 px of a channel LED are dropped**: they are a channel
     LED resting lit. The misfiled `red_5`/`red_6` sat ~4 px off; the nearest real
     fiducial is ~7 px away.

4. **Warnings** printed with the map: a channel with fewer LEDs than the layout
   expects; a channel with fewer than 3 clean events (not localised); a
   bright→dim pair that is more than 3 px off the board's median row offset
   (usually a neighbour's LED picked by mistake); pass-1 contradictions.

5. **Map schema v4** (`led_map.json`) adds:
   - `reference.sensor_polarity` — which wire state the channel names' `=0`/`=1`
     meant in the reference session;
   - `event_map` — the pass-2 settings plus the layout file used;
   - `bleed` — `bleed[source][led_id]`: how much that LED's disk-minus-ring trace
     moves when the source channel fires, as a fraction of its move for its own
     channel (only |fraction| ≥ 0.05). It is computed from the composites with
     the per-session trace sampler's own measure (linear, so exact). This is the
     crosstalk record the per-session one-active-per-function rule will use (see
     Consequences).

   v1–v3 maps still load.

6. **Progress indicators.** Map building reads the video three times (find
   LEDs, LED traces, event images), about 40–75 s each for a 9-minute clip, so
   each read prints its percentage to stderr. This is map-build UX only.

7. **Per-session fix found by the self-check.** `hybrid_blobs` paired map
   entries with detected blobs within 4 px **in map order**. With the full map,
   `S0_a` sits 4 px from `S4_a` and comes first, so it took S4's blob and both
   traced the same LED. Five LEDs (`S0_a/b`, `S1_a/b`, `L1_b`) re-bound to a
   neighbour's channel. Pairing is now **closest pair first**, each blob used at
   most once. With that, all 33 re-bind to their own channel.

A regular session still uses the unsupervised hybrid. The map it reads is just
complete and correctly classified.

## Alternatives considered

- **Hardcode the ENTRY/EXIT/L0 pixels into the map.** Rejected: fragile to cart
  movement (the pipeline exists to re-derive positions per session), and only as
  good as a single hand measurement. The user flagged this directly.
- **Single-pass event-triggered detection (no pass-1 fit).** Rejected: gating
  frames needs a camera↔serial alignment, and there is a real ~40–70 ms LED
  display lag plus frame quantisation. Without the fit the composites are
  contaminated by mis-assigned frames; the pass-1 fit supplies that alignment,
  and it already absorbs the mean display lag.
- **A fixed 2 px same-LED match radius (half the pitch).** First tried: pass 1's
  bloom-pulled centroids of `S2_b`/`S3_b` sat 2.06–2.1 px off, and each came back
  as a spurious third LED `S2_c`/`S3_c`. 3 px separates "same LED" (≤ 2.1 px)
  from "neighbour" (~4 px).
- **Drop events whose frames overlap a reward.** Rejected in favour of the
  strong-first claim: on a rig where a lick always dispenses a reward, it would
  starve that lick channel of events.
- **Tune `fid_min_frac` so rarely-broken LEDs are not fiducials.** Rejected:
  it would also demote genuine always-on status LEDs (ADR-0153).
- **Re-record the mapping session holding ENTRY/EXIT/L0 broken far longer.**
  Rejected as the primary fix: a fragile manual procedure that does not help
  brief-flash LEDs like `L0` or the dim row.

## Consequences

- **Makes easy:** every channel whose LED is in view is bound, both rows
  included, with a stable bright/dim naming. Fiducials are cleanly "persistent
  LEDs minus channel LEDs". The easy LEDs self-validate the method (pass 2
  reproduced every pass-1 binding). The map records its own polarity and the
  LED-to-LED crosstalk.
- **Costs:** `--map` reads the video three times (offline, once per rig; ~2.5
  min for 9 minutes of video). Pass 2 depends on pass 1's fit: a bundle too
  degraded to fit gets no pass 2 (the map then holds pass 1's LEDs, with a
  warning).
- **Bleed is recorded, not yet acted on.** On the test bundle the lick row
  bleeds strongly: L1 moves `L0_b` by 37 % and `L0_a` by 28 % of their own
  swings. The beams show mostly negative values (S0 → `S5_b` −34 %), which is a
  neighbour's flash landing in the background ring. `L2 → SOL2.4_a` (27 %) is
  the centre reward genuinely firing during lick events, not optics. Using these
  numbers — winner-take-all within a function, and excluding neighbouring LEDs
  from the ring — is the next, separate change (the one-active-per-function
  rule).
- **A native crash was seen once** (access violation ~10 % into the third video
  read, not reproduced on the rerun of the identical code). It looks like the
  OpenCV/FFmpeg decoder; if it recurs, rerun, and consider reading pass 2 in
  chunks.
- **Revisit if:** the display lag grows beyond the guard frames, a board packs
  channel LEDs closer than ~3 px, or the layout gains a third row.

## Evidence / links

- ADR-0153 (fiducial ROI + binding), ADR-0195 (board search polygon), ADR-0199
  (per-bundle sensor polarity), FW-0045 (the sync feature).
- Mapping bundle `000_20260922_163830` (sensor polarity `corrected`, from
  `format.json`). The built map is installed as `host/led_map.json`:
  - 33 channel LEDs, all bound, 8 fiducials (5 red, 3 green), no warnings;
  - `S0` `(950,735)/(961,737)`, `S1` `(1012,731)/(1025,734)`, `L0`
    `(892,698)/(904,701)`, matching the 7b hand measurement (ENTRY ≈ `(949,735)`,
    EXIT ≈ `(1013,730)`; resources repo `rig-hardware/camera/README.md`) and the
    scratch prototype `t-maze_recordings/_led_fiducial/dual_led_panels.py`.
- **Self-check:** an ordinary per-session run of the same bundle against the new
  map locates and re-binds all 33 LEDs to their own channel (after the
  `hybrid_blobs` fix). The clock fit uses 652 pairs, MAD 11.4 ms.
- On the committed 60 s legacy-polarity clip (`600_20260903_134552`) the map
  build grows from 11 to 15 LEDs: both rows of `S0` and `S1` are now found
  (`test_sync_fit.TestEndToEndOnClip`).
- Tests: `test_sync_event_frames` (frame windows, quiet baseline, layout,
  row-offset check), `test_led_event_map` (synthetic two-row beam, lick + co-firing
  reward, strong-first claim, bleed, progress), `test_sync_fit` (pass merge,
  fiducial exclusion, warnings), `test_led_detector` (schema v4, closest-first
  hybrid pairing).
