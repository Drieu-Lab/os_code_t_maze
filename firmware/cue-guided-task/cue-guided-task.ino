/*
 * cue-guided-task.ino - imperative shell for the cue-guided T-maze task.
 *
 * Architecture: functional core + imperative shell.
 *   - task_core.h holds the pure decision logic (Session, step(), no I/O).
 *   - This .ino reads hardware into Inputs, calls step(), and performs the
 *     returned Effects (DAC tone, TTL pulses, valves, serial logging).
 *
 * Pin map and tunables live in rig_config.h, a symlink to the canonical
 * ../common/rig_config.h (see docs/decisions/0006). Serial output follows
 * protocol v2: every line is "<ms>,<TYPE>,<payload...>". See ../PROTOCOL.md.
 */

#include "rig_config.h"
#include "sound.h"        // DAC tone engine + TTLPulse + tone TTLs (owns tone TTL timing)
#include "task_core.h"    // pure core

#include <string.h>

// Arduino Due console: Native USB (SerialUSB) by default, matching tmaze_test so
// the broker/GUIs use the same port. To use the Programming port instead, set
// DUE_USE_NATIVE_USB 0 below (or -D it). CONSOLE replaces Serial throughout.
#if defined(__SAM3X8E__)
#ifndef DUE_USE_NATIVE_USB
#define DUE_USE_NATIVE_USB 1
#endif
#if DUE_USE_NATIVE_USB
#define CONSOLE SerialUSB
#else
#define CONSOLE Serial
#endif
#else
#define CONSOLE Serial
#endif

// ---------------- Pin tables (from rig_config.h) ----------------
const int sensorPins[BEAM_COUNT] = SENSOR_PINS;   // order matches Beam enum
const int lickPins[3]            = LICK_PINS;     // display/monitor only
const int valvePins[3]           = VALVE_PINS;    // order matches Valve enum

// ---------------- Choice / return-arm TTLs (shell-owned) ----------------
// (tone on/off TTLs are owned by sound.h, driven next to the DAC ISR.)
TTLPulse ttlChoice    = {PIN_TTL_CHOICE,     0, TTL_PULSE_MS, false};
TTLPulse ttlReturnArm = {PIN_TTL_RETURN_ARM, 0, TTL_PULSE_MS, false};

// ---------------- Shell state ----------------
TaskConfig cfg;
Session    session;
int8_t     gSeq[MAX_TRIALS];

// Non-blocking reward valves
unsigned long valveOffAt[3] = {0, 0, 0};
bool          valveActive[3] = {false, false, false};

// Sensor / lick debounce state
bool          sensRaw[BEAM_COUNT], sensStable[BEAM_COUNT];
unsigned long sensLastChange[BEAM_COUNT];
bool          lickRaw[3], lickStable[3];
unsigned long lickLastChange[3];

// Serial command accumulator
char    cmdBuf[16];
uint8_t cmdLen = 0;

// ---------------- Serial output (protocol v2) ----------------
void printSL(unsigned long ms, const char* type, int idx, int state) {
  CONSOLE.print(ms); CONSOLE.print(','); CONSOLE.print(type);
  CONSOLE.print(','); CONSOLE.print(idx);
  CONSOLE.print(','); CONSOLE.println(state);
}

void printLog(unsigned long ms, const Effect& e) {
  CONSOLE.print(ms); CONSOLE.print(','); CONSOLE.print(e.type);
  if (e.a != FX_NONE) { CONSOLE.print(','); CONSOLE.print(e.a); }
  if (e.b != FX_NONE) { CONSOLE.print(','); CONSOLE.print(e.b); }
  if (e.detail && e.detail[0]) { CONSOLE.print(','); CONSOLE.print(e.detail); }
  CONSOLE.println();
}

// ---------------- Reward valves ----------------
void openValvePin(int port) {
  if (port < 0 || port > 2) return;
  digitalWrite(valvePins[port], HIGH);
  valveOffAt[port] = millis() + REWARD_PULSE_MS;
  valveActive[port] = true;
}

void updateValves() {
  unsigned long now = millis();
  for (int i = 0; i < 3; i++) {
    if (valveActive[i] && (long)(now - valveOffAt[i]) >= 0) {
      digitalWrite(valvePins[i], LOW);
      valveActive[i] = false;
    }
  }
}

// ---------------- Serial command channel ----------------
Command parseCommand(const char* b) {
  if (!strcmp(b, "ABORT") || !strcmp(b, "STOP")) return Command{Command::Abort, 0};
  if (!strcmp(b, "PING"))   return Command{Command::Ping, 0};
  if (!strcmp(b, "STATUS")) return Command{Command::Status, 0};
  // R<port>[<sol>] - manual valve flush; trailing solenoid digit (maze_gui) ignored
  if (b[0] == 'R' && b[1] >= '0' && b[1] <= '2') return Command{Command::Flush, (int8_t)(b[1] - '0')};
  return Command{Command::None, 0};
}

Command drainSerial() {
  Command last = {Command::None, 0};
  while (CONSOLE.available()) {
    char c = CONSOLE.read();
    if (c == '\r') continue;
    if (c == '\n') {
      cmdBuf[cmdLen] = '\0';
      if (cmdLen > 0) {
        Command k = parseCommand(cmdBuf);
        if (k.kind != Command::None) last = k;
      }
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    } else {
      cmdLen = 0;  // overrun: drop the line
    }
  }
  return last;
}

// ---------------- Inputs: debounce + edges + commands ----------------
Inputs readInputs() {
  Inputs in;
  unsigned long now = millis();
  unsigned long t = now - session.startMs;
  in.now = now;

  // Body beams: active-LOW, debounced; beam[i] is a RISING-edge flag for the core.
  for (int i = 0; i < BEAM_COUNT; i++) {
    bool raw = (digitalRead(sensorPins[i]) == LOW);
    if (raw != sensRaw[i]) { sensRaw[i] = raw; sensLastChange[i] = now; }
    bool rising = false;
    if (raw != sensStable[i] && (now - sensLastChange[i]) >= SENSOR_DEBOUNCE_MS) {
      sensStable[i] = raw;
      printSL(t, "S", i, raw ? 1 : 0);
      if (raw) rising = true;
    }
    in.beam[i] = rising;
  }

  // Lick detectors: active-HIGH, debounced; monitor/display only (not fed to core).
  for (int i = 0; i < 3; i++) {
    bool raw = (digitalRead(lickPins[i]) == HIGH);
    if (raw != lickRaw[i]) { lickRaw[i] = raw; lickLastChange[i] = now; }
    if (raw != lickStable[i] && (now - lickLastChange[i]) >= SENSOR_DEBOUNCE_MS) {
      lickStable[i] = raw;
      printSL(t, "L", i, raw ? 1 : 0);
    }
  }

  in.tonePlaying = tonePlaying;
  in.cmd         = drainSerial();
  return in;
}

// ---------------- Apply described effects to hardware ----------------
void applyEffects(const Effects& fx) {
  unsigned long t = millis() - session.startMs;
  for (uint8_t i = 0; i < fx.n; i++) {
    const Effect& e = fx.items[i];
    switch (e.tag) {
      case FxTag::StartTone:
        startToneAsync((float)e.a);
        break;
      case FxTag::StopTone:
        stopToneAsync();
        break;
      case FxTag::PulseChoice:
        ttlChoice.startTime = millis(); ttlChoice.active = true;
        digitalWrite(ttlChoice.pin, HIGH);
        break;
      case FxTag::PulseReturn:
        ttlReturnArm.startTime = millis(); ttlReturnArm.active = true;
        digitalWrite(ttlReturnArm.pin, HIGH);
        break;
      case FxTag::OpenValve:
        openValvePin((int)e.a);
        break;
      case FxTag::Log:
        printLog(t, e);
        break;
    }
  }
}

// ---------------- Config + seeding + startup dump ----------------
TaskConfig makeConfig() {
  TaskConfig c;
  c.highToneIsLeft     = HIGH_TONE_IS_LEFT;
  c.startRewardEnabled = START_REWARD_ENABLED;
  c.maxTrials          = MAX_TRIALS;
  c.maxTimeMs          = MAX_TIME_MS;
  c.blockSize          = BLOCK_SIZE;
  c.highToneHz         = TONE_HIGH_HZ;
  c.lowToneHz          = TONE_LOW_HZ;
  return c;
}

uint32_t makeSeed() {
  // Floating analog pin -> per-session entropy. CONFIRM A0 is unconnected.
  uint32_t s = 0;
  for (int i = 0; i < 32; i++) s = (s << 1) ^ (uint32_t)analogRead(A0);
  if (s == 0) s = 0xA5A5A5A5u;
  return s;
}

void logTrialSequence() {
  CONSOLE.print("0,SEQ_START,"); CONSOLE.println(cfg.maxTrials);
  for (int i = 0; i < cfg.maxTrials; i++) {
    CONSOLE.print("0,SEQ,"); CONSOLE.print(i + 1);
    CONSOLE.print(','); CONSOLE.println(gSeq[i] == 0 ? "LEFT" : "RIGHT");
  }
  CONSOLE.println("0,SEQ_END");
}

// ---------------- Setup ----------------
void setup() {
  CONSOLE.begin(115200);
#if defined(__SAM3X8E__) && DUE_USE_NATIVE_USB
  while (!SerialUSB) { delay(10); }  // wait for the native USB CDC host to attach
#endif

  for (int i = 0; i < BEAM_COUNT; i++) {
    pinMode(sensorPins[i], INPUT_PULLUP);
    sensRaw[i] = (digitalRead(sensorPins[i]) == LOW);
    sensStable[i] = sensRaw[i];
    sensLastChange[i] = millis();
  }
  for (int i = 0; i < 3; i++) {
    pinMode(lickPins[i], INPUT_PULLUP);
    lickRaw[i] = (digitalRead(lickPins[i]) == HIGH);
    lickStable[i] = lickRaw[i];
    lickLastChange[i] = millis();
  }
  for (int i = 0; i < 3; i++) { pinMode(valvePins[i], OUTPUT); digitalWrite(valvePins[i], LOW); }

  pinMode(ttlChoice.pin,    OUTPUT); digitalWrite(ttlChoice.pin,    LOW);
  pinMode(ttlReturnArm.pin, OUTPUT); digitalWrite(ttlReturnArm.pin, LOW);
  pinMode(ttlToneOn.pin,    OUTPUT); digitalWrite(ttlToneOn.pin,    LOW);
  pinMode(ttlToneOff.pin,   OUTPUT); digitalWrite(ttlToneOff.pin,   LOW);

  setupSound();   // DAC + timer ISR

  cfg = makeConfig();
  uint32_t seed = makeSeed();
  session = initialSession(millis(), seed);
  session.rng = generateSequence(gSeq, cfg, seed);

  // Startup protocol lines (ms = 0 by convention before the session clock starts)
  CONSOLE.println("0,PROTO,2");
  CONSOLE.print("0,ANIMAL,"); CONSOLE.println(ANIMAL_ID);
  CONSOLE.print("0,SEED,");   CONSOLE.println(seed);
  logTrialSequence();

  // Begin the session clock now, so trial timestamps start near 0.
  session.startMs = millis();
}

// ---------------- Loop ----------------
void loop() {
  Inputs in = readInputs();   // also drains serial + prints S,/L, edges

  if (session.active) {
    Effects fx; fx.n = 0;
    session = step(session, in, cfg, gSeq, fx);
    applyEffects(fx);
  }

  updateTTLPulse(ttlToneOn);
  updateTTLPulse(ttlToneOff);
  updateTTLPulse(ttlChoice);
  updateTTLPulse(ttlReturnArm);
  updateValves();
}
