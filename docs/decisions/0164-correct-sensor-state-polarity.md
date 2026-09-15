# ADR 0164: Correct the body-beam and lick state polarity in firmware (proto v8)

- **Status:** Accepted
- **Date:** 2026-09-15
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/cue-guided-task.ino` (beam + lick reads),
  `firmware/tests/tmaze_test/tmaze_test.ino` (same reads), `firmware/common/rig_config.h`
  (`PROTO_VERSION` 7->8 + sensor comments), `firmware/PROTOCOL.md` (`S`/`L` rows).
  Downstream (separate increments): `host/tmaze/core/phase3/bundle_format.py`
  (`sensor_polarity` flag), `host/tmaze/core/sync/spans.py` (the one host consumer
  that hard-codes the legacy active state), and the `drieu_lab_analysis` reader.
- **Supersedes / Superseded by:** — (resolves `docs/future-work.md` FW-0058)

## Context

The rig reported body-beam (`S,<i>,<state>`) and lick (`L,<i>,<state>`) edges
with the state bit INVERTED relative to the physical event. Confirmed two ways:

- **Recorded logs (2026-09-08).** Over full sessions the `1` spans of every beam
  ran for tens of seconds while the `0` spans never exceeded ~0.4 s (median
  50-230 ms), and the board's channel indicator LEDs went dark during `0`. Same
  for licks (`L=1` unbroken for minutes; `L=0` median 92 ms). So the RESTING
  state was being reported as `1` and the brief crossing/contact as `0`.
- **Rig hand check (2026-09-14).** Breaking each beam and touching the hub lick
  port by hand, with `rig_logger` running, produced `S,<i>,0` / `L,<i>,0` on
  actuation and `...,1` on release/rest for all six beams and the centre lick
  (log `host/data/unknown_20260914_155725.csv`).

Root cause (traced end to end, documented on the rig schematic):

- Beams: Banner VS2-RN5CV30 (NPN, dark-operate) -> a **NOYIYO 4-channel
  optocoupler** (12 V -> 3.3 V) whose output the master schematic annotates
  **"Normal Low / Actuated High"** -> Due `INPUT_PULLUP`. So at the Due pin a
  broken beam reads HIGH and rest reads LOW. The firmware read `raw =
  (digitalRead == LOW)`, which was written for the bare Banner and so labelled
  REST as active.
- Licks: Banner D12DAB6 fiber amp -> Due `INPUT_PULLUP`; the pin idles HIGH and
  is pulled LOW on tongue contact (at the amp's current LO/DO setting). The
  firmware read `raw = (digitalRead == HIGH)`, again labelling idle as active.

The inversion had three concrete effects, all keyed to the same `raw` sense:

1. The Open Ephys choice / return-arm TTLs (fired on `in.beam[...]`, which pulsed
   when the beam was RESTORED) marked the wrong edge - late by the beam-occlusion
   duration (behaviour-dependent, tens to hundreds of ms). Only the firmware can
   fix this; a host-side relabel cannot.
2. The lick-gated reward top-up (`rules_engine`, acting on `L,<i>,1`) fired on
   lick RELEASE, ~90 ms late.
3. At boot the init read rest as active, so the FSM started believing every beam
   was broken.

The full signal-flow trace is recorded in the resources repo
(`rig-hardware/electronics/signal_chain.md`, added in the documentation
increment).

## Decision

Fix at the single source: FLIP the firmware read so it is true on the physical
event, and bump the serial protocol to mark the semantic change.

- `cue-guided-task.ino` + `tmaze_test.ino`: beams `raw = (digitalRead == HIGH)`,
  licks `raw = (digitalRead == LOW)` (both the loop reads and the `setup()`
  init), with corrected comments. After the flip `S,<i>,1` = beam broken,
  `L,<i>,1` = tongue contact; `in.beam` pulses on the break; boot reads rest as
  inactive.
- `rig_config.h`: `PROTO_VERSION` 7 -> 8, and the beam/lick wiring comments now
  describe the Due-pin sense (post-opto), not the bare sensor.
- `PROTOCOL.md`: the `S`/`L` rows state the v8 convention and record that pre-v8
  logs carry the inverted meaning.

The host [`protocol.py`](../../host/tmaze/core/protocol.py) is deliberately left
UNCHANGED: it always decoded `active = (state == "1")`, so after the firmware
flip the live GUI (`rig_view`), the rules engine, and every `protocol.py`
consumer become correct with no host edit. The bump is a SEMANTIC break, not an
appended field, so it is a version change rather than an append (contrast the
v4/v6/v7 appends).

Provenance for recorded data (separate increment): the host stamps a per-bundle
`sensor_polarity` flag (`corrected` when the firmware `0,PROTO,<n>` handshake is
>= 8, else `legacy_inverted`) and bumps the bundle `FORMAT_VERSION`; the analysis
repo reads the flag so pre-v8 bundles are interpreted with `0` = active and v8
bundles with `1` = active. Old logs are never rewritten.

## Alternatives considered

- **Host-only relabel** (`protocol.py` `active = state == "0"`, no reflash) -
  rejected as the primary fix: it cannot reach the firmware FSM/TTL edge (effect
  1) or the boot state (effect 3), which are the scientifically important ones
  for ephys alignment.
- **Both host and firmware** - rejected: after the firmware flip `state == "1"`
  is already correct, so also flipping the host would double-invert. If an
  interim host flip were ever wanted it would have to be explicitly reverted at
  reflash.
- **Hardware switch flip** - not available for beams (the VS2-RN5CV30 is a
  fixed-mode NPN dark-operate part with no LO/DO selector) and deliberately
  declined for the licks (the D12DAB6 LO/DO selector is frozen), to keep one
  consistent fix location in code.
- **No version bump** (doc-only) - rejected: the state bit's MEANING changes, so
  a log/bundle needs a machine-readable discriminator; `PROTO_VERSION` is already
  on the wire (`0,PROTO,<n>`) and in the bundle.

## Consequences

- New (reflashed) sessions have correct, physically-aligned `S`/`L` states, TTL
  edges, and boot state, and the live GUI/rules engine become correct with no
  host change.
- One host consumer, `tmaze/core/sync/spans.py`, hard-codes the legacy active
  states (`BEAM_ACTIVE_STATE = LICK_ACTIVE_STATE = "0"`, with a fixture test).
  After reflashing it will MISREAD corrected bundles until the provenance
  increment makes it read `sensor_polarity`. Until then, do not run the offline
  LED sync (`sync_fit`) on v8-recorded bundles - or land the provenance increment
  first. This is the reason the fix is split into increments.
- Recorded pre-v8 bundles keep their inverted meaning; interpretation is now
  per-`PROTO_VERSION` / per-`sensor_polarity`, which the analysis repo must honor.
- Physical follow-up: reflash both sketches to the Due and re-run the hand check
  to confirm `S,<i>,1` on break and `L,<i>,1` on contact before recording animals.

## Evidence / links

- Hand-check log: `host/data/unknown_20260914_155725.csv` (2026-09-14).
- Log-analysis evidence and prior discussion: `docs/future-work.md` FW-0058;
  `docs/design/cross-stream-sync-design.md` §3.7.
- Schematic annotation "Normal Low / Actuated High": the resources repo
  `rig-hardware/generate_rig_manual.py` (section 9, row 5) and IMG_1135.
- Signal-flow trace: resources repo `rig-hardware/electronics/signal_chain.md`
  (documentation increment).
