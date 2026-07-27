# ADR 0008: Generate the tone cue from a wavetable, not sin() in the ISR

- **Status:** Accepted
- **Date:** 2026-06-03
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `firmware/cue-guided-task/sound.h`, `firmware/common/rig_config.h`
- **Supersedes / Superseded by:** Partially superseded by
  [ADR-0063](0063-gate-the-tone-timer.md) (the timer is no longer left running
  between tones); the wavetable, phase accumulator, direct DAC write, and 80 kHz
  sample rate below all stand.

## Context

The cue-guided task plays a constant tone (4 kHz or 8 kHz) on the Arduino Due
DAC0 via a timer ISR (`TC0_Handler`) that fires at `DAC_SAMPLE_RATE`. The
original ISR computed each sample on the fly:

```
float phaseIncrement = 2.0 * PI * toneFreq / SAMPLE_RATE;
phase += phaseIncrement;
...
float val = 0.5 + 0.5 * sin(phase);
analogWrite(DAC_PIN, (int)(val * 4095));
```

with `DAC_SAMPLE_RATE = 100000` (100 kHz, a 10 us per-sample budget).

The Due's CPU (SAM3X8E, Cortex-M3) has **no hardware FPU**, so `sin()` and the
float divides are emulated in software and take tens of microseconds — far more
than the 10 us budget. Once a tone started, the ISR re-entered essentially
back-to-back and **starved everything else**: `loop()` stopped running (body
sensors froze), serial was not serviced, and the native-USB stack could not
respond to the 1200 bps upload auto-reset (uploads required physically
unplugging the board). Because the loop that stops the tone was itself starved,
the condition was self-locking.

Observed symptom: triggering the entry beam started the tone, after which no
other beam registered and the board could not be reprogrammed.

## Decision

Synthesize the tone from a precomputed **sine wavetable** and keep the ISR
**integer-only**:

- Build a 256-entry `uint16_t sineTable` once in `setupSound()` (this is where
  `sinf()` runs, before the timer starts).
- Use a 32-bit **phase accumulator**: `phaseInc = freq * 2^32 / fs`, computed
  once per tone in `startToneAsync()`. The ISR does only
  `phaseAcc += phaseInc; write(sineTable[phaseAcc >> 24])`.
- Write the sample **straight to the DAC register** with
  `dacc_write_conversion_data(DACC, ...)` (channel selection fixed once in
  setup) instead of `analogWrite()`, so the ISR cost is ~sub-microsecond.
- Lower `DAC_SAMPLE_RATE` from 100 kHz to **80 kHz**. Not strictly required once
  the ISR is cheap, but it adds CPU/interrupt headroom while still giving a clean
  sine (>=10 samples/cycle at the 8 kHz tone).

The public API (`setupSound`, `startToneAsync(freq)`, `stopToneAsync`, the
`ttlToneOn/Off` pulses) is unchanged, so `cue-guided-task.ino` is untouched.

## Alternatives considered

- **Just lower the sample rate, keep `sin()` in the ISR.** — Rejected: software
  `sin()` can exceed even a 25-40 us budget, so the starvation risk remains and
  the result is fragile/jittery rather than robustly fixed.
- **Switch to the Arduino `tone()` library on a digital pin.** — Rejected: it
  produces a square wave (rich in harmonics), not the clean sine the task wants,
  and it would move output off DAC0 and change the amp wiring.
- **Keep `analogWrite()` in the ISR (as the blocking bench test does).** —
  Acceptable but heavier (~2-3 us/call); a direct register write is cheaper and
  leaves more headroom. The bench test (`firmware/tests/sound_test/...`) uses the
  same wavetable + phase-accumulator math in a blocking loop; this ADR moves that
  proven technique into the non-blocking ISR.

## Consequences

- The tone is now non-blocking in practice: `loop()` keeps reading sensors and
  servicing serial/USB while a tone plays, so beams register and uploads work
  without unplugging.
- Tone frequency resolution is set by the 32-bit accumulator (sub-Hz); waveform
  quality is set by the 256-entry table and the 80 kHz rate (clean for 4-8 kHz).
- `sin()`/float math only runs at setup, never per-sample.
- Recovery note for a board still wedged by old firmware: press **ERASE then
  RESET** on the Due (or upload via the **Programming port**) to force the
  bootloader, instead of unplugging.

## Evidence / links

- Code: `firmware/cue-guided-task/sound.h` — `buildSineTable`, 32-bit
  `phaseAcc`/`phaseInc`, `TC0_Handler` (integer-only, direct DAC write).
- Config: `firmware/common/rig_config.h` — `DAC_SAMPLE_RATE` 100 kHz -> 80 kHz.
- Prior art: `firmware/tests/sound_test/due_sound_test/due_sound_test.ino`
  (wavetable + phase accumulator, blocking reference).
