# ADR 0202: An LED that matches a channel other than its map channel is reported and kept out of the clock fit

- **Status:** Accepted
- **Date:** 2026-09-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/sync/pipeline.py` (`analyze(expected=)`, verdict
  `contradicts_map`, `SyncResult.contradictions` / `map_preferred`);
  `host/tmaze/tools/sync_fit.py` (`map_direction`, `map_expectations`,
  `map_check_record`, `map_check_lines`, `--no-map-check`, `clock_fit` `map_check`
  block, `TOOL_VERSION` 3); `host/tests/test_sync_core.py`, `host/tests/test_sync_exclusive.py`
- **Supersedes / Superseded by:** —

## Context

The offline LED sync (`sync_fit`, FW-0045) matches every board LED against
**every** serial channel and keeps the best match. That is a useful self-check: if
the board were rewired, the LED would simply show up on its new channel. But once
a [map](../glossary.md) exists (`led_map.json`, ADR-0196), we already know which
channel each LED belongs to, and the per-session sync ignored that.

It matters because an LED can match the wrong channel. Light from a neighbouring
LED spills into it, and when its own channel never fires in a session, that spill
is the only thing it shows. On rat 602's session `602_20260923_151520` (first 10
minutes, one-active rule off):

- both `L1` LEDs matched the `L2` lick channel (153 and 107 events), and
- `SOL2.0_a` and `SOL2.2_a` matched the centre valve `SOL2.1` **dark**: `SOL2.1`'s
  flash blooms into their background rings, so their brightness *dips* on every
  `SOL2.1` pulse. Their map channels are `SOL2.0` / `SOL2.2`, lit when open.

Those wrong matches then fed the clock fit. Each edge enters the fit once per
(channel, firmware time) - the de-duplication meant for the two LEDs of one beam -
so a spill LED's edges can *replace* the real LED's edges when it happens to be
processed first. On `602`, the fit's `SOL2.1` edges came from `SOL2.0_a`'s dips
(lag 65.8 ms) instead of `SOL2.1_a`'s own flashes (50.8 ms).

The one-active rule (ADR-0201) removes much of this spill for the groups it
covers, but it only runs with a v4 map and a layout group, and it can be turned
off. The operator asked for a direct check against the map.

## Decision

With a map, `pipeline.analyze` takes an `expected` table: per located LED, its map
channel (without the wire state, e.g. `SOL2.0`) and what it does while that
channel is active (`lit` / `dark`; converted with the map's reference sensor
polarity by `sync_fit.map_direction`, the same conversion the one-active rule
uses; `None` for a map older than v4, which then checks the channel only).

- Every LED is still matched against **all** channels (the self-check stays).
- If its **own** channel, in the expected direction, qualifies under the usual
  rules (precision >= 0.5, >= 3 matches, plausible lag), the LED is bound to it,
  even when another channel scored higher. That case is recorded as
  `map_preferred`.
- If its own channel does not qualify but another does, the LED gets the new
  verdict **`contradicts_map`**: it is not bound and adds nothing to the clock
  fit. What it matched is kept in `SyncResult.contradictions`.
- An LED that matches nothing stays `unbound` / `silent` as before.
- **Recorded:** `clock_fit_<id>.json` gains a `map_check` block (applied or why
  not, how many LEDs were checked, whether direction was checked, and per LED
  "map says X, matched Y" with its match counts and lags). The printed report
  lists each contradiction as `L1_a: map says L1 lit, this session matched L2=1
  lit (...) - not used for the fit`.
- **Revert:** `--no-map-check` for one run. It is never applied while building a
  map (`--map`), so an old map cannot steer a new one.
- `TOOL_VERSION` becomes 3 (together with ADR-0203), because the same bundle can
  now yield different bindings.

A contradiction is a warning sign, not a failure: the fit's verdict still comes
from the remaining edges.

## Alternatives considered

- **Bind each LED only to its map channel (skip the other channels).** Rejected:
  a rewired board or a wrong map would then look like a silent LED instead of
  showing where it went. Matching everything and then comparing costs nothing.
- **Report the contradiction but still use the LED's edges.** Rejected: the edges
  are the neighbour's event seen second-hand (through bloom), with a different
  lag, and they can displace the real LED's edges in the fit.
- **Always bind to the map channel's best candidate, even below the thresholds.**
  Rejected: a weak, mostly-coincidental match would then enter the fit.
- **Rely on the one-active rule alone.** Rejected: it needs a v4 map plus a
  layout group and can be turned off; the check works with any map that names
  channels. The two catch the same spill independently (see Consequences).

## Consequences

- **`602_20260923_151520`**, 10 min, dry run:
  - With the one-active rule OFF, the check flags all four wrong matches (`L1_a`,
    `L1_b` -> `L2`; `SOL2.0_a`, `SOL2.2_a` -> `SOL2.1` dark). The fit uses
    `SOL2.1_a`'s own edges: 138 edges, MAD 11.7 ms.
  - With the rule ON (default), the rule already leaves those LEDs silent. The
    check finds 0 contradictions, as it should.
- **Mapping session `000_20260922_163830`** (dry run against its own map): 33
  LEDs checked, 0 contradictions, 0 kept-over-higher. Nothing changes there.
- A future session in which the board was rewired will show contradictions
  instead of silently re-binding. If that is intended, rebuild the map (`--map`).
- **Revisit if** an LED is deliberately wired to two channels, or if the map's
  lit/dark labels turn out to be unreliable for some LEDs (the channel-only check
  then still applies with `direction_checked: false`, via an old map).

## Evidence / links

- Dry-run reports (read-only scratch, outside the repos):
  `t-maze_recordings/_led_fiducial/one_active/` - `rat_v3_norule.txt` (check on,
  rule off), `rat_v3.txt` (both on), `rat_on.txt` (before this change),
  `map_v3.txt`.
- Related: ADR-0201 (one-active rule), ADR-0203 (reward-valve group), ADR-0196
  (two-pass map, bleed table), ADR-0199 (per-bundle sensor polarity), ADR-0153,
  FW-0045.
