# ADR 0021: Read rig_config.h on the host as the single source of truth for the pin map

- **Status:** Accepted
- **Date:** 2026-07-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/rig_config.py` (new), `host/lick_tuner.py`, `host/rig_view.py`, `host/experiment_control.py`; reads `firmware/common/rig_config.h`
- **Supersedes / Superseded by:** —

## Context

The firmware's wiring lives in one place, `firmware/common/rig_config.h`
(pin numbers, `LICK_PINS`/`SENSOR_PINS`/`SOLENOID_TABLE`, and the
`PIN_LICK_CENTER/LEFT/RIGHT` role aliases). The serial protocol, however, only
ever carries the channel *index* (`L,<i>`, `S,<i>`); the pin number and the port
role are not on the wire.

Each host tool had therefore grown its **own** hard-coded copy of the lick
mapping — `lick_tuner.py`'s `CHANNEL_LABELS` said `L2 = pin 37`, `rig_view.py`'s
`LICK_MARKERS` comment said `LICK_PINS = {35,36,37}`, while the header's literals
were `{37,36,35}`. These three copies had drifted out of sync with the header
and with each other. A bench check on 2026-07-01 confirmed the central/reward
lick port is serial index `L2`, which also exposed that the header's role alias
(`PIN_LICK_CENTER = PIN_LICK_0`, i.e. index 0) was wrong. Every hard-coded copy
was an independent chance to be wrong.

## Decision

Add `host/rig_config.py`, a small standard-library parser that reads
`firmware/common/rig_config.h` and resolves the `#define` chains into a pin map
(lick/beam/solenoid index→pin plus port roles). The host tools that label ports
— `lick_tuner.py` channel labels, `rig_view.py` lick markers and the reward
solenoid column, and the new `experiment_control.py` lick counter — read from it
instead of hard-coding their own guesses. The header is the single source of
truth; the parser degrades gracefully (`ok == False`) to each caller's fallback
labels if the header can't be read.

This rests on one assumption — **flashed == source**: the host reads the header
on disk, so the mapping is only correct if the Due is running that same header.
The workflow makes that solid by reflashing before trusting the mapping, and the
assumption is documented in `rig_config.py`'s module docstring and next to each
consumer's use.

## Alternatives considered

- **Firmware reports its compiled pin map over serial** (e.g. a
  `0,LICKPINS,37,36,35` handshake line the host displays). — This is strictly
  more authoritative: it removes the flashed==source assumption because the
  firmware describes itself at runtime. Rejected *for now* as heavier: it is a
  protocol change (version bump, PROTOCOL.md, ADR) plus a firmware edit and a
  reflash. Revisit if the "edited the header but forgot to reflash" footgun ever
  bites. Captured as the deferred alternative here.
- **Keep hard-coding the mapping in each tool** (status quo). — Rejected: this is
  exactly what produced the three-way drift and masked the role-alias bug.
- **A separate host-side mapping file (JSON/py) the header and host both derive
  from.** — Rejected: reintroduces two sources that must be kept in sync; the
  header is already the canonical wiring definition.

## Consequences

- One place to fix the mapping (the header); host tools follow automatically,
  which is what reconciles the previously-drifted labels.
- New coupling: the host now parses a C header. The parser is deliberately
  narrow (targets the specific `PIN_*`/`*_PINS`/`*_TABLE` macros, tolerates
  comments and line continuations) and never raises, so a header edit it can't
  parse degrades to fallback labels rather than crashing a GUI.
- Residual footgun: editing `rig_config.h` without reflashing silently desyncs
  the host labels from the running firmware. Mitigated by documentation; the
  firmware-self-report alternative above would remove it entirely.
- The verified role mapping itself (which index is center/left/right, and the
  matching header-alias correction) is being recorded separately once the bench
  check is complete; this ADR is only about *where the host gets the map*.

## Evidence / links

- `host/rig_config.py` (parser + `RigConfig` model; `python rig_config.py` dumps
  the parsed mapping for a quick check).
- Bench observation (2026-07-01): central/reward lick port = serial index `L2`,
  and the header's `PIN_LICK_CENTER = PIN_LICK_0` alias contradicts it.
- Related: ADR-0006 (the single shared `rig_config.h` this reads), ADR-0009
  (the `ANIMAL`/mode handshake the protocol already emits).
