#ifndef SOUND_H
#define SOUND_H

#include <Arduino.h>
#include "rig_config.h"

const int DAC_PIN = DAC0;              // Arduino Due DAC output
const float SAMPLE_RATE = DAC_SAMPLE_RATE; // DAC update rate (Hz), from rig_config.h

// Tone control: a single constant-frequency tone that plays until stopped.
volatile bool  tonePlaying = false;
volatile float toneFreq = 0;    // current tone frequency (Hz)
volatile float phase = 0;       // phase accumulator (radians)

// --- Non-blocking TTL pulse structure ---
struct TTLPulse {
    int pin;
    unsigned long startTime;
    unsigned long duration;
    bool active;
};

TTLPulse ttlToneOn  = {PIN_TTL_TONE_ON,  0, TTL_PULSE_MS, false};
TTLPulse ttlToneOff = {PIN_TTL_TONE_OFF, 0, TTL_PULSE_MS, false};

// --- Setup DAC and timer ---
void setupSound() {
  analogWriteResolution(12);  // 12-bit DAC
  analogWrite(DAC_PIN, 0);

  // Configure Timer0, channel 0 once and leave running
  pmc_set_writeprotect(false);
  pmc_enable_periph_clk(ID_TC0);
  TC_Configure(TC0, 0,
               TC_CMR_TCCLKS_TIMER_CLOCK1 | TC_CMR_WAVE | TC_CMR_WAVSEL_UP_RC);
  TC0->TC_CHANNEL[0].TC_RC = (SystemCoreClock / 2) / SAMPLE_RATE; // 100 kHz
  TC0->TC_CHANNEL[0].TC_IER = TC_IER_CPCS; // interrupt on RC
  TC0->TC_CHANNEL[0].TC_IDR = ~TC_IER_CPCS;
  NVIC_EnableIRQ(TC0_IRQn);
  TC_Start(TC0,0);
}

// --- Start a constant tone asynchronously ---
void startToneAsync(float freq) {
  if(tonePlaying) return;

  toneFreq = freq;
  phase = 0;
  tonePlaying = true;

  // Start TTL pulse
  ttlToneOn.startTime = millis();
  ttlToneOn.active = true;
  digitalWrite(ttlToneOn.pin,HIGH);
}

// --- Stop the tone ---
void stopToneAsync() {
  tonePlaying = false;
  analogWrite(DAC_PIN, 0);     // reset DAC

  // Start TTL offset pulse
  ttlToneOff.startTime = millis();
  ttlToneOff.active = true;
  digitalWrite(ttlToneOff.pin,HIGH);
}

// --- Timer interrupt for DAC updates ---
void TC0_Handler() {
  TC_GetStatus(TC0, 0); // clear interrupt flag
  if(!tonePlaying) return;

  // --- Phase accumulation, constant frequency ---
  float phaseIncrement = 2.0 * PI * toneFreq / SAMPLE_RATE;
  phase += phaseIncrement;
  if(phase > 2.0 * PI) phase -= 2.0 * PI;

  float val = 0.5 + 0.5 * sin(phase); // 0-1
  int dacVal = (int)(val * 4095);
  analogWrite(DAC_PIN,dacVal);
}

// --- Check TTL pulses in main loop ---
void updateTTLPulse(TTLPulse &pulse){
  if(pulse.active && millis() - pulse.startTime >= pulse.duration){
    digitalWrite(pulse.pin,LOW);
    pulse.active = false;
  }
}

#endif
