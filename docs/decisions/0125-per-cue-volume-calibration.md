# ADR 0125: Per-cue playback volume (CUE_ON gain), PROTO_VERSION 7

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/common/rig_config.h` (`PROTO_VERSION`, `CUE_GAIN_PCT_*`),
  `firmware/cue-guided-task/sound.h` (signed wavetable + `toneGainQ8` scaling,
  `startToneAsync` gain arg), `firmware/cue-guided-task/cue-guided-task.ino`
  (`handleCueOn` gain parse + `CUE,ON` echo), `firmware/PROTOCOL.md`,
  `host/tmaze/core/cue_volume.py`, `host/cue_calibration.json`,
  `host/tmaze/ui/cue_volume_view.py`, `host/tmaze/ui/experiment_control.py`
  (Setup subtab + speaker test), `host/tmaze/ui/phase3_controller.py`,
  `host/tmaze/core/session/session_config.py`
- **Supersedes / Superseded by:** —

## Context

On the bench the LOW cue (4 kHz) measures noticeably quieter on the dB meter
than the HIGH cue (8 kHz) even though both are synthesized at the SAME amplitude.
The cause is hardware: a cheap speaker whose frequency response is not flat. The
two cues must be perceptually equal so cue identity, not loudness, drives the
rat's choice (bug `#260810_CueVolumeMismatch`).

The tone is synthesized on the Arduino Due, not the PC: an 80 kHz timer ISR
streams a precomputed sine wavetable to `DAC0` (see ADR-0008), and the amplitude
was a single fixed compile-time constant `DAC_AMPLITUDE = 2000` shared by both
cues. The host (PROTOCOL v6) only chose *when* and *which frequency*
(`CUE_ON,<kind>,<hz>`); it had no way to set a level. So equalizing loudness
requires the firmware to accept and apply a per-tone amplitude, plus a wire field
to carry it.

Two hardware facts shape the choice. `DAC_AMPLITUDE = 2000` already sits just
under the mid-rail clip ceiling (`DAC_CENTER = 2048`), so there is essentially no
room to make the quieter cue LOUDER - only to make the louder cue quieter. And
overall loudness can be restored afterward with the speaker's own gain
potentiometer, so attenuating one cue digitally does not cost usable volume.

## Decision

Add a per-cue playback volume and bump `PROTO_VERSION` 6 -> 7. Reflashing the Due
is required.

- **Wire (append-only):** `CUE_ON,<CHOICE|RETURN>,<hz>,<gain>` where `<gain>` is a
  percent `0..100` of full amplitude, optional and clamped to
  `[CUE_GAIN_PCT_MIN, CUE_GAIN_PCT_MAX]`. Absent == `100` == the v6 level, so an
  un-reflashed expectation and old callers are unaffected. The firmware echoes
  the applied gain: `CUE,ON,<type>,<freq>,<gain>`. The host parser reads only the
  first three `CUE,ON` fields, so the appended gain does not break it.
- **Firmware:** the wavetable now stores SIGNED deviations (`int16_t sineDev[]`,
  +/- `DAC_AMPLITUDE`) and the ISR emits `DAC_CENTER + ((sineDev[i] * toneGainQ8)
  >> 8)`, where `toneGainQ8` is a Q8 fixed-point gain (256 == full). Scaling
  happens per sample in the ISR (one integer multiply + shift) rather than by
  rebuilding the table, so tone ONSET stays instant and the `TONE_ON` TTL sync is
  unaffected. `startToneAsync` gained a `gainQ8` argument defaulting to full, so
  the beam-driven auto-tone path (`task_core` `StartTone`) is unchanged. The gain
  is clamped so it can only ATTENUATE (never boost past full, which would clip).
- **Host:** the measured balance lives in a rig-level `host/cue_calibration.json`
  (gain percent keyed by frequency; default 100 % when unset), read via a new
  pure core `tmaze.core.cue_volume`. Both the Phase 3 cue sends and the Rig-view
  speaker test append `,<gain>` from it, and a new "Cue volume" subtab under Setup
  lets the operator play each cue at an adjustable level, balance them against a
  dB meter, and Save. The file joins the per-session config snapshot.

## Alternatives considered

- **Boost the quieter (LOW) cue instead of attenuating the louder (HIGH).**
  Rejected: `DAC_AMPLITUDE = 2000` leaves ~2 % headroom below the 2048 clip
  ceiling, so LOW cannot be raised meaningfully. Attenuating HIGH is the only
  direction with range; the potentiometer restores overall level.
- **Lower the shared base amplitude to create symmetric headroom.** Possible, but
  it reduces the maximum loudness available to both cues for no benefit here (we
  only need a RELATIVE balance, and the pot handles absolute level). Kept the
  base at 2000; the per-cue gain only ever reduces it.
- **Rebuild the wavetable at each tone start for the new amplitude.** Rejected:
  256 `sinf()` calls on the FPU-less SAM3X add milliseconds of jitter to tone
  onset, degrading the `TONE_ON` Open Ephys mark. Per-sample integer scaling in
  the ISR is cheap (Cortex-M3 single-cycle multiply) and keeps onset instant.
- **Store the gain in `phase3.json` (per HIGH/LOW identity).** Rejected: cue
  loudness is a property of the RIG hardware (this speaker), not of a Phase 3
  schedule, and the Rig-view speaker test also needs it. A dedicated rig-level
  file keyed by frequency (like `rig_scale.json` / `reward_calibration.csv`)
  serves both and survives a change of which arm is HIGH.
- **Auto-measure the level in the GUI.** Not possible: the rig has no microphone
  input. Calibration is operator-driven with an external dB meter; the GUI
  provides live playback + save.

## Consequences

- The Due must be reflashed for the gain to take effect; until then it plays both
  cues at full amplitude (the absent-gain default), i.e. today's behavior, so the
  host change is safe against an un-reflashed board.
- A protocol change touches `PROTOCOL.md` and `rig_config.h` (`PROTO_VERSION`);
  the host `cue_info` parser needed no change because the gain is appended and it
  reads only the first three `CUE,ON` fields.
- Gain resolution is 1 % (~20 DAC counts, ~0.09 dB), fine for perceptual
  matching. The gain is logged on the `CUE,ON` line so the applied level is in
  the behavioral record.
- The default `cue_calibration.json` (both cues 100 %) is a no-op, so a rig that
  never calibrates is unchanged. Editing the file / the Setup subtab is the only
  way loudness differs between cues.

## Evidence / links

- `firmware/PROTOCOL.md` (v7) - the wire contract this implements.
- ADR-0008 (wavetable tone ISR), ADR-0044/0046 (host-driven cue command + host
  identity), ADR-0091 (reward-volume calibration file pattern this mirrors),
  ADR-0072/0083 (host config files + `tmaze.paths`).
- `docs/notes/Bugs.md` `#260810_CueVolumeMismatch` - the bench observation.
