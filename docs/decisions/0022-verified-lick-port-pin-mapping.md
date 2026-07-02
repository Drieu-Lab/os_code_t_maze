# ADR 0022: Verified lick-port <-> pin/index mapping

- **Status:** Accepted
- **Date:** 2026-07-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/common/rig_config.h` (`PIN_LICK_CENTER`/`_LEFT`/`_RIGHT`/`_REWARD` aliases); the host tools that read them via `host/rig_config.py`. Corrects an incidental pin note in ADR-0007.
- **Supersedes / Superseded by:** —

## Context

The lick-port role aliases in `rig_config.h` had always been tagged `CONFIRM`
(physical mapping unverified) and had drifted: the header claimed
`PIN_LICK_CENTER = PIN_LICK_0` (index 0 / pin 37), while a stale in-file comment,
`lick_tuner.py`, `rig_view.py`, and ADR-0007 variously assumed "center == pin 37"
or "L2 = pin 37" with `LICK_PINS = {35,36,37}`. The header's actual `LICK_PINS`
literals are `{37, 36, 35}` (index 0→pin 37, 1→36, 2→35).

Once the host tools were changed to read the header as the single source of truth
(ADR-0021), the mapping could be verified on the bench: with `lick_tuner`
labeling each channel from the header, each port was licked and the firing
pin/index recorded.

## Decision

Record the **verified** lick mapping (bench test, 2026-07-01):

| Port          | Serial index | Pin |
|---------------|--------------|-----|
| Left          | `L0`         | 37  |
| Right         | `L1`         | 36  |
| Center / reward | `L2`       | 35  |

Correct the role aliases in `rig_config.h` accordingly: `PIN_LICK_CENTER =
PIN_LICK_2` and `PIN_LICK_LEFT = PIN_LICK_0` (they were swapped);
`PIN_LICK_RIGHT = PIN_LICK_1` was already correct. `PIN_LICK_REWARD` (=
`PIN_LICK_CENTER`) now names the true center detector (pin 35 / `L2`).

The `PIN_LICK_0..2` pin **numbers** are unchanged — they were already correct, as
the bench test confirmed each port fires the pin the header assigns to its index.
Because the corrected aliases are monitor-only (the active firmware path uses
`LICK_PINS` by index, which is untouched), this is a documentation/correctness
change with **no runtime behavior change and no reflash required**; reflash at
convenience to keep source == flashed.

## Alternatives considered

- **Leave the aliases `CONFIRM` / as-was.** — Rejected: they were actively wrong
  (center aliased to `L0`), a latent bug for the deferred lick-gated-reward path.
- **Change the `PIN_LICK_0..2` pin numbers instead of the aliases.** — Rejected:
  the bench test shows the pin numbers are already correct; only the role→index
  aliases were swapped.
- **Firmware self-reports its pin map over serial** (ADR-0021's deferred
  alternative). — Would have avoided the drift entirely and removed the
  flashed==source assumption; still deferred.

## Consequences

- `PIN_LICK_REWARD` now correctly identifies the center lick detector, so the
  deferred "gate reward on a central lick" feature (ADR-0007) would monitor the
  right port.
- This corrects the incidental "center == pin 37" statement in
  [ADR-0007](0007-reward-at-central-port.md); that ADR's core decision (reward is
  delivered at the central port) is unaffected and remains Accepted.
- Left/right (`L0`/`L1`) are monitor-only in the v1 task, so their correction has
  no task effect today; it matters for analysis and any future per-arm logic.

## Evidence / links

- Bench test (2026-07-01): center → pin 35, left → pin 37, right → pin 36 (via
  `lick_tuner.py` reading the header, per ADR-0021).
- `python host/rig_config.py` after the fix prints: `L0 (pin 37, left)`,
  `L1 (pin 36, right)`, `L2 (pin 35, center)`.
- Related: ADR-0021 (host reads the header), ADR-0007 (reward at central port),
  ADR-0006 (the shared `rig_config.h`).
