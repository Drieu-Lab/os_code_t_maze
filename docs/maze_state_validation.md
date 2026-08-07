# Maze-state vs beam validation - results note

Companion to [ADR-0025](decisions/0025-maze-state-module.md). Records how the
vision-derived maze-state transitions (from `tmaze.core.maze_state`) are validated
against the firmware body-beam edges, and the current results.

## Why beams are the reference

The rig's body-beam sensors are **non-directional**: a single break says "the
beam at index `i` was crossed", not which way the rat was going. But a beam edge
IS an unambiguous *crossing event* at a known location, so it is exactly the
ground truth to score the vision zone-entry transitions against. The point of
the module is that continuous position *adds* what the beams can't give
(direction up-stem vs returning, completed loops); agreement on the crossings it
CAN check is the sanity check that the zone map + estimator are correct.

## Method (`tmaze.tools.maze_state_validate`)

Two CSVs sharing the host wall-clock timeline:

- **beams** - a `rig_logger.py` session CSV (`<wall_clock_iso>,<ms>,<TYPE>,...`);
  rising `S,<i>,1` edges are the crossing events (parsed via
  `protocol.sensor_edge`).
- **track** - a tracker track CSV (`host_unix_s` + `track_*` columns); replayed
  through `MazeStateEstimator` to produce `entered`-zone transitions.

Each beam role maps to the zone whose entry it marks (firmware `SENSOR_PINS` /
`Beam` order, see `firmware/cue-guided-task/rig_config.h`):

| beam | role | expected zone entry |
|------|------|---------------------|
| 0 | ENTRY | CENTRAL_STEM |
| 1 | EXIT | JUNCTION |
| 2 | LCHOICE | LEFT_CHOICE |
| 3 | RCHOICE | RIGHT_CHOICE |
| 4 | LRETURN | LEFT_RETURN |
| 5 | RRETURN | RIGHT_RETURN |

The central reward `HUB` (where the well sits and the stem + both return arms
converge) has **no body beam** - arrival there is sensed by the lick port, not a
crossing beam. So "entered HUB" is a vision-only transition and is excluded from
the candidate-false-transition count (it can never match a beam). This is itself
a small demonstration of the module's purpose: vision reports a behaviorally
meaningful transition the beams cannot.

For each beam edge the harness finds the nearest same-zone vision entry within
`+/- window` (default 750 ms) and records the signed latency `vision - beam`.
It reports, per beam role: count, matched, match rate, and latency
median/mean/sd; overall match rate; and the number of vision transitions not
explained by any beam (candidate false transitions).

Run:

```
python -m tmaze.tools.maze_state_validate --selftest
python -m tmaze.tools.maze_state_validate --beams data/<rat>_<t>.csv \
    --track recordings/track_logs/<rat>_<t>_track.csv --frame-size 1280 1024
```

## Synthetic self-test (`--selftest`)

A scripted left-lap figure-8 traversal (HUB -> CENTRAL_STEM -> JUNCTION ->
LEFT_CHOICE -> LEFT_RETURN -> HUB) sampled at 20 Hz, with ground-truth beams
placed 60 ms before each *beamed* zone entry, reconciles cleanly:

```
beam     zone             n  matched   rate  lat_ms_med  lat_ms_mean  lat_ms_sd
ENTRY    CENTRAL_STEM     1        1   1.00         160          160          0
EXIT     JUNCTION         1        1   1.00         160          160          0
LCHOICE  LEFT_CHOICE      1        1   1.00         160          160          0
LRETURN  LEFT_RETURN      1        1   1.00         160          160          0

overall: 4/4 beams matched (rate 1.00); 6 vision transitions,
0 unmatched by a beam (candidate false transitions)
```

The 6 vision transitions include the two beamless `HUB` entries (start + lap
close), which is why 4 beams reconcile with 6 transitions at 0 candidate false
transitions. The ~160 ms latency is expected and instructive: it is the beam's
60 ms pre-trip plus the estimator's commit lag - a new zone must persist
`min_dwell_ticks` (3) before it commits, i.e. ~2 sample intervals x 50 ms =
100 ms at 20 Hz. The min-dwell is the hysteresis that stops border jitter from
flickering the state (ADR-0025); it trades a small, predictable latency for
transition stability. On real data, use the reported latency to pick
`min_dwell_ticks` for the tracker's actual frame rate.

## Real-session validation - PENDING

Validation on a **recorded rig session** (a paired `rig_logger` beam CSV + a
`track_logs` track CSV of the same run) is pending such a recording. When one is
available:

1. Draw the zone map in the Maze state tab on that session's background frame
   (or a still), save `host/zones.json`.
2. Run the harness with the two CSVs and the recording's `--frame-size`.
3. Record the per-beam match rate + latency here and tune `min_dwell_ticks` /
   `min_confidence` if the rates are low or latencies large.

Acceptance target (from FW-0004 / ADR-0025): position-derived "entered zone"
agrees with the corresponding beam edge for the large majority of crossings,
within a small, consistent latency, with few false transitions.
