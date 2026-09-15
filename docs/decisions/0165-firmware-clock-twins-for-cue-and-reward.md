# ADR 0165: Firmware-clock twins for cue onset and reward on the trial row (proto 11)

- **Status:** Accepted
- **Date:** 2026-09-15
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_log.py` (two new columns + setters,
  `PROTO_VERSION` 10->11), `host/tmaze/core/phase3/bundle_format.py` (`_COLUMN_DOC`),
  `host/tools/make_golden_bundle.py` + `host/tests/fixtures/.../` (fixture regen),
  `host/tmaze/ui/phase3_controller.py` (echo capture + binding, separate commit),
  and the `drieu_lab_analysis` data dictionary (separate commit).
- **Supersedes / Superseded by:** — (addresses `docs/future-work.md` S2/L5)

## Context

Every behaviourally important instant on the Phase 3 trial row is a HOST
wall-clock ISO stamp (`choice_cue_on_iso`, `reward_cmd_iso`, ...), taken on the
actuation thread at the moment the host DECIDED to act. Those stamps carry the
serial round-trip latency to the Due and back, and they live on the host clock,
not the firmware `millis()` clock that the Open Ephys TTLs (and therefore the
neural recording) are aligned to. The one exception already on the row is
`lick_fw_ms`: the firmware hardware-clock time of the reward-collection lick,
which is why it was added (ADR-0047) - a lick is the anchor an offline reader can
trust to the millisecond.

The two other host-commanded actuations - the CHOICE cue turning ON and the
prime reward pulse - have no firmware-clock twin, even though the firmware
already ECHOES both on the serial line:

- `CUE,ON,<CHOICE|RETURN>,<freq>` - the firmware's report of the ACTUAL acoustic
  state (ADR-0044), carrying the firmware `millis()` at cue onset.
- `SOL,<port>,<sol>` / `REWARD,...` - the firmware's report that it pulsed the
  reward solenoid, carrying the firmware `millis()` at the pulse.

So the firmware-clock time of cue onset and reward is already on the wire; the
host just was not recording it onto the trial. Without it, aligning cue onset or
reward to the neural axis means going through the latency-bearing host ISO stamp
(or not at all). This is the "cheapest, highest scientific value" item flagged in
the review (S2/L5): the data needed already exists, it only needs capturing.

## Decision

Add two append-only columns to the Phase 3 trial CSV, the firmware-clock twins of
the existing host ISO stamps, and bump the trial-CSV `PROTO_VERSION` 10 -> 11:

- `choice_cue_on_fw_ms` - firmware `millis()` of the CHOICE cue onset, from the
  first `CUE,ON` echo for the trial (twin of `choice_cue_on_iso`).
- `reward_cmd_fw_ms` - firmware `millis()` of the PRIME reward pulse, from the
  first `SOL` / `REWARD` echo after the prime (twin of `reward_cmd_iso`).

They are set by two pure `Phase3Log` methods, `cue_acked(index, fw_ms)` and
`reward_acked(index, fw_ms)`, first-echo-wins (idempotent for the trial; a
later top-up SOL does not overwrite the prime's time), blank when no echo was
seen (the honest signal, exactly like `lick_fw_ms`). The columns are APPENDED
(after `probe_trigger`, before the trial-edit lifecycle columns), so every
existing column keeps its position; the version bump marks the new columns, not a
semantic break.

The RETURN cue is deliberately NOT given a firmware-clock twin for now: the
scientifically important onset is the CHOICE cue (it guides the decision and is
what a cue-aligned neural analysis keys on). A return twin can be appended later
if wanted.

The wiring (a separate commit) captures the echo's `millis()` on the
serial-reader thread and routes it to the actuation thread - the sole writer of
the log (ADR-0043/0047) - which binds it to the trial whose CHOICE cue / prime is
pending, mirroring the existing pending-lick attribution.

## Alternatives considered

- **Get the firmware ms into the bundle's serial file instead of onto the trial
  row** (the other half of the review's S2/L5) - not exclusive; the full serial
  capture (plan Increment 4a) will also carry these echoes verbatim. But a column
  on the trial row is the cheapest immediately-usable form: an analysis that
  already reads the trial CSV gets a firmware-clock cue/reward time with no join.
  Both land eventually; this is the smaller first step.
- **Compute the firmware ms from the host ISO stamp + a clock fit** - rejected:
  that is exactly the latency/clock-fit inference this avoids; the firmware
  already reports its own clock, so record it directly.
- **A generic `cue_ack_fw_ms` bound to whichever cue is active** - rejected in
  favour of the `*_fw_ms` naming that mirrors the existing `*_iso` twins
  (`choice_cue_on_fw_ms`, `reward_cmd_fw_ms`, matching `lick_fw_ms`), so the
  column's meaning is unambiguous and a RETURN twin has an obvious name if added.
- **No version bump** - rejected: the column set changed, so an offline reader
  needs the discriminator; `proto=` is already in the trial-CSV header.

## Consequences

- New (proto-11) sessions carry a firmware-clock cue onset and reward time, on
  the same clock as the ephys TTLs, so cue- and reward-aligned neural analyses no
  longer route through the host ISO stamp's serial latency.
- Cells are blank when the echo was not seen (a dropped or un-echoed line), which
  is the truthful signal - an analysis must treat blank as "unknown", not zero.
- The bundle data format changed, so the golden fixture is regenerated and the
  analysis-repo data dictionary gains the two columns (separate commit); the
  `FORMAT_VERSION` guard is untouched (this is a trial-CSV `proto` change, an
  appended-column one, not a manifest-schema change).
- Follow-ups still open (plan Increment 2e): make `RigClient.send` truthful
  (bool/raise) so a cue is logged only when it went out, and the full verbatim
  serial capture (4a). Those are tracked separately.

## Evidence / links

- Firmware echoes: `firmware/PROTOCOL.md` (`CUE`, `SOL`, `REWARD` rows);
  `host/tmaze/core/protocol.py` `cue_info` / `sol_pulse` / `reward_info`.
- Existing firmware-clock precedent: `lick_fw_ms` (ADR-0047).
- Review item: `docs/future-work.md` S2/L5; roadmap Increment 2e.
- Pure-core tests: `host/tests/test_phase3_log.py` (`TestFirmwareClockAcks`).
