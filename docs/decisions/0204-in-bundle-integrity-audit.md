# ADR 0204: Audit each session bundle by reconciling its streams against each other

- **Status:** Accepted
- **Date:** 2026-09-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/session/bundle_audit.py` (new, the checks),
  `host/tmaze/core/session/bundle_audit_inputs.py` (new, reads a bundle),
  `host/tests/test_bundle_audit.py` (new). Wiring into `records_check`, the
  Records Manager and the session registry follows in a separate change.
- **Supersedes / Superseded by:** —

## Context

Bug `#260720_DataLogging` asked whether the rig's data logging has integrity
problems: is every session complete, routed to the right animal and folder, and
free of dropped or duplicated records? `records_audit` / `records_check`
(ADR-0097/0105/0155) already check the records *around* the sessions (daily
husbandry rows, annotations, the registry), but nothing checked what each
session bundle holds against itself.

The streams of one session record many events more than once, which makes them
checkable against each other. A reward is a trial-CSV row (`rewarded=1`,
`reward_cmd_fw_ms`), a `REWARD` echo in the serial capture and a count in the
summary. A choice cue is a trial-CSV stamp and a `CUE,ON,CHOICE` line. The
trial CSV's firmware-clock stamps are copied from those serial lines, so a real
stamp matches its line exactly.

The operator confirmed that every valve pulse goes through the host. Task
rewards, top-ups and the Solenoid-test button all send `R<port>`, and the board
echoes each as `REWARD,…,MANUAL`. So the serial count must equal the task pulses
plus the summary's hand-given pulses exactly, and a mismatch is an error, not a
tolerance question.

## Decision

A pure core, `bundle_audit`, runs a fixed set of read-only checks on a
`BundleInputs` record. A separate stdlib reader, `bundle_audit_inputs.load_inputs`,
fills that record from one bundle folder. It reads the small files plus the video
sidecar, never the video, and never writes anything. The checks are:

| check | what must hold | severity |
| --- | --- | --- |
| files | every manifest stream exists, inside its own bundle, non-empty (for the must-have streams) | error |
| config | each snapshotted config file still matches its SHA-256 from Start | error |
| identity | session id agrees across manifest, trial CSV, serial, sidecar, animal state; so does the animal | error (sidecar animal: warning) |
| trials | numbers 1..N, no repeats, no orphan re-run attempts; times in order and not overlapping | error (skipped trial, trial ending > 30 s after Stop: warning) |
| rewards | serial `REWARD` count = rewarded + top-ups + hand-given; each rewarded trial has its pulse; summary task pulses = the CSV's | error |
| cues / licks | each trial's `choice_cue_on_fw_ms` / `lick_fw_ms` is a line in the capture (±2 ms) | error |
| serial | `TXDROP` did not rise during the session; no gap > 30 s with no serial line at all; clock jumps match recorded resets | error / warning |
| summary | the summary's completed / correct = the trial CSV's | error |
| video | camera `saved` = sidecar rows; frames lost; sidecar covers Start..Stop | warning (lost frames < 1%: note) |
| across | no two sessions' Start..Stop overlap | error |

Each finding has a stable key, `<check>:<session id>:<detail>`, so the existing
acknowledgement store (`records_audit_ack`, ADR-0105) can silence a known,
explained problem.

Older formats are checked as far as they allow. A check whose inputs a bundle
predates goes in `BundleAudit.not_checked`, with the reason, instead of being
reported as a problem. The first run over all 148 real sessions (C: and the Z:
archive) found five such cases, and the checks were made format-aware so the
report shows real problems rather than format history:

- **Filtered serial log.** Sessions before ADR-0178 kept only
  `sensors_<id>.csv`, which holds beams and central-port lines only. They skip
  the cue and link-silence checks.
- **Old summaries.** Summaries before schema v5 counted *every* pulse as
  `manual_water` (ADR-0078, corrected by ADR-0084). For those, the serial count
  must equal that figure.
- **Link silence.** STATUS is a probe sent only after 10 s of quiet
  (`serial_liveness`, ADR-0062). The spacing between STATUS replies therefore
  means nothing. The test is instead the longest gap between *any* two lines,
  which on a healthy link stays under the probe's 25 s give-up time.
- **TXDROP.** The count is cumulative since the board booted. Only a rise
  during the session is a loss in this capture; a nonzero start value is a note.
- **Restarted recordings.** When the recording is restarted mid-task
  (ADR-0198), the new bundle's trial CSV still holds the earlier trials. Their
  serial lines are in the previous bundle. Those trials (started > 5 s before
  Start) are reported once, as a warning, and left out of the serial checks.

Two more adjustments came from the same run:

- **Video sidecar animal.** Every sidecar so far says `animal: animal`, because
  the camera view is never told the animal ID. The audit skips that placeholder
  (it is listed under not-checked) until the rig is fixed (FW-0073).
- **Firmware version.** The manifest's `firmware_proto_seen` is `"unknown"` on
  every session, so the reader takes the version from the board's own
  `VERSION,<proto>,…` line in the capture.

## Alternatives considered

- **Tolerances on the reward reconciliation** (e.g. ±1 pulse). Rejected: every
  pulse is host-issued, so the counts are exact by construction. A tolerance
  would hide exactly the one-off drops this is meant to find.
- **Putting the checks in `records_audit`.** Rejected for the core: that module
  checks records files, and this checks bundle contents with its own inputs. The
  *report* joins `records_check` (next change), so the operator has one place to
  look and one acknowledgement mechanism.
- **Checking the video file itself** (decode and count frames). Rejected for
  now: too slow for a whole-archive scan over the network drive. The camera's
  own counters plus the sidecar row count give the same answer for a
  normally-closed recording.

## Consequences

- A whole-archive scan takes about 50 s for 148 sessions, most of it spent
  parsing video sidecars on the network drive. That is fine for `records_check`.
  For the per-session registry column, it should reuse the same reader once per
  bundle.
- First real result: 120 sessions clean, 15 with warnings, 13 with errors. The
  errors are real and need a human decision, not a code change:
  - The trial CSV names animal 600 in two 602 bundles (`602_20260916_145611`,
    `…145627`) and in two July test bundles.
  - Five July test sessions keep their trial CSV outside the bundle, in the
    pre-bundle `host/data` layout, and one bundle's CSV is missing.
  - `602_20260813_170713` has two more valve pulses than it can account for.
  - `600_20260806_150601` and `601_20260819_154714` have summary counts one
    off the trial CSV.
  - `600_20260806_150601` has one lick stamp with no serial line.
- Adding a check to a new format needs one new `BundleInputs` field, the reader
  line that fills it, and a test broken in exactly that way.

## Evidence / links

- Bug `#260720_DataLogging` (`docs/notes/Bugs.md`).
- Scan of all bundles on 2026-09-24: scratch output kept outside the repos at
  `t-maze_recordings/_led_fiducial/one_active/audit_try2.txt`.
- Related: ADR-0062 (liveness probe), ADR-0078/0084 (manual water), ADR-0097/
  0105/0155 (records audit + acknowledgements), ADR-0178 (full serial capture),
  ADR-0193 (TXDROP), ADR-0198 (recording restart), FW-0049 (absolute stream paths).
