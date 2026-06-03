/*
 * 440 Hz sine wave on Arduino Due DAC0 -> PAM8403
 *
 * Plays 440 Hz for 3 s, silence for 3 s, repeat.
 * DAC is 12-bit (0..4095). Output swings ~0.55 V to ~2.75 V.
 * The PAM8403's input cap removes the DC offset, so what the
 * amp sees is a ~2.2 Vpp sine wave centered on its own bias.
 */
const uint8_t  DAC_PIN       = DAC0;
const float    TONE_FREQ_HZ  = 440.0f;
const uint32_t SAMPLE_RATE   = 22050;        // Hz
const uint16_t TABLE_SIZE    = 256;          // must be power of 2
const uint32_t TONE_ON_MS    = 3000;
const uint32_t TONE_OFF_MS   = 3000;
const uint16_t DAC_CENTER    = 2048;         // mid-scale (silent)
const uint16_t DAC_AMPLITUDE = 2000;         // peak deviation (<2048)
uint16_t sineTable[TABLE_SIZE];
void buildSineTable() {
  for (uint16_t i = 0; i < TABLE_SIZE; i++) {
    float theta = (2.0f * PI * i) / TABLE_SIZE;
    sineTable[i] = (uint16_t)(DAC_CENTER + DAC_AMPLITUDE * sinf(theta));
  }
}
void setup() {
  analogWriteResolution(12);                 // 0..4095 on the DAC
  analogWrite(DAC_PIN, DAC_CENTER);          // park at mid-rail (silent)
  buildSineTable();
}
void playTone(float freqHz, uint32_t durationMs) {
  // Phase accumulator: 32-bit, natural overflow = one full cycle.
  // Top 8 bits index the 256-entry table.
  const uint32_t phaseInc =
      (uint32_t)(((uint64_t)(freqHz * 4294967296.0)) / SAMPLE_RATE);
  const uint32_t sampleIntervalUs = 1000000UL / SAMPLE_RATE;
  uint32_t phase        = 0;
  uint32_t startMs      = millis();
  uint32_t lastSampleUs = micros();
  while (millis() - startMs < durationMs) {
    while (micros() - lastSampleUs < sampleIntervalUs) { /* wait */ }
    lastSampleUs += sampleIntervalUs;
    uint16_t idx = phase >> 24;              // top 8 bits -> 0..255
    analogWrite(DAC_PIN, sineTable[idx]);
    phase += phaseInc;
  }
  analogWrite(DAC_PIN, DAC_CENTER);          // silence between tones
}
void loop() {
  //playTone(TONE_FREQ_HZ, TONE_ON_MS);
  delay(TONE_OFF_MS);
}