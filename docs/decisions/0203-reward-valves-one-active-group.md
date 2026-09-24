# ADR 0203: The reward valves form a one-active group, measured against the channels that fired

- **Status:** Accepted
- **Date:** 2026-09-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/led_board_layout.json` (`exclusive_groups.rewards`);
  `host/tmaze/core/sync/exclusive.py` (`enforce(fired=)`);
  `host/tmaze/tools/sync_fit.py` (`apply_one_active(fired=)`, `one_active.reference`);
  `host/tests/test_sync_exclusive.py`
- **Supersedes / Superseded by:** — (refines ADR-0201)

## Context

ADR-0201 made the offline LED sync keep at most one beam and one lick channel
active per frame, because light spills between neighbouring board LEDs. The same
spill happens between the 15 reward-valve LEDs (`SOL0.0`-`SOL2.4`, one LED each):
on rat 602's session `602_20260923_151520`, `SOL2.1`'s flash dims its neighbours
`SOL2.0_a` and `SOL2.2_a`, and those two LEDs were bound to `SOL2.1` (see
ADR-0202 for the details).

The operator asked to add the rule "two solenoids are never active at the same
time". The serial logs support it: over all 44 readable bundles to 2026-09-24,
**0** overlaps among 2526 valve pulses (all ports counted as one group). The same
survey found 0 overlaps among 144,090 lick contacts, and 3 among 40,200 beam
breaks, each 5-6 ms long (two sessions of rat 602 on 2026-09-11) - far below one
40 ms frame, so they cannot matter for the video.

Adding the valves exposed a weakness ADR-0201 had named under "Revisit if": the
rule scores each LED against the **median** full change of its row this session.
That works when most channels of the group fire. In a rat session only 2-3 of
the 15 valves ever open, so the median would be set by idle LEDs whose only
change is noise or spill, and noise would then score as "really lit".

## Decision

1. **`host/led_board_layout.json`** gains a third group, `rewards`: all 15 valve
   channels `SOL0.0`-`SOL0.4`, `SOL1.0`-`SOL1.4`, `SOL2.0`-`SOL2.4`, listed
   explicitly, one group across all three ports.
2. **Reference from the channels that fired.** `exclusive.enforce` takes
   `fired`: the channels with serial events in this session (`sync_fit` reads
   them off the log). A row's typical change is then the median over the LEDs of
   those channels only. A row in which none of them fired falls back to all its
   LEDs (the ADR-0201 behaviour). This applies to every group, so beams and licks
   benefit too.
3. `clock_fit`'s `one_active` block records `reference` (`channels that fired` or
   `all LEDs`). Revert as for ADR-0201: remove the group from the layout file, or
   `--no-one-active` for one run.

The rule acts on the frames where the top valve scores at least 0.5 (it is
open), and sets every other valve LED to its resting level on those frames. That
also removes the *dip* a neighbour shows from bloom, even though a dip never
counts as "spill removed" (that counter only counts movement towards an LED's
active level).

## Alternatives considered

- **One group per port** (`SOL0.*`, `SOL1.*`, `SOL2.*`). Rejected: the logs show
  no overlap across ports either, so one group is simpler and also covers
  neighbouring LEDs that belong to different ports.
- **Keep the all-LED median and raise the thresholds for valves.** Rejected: the
  idle LEDs' change is noise whose size varies by session; any fixed threshold
  would be wrong somewhere. The channels that fired give the real full change.
- **Use each channel's own change for channels that fired and the row median for
  the rest.** Rejected: more rules for no measured gain; ADR-0201's reason for a
  shared row reference (a never-fired channel must not tie with the real one) is
  unchanged.
- **Leave the valves out and rely on the map check (ADR-0202).** Rejected: the
  map check only drops a mis-bound LED, while the rule removes the spill before
  the on/off threshold is set, and works without trusting the map's labels.

## Consequences

- **`602_20260923_151520`** (10 min, dry run, map check off to isolate this
  change): `SOL2.0_a` and `SOL2.2_a` are now silent instead of bound to `SOL2.1`.
  The valve rule acted on 79 frames; spill removed on 0. The fit keeps 138 edges;
  its `SOL2.1` edges now come from `SOL2.1_a` itself (lag 50.8 ms) instead of the
  neighbours' dips (65.8 ms). MAD 11.5 ms (10.9 before, when the neighbours' more
  regular dips stood in for the real flashes).
  With `fired`, the lick reference comes from `L2` only (the only port licked),
  so the lick rule acts on 1220 frames instead of 1462 - fewer small lick
  fluctuations count as "active". Lick bindings are unchanged.
- **Mapping session `000_20260922_163830`**: all 15 valve LEDs still bind to their
  own valve with 100 % precision; the valve rule acted on 2097 frames, spill
  removed on 0; 650 edges, MAD 11.1 ms (11.5 before).
- **Revisit if** a protocol ever opens two valves together (e.g. a mixed reward),
  or valves are driven in overlapping bursts for cleaning: remove the group or
  split it. The conflict counter (a second valve scoring >= 0.75) will show it.

## Evidence / links

- Overlap survey across all bundles (serial logs only, read-only scratch):
  `t-maze_recordings/_led_fiducial/one_active/overlaps_all.py` / `.txt`.
- Dry-run reports: `rat_v3_nocheck.txt`, `rat_v3.txt`, `map_v3.txt` (and
  `rat_on.txt` / `map_on.txt` from before) in the same folder.
- Related: ADR-0201 (one-active rule), ADR-0202 (map check), ADR-0196, FW-0045.
