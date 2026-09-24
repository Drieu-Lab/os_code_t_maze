# ADR 0201: The LED sync keeps at most one beam and one lick channel active per frame

- **Status:** Accepted
- **Date:** 2026-09-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** new `host/tmaze/core/sync/exclusive.py`; `host/tmaze/core/sync/board_layout.py`
  (`exclusive_groups`); `host/led_board_layout.json`; `host/tmaze/tools/sync_fit.py`
  (`apply_one_active`, `one_active_lines`, `--no-one-active`, `clock_fit` `one_active`
  block, `TOOL_VERSION` 2); `host/tests/test_sync_exclusive.py`
- **Supersedes / Superseded by:** —

## Context

The offline LED sync (`sync_fit`, FW-0045) reads each board LED's brightness on
every frame, turns it into on/off periods, and matches those against the serial
log. It reads each LED on its own. But light spills between neighbouring LEDs
(the [bleed](../glossary.md#bleed-led-crosstalk) table that ADR-0196 stores in
`led_map.json`). On the mapping session, when `L1` fires, `L0`'s dim-row LED moves
by 46 % of its own full change, and beams bleed up to about 25 %.

The on/off threshold is set from each LED's own brightest and darkest values this
session. So when a channel never fires in a session, its whole range comes from
a neighbour's spill, and that spill crosses the threshold like a real event. This
happens: on rat 602's session `602_20260923_151520` (first 10 minutes) the rat
licked only the centre port (`L2`, 2094 events). Both `L1` LEDs were then bound
to the `L2` channel (153 matched events), purely from `L2`'s light.

A rat cannot break two beams, or touch two lick ports, at the same instant. The
operator confirmed this, and on the mapping session no two beams or licks
overlapped at any point. So "at most one active channel per function" is a safe
rule for one animal. The operator asked for it as a hard rule that is logged and
can be reverted, for example for two rats in the rig.

## Decision

Within each **one-active group** listed in `host/led_board_layout.json` under
`exclusive_groups` (now `beams`: `S0`-`S5`, `licks`: `L0`-`L2`), the per-session sync
lets only the strongest channel be active on each frame. It runs between reading
the traces and applying the on/off threshold (`sync_fit.apply_one_active` feeding
the pure core `tmaze.core.sync.exclusive.enforce`):

- **Score.** Per frame, each LED gets how far it has moved from rest (its median)
  towards its active level. Lick LEDs brighten when active and beam LEDs dim (the
  map's lit/dark, converted with the map's reference sensor polarity so it does
  not depend on the firmware of the session being fitted). That distance is taken
  as a fraction of the **typical full change of LEDs in the same row and group
  this session** (the median). A channel's score is the mean over its LEDs.
- **Rule.** When the top channel scores at least 0.5 (`active_min`), every other
  channel of the group is set to its resting level on that frame. Otherwise
  nothing changes.
- **Record.** `clock_fit_<id>.json` gains `one_active`: whether the rule ran (or
  why not), the layout file, the settings, and per group the frames it acted on,
  the frames where a second channel also looked active (spill removed, by
  channel), and the frames where a second channel scored at least 0.75
  (`conflict_min`), meaning really lit. Those last frames mean the premise failed,
  and the printed report shows a WARNING with the first frame numbers.
- **Revert.** Remove a group from the layout file (or pass `--no-one-active` for
  one run). The rule also stays off without a v4 map, since it needs each LED's
  channel and direction.
- `TOOL_VERSION` becomes 2, because the same bundle now yields different spans.
  The manifest's `post_hoc` entry records it.

Scoring against the **row's** typical change, not the LED's own, is deliberate.
Against its own change, a channel that never fired would score 100 % during a
neighbour's event, tie with the real channel, and the rule could not tell them
apart. The row median holds as long as most channels of the group fire in a
session. This refines what was first proposed to the operator (each LED against
its own change), which fails exactly in the `602` case above.

## Alternatives considered

- **Leave each LED independent and rely on matching to reject spill.** Rejected:
  it doesn't. The spill copies the real event's timing, so it matches (the
  `602` mis-binding).
- **Subtract the stored bleed table (unmixing).** Rejected for now: it depends
  on the bleed staying as measured (board position, exposure, codec), and it
  needs the unmixed channel's own full change, which is unknown when that channel
  never fires. Winner-take-all needs only the ranking. The table stays in the map
  as a baseline for comparing future map builds.
- **Only warn, don't enforce.** Rejected by the operator: the rule is safe for one
  animal, and a warning doesn't fix the binding.
- **Hard-code the groups.** Rejected: the operator wants to be able to revert it
  without a code change.
- **Exclude neighbouring LEDs from each background ring** (planned as part of this
  change). Already done: `TraceSampler` leaves every located LED's disk out of
  every ring. The remaining spill is optical, inside the LED's own disk.

## Consequences

- **Rat session `602_20260923_151520`** (10 min, dry run): `L1_a`/`L1_b` are no
  longer bound to `L2` (correctly silent, since `L1` never fired). `S3_b` matched
  12 beam breaks instead of 10, `S5_b` 9 instead of 7. The clock fit is unchanged
  in size (138 edges) and slightly tighter (MAD 10.9 vs 11.2 ms).
- **Mapping session `000_20260922_163830`** (dry run against its own map): all 33
  LEDs bind to their own channel either way. 650 edges instead of 652, MAD 11.5 vs
  11.4 ms. `S0_b` matched 10 breaks instead of 12, now exactly the same breaks as
  `S0_a`. A frame-by-frame check found `S0` the winner on every one of its own
  break frames, so the rule never silenced an `S0` event. The change comes from
  the on/off threshold: removing other beams' spill from `S0_b` narrowed its
  range, and several `S0` breaks are shorter than a frame (17-29 ms at 40 ms)
  and sit at the threshold. `S3_a` gained 2 breaks the same way.
- **No spill was strong enough to count** on either session (0 frames where a
  second channel reached 0.5). The rule's effect is on how the threshold is set,
  not on stopping strong double-lit frames. The counters are there to show when
  that changes.
- **Costs:** negligible next to the video reads (0.08 s for 10 minutes' worth of
  frames, 15,000 frames by 18 LEDs, in plain Python). Transition frames where one beam ends and the next starts in the same
  frame keep only the stronger one. At 25 fps that is at most one frame at the
  edge of a span.
- **Revisit if:** two animals share the rig; a sensor gets stuck active (the
  conflict counter will show it); or a session in which most of a group's
  channels never fire makes the row median unrepresentative.

## Evidence / links

- Dry-run reports and the `S0` frame check (read-only scratch, outside the
  repos): `t-maze_recordings/_led_fiducial/one_active/` (`map_on.txt`,
  `map_off.txt`, `rat_on.txt`, `rat_off.txt`, `s0_frames.py` / `.txt`).
- Bleed measurements and the zero-overlap check: ADR-0196; scratch
  `_led_fiducial/same_group_overlap.py`, `full_crosstalk.py`.
- Related: ADR-0196 (two-pass map, bleed table, board layout), ADR-0199 (per-bundle
  sensor polarity), ADR-0153 (fiducials + binding), FW-0045.
