# ADR 0222: Choose the Phase 3 cue type per session (tone or AM noise)

- **Status:** Accepted
- **Date:** 2026-10-20
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/cue-guided-task.ino` (`handleCueOn` rate parse), `firmware/cue-guided-task/sound.h` (`startAmNoiseAsync` in milli-Hz), `firmware/common/rig_config.h` (`PROTO_VERSION` 10 -> 11), `firmware/PROTOCOL.md`, `firmware/tests/sound_test/due_am_noise_test/`; `host/tmaze/core/cue_command.py` (new), `core/phase3/phase3_config.py` (`cue.stimulus`, `cue.am_fast_hz`, `cue.am_slow_hz`), `core/cue_calibration.py` (schema v3, `noise_gain_pct`), `core/protocol.py` (`cue_seed_from`, decimal rate), `core/phase3/phase3_log.py` (trial CSV proto 12 -> 13), `phase3_log_reader.py`, `bundle_format.py` (`FORMAT_VERSION` 5 -> 6), `phase3_ribbon.py`, `core/session/session_summary.py`; `host/tmaze/ui/phase3_controller.py`, `ui/cue_calibration_view.py`, `ui/trial_ribbon_view.py`, `ui/session_controller.py`; the golden fixture; analysis mirror `drieu_lab_analysis` ADR-0167
- **Supersedes / Superseded by:** — (implements [FW-0082](../future-work.md#completed); extends [ADR-0221](0221-am-noise-cue-live-synthesis.md), [ADR-0186](0186-per-animal-cue-side-mapping.md), [ADR-0125](0125-per-cue-volume-calibration.md))

## Context

ADR-0221 put a white-noise cue whose loudness rises and falls at 2 Hz or 32 Hz
(amplitude-modulated, "AM", noise) into the firmware, playable only from the
Setup test bar. Phase 3 still played pure tones. The lab wants to train on the
AM cue while keeping tones available, and to be able to try rates other than
2 and 32 Hz (for example 2.5 Hz) without reflashing.

Facts that shaped the design:

- The trial schedule, the trial CSV and every analysis speak of the cue as
  `HIGH` / `LOW`, and each rat's side mapping is stored as `high_tone_is_left`
  (ADR-0186). Renaming that vocabulary touches both repos and every analysis.
- Firmware v10 read the rate as whole Hz.
- A board running older firmware silently plays the wrong sound for an AM
  command (pre-v10 ignores the `AM` token and plays a tone), so a session could
  run on the wrong stimulus with nothing in the data to show it.
- The CSV header is read by a parser that folds every `key=value` on every `#`
  line into one flat dictionary, last write wins (both repos).

## Decision

- **Per session, not per animal or per trial.** `phase3.json` gains
  `cue.stimulus` (`tone` default, or `am_noise`), `cue.am_fast_hz` (32) and
  `cue.am_slow_hz` (2). The Phase 3 tab shows a Tone / AM noise choice that is
  saved to the same key and is **locked while armed**; the run's stimulus is
  fixed at arm. The RETURN cue always uses the same type as the CHOICE cue.
- **HIGH = the FAST rate.** Stored data stays `HIGH` / `LOW`, and
  `high_tone_is_left` keeps its meaning: in an AM session the FAST rate plays
  on the side the HIGH tone would. FAST / SLOW are display labels only (the tab,
  the trial ribbon, the Stop summary: "FAST 32 Hz", "fast = LEFT").
- **Fractional rates, firmware protocol v11.** Rates are 1-128 Hz with at most
  3 decimals. The firmware parses the rate into milli-Hz and steps the envelope
  with `phaseInc = (mHz << 32) / (1000 * 80000)`, so 2.5 Hz is exact; the echo
  trims trailing zeros (`32`, `2.5`, `2.125`). Tone commands are unchanged.
  `phase3_config` rejects a rate out of range, with more than 3 decimals, or
  with fast <= slow.
- **Firmware gate.** Phase 3 refuses to arm an AM session until the board has
  reported protocol 11 or newer (`cue_command.MIN_FIRMWARE_PROTO_FOR_AM`). If
  the version is unknown the controller asks (`VERSION`, at most every 5 s,
  sent from the actuation thread per ADR-0043). The arm-gate line says to
  reflash or choose Tone. Tone sessions are not gated.
- **One noise level.** `cue_calibration.json` schema v3 adds a single
  `noise_gain_pct`, shared by both rates (same noise, only the rhythm differs),
  set on the Cue calibration subtab with Play FAST / Play SLOW buttons. Absent
  = 100 %. Tone calibration is untouched.
- **Data (trial CSV proto 13, bundle format 6).** A `# cue_stimulus` header line
  is always written: `cue_stimulus=<tone|am_noise> cue_am_fast_hz= cue_am_slow_hz=
  cue_am_gain_pct=` (the `cue_am_*` values blank for tones). The keys are
  prefixed `cue_am_` so they cannot collide with other header keys in the flat
  parser. Two columns are appended LAST, after the trial-edit columns:
  `choice_cue_seed` / `return_cue_seed`, the noise seed from the firmware's
  `CUE,ON,...,AM,<seed>` echo (first echo per trial wins, like the `_fw_ms`
  stamps; blank for tones). `format.json` lists `cue_stimuli`.
- The pure command builder `core/cue_command.py` owns all of this (stimulus
  object, `CUE_ON` line, labels, the arm-block reason); the controller only
  calls it.

## Alternatives considered

- **Per-animal stimulus in `animals.json`.** Rejected: the lab switches the cue
  type as an experimental phase, for all rats at once; per session keeps the
  setting with the rest of the session's task configuration.
- **Change the stimulus while armed.** Rejected: a mid-run switch would mix cue
  types within one CSV whose header describes one.
- **Rename HIGH/LOW to FAST/SLOW in AM sessions, or to neutral cue A / cue B
  everywhere.** Rejected for now: it splits one column into two vocabularies or
  touches every analysis at once. The neutral A/B naming is a separate,
  deliberate change across both repos (FW-0084).
- **Whole-Hz rates only (stay on v10).** Rejected: the lab wants to try
  fractional rates; milli-Hz costs one wider multiply at cue start, nothing in
  the interrupt.
- **Warn instead of blocking on old firmware.** Rejected: the wrong sound would
  still play, and the CSV would claim AM noise.
- **Separate gains for the two rates.** Rejected: the two cues are the same
  noise with the same average level by construction (ADR-0221).
- **Store the seeds in a sidecar file.** Rejected: one per trial, they belong
  in the trial row beside the `_fw_ms` stamps they come with.

## Consequences

- A tone session is unchanged except for the new header line and two blank
  columns; the golden fixture was regenerated for the format bump only.
- Analyses must split by `cue_stimulus` before pooling across sessions. The
  analysis repo mirrors format 6 (its ADR-0167); its MATLAB copy accepts v6.
- The 2 Hz cue starts silent and needs ~250 ms to its first loudness peak
  (ADR-0221). Whether the cue zone should be longer for AM sessions is deferred
  (FW-0083).
- The session registry does not carry the cue type yet; readers get it from
  the trial CSV header.
- Reflashing to v11 is needed before the first AM session; v11 accepts every
  v10 command unchanged.

## Evidence / links

- Firmware: both sketches compile for the Due (`arduino-cli`, `arduino:sam`);
  bench sketch `due_am_noise_test` covers 2, 2.5 and 32 Hz.
- Tests: `host/tests/test_cue_command.py`, `test_phase3_config.py`,
  `test_cue_calibration.py` (`NoiseGainTests`), `test_protocol.py`,
  `test_phase3_log.py` (`TestCueStimulus`), `test_phase3_log_reader.py`,
  `test_session_summary.py`, `test_session_phase3_wiring.py`
  (`test_am_noise_session_blocks_on_old_firmware_then_sends_am_cues`).
- Analysis mirror: `drieu_lab_analysis` ADR-0167.
