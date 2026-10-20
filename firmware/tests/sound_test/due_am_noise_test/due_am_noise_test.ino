/*
 * Bench test for the cue engine in ../../../cue-guided-task/sound.h (symlinked
 * here, with rig_config.h, the same way the task sketch shares its config; see
 * docs/decisions/0006). Plays the REAL interrupt-driven cues on DAC0 in a loop:
 *
 *   4 kHz tone (reference) -> 2 Hz SAM noise -> 32 Hz SAM noise, 3 s each,
 *   with 1 s of silence between them.
 *
 * Listen (or scope DAC0): each noise cue should start silent and swell, at 2 or
 * 32 loudness peaks per second. The TONE_ON / TONE_OFF TTL pins pulse at each
 * onset / offset as in the task.
 *
 * CPU cost of the interrupt, measured without touching it: loop() spins a
 * counter for a fixed window with the cue off, then with each cue on. The drop
 * in count is the share of CPU the 80 kHz interrupt takes, and
 * share x 12.5 us is its average time per sample. Results print on the
 * Programming port at 115200 baud. see docs/decisions/0221
 */
#include "rig_config.h"
#include "sound.h"

const uint32_t CUE_MS     = 3000;
const uint32_t SILENCE_MS = 1000;
const float    SAMPLE_PERIOD_US = 1e6f / DAC_SAMPLE_RATE;

uint32_t baselineCount = 0;
uint32_t seedCounter   = 0;

// Spin for `ms` and return how many iterations fit; fewer iterations while a
// cue plays means the interrupt took that much CPU. The TTL pulses are ended
// here too, so every window does the same work apart from the interrupt.
uint32_t spinCount(uint32_t ms) {
  volatile uint32_t n = 0;
  uint32_t t0 = millis();
  while (millis() - t0 < ms) {
    updateTTLPulse(ttlToneOn);
    updateTTLPulse(ttlToneOff);
    n++;
  }
  return n;
}

void report(const char* label, uint32_t count) {
  float load = 1.0f - (float)count / (float)baselineCount;
  Serial.print(label);
  Serial.print(": interrupt load ");
  Serial.print(100.0f * load, 1);
  Serial.print(" % of CPU, ~");
  Serial.print(load * SAMPLE_PERIOD_US, 2);
  Serial.println(" us per sample");
}

void playAm(const char* label, float rateHz) {
  uint32_t seed = 0x9E3779B9u * ++seedCounter;
  startAmNoiseAsync(rateHz, TONE_GAIN_UNITY, seed);
  uint32_t n = spinCount(CUE_MS);
  stopToneAsync();
  Serial.print(label);
  Serial.print(" seed=");
  Serial.println(seed);
  report(label, n);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TTL_TONE_ON, OUTPUT);
  pinMode(PIN_TTL_TONE_OFF, OUTPUT);
  setupSound();
  delay(500);
  baselineCount = spinCount(CUE_MS);
  Serial.print("baseline spin count (cue off): ");
  Serial.println(baselineCount);
}

void loop() {
  startToneAsync(TONE_LOW_HZ);
  uint32_t n = spinCount(CUE_MS);
  stopToneAsync();
  report("tone 4 kHz", n);
  spinCount(SILENCE_MS);

  playAm("AM noise 2 Hz", 2.0f);
  spinCount(SILENCE_MS);

  playAm("AM noise 32 Hz", 32.0f);
  spinCount(SILENCE_MS);
}
