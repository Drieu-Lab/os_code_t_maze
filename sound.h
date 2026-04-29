#ifndef SOUND_H
#define SOUND_H

#include <Arduino.h>

const int DAC_PIN = DAC0;        // Arduino Due DAC output
const float SAMPLE_RATE = 100000; // 100 kHz DAC updates

// Sweep control
volatile bool sweepPlaying = false;
volatile unsigned long sweepSamples = 0;
volatile unsigned long sweepIndex = 0;
volatile float sweepFStart = 0;
volatile float sweepFEnd = 0;
volatile float phase = 0;       // phase accumulator

// Inter-sweep timing
unsigned long lastSweepEndTime = 0;
unsigned long interSweepInterval = 50; // ms between sweeps

// --- Non-blocking TTL pulse structure ---
struct TTLPulse {
    int pin;
    unsigned long startTime;
    unsigned long duration;
    bool active;
};

TTLPulse ttlSweepOn  = {34,0,5,false};
TTLPulse ttlSweepOff = {35,0,5,false};

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

// --- Start sweep asynchronously ---
void startSweepAsync(float fStart, float fEnd, unsigned long duration_ms) {
  if(sweepPlaying) return;

  sweepFStart = fStart;
  sweepFEnd = fEnd;
  sweepSamples = duration_ms * SAMPLE_RATE / 1000;
  sweepIndex = 0;
  phase = 0;
  sweepPlaying = true;

  // Start TTL pulse
  ttlSweepOn.startTime = millis();
  ttlSweepOn.active = true;
  digitalWrite(ttlSweepOn.pin,HIGH);
}

// --- Stop sweep ---
void stopSweepAsync() {
  sweepPlaying = false;
  analogWrite(DAC_PIN, 0);     // reset DAC
  lastSweepEndTime = millis();

  // Start TTL offset pulse
  ttlSweepOff.startTime = millis();
  ttlSweepOff.active = true;
  digitalWrite(ttlSweepOff.pin,HIGH);
}

// --- Timer interrupt for DAC updates ---
void TC0_Handler() {
  TC_GetStatus(TC0, 0); // clear interrupt flag
  if(!sweepPlaying) return;

  if(sweepIndex >= sweepSamples){
    stopSweepAsync();
    return;
  }

  // --- Phase accumulation FM sweep ---
  float frac = (float)sweepIndex / sweepSamples;
  float freq = sweepFStart + (sweepFEnd - sweepFStart) * frac;
  float phaseIncrement = 2.0 * PI * freq / SAMPLE_RATE;
  phase += phaseIncrement;
  if(phase > 2.0 * PI) phase -= 2.0 * PI;

  float val = 0.5 + 0.5 * sin(phase); // 0-1
  int dacVal = (int)(val * 4095);
  analogWrite(DAC_PIN,dacVal);

  sweepIndex++;
}

// --- Check TTL pulses in main loop ---
void updateTTLPulse(TTLPulse &pulse){
  if(pulse.active && millis() - pulse.startTime >= pulse.duration){
    digitalWrite(pulse.pin,LOW);
    pulse.active = false;
  }
}

// --- Helper: check inter-sweep timing ---
bool canStartNextSweep(){
  return (!sweepPlaying) && (millis() - lastSweepEndTime >= interSweepInterval);
}

#endif
