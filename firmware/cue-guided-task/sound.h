#ifndef SOUND_H
#define SOUND_H

#include <Arduino.h>
#include "rig_config.h"

// =============================== Tone synthesis ==============================
// The Arduino Due (SAM3X8E, Cortex-M3) has NO hardware FPU, so floating-point
// sin() is emulated in software and costs tens of microseconds. Calling it once
// per DAC sample inside the timer ISR overruns the sample budget, the ISR
// re-enters back-to-back, and it starves loop() + the native-USB stack (frozen
// sensors, and uploads that need an unplug). See docs/decisions/0008.
//
// Fix: precompute a sine WAVETABLE once at setup, then in the ISR do only
// integer work: advance a 32-bit phase accumulator and write one table sample
// straight to the DAC register. No sin(), no float, no division in the ISR.

const int   DAC_PIN     = DAC0;               // Arduino Due DAC0 output
const float SAMPLE_RATE = DAC_SAMPLE_RATE;    // DAC update rate (Hz), from rig_config.h

static const uint16_t SINE_TABLE_SIZE = 256;  // power of 2; top 8 bits of phase index it
static const uint16_t DAC_CENTER      = 2048; // 12-bit mid-rail (silent)
static const uint16_t DAC_AMPLITUDE   = 2000; // peak deviation at full volume (< 2048)
// Signed deviation from mid-rail, +/- DAC_AMPLITUDE (fits int16_t). The ISR adds
// this to DAC_CENTER after scaling by the per-tone gain, so a runtime volume can
// attenuate the tone without rebuilding the table. see docs/decisions/0125
int16_t sineDev[SINE_TABLE_SIZE];

// Q8 gain fixed-point unit: toneGainQ8 == TONE_GAIN_UNITY is full volume, and
// the ISR does (dev * gain) >> 8, so the multiply/shift is integer-only.
static const uint16_t TONE_GAIN_UNITY = 256;   // 256/256 == 100%

// Tone control. ISR reads phaseAcc/phaseInc/tonePlaying/toneGainQ8 only.
volatile bool     tonePlaying = false;
volatile uint32_t phaseAcc    = 0;   // 32-bit phase accumulator (wraps == one cycle)
volatile uint32_t phaseInc    = 0;   // per-sample phase step (set when a tone starts)
volatile float    toneFreq    = 0;   // current tone frequency (Hz), for reference/logging
volatile uint16_t toneGainQ8  = TONE_GAIN_UNITY;  // per-tone volume (Q8; set when a tone starts)

// --- Non-blocking TTL pulse structure ---
struct TTLPulse {
    int pin;
    unsigned long startTime;
    unsigned long duration;
    bool active;
};

TTLPulse ttlToneOn  = {PIN_TTL_TONE_ON,  0, TTL_PULSE_MS, false};
TTLPulse ttlToneOff = {PIN_TTL_TONE_OFF, 0, TTL_PULSE_MS, false};

// --- Build the sine lookup table (sin() runs here, ONCE, not in the ISR) ---
static void buildSineTable() {
  for (uint16_t i = 0; i < SINE_TABLE_SIZE; i++) {
    float theta = (2.0f * PI * i) / SINE_TABLE_SIZE;
    sineDev[i] = (int16_t)lroundf(DAC_AMPLITUDE * sinf(theta));  // signed, +/- DAC_AMPLITUDE
  }
}

// --- Setup DAC and timer ---
void setupSound() {
  buildSineTable();

  analogWriteResolution(12);            // 12-bit DAC (0..4095)
  analogWrite(DAC_PIN, DAC_CENTER);     // initialise DACC + channel/pin, park mid-rail (silent)
  dacc_set_channel_selection(DACC, 0);  // DAC0 == DACC channel 0; fix selection for direct writes

  // Configure Timer0, channel 0 but leave it STOPPED: the tone functions start
  // and stop it, so the ISR fires only while a tone sounds. It used to run from
  // boot to power-off - 80,000 interrupts a second spent returning immediately,
  // for the ~1% of the time a cue is playing. see docs/decisions/0063
  pmc_set_writeprotect(false);
  pmc_enable_periph_clk(ID_TC0);
  TC_Configure(TC0, 0,
               TC_CMR_TCCLKS_TIMER_CLOCK1 | TC_CMR_WAVE | TC_CMR_WAVSEL_UP_RC);
  TC0->TC_CHANNEL[0].TC_RC = (SystemCoreClock / 2) / (uint32_t)SAMPLE_RATE;
  TC0->TC_CHANNEL[0].TC_IER = TC_IER_CPCS;     // interrupt on RC compare
  TC0->TC_CHANNEL[0].TC_IDR = ~TC_IER_CPCS;
  NVIC_EnableIRQ(TC0_IRQn);
}

// --- Start a constant tone asynchronously ---
// gainQ8 scales the amplitude (Q8: TONE_GAIN_UNITY == full volume); it defaults
// to full so the beam-driven auto-tone (task_core StartTone) is unchanged. The
// host-driven cue passes a calibrated gain to equalize HIGH/LOW (docs/decisions/0125).
void startToneAsync(float freq, uint16_t gainQ8 = TONE_GAIN_UNITY) {
  if (tonePlaying) return;

  toneFreq = freq;
  toneGainQ8 = (gainQ8 > TONE_GAIN_UNITY) ? TONE_GAIN_UNITY : gainQ8;  // never boost past full
  // inc = freq * 2^32 / fs : the accumulator spans one full table cycle per 2^32.
  phaseInc = (uint32_t)(((uint64_t)((double)freq * 4294967296.0)) / (uint32_t)SAMPLE_RATE);
  phaseAcc = 0;
  tonePlaying = true;
  // Start the timer only after the state above is set, so the first interrupt
  // never samples a half-written tone. Onset costs one sample period (~12.5 us),
  // far below audibility and negligible beside the TONE_ON TTL fired just below.
  TC_Start(TC0, 0);

  ttlToneOn.startTime = millis();
  ttlToneOn.active = true;
  digitalWrite(ttlToneOn.pin, HIGH);
}

// --- Stop the tone ---
void stopToneAsync() {
  tonePlaying = false;
  TC_Stop(TC0, 0);                               // silence the ISR before parking
  dacc_write_conversion_data(DACC, DAC_CENTER);  // park mid-rail (silent)

  ttlToneOff.startTime = millis();
  ttlToneOff.active = true;
  digitalWrite(ttlToneOff.pin, HIGH);
}

// --- Timer interrupt: emit one DAC sample. Integer-only: add + shift + write. ---
// The timer only runs while a tone sounds (see docs/decisions/0063), so this
// normally fires only for real samples; the tonePlaying guard stays to swallow
// any interrupt already pending when the tone stops.
void TC0_Handler() {
  TC_GetStatus(TC0, 0);            // clear interrupt flag
  if (!tonePlaying) return;
  phaseAcc += phaseInc;
  // Scale the signed sample by the Q8 gain (integer multiply + shift), then
  // re-center. gain <= unity so the result stays within 0..4095. see docs/decisions/0125
  int32_t sample = DAC_CENTER +
      (((int32_t)sineDev[phaseAcc >> 24] * (int32_t)toneGainQ8) >> 8);  // top 8 bits -> 0..255
  dacc_write_conversion_data(DACC, (uint16_t)sample);
}

// --- Check TTL pulses in main loop ---
void updateTTLPulse(TTLPulse &pulse){
  if(pulse.active && millis() - pulse.startTime >= pulse.duration){
    digitalWrite(pulse.pin,LOW);
    pulse.active = false;
  }
}

#endif
