# ADR 0221: Generate the amplitude-modulated noise cue live in the DAC interrupt

- **Status:** Accepted
- **Date:** 2026-10-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/sound.h`, `firmware/cue-guided-task/cue-guided-task.ino` (`handleCueOn`), `firmware/common/rig_config.h` (`CUE_AM_HZ_MIN/MAX`, `PROTO_VERSION` 9 -> 10), `firmware/PROTOCOL.md`, `firmware/tests/sound_test/due_am_noise_test/`, `host/tmaze/ui/experiment_control.py` (audio cue test bar)
- **Supersedes / Superseded by:** — (extends [ADR-0008](0008-wavetable-tone-isr.md) and [ADR-0125](0125-per-cue-volume-calibration.md))

## Context

The lab wants an alternative to the two pure-tone cues: broadband white noise
whose loudness rises and falls sinusoidally (sinusoidal amplitude modulation,
SAM), at **2 Hz** for one cue and **32 Hz** for the other. Both cues share the
same noise, so they differ only in the rhythm of the loudness, not in pitch.

The natural first idea was to copy "what the tones do" and pre-generate the
waveforms to avoid per-trial computation. But the tones are not pre-generated:
the firmware stores one 256-entry sine cycle and the 80 kHz timer interrupt
steps through it with integer math (ADR-0008). Only `sin()` itself is
precomputed, because the Due has no hardware floating point.

Storing whole noise waveforms on the Due does not fit:

- **Memory.** One 2 Hz period at 80 kHz is 40,000 samples, about 80 KB of the
  Due's 96 KB RAM. Flash (512 KB) could hold a few seconds, no more.
- **A looped buffer makes the noise repeat.** If the stored noise is one
  modulation period long, every cycle has identical noise, so the sound becomes
  periodic at 2 or 32 Hz: the two cues would differ by a repeating noise pattern
  as well as by their envelope. Shorter loops (tens of ms) are heard as a buzz.
- **No onset time is saved.** The host decides cue on/off from video and sends
  `CUE_ON` over serial (ADR-0044/0045); the firmware already starts sound within
  one sample (12.5 us). Lag lives in the vision/serial path, not in synthesis.

Design choices settled with the lab before implementation: the envelope starts
at its silent trough, 100 % modulation depth, fresh noise on every presentation,
broadband (no filter), tones kept as the default stimulus.

## Decision

Synthesize the SAM cue sample by sample in the existing `TC0_Handler`, keeping
it integer-only:

- **Carrier:** a fresh random number every tick from **xorshift32** (Marsaglia
  2003; three shifts and three XORs). The top 12 bits give a uniform sample in
  -2048..2047, scaled to +/- `DAC_AMPLITUDE`.
- **Envelope:** a 256-entry raised-cosine table `envQ8[i] = round(256 * (1 -
  cos(2*pi*i/256)) / 2)` built at setup beside the sine table, read by the same
  32-bit phase accumulator a tone uses, stepped at the modulation rate. Phase 0
  is the trough, so every presentation starts silent and swells.
- **Output:** `DAC_CENTER + ((noise * env >> 8) * gainQ8 >> 8)`, the same Q8
  volume path as a tone (ADR-0125). Bench simulation of the integer math: output
  stays in 48..4047 of 0..4095 and the first samples are exactly mid-rail.
- **Protocol v10 (append-only):** `CUE_ON,<kind>,<hz>,<gain>,AM` selects the
  noise cue, with `<hz>` meaning the modulation rate, clamped to
  `[CUE_AM_HZ_MIN, CUE_AM_HZ_MAX]` = 1..128 Hz (room around 2 and 32, while a
  typo can't become an audio-rate "modulation" that sounds like a tone; the
  256-step envelope stays smooth to ~300 Hz). The echo appends `AM,<seed>`.
  Tone commands and tone echoes are byte-identical to v9.
- **Seed per presentation:** derived in firmware from the session PRNG state and
  a presentation counter through a splitmix32-style mix, without advancing
  `session.rng` (so the RETURN cue's hi/lo draws are unchanged). Echoing it means
  the exact waveform can be rebuilt offline (recipe in `firmware/PROTOCOL.md`,
  "Notes for analysis") - useful for relating auditory-cortex responses to the
  sound actually played.

## Alternatives considered

- **Pre-generated waveforms in RAM or flash.** Rejected: memory (above), and a
  looped buffer turns the noise periodic at the cue rate, confounding the two
  cues.
- **A short looped noise table (e.g. 4096 samples) times a live envelope.**
  Cheap, but the ~51 ms repetition is audible as a buzz/pitch and is close to the
  32 Hz cue's 31 ms period. Rejected; the generator costs about the same.
- **Generate the noise on the PC and play it through a sound card.** Rejected:
  adds an audio-device latency and jitter the TTL path doesn't see, and moves the
  DAC authority off the firmware (ADR-0044).
- **Frozen noise (same seed every presentation).** Not chosen by the lab; it
  remains a one-line change (a fixed seed) if wanted later.
- **Start at the envelope peak.** Gives an immediate, identical loud onset for
  both cues, but with a click and an onset that carries no identity. The lab
  chose the trough.

## Consequences

- **Identity takes time to arrive.** A tone is identified instantly; a 2 Hz SAM
  cue starts silent and reaches its first peak only after 250 ms, and the 32 Hz
  cue needs a few cycles (~60-100 ms) before it is distinguishable. With a
  cue-zone dwell around 1 s (ADR-0108), this changes how much of the stem the rat
  has to identify the cue. Analyses of cue-onset latency must treat the AM cue
  separately from tones.
- **Level.** Uniform noise has a lower RMS than a sine of the same peak, and
  100 % SAM lowers the average further (~6 dB below the tone on average). Match
  levels with a sound-level meter and the speaker pot / gain, not by assuming
  equal amplitude. The two AM cues are equal in level by construction.
- **Envelope steps.** The table has 256 steps per cycle, so the gain changes in
  small stairs (at most ~1.2 % of full scale per step, 8 kHz step rate for the
  32 Hz cue). Not expected to be audible; interpolate if a recording shows
  otherwise.
- **Offset is abrupt,** as with tones: `CUE_OFF` stops wherever the envelope is.
- **Calibration does not carry over.** The per-cue volume and frequency
  corrections (ADR-0125/0148) are keyed by tone frequency; the test bar plays
  AM cues at 100 %. A noise-level calibration belongs with the Phase 3 wiring.
- **Not yet in the task.** Phase 3 still plays tones only. Wiring the AM cue in
  (a stimulus choice in `phase3.json`, rate-to-side mapping, trial-CSV /
  bundle `format_version` changes, analysis-repo mirror) is deferred to
  [FW-0082](../future-work.md#fw-0082-run-phase-3-with-the-amplitude-modulated-noise-cue).
- Interrupt cost rises by a few integer operations per sample; the bench sketch
  `firmware/tests/sound_test/due_am_noise_test` measures it against the 12.5 us
  budget.

## Evidence / links

- Code: `sound.h` (`envQ8`, `startAmNoiseAsync`, the `STIM_AM_NOISE` branch of
  `TC0_Handler`), `cue-guided-task.ino` (`amNoiseSeed`, `handleCueOn`).
- Both sketches compile for the Due (`arduino-cli`, `arduino:sam` 1.6.12).
- Host test: `host/tests/test_protocol.py::TestCue.test_on_ignores_appended_fields`.
- Reference stimulus: the paper the lab cited for SAM-noise cues,
  <https://www.sciencedirect.com/science/article/pii/S0960982225008802>.
  Its exact parameters (depth, bandwidth, onset phase) were not checked against
  the implementation; the choices above were made with the lab directly.
