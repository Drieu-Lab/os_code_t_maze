# ADR 0166: Firmware-clock twin for the return cue too (proto 12)

- **Status:** Accepted
- **Date:** 2026-09-17
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_log.py` (one new column + setter,
  `PROTO_VERSION` 11->12), `host/tmaze/core/phase3/bundle_format.py` (`_COLUMN_DOC`),
  `host/tests/fixtures/.../` (fixture regen), `host/tmaze/ui/phase3_controller.py`
  (echo capture + binding, separate commit), and the `drieu_lab_analysis` data
  dictionary (separate commit).
- **Supersedes / Superseded by:** extends [ADR-0165](0165-firmware-clock-twins-for-cue-and-reward.md)
  (does not supersede it).

## Context

ADR-0165 added firmware hardware-clock twins for the CHOICE cue onset
(`choice_cue_on_fw_ms`) and the prime reward (`reward_cmd_fw_ms`), and
DELIBERATELY left the RETURN cue without one on the grounds that the choice cue is
the decision-relevant onset. The return cue is currently disabled on the rig
(`CueParams.return_enabled` off), so its host stamp `return_cue_on_iso` is blank
on every recorded session today.

The operator plans to enable the return cue later. When that happens it should get
the SAME firmware-clock treatment as the choice cue with no further schema churn -
better to add the (harmless, blank-until-used) column now, alongside the choice
and reward twins, than to bump the format again mid-experiment when the return cue
is switched on.

## Decision

Add one more append-only column, `return_cue_on_fw_ms` - the firmware `millis()`
of the RETURN cue onset, from the firmware's `CUE,ON` return echo, the twin of the
existing `return_cue_on_iso` - and bump the trial-CSV `PROTO_VERSION` 11 -> 12.

- Pure core: a new `Phase3Log.return_cue_acked(index, fw_ms)`, identical in shape
  to `cue_acked` (first-echo-wins, blank when unseen). It stays blank on a
  return-cue-disabled run, exactly like `return_cue_on_iso`.
- The column is appended LAST (after `reward_cmd_fw_ms`), not inserted beside the
  choice twin, so every proto-11 column keeps its position.
- The wiring (separate commit) captures the return `CUE,ON` echo on the serial
  thread and binds it, on the actuation thread, to the trial whose return cue is
  pending - mirroring the choice-cue path with its own pending index.

`PROTO_VERSION` is bumped rather than folding the new column into proto 11 because
proto 11 shipped two days ago and rig sessions may already have been recorded
against it; keeping "version = exact column set" means a proto-11 bundle is
correctly described as having two fw twins and a proto-12 bundle as having three.
The bump is append-only, so a proto-11 reader still reads a proto-12 file.

## Alternatives considered

- **Leave it until the return cue is enabled** - rejected: it would force a format
  bump in the middle of a live experiment; adding a blank-until-used column now is
  cheaper and keeps the enable-the-return-cue change firmware/GUI-only.
- **Redefine proto 11 to include all three twins** (no bump) - rejected: proto 11
  is already committed and may already be on disk in recorded sessions; two
  meanings for one version number breaks the discriminator.
- **Insert `return_cue_on_fw_ms` beside `choice_cue_on_fw_ms`** for readability -
  rejected in favour of appending last, to keep the proto-11 column positions
  stable (readers are name-based, but the append-only habit is worth keeping).

## Consequences

- Proto-12 sessions carry a firmware-clock return-cue onset the moment the return
  cue is enabled, with no further schema change; until then the cell is blank
  (honest - no return cue was played), like `return_cue_on_iso`.
- The golden fixture is regenerated at proto 12 and the analysis-repo data
  dictionary gains the column (separate commit); the manifest `FORMAT_VERSION` is
  untouched (a trial-CSV `proto` append, not a manifest-schema change).

## Evidence / links

- Builds on ADR-0165 (choice + reward twins) and ADR-0044 (the `CUE,ON` echo).
- Return-cue host stamp + enable flag: `phase3_log.return_cue_started` /
  `CueParams.return_enabled` (ADR-0141).
- Pure-core tests: `host/tests/test_phase3_log.py` (`TestFirmwareClockAcks`).
