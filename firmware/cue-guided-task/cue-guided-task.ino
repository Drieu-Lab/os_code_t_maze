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
 * protocol v4: every line is "<ms>,<TYPE>,<payload...>". See ../PROTOCOL.md.
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
// Full 15-solenoid table [port][R1,R2,R3,DRAIN,RINSE]; ports 0=left 1=right
// 2=center(start). Same layout as tmaze_test so SOL,<port>,<sol> matches.
const int solenoidPins[3][SOLENOIDS_PER_PORT] = SOLENOID_TABLE;

// ---------------- Choice / return-arm TTLs (shell-owned) ----------------
// (tone on/off TTLs are owned by sound.h, driven next to the DAC ISR.)
TTLPulse ttlChoice    = {PIN_TTL_CHOICE,     0, TTL_PULSE_MS, false};
TTLPulse ttlReturnArm = {PIN_TTL_RETURN_ARM, 0, TTL_PULSE_MS, false};

// ---------------- Shell state ----------------
TaskConfig cfg;
Session    session;
int8_t     gSeq[MAX_TRIALS];

// Non-blocking 15-solenoid pulse manager. Each valve can be scheduled with an
// optional start delay (onAt) so a sequence like rinse-then-drain runs without
// ever blocking loop(). Mirrors tmaze_test's capability minus its delay().
unsigned long solOnAt[3][SOLENOIDS_PER_PORT];   // scheduled turn-on (millis); valid while pending
unsigned long solOffAt[3][SOLENOIDS_PER_PORT];  // scheduled turn-off (millis); valid while active/pending
bool          solPending[3][SOLENOIDS_PER_PORT]; // waiting for its delayed start
bool          solActive[3][SOLENOIDS_PER_PORT];  // currently energized

// Sensor / lick debounce state
bool          sensRaw[BEAM_COUNT], sensStable[BEAM_COUNT];
unsigned long sensLastChange[BEAM_COUNT];
bool          lickRaw[3], lickStable[3];
unsigned long lickLastChange[3];

// Serial command accumulator
char    cmdBuf[16];
uint8_t cmdLen = 0;

// ---------------- Serial output (protocol v4) ----------------
void printSL(unsigned long ms, const char* type, int idx, int state) {
  CONSOLE.print(ms); CONSOLE.print(','); CONSOLE.print(type);
  CONSOLE.print(','); CONSOLE.print(idx);
  CONSOLE.print(','); CONSOLE.println(state);
}

void printLog(unsigned long ms, const Effect& e) {
  CONSOLE.print(ms); CONSOLE.print(','); CONSOLE.print(e.type);
  if (e.a != FX_NONE) { CONSOLE.print(','); CONSOLE.print(e.a); }
  if (e.b != FX_NONE) { CONSOLE.print(','); CONSOLE.print(e.b); }
  if (e.c != FX_NONE) { CONSOLE.print(','); CONSOLE.print(e.c); }
  if (e.detail && e.detail[0]) { CONSOLE.print(','); CONSOLE.print(e.detail); }
  // Trailing numeric field after the detail (e.g. REWARD,CENTER,<dur_ms>). see docs/decisions/0019
  if (e.tail != FX_NONE) { CONSOLE.print(','); CONSOLE.print(e.tail); }
  CONSOLE.println();
}

// ---------------- Solenoids (non-blocking) ----------------
// Energize solenoid [port][sol] for `dur` ms, optionally starting `startDelay` ms
// from now (0 = immediately). Re-firing a valve simply reschedules its off-time.
void openSolenoid(int port, int sol, unsigned long dur, unsigned long startDelay = 0) {
  if (port < 0 || port > 2 || sol < 0 || sol >= SOLENOIDS_PER_PORT) return;
  unsigned long now = millis();
  if (startDelay == 0) {
    digitalWrite(solenoidPins[port][sol], HIGH);
    solPending[port][sol] = false;
    solActive[port][sol]  = true;
    solOffAt[port][sol]   = now + dur;
  } else {
    solPending[port][sol] = true;
    solActive[port][sol]  = false;
    solOnAt[port][sol]    = now + startDelay;
    solOffAt[port][sol]   = now + startDelay + dur;
  }
}

void updateValves() {
  unsigned long now = millis();
  for (int p = 0; p < 3; p++) {
    for (int s = 0; s < SOLENOIDS_PER_PORT; s++) {
      if (solPending[p][s] && (long)(now - solOnAt[p][s]) >= 0) {
        digitalWrite(solenoidPins[p][s], HIGH);
        solPending[p][s] = false;
        solActive[p][s]  = true;
      }
      if (solActive[p][s] && (long)(now - solOffAt[p][s]) >= 0) {
        digitalWrite(solenoidPins[p][s], LOW);
        solActive[p][s] = false;
      }
    }
  }
}

// Manual solenoid fire: R<port><sol>[,<ms>]. Handled entirely shell-side and
// independent of session.active (so the buttons work in any mode, even after a
// session completes), mirroring tmaze_test but NON-BLOCKING. Emits a SOL ack so
// the Rig view flashes the exact marker, and for the reward spout also
// REWARD,<port>,MANUAL for the behavioral log. A bare R<port> fires the reward
// spout (REWARD_SOLENOID_INDEX). see docs/decisions/0010
void handleManualSolenoid(const char* b) {
  if (b[0] != 'R' || b[1] < '0' || b[1] > '2') return;
  int port = b[1] - '0';
  int sol  = REWARD_SOLENOID_INDEX;
  if (b[2] >= '0' && b[2] <= '4') sol = b[2] - '0';
  unsigned long dur = REWARD_PULSE_MS;
  const char* comma = strchr(b, ',');
  if (comma && comma[1]) {
    long v = 0;
    for (const char* p = comma + 1; *p >= '0' && *p <= '9'; p++) v = v * 10 + (*p - '0');
    if (v > 0) dur = (unsigned long)(v > 5000 ? 5000 : v);
  }
  openSolenoid(port, sol, dur);
  unsigned long t = millis() - session.startMs;
  // Trailing <dur_ms> lets the host map the pulse to a calibrated volume. see docs/decisions/0019
  CONSOLE.print(t); CONSOLE.print(",SOL,"); CONSOLE.print(port);
  CONSOLE.print(','); CONSOLE.print(sol);
  CONSOLE.print(','); CONSOLE.println(dur);
  if (sol == REWARD_SOLENOID_INDEX) {
    CONSOLE.print(t); CONSOLE.print(",REWARD,"); CONSOLE.print(port);
    CONSOLE.print(",MANUAL,"); CONSOLE.println(dur);
  }
}

// ---------------- Host-driven positional cue (protocol v5) ----------------
// The HOST owns the ON/OFF decision from the rat's tracked position; the firmware
// just makes the sound and (via startTone/stopToneAsync) fires the TONE_ON/OFF
// TTLs. Identity stays in firmware: CHOICE uses the current trial's correctArm;
// RETURN is a random hi/lo from the session PRNG. Handled shell-side (like
// R<port>), session-independent. The CUE line reflects the ACTUAL acoustic state
// (emitted only on a real start/stop, never a no-op). see docs/decisions/0044
void handleCueOn(const char* kind) {
  uint16_t freq;
  const char* type;
  if (!strcmp(kind, "CHOICE")) {
    freq = cueFor(session.trial.correctArm, cfg);   // -1 arm (no trial) -> low-tone fallback
    type = "CHOICE";
  } else if (!strcmp(kind, "RETURN")) {
    session.rng = lcg(session.rng);                 // advance the threaded PRNG
    bool high = ((session.rng >> 16) & 1u);
    freq = high ? cfg.highToneHz : cfg.lowToneHz;
    type = "RETURN";
  } else {
    return;                                          // unknown cue kind: ignore
  }
  if (tonePlaying) return;                           // already sounding: don't relog / retrigger
  startToneAsync((float)freq);                       // also raises the TONE_ON TTL
  unsigned long t = millis() - session.startMs;
  CONSOLE.print(t); CONSOLE.print(",CUE,ON,");
  CONSOLE.print(type); CONSOLE.print(','); CONSOLE.println(freq);
}

void handleCueOff() {
  if (!tonePlaying) return;                          // nothing playing: don't pulse a spurious TTL
  stopToneAsync();                                   // also raises the TONE_OFF TTL
  unsigned long t = millis() - session.startMs;
  CONSOLE.print(t); CONSOLE.println(",CUE,OFF");
}

// ---------------- Serial command channel ----------------
Command parseCommand(const char* b) {
  if (!strcmp(b, "ABORT") || !strcmp(b, "STOP")) return Command{Command::Abort, 0};
  if (!strcmp(b, "PING"))   return Command{Command::Ping, 0};
  if (!strcmp(b, "STATUS")) return Command{Command::Status, 0};
  // MODE,FREE | MODE,FULL - switch training mode (and (re)start the session)
  if (!strcmp(b, "MODE,FREE")) return Command{Command::SetMode, (int8_t)Mode::FreeRoam};
  if (!strcmp(b, "MODE,FULL")) return Command{Command::SetMode, (int8_t)Mode::FullTask};
  // R<port><sol>[,<ms>] is NOT a core command: it is handled shell-side in
  // drainSerial() via handleManualSolenoid().
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
        // Manual solenoid fires are a shell concern (session-independent); all
        // other lines parse into a core Command.
        if (cmdBuf[0] == 'R' && cmdBuf[1] >= '0' && cmdBuf[1] <= '2') {
          handleManualSolenoid(cmdBuf);
        } else if (!strncmp(cmdBuf, "CUE_ON,", 7)) {
          handleCueOn(cmdBuf + 7);                 // host-driven cue on (v5)
        } else if (!strcmp(cmdBuf, "CUE_OFF")) {
          handleCueOff();                          // host-driven cue off (v5)
        } else if (!strcmp(cmdBuf, "CUE,VISION")) {
          cfg.cueSourceVision = true;              // host drives the cue; suppress beam auto-tone
        } else if (!strcmp(cmdBuf, "CUE,BEAM")) {
          cfg.cueSourceVision = false;             // firmware beam-driven auto-tone (v4 behavior)
        } else {
          Command k = parseCommand(cmdBuf);
          if (k.kind != Command::None) last = k;
        }
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
        // Automatic task reward: the configured reward spout (R2) of the port.
        // Drive from cfg.rewardPulseMs so the fired duration matches the value the
        // core logs on the REWARD,CENTER line (both == REWARD_PULSE_MS). see docs/decisions/0019
        openSolenoid((int)e.a, REWARD_SOLENOID_INDEX, cfg.rewardPulseMs);
        break;
      case FxTag::CleanWell: {
        // Auto well-clean at trial start: rinse the central well now, then drain
        // after the rinse (non-blocking). Emit a SOL ack for each so the Rig view
        // flashes and the clean is in the log. e.a=rinse ms, e.b=drain ms.
        const int port = (int)V_START;   // central/start well
        openSolenoid(port, RINSE_SOLENOID_INDEX, (unsigned long)e.a, 0);
        openSolenoid(port, DRAIN_SOLENOID_INDEX, (unsigned long)e.b, (unsigned long)e.a);
        // Trailing <dur_ms> on each SOL line (rinse=e.a, drain=e.b). see docs/decisions/0019
        CONSOLE.print(t); CONSOLE.print(",SOL,"); CONSOLE.print(port);
        CONSOLE.print(','); CONSOLE.print(RINSE_SOLENOID_INDEX);
        CONSOLE.print(','); CONSOLE.println((unsigned long)e.a);
        CONSOLE.print(t); CONSOLE.print(",SOL,"); CONSOLE.print(port);
        CONSOLE.print(','); CONSOLE.print(DRAIN_SOLENOID_INDEX);
        CONSOLE.print(','); CONSOLE.println((unsigned long)e.b);
        break;
      }
      case FxTag::Log:
        printLog(t, e);
        break;
    }
  }
}

// ---------------- Config + seeding + startup dump ----------------
TaskConfig makeConfig() {
  TaskConfig c;
  c.mode               = DEFAULT_MODE;
  c.highToneIsLeft     = HIGH_TONE_IS_LEFT;
  c.startRewardEnabled = START_REWARD_ENABLED;
  c.maxTrials          = MAX_TRIALS;
  c.maxTimeMs          = MAX_TIME_MS;
  c.blockSize          = BLOCK_SIZE;
  c.maxSameRun         = MAX_SAME_RUN;
  c.autoDrainOnTrial   = AUTO_DRAIN_ON_TRIAL;
  c.rinseMs            = WELL_RINSE_MS;
  c.drainMs            = WELL_DRAIN_MS;
  c.rewardPulseMs      = REWARD_PULSE_MS;   // logged on REWARD,CENTER for host volume calc
  c.highToneHz         = TONE_HIGH_HZ;
  c.lowToneHz          = TONE_LOW_HZ;
  c.cueSourceVision    = DEFAULT_CUE_SOURCE_VISION;  // host-driven cue off by default. see docs/decisions/0044
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

// Emit a session-boundary MODE marker (ms = 0 by convention, like the handshake).
void logModeLine(const char* name) {
  CONSOLE.print("0,MODE,"); CONSOLE.println(name);
}

// ---------------- Mode / session lifecycle ----------------
// Both helpers (re)initialize the session clock; switching mode at runtime via
// the MODE command starts a fresh session. FreeRoam has no trial sequence; the
// trial state machine is gated off in task_core's step(). see docs/decisions/0009
void startFreeRoam() {
  cfg.mode = Mode::FreeRoam;
  uint32_t seed = makeSeed();
  session = initialSession(millis(), seed);
  logModeLine("FREEROAM");
  session.startMs = millis();
}

void startFullTask() {
  cfg.mode = Mode::FullTask;
  uint32_t seed = makeSeed();
  session = initialSession(millis(), seed);
  session.rng = generateSequence(gSeq, cfg, seed);
  CONSOLE.print("0,SEED,"); CONSOLE.println(seed);
  logTrialSequence();
  logModeLine("FULLTASK");
  session.startMs = millis();
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
  for (int p = 0; p < 3; p++) {
    for (int s = 0; s < SOLENOIDS_PER_PORT; s++) {
      pinMode(solenoidPins[p][s], OUTPUT);
      digitalWrite(solenoidPins[p][s], LOW);
      solPending[p][s] = false;
      solActive[p][s]  = false;
    }
  }

  pinMode(ttlChoice.pin,    OUTPUT); digitalWrite(ttlChoice.pin,    LOW);
  pinMode(ttlReturnArm.pin, OUTPUT); digitalWrite(ttlReturnArm.pin, LOW);
  pinMode(ttlToneOn.pin,    OUTPUT); digitalWrite(ttlToneOn.pin,    LOW);
  pinMode(ttlToneOff.pin,   OUTPUT); digitalWrite(ttlToneOff.pin,   LOW);

  setupSound();   // DAC + timer ISR

  cfg = makeConfig();

  // Startup protocol lines (ms = 0 by convention before the session clock starts)
  CONSOLE.print("0,PROTO,");  CONSOLE.println(PROTO_VERSION);
  CONSOLE.print("0,ANIMAL,"); CONSOLE.println(ANIMAL_ID);

  // Boot into the configured default mode. FreeRoam is the safe default: no
  // trials run until the experimenter selects Full task (MODE,FULL), so a
  // session never auto-starts on the wrong animal. see docs/decisions/0009
  if (cfg.mode == Mode::FullTask) startFullTask();
  else                            startFreeRoam();
}

// ---------------- Loop ----------------
void loop() {
  Inputs in = readInputs();   // also drains serial + prints S,/L, edges

  // Mode switch is a shell concern (it re-seeds, regenerates the sequence, and
  // re-dumps the handshake), so it is handled here rather than in the pure core.
  if (in.cmd.kind == Command::SetMode) {
    // A mode switch starts a fresh session; silence any tone the host (or a v4
    // beam cue) left playing so a new session never begins mid-tone. see docs/decisions/0044
    if (tonePlaying) stopToneAsync();
    if (in.cmd.arg == (int8_t)Mode::FullTask) startFullTask();
    else                                      startFreeRoam();
  } else if (session.active) {
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
