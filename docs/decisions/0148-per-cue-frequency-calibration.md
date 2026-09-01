# ADR 0148: Per-cue frequency calibration (host-commanded corrected tone)

- **Status:** Accepted
- **Date:** 2026-09-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/cue_calibration.py` (renamed from `cue_volume.py`;
  schema v1 -> v2 with a `commanded_hz` block, `commanded_hz_for` /
  `clamp_freq_hz` / `load_commanded` / `save_commanded` / `save_calibration`),
  `host/cue_calibration.json`, `host/tmaze/ui/cue_calibration_view.py` (renamed
  from `cue_volume_view.py`, "Cue calibration" subtab), `host/tmaze/ui/experiment_control.py`
  (subtab label + speaker test), `host/tmaze/ui/phase3_controller.py` (CHOICE /
  RETURN cue sends), `host/tmaze/ui/tooltip_text.py`,
  `host/tmaze/core/session/session_config.py` (comment), `host/tests/test_cue_calibration.py`
- **Supersedes / Superseded by:** extends ADR-0125 (per-cue volume)

## Context

Measured with an audio spectrum app on the bench, the tones the speaker EMITS sit
slightly BELOW the commanded frequency (e.g. a commanded 8000 Hz reads a bit
under 8 kHz). The two cues must land on their intended pitches so cue identity is
unambiguous and the emitted frequencies match what the behavioural record and the
grant's task design assume.

The tone is synthesized on the Arduino Due: an 80 kHz timer ISR steps a 32-bit
phase accumulator through a 256-entry sine wavetable (ADR-0008), and the host
already tells it exactly which frequency to play per cue (`CUE_ON,<kind>,<hz>`;
ADR-0044/0046). A small fixed offset between commanded and emitted frequency is
consistent with the Due's master-clock / timer tolerance - a PROPORTIONAL error:
if commanding `C` Hz is measured at `E` Hz, the ratio `E/C` is ~constant, so to
emit a target `T` Hz you should command `C_new = round(C * T / E)`.

Because the host already chooses the commanded frequency, this can be corrected
ENTIRELY on the host - just send a compensated `<hz>` - with no firmware change,
no protocol change, and no reflash. This mirrors and extends the per-cue VOLUME
calibration (ADR-0125), which already added the rig-level `host/cue_calibration.json`
and a Setup subtab; folding frequency into the same file and tab keeps one place
for "how this speaker is calibrated".

## Decision

Add a per-cue FREQUENCY correction, host-side only, alongside the existing volume
calibration.

- **Model:** treat the offset as a fixed proportional clock error. The operator
  plays a cue, reads the EMITTED frequency `E` on a spectrum app, enters it, and
  the GUI computes `C_new = round(C_current * T / E)` where `T` is the target and
  `C_current` is the currently-commanded value (starting at `T`). This is
  iterative: re-play at `C_new`, re-measure, Set again to refine.
- **Data (schema v1 -> v2):** `host/cue_calibration.json` gains a `commanded_hz`
  block (commanded Hz keyed by TARGET frequency) beside the v1 `gain_pct` block.
  An absent block (a v1 file) or unknown key falls back to identity
  (command == target), so an un-calibrated rig is unchanged. Commanded values are
  clamped to `[FREQ_HZ_MIN, FREQ_HZ_MAX]` (500..20000, mirroring `CUE_HZ_MIN/MAX`
  in `firmware/common/rig_config.h`).
- **Core:** the module is renamed `cue_volume.py` -> `cue_calibration.py` (its
  scope is now volume + frequency). `commanded_hz_for(target)` returns the
  commanded Hz (identity fallback); `save_gains` / `save_commanded` each PRESERVE
  the other block, and `save_calibration` writes both at once.
- **Consumers:** the Phase 3 CHOICE/RETURN cue sends and the Rig-view speaker test
  now command `commanded_hz_for(freq)` as the `<hz>`, while the VOLUME gain stays
  keyed by the TARGET `freq` (the volume balance was measured at the intended
  tone). The subtab is renamed "Cue volume" -> "Cue calibration" and gains a
  per-cue "measured actual Hz" entry + Set + a commanded display + Reset, plus a
  Play that previews the CURRENT commanded value so the operator can measure it.

## Alternatives considered

- **A single global frequency correction factor.** Rejected in favour of per-cue
  to match the per-cue volume model and because the response need not be a single
  clean ratio across the band; per-cue is strictly more general and no harder to
  operate (one measurement per cue).
- **Operator enters the corrected commanded frequency directly.** Rejected: it
  pushes the arithmetic (and the iterative `C*T/E` refinement) onto the operator.
  Entering the MEASURED value and letting the GUI compute is less error-prone.
- **Fix it in firmware (calibrate the timer/clock).** Rejected as disproportionate:
  the host already owns the commanded frequency, so a host correction needs no
  reflash and no protocol change, and it is per-rig data anyway (belongs beside
  the other host calibration files, not baked into firmware).
- **Store the correction in `phase3.json` (per HIGH/LOW identity).** Rejected for
  the same reason as the volume calibration (ADR-0125): the offset is a property
  of THIS rig's hardware, not of a Phase 3 schedule, and the speaker test needs
  it too. A rig-level file keyed by frequency serves both.
- **Auto-measure in the GUI.** Not possible: the rig has no audio input.
  Calibration is operator-driven with an external spectrum app.

## Consequences

- Host-only: no firmware/protocol change and no reflash. An un-calibrated rig (or
  a v1 `cue_calibration.json` with no `commanded_hz` block) commands the target
  frequency exactly as before, so the change is safe by default.
- LOGGING NOTE: the firmware's raw serial `CUE,ON,<type>,<freq>,<gain>` line now
  echoes the COMMANDED (corrected) frequency (e.g. `8100`), while the Phase 3
  config header / `phase3.json` still record the TARGET tones (4000/8000).
  Downstream analysis keys on cue IDENTITY, not the exact Hz, so this is harmless;
  it is documented here so a reader who compares the two isn't surprised.
- Correction resolution is 1 Hz (the commanded value is an integer), fine against
  the observed small offset.
- The default `cue_calibration.json` seeds `commanded_hz` as identity
  (4000->4000, 8000->8000), a no-op until the operator calibrates.

## Evidence / links

- ADR-0125 (per-cue volume; the file + subtab this extends), ADR-0008 (wavetable
  tone ISR), ADR-0044/0046 (host-driven cue command + host-owned Phase 3),
  ADR-0091 (reward-volume calibration file pattern), ADR-0072/0083 (host config
  files + `tmaze.paths`).
- `docs/notes/Bugs.md` `#260901_CueFrequencyOffset` - the bench observation.
